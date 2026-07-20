# Voice box — operational shortcuts.
# Uses the 'voicebox' SSH alias (key auth). Override: make PI=user@host <target>
PI   ?= voicebox
HOST ?= 172.16.10.117
VENV = ~/voicebox-app/.venv/bin/python
APP  = ~/voicebox-app

.PHONY: help test lint deploy setup preflight status logs restart stop-all \
        install-services mcp-add say ask web run devices gateway ssh

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

test: ## Run off-device unit tests (mocked audio/hardware, no Pi needed)
	python3 -m unittest discover -s tests -v

lint: ## Byte-compile all modules to catch syntax errors
	python3 -m py_compile voicebox/*.py voicebox/web/app.py

gateway: ## Check the three AgentGateway model routes from here
	@python3 -c "import sys; sys.path.insert(0,'voicebox'); \
	import config, llm; r=llm.Router(); \
	[print(f'{b:7} {llm.Router(b).ask(\"say ok\", remember=False)[\"reply\"][:60]}') \
	 for b in sorted(config.BACKENDS)]"

deploy: ## Sync voicebox/ to the Pi (excludes .env, venv, models, music)
	./scripts/deploy.sh

setup: ## Push and run the idempotent Pi bootstrap (installs piper/whisper/models)
	scp scripts/setup_pi.sh $(PI):/tmp/setup_pi.sh
	ssh -t $(PI) "bash /tmp/setup_pi.sh"

preflight: ## Run the health-check on the Pi
	ssh $(PI) "cd $(APP) && $(VENV) preflight.py"

status: ## Show backend, audio engines, and music state
	ssh $(PI) "cd $(APP) && $(VENV) -c 'import config, llm, music, tts; \
	  print(\"backend:\", llm.get_router().label()); \
	  print(\"tts:    \", \"piper\" if tts.available() else \"espeak\"); \
	  print(\"gateway:\", config.GATEWAY_HOST); \
	  print(\"music:  \", music.get_player().status())'"

devices: ## List audio input devices on the Pi
	ssh $(PI) "cd $(APP) && $(VENV) assistant.py --list-devices"

say: ## Speak text through the box: make say TEXT="hello there"
	ssh $(PI) "cd $(APP) && $(VENV) -c 'import tts,sys; print(tts.say(sys.argv[1]))' \"$(TEXT)\""

ask: ## One text turn, no mic: make ask Q="what is the capital of peru"
	ssh $(PI) "cd $(APP) && $(VENV) assistant.py --once \"$(Q)\""

logs: ## Tail the assistant service logs
	ssh $(PI) "journalctl -u voicebox -n 60 --no-pager -f"

restart: ## Restart the assistant service
	ssh $(PI) "sudo systemctl restart voicebox && systemctl is-active voicebox"

stop-all: ## Stop all services (free the mic/speaker for manual runs)
	ssh $(PI) "sudo systemctl stop voicebox voicebox-mcp voicebox-web 2>/dev/null; echo stopped"

run: ## Run the assistant in the foreground on the Pi (stops the service first)
	ssh -t $(PI) "sudo systemctl stop voicebox; cd $(APP) && $(VENV) assistant.py"

web: ## Run the web panel in the foreground (stops the assistant first)
	ssh -t $(PI) "sudo systemctl stop voicebox; cd $(APP) && $(VENV) web/app.py"

install-services: ## Copy + enable systemd units (assistant autostarts)
	scp scripts/voicebox.service scripts/voicebox-mcp.service \
	    scripts/voicebox-web.service $(PI):/tmp/
	ssh $(PI) "sudo mv /tmp/voicebox.service /tmp/voicebox-mcp.service \
	  /tmp/voicebox-web.service /etc/systemd/system/ && \
	  sudo systemctl daemon-reload && sudo systemctl enable --now voicebox && \
	  systemctl is-active voicebox"

mcp-add: ## Register the box's MCP server with Claude Code
	claude mcp add --transport http voicebox http://$(HOST):8000/mcp

ssh: ## Open a shell on the Pi
	ssh $(PI)

# Model calls go through AgentGateway at 172.16.10.155 (openai :30160,
# spark/Qwen :31944, grok :31397). Gateway config lives in the k8s-goose
# GitOps repo. The gateway proxies /chat/completions only — no audio routes —
# which is why STT (whisper.cpp) and TTS (piper) run locally on the Pi.
