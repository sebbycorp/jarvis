# PiCrawler control — operational shortcuts.
# Uses the 'pi-crawler' SSH alias (key auth). Override: make PI=user@host <target>
PI  ?= pi-crawler
VENV = ~/picrawler-app/.venv/bin/python
APP  = ~/picrawler-app

.PHONY: help test lint deploy preflight status battery logs restart stop-all \
        install-services mcp-add walk stand rest web ai ssh

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

# AgentGateway config for the picrawler MCP lives in the k8s-goose GitOps repo
# (config/backends/picrawler-mcp.yaml + config/routes/picrawler-mcp-route.yaml),
# synced to goose by ArgoCD. See PR sebbycorp/k8s-goose#9.
