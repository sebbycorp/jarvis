#!/usr/bin/env bash
# Deploy voicebox/ -> Pi:~/voicebox-app/ (excludes secrets, venv, models, media).
# Uses the 'voicebox' SSH alias (key auth). Override host with PI=user@host.
set -euo pipefail
PI="${PI:-voicebox}"
SRC="$(cd "$(dirname "$0")/../voicebox" && pwd)/"
rsync -az --delete \
  --exclude '.venv' --exclude '.env' --exclude '__pycache__' \
  --exclude 'models' --exclude 'music' --exclude 'photos' \
  -e "ssh -o StrictHostKeyChecking=accept-new" \
  "$SRC" "$PI:voicebox-app/"
echo "deployed $SRC -> $PI:~/voicebox-app/"
