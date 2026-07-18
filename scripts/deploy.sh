#!/usr/bin/env bash
# Deploy robot/ -> Pi:~/picrawler-app/ (excludes secrets, venv, photos).
# Uses the 'pi-crawler' SSH alias (key auth). Override host with PI=user@host.
set -euo pipefail
PI="${PI:-pi-crawler}"
SRC="$(cd "$(dirname "$0")/../robot" && pwd)/"
rsync -az --delete \
  --exclude '.venv' --exclude '.env' --exclude 'photos' --exclude '__pycache__' \
  -e "ssh -o StrictHostKeyChecking=no" \
  "$SRC" "$PI:picrawler-app/"
echo "deployed $SRC -> $PI:~/picrawler-app/"
