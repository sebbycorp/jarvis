# PiCrawler control — operational shortcuts.
# Uses the 'pi-crawler' SSH alias (key auth). Override: make PI=user@host <target>
PI  ?= pi-crawler
VENV = ~/picrawler-app/.venv/bin/python
APP  = ~/picrawler-app

.PHONY: help test lint deploy preflight status battery logs restart stop-all \
        install-services mcp-add walk stand rest web ai ssh \
        gw-apply gw-status gw-forward gw-test gw-mcp-add

GWCTX ?= kind-agw-picrawler

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

test: ## Run off-robot unit tests (mocked hardware, no Pi needed)
	python3 -m unittest discover -s tests -v

lint: ## Byte-compile all robot modules to catch syntax errors
	python3 -m py_compile robot/*.py robot/web/app.py

deploy: ## Sync robot/ to the Pi (excludes .env, venv, photos)
	./scripts/deploy.sh

preflight: ## Run the health-check on the Pi
	ssh $(PI) "cd $(APP) && $(VENV) preflight.py"

status: ## Show controller status + battery
	ssh $(PI) "cd $(APP) && $(VENV) -c 'from picrawler_ctl import get_controller as g; print(g().status())'"

battery: ## Read battery voltage only
	ssh $(PI) "$(VENV) -c 'from robot_hat import get_battery_voltage as v; print(round(v(),2),\"V\")'"

logs: ## Tail the MCP service logs
	ssh $(PI) "journalctl -u picrawler-mcp -n 60 --no-pager"

restart: ## Restart the MCP service
	ssh $(PI) "sudo systemctl restart picrawler-mcp && systemctl is-active picrawler-mcp"

stop-all: ## Stop MCP + web services (free the HAT/camera for manual use)
	ssh $(PI) "sudo systemctl stop picrawler-mcp picrawler-web 2>/dev/null; echo stopped"

install-services: ## Copy + enable systemd units (mcp autostart; web on-demand)
	scp scripts/picrawler-mcp.service scripts/picrawler-web.service $(PI):/tmp/
	ssh $(PI) "sudo mv /tmp/picrawler-mcp.service /tmp/picrawler-web.service /etc/systemd/system/ && \
	  sudo systemctl daemon-reload && sudo systemctl enable --now picrawler-mcp && \
	  systemctl is-active picrawler-mcp"

mcp-add: ## Register the MCP server with Claude Code
	claude mcp add --transport http picrawler http://172.16.10.117:8000/mcp

walk: ## Quick 2-step forward test (respects battery guard)
	ssh $(PI) "cd $(APP) && $(VENV) -c 'from picrawler_ctl import get_controller as g; print(g().forward(2))'"

stand: ## Stand pose
	ssh $(PI) "cd $(APP) && $(VENV) -c 'from picrawler_ctl import get_controller as g; print(g().stand())'"

rest: ## Rest/sit pose
	ssh $(PI) "cd $(APP) && $(VENV) -c 'from picrawler_ctl import get_controller as g; print(g().rest())'"

web: ## Run the web panel in the foreground (stops MCP first for HAT access)
	ssh $(PI) "sudo systemctl stop picrawler-mcp; cd $(APP) && $(VENV) web/app.py"

ai: ## Run the AI assistant in the foreground (stops MCP first)
	ssh -t $(PI) "sudo systemctl stop picrawler-mcp; cd $(APP) && $(VENV) ai_assistant.py"

ssh: ## Open a shell on the Pi
	ssh $(PI)

# --- AgentGateway (LAN-local kind cluster fronting the picrawler MCP) ---
gw-apply: ## Apply the gateway config (gateway/*.yaml) to the AGW cluster
	kubectl --context $(GWCTX) apply -f gateway/

gw-status: ## Show gateway / route / backend status
	kubectl --context $(GWCTX) -n agentgateway-system get gateway,httproute,agentgatewaybackend

gw-forward: ## Port-forward the gateway proxy to localhost:8080
	kubectl --context $(GWCTX) -n agentgateway-system port-forward svc/agentgateway-proxy 8080:8080

gw-test: ## Send an MCP initialize through the gateway (needs gw-forward running + Pi up)
	curl -s -X POST http://localhost:8080/mcp \
	  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
	  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"make","version":"1"}}}' | head -c 600; echo

gw-mcp-add: ## Register the gateway-fronted MCP with Claude Code (needs gw-forward)
	claude mcp add --transport http picrawler-gw http://localhost:8080/mcp
