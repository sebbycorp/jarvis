#!/usr/bin/env bash
# Idempotent PiCrawler bootstrap — run ON the Pi:
#   bash ~/picrawler-app/setup_pi.sh          (after deploying robot/)
# or scp this over and run it. Safe to re-run. Captures the full install that
# was done by hand, so a fresh SD card (e.g. after a Bookworm reflash) is one
# command away.
#
# It does NOT move any servos. A reboot is required after the first run to
# activate I2C and the speaker overlay — the script tells you when.
set -euo pipefail

APP="$HOME/picrawler-app"
SF="$HOME/sf"
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt   # older layout

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

need_reboot=0

say "1/6  Enable I2C on the GPIO header"
if ! grep -q '^dtparam=i2c_arm=on' "$CONFIG"; then
  sudo cp "$CONFIG" "$CONFIG.bak.$(date +%s)"
  sudo sed -i 's/^#\?dtparam=i2c_arm=on/dtparam=i2c_arm=on/' "$CONFIG"
  grep -q '^dtparam=i2c_arm=on' "$CONFIG" || echo 'dtparam=i2c_arm=on' | sudo tee -a "$CONFIG" >/dev/null
  need_reboot=1
fi
grep -qx 'i2c-dev' /etc/modules || echo 'i2c-dev' | sudo tee -a /etc/modules >/dev/null

say "2/6  Base apt dependencies"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git python3-venv python3-pip python3-picamera2 python3-libcamera python3-dev \
  build-essential portaudio19-dev libsdl2-mixer-2.0-0 ffmpeg i2c-tools

say "3/6  SunFounder libraries (system-wide, from source)"
mkdir -p "$SF"; cd "$SF"
[ -d robot-hat ] || git clone -b 2.5.x --depth 1 https://github.com/sunfounder/robot-hat.git
[ -d vilib ]     || git clone --depth 1 https://github.com/sunfounder/vilib.git
[ -d picrawler ] || git clone --depth 1 https://github.com/sunfounder/picrawler.git
python3 -c "import robot_hat" 2>/dev/null || (cd "$SF/robot-hat" && sudo python3 install.py)
python3 -c "import vilib"     2>/dev/null || (cd "$SF/vilib" && sudo python3 install.py)
python3 -c "import picrawler" 2>/dev/null || sudo pip3 install "$SF/picrawler" --break-system-packages

say "4/6  Onboard speaker (i2samp) — interactive; skip if already configured"
if ! grep -qi 'hifiberry-dac\|max98357' "$CONFIG"; then
  echo "  Run manually (needs a terminal):  cd $SF/robot-hat && sudo bash i2samp.sh"
  echo "  (answer yes to the prompts; decline its auto-reboot — this script handles it)"
else
  echo "  speaker overlay already present — skipping"
fi

say "5/6  App virtualenv (--system-site-packages) + our pip deps"
[ -d "$APP/.venv" ] || python3 -m venv --system-site-packages "$APP/.venv"
if [ -f "$APP/requirements.txt" ]; then
  "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
else
  "$APP/.venv/bin/pip" install -q fastmcp flask openai sounddevice opencv-python-headless
fi

say "6/6  Config + services"
[ -f "$APP/.env" ] || { [ -f "$APP/config.example.env" ] && cp "$APP/config.example.env" "$APP/.env" && echo "  created $APP/.env (edit to add OPENAI_API_KEY)"; }
# passwordless restart for the code+deploy MCP tool
SUDOERS=/etc/sudoers.d/picrawler
if [ ! -f "$SUDOERS" ]; then
  echo "$USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart picrawler-mcp, /usr/bin/systemctl restart picrawler-web" | \
    sudo tee "$SUDOERS" >/dev/null && sudo chmod 440 "$SUDOERS"
fi
for unit in picrawler-mcp picrawler-web; do
  if [ -f "$APP/../$unit.service" ] || [ -f "$HOME/spiderman/scripts/$unit.service" ]; then :; fi
done
echo "  install services with:  make install-services   (from the dev repo)"

say "Done."
"$APP/.venv/bin/python" -c "import robot_hat, vilib, picrawler; print('imports OK')" 2>/dev/null \
  && echo "verified: SunFounder imports OK" || echo "NOTE: imports need a reboot to finish"
if [ "$need_reboot" = "1" ]; then
  echo
  echo "  *** REBOOT REQUIRED to activate I2C.  Run:  sudo reboot  ***"
fi
