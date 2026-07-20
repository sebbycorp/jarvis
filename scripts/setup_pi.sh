#!/usr/bin/env bash
# Idempotent bootstrap for the voice box. Run ON the Pi.
# Safe to re-run: every step checks before it acts. This is the reflash
# insurance — it should take a blank Pi OS image to a working box.
set -euo pipefail

APP="${APP:-$HOME/voicebox-app}"
MODELS="$APP/models"
WHISPER_MODEL="${WHISPER_MODEL:-ggml-tiny.en.bin}"
PIPER_VOICE="${PIPER_VOICE:-en_US-amy-medium}"
PIPER_VERSION="${PIPER_VERSION:-2023.11.14-2}"

say() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---- 1. system packages ----------------------------------------------------
say "system packages"
PKGS=(python3-venv python3-dev python3-picamera2 alsa-utils ffmpeg
      espeak-ng libportaudio2 portaudio19-dev build-essential cmake git curl)
MISSING=()
for p in "${PKGS[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  sudo apt-get update
  sudo apt-get install -y "${MISSING[@]}"
else
  echo "all present"
fi

# ---- 2. robot-hat (speaker amp + battery ADC only) -------------------------
# The legs are gone but the HAT still drives the hifiberry DAC and the amp
# enable pin, so we keep the library. i2samp.sh sets up the sound card.
say "robot-hat"
if python3 -c "import robot_hat" 2>/dev/null; then
  echo "already installed"
else
  mkdir -p "$HOME/sf"
  [ -d "$HOME/sf/robot-hat" ] || \
    git clone -b 2.5.x --depth 1 https://github.com/sunfounder/robot-hat "$HOME/sf/robot-hat"
  (cd "$HOME/sf/robot-hat" && sudo python3 install.py)
  echo "NOTE: run 'sudo bash ~/sf/robot-hat/i2samp.sh' once to enable the DAC, then reboot."
fi

# ---- 3. app venv -----------------------------------------------------------
say "python venv"
mkdir -p "$APP" "$MODELS" "$APP/music" "$APP/photos"
if [ ! -x "$APP/.venv/bin/python" ]; then
  # --system-site-packages so picamera2 and robot_hat are visible
  python3 -m venv --system-site-packages "$APP/.venv"
fi
if [ -f "$APP/requirements.txt" ]; then
  "$APP/.venv/bin/pip" install --upgrade pip
  "$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
  # openwakeword declares a hard tflite-runtime dependency on Linux, and there
  # is no tflite wheel for Python 3.13 (same wall that killed vilib's mediapipe
  # recognition on this box). We run inference on onnxruntime, so install it
  # without deps — they are pinned in requirements.txt instead.
  "$APP/.venv/bin/pip" install --no-deps openwakeword
else
  echo "no requirements.txt yet — run 'make deploy' from the laptop first"
fi

# ---- 4. whisper.cpp model (local STT) --------------------------------------
say "whisper model: $WHISPER_MODEL"
if [ -s "$MODELS/$WHISPER_MODEL" ]; then
  echo "already downloaded"
else
  curl -fL --retry 3 -o "$MODELS/$WHISPER_MODEL" \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_MODEL"
fi

# ---- 5. piper (local TTS) --------------------------------------------------
say "piper"
if have piper; then
  echo "already installed"
else
  ARCH="$(uname -m)"
  case "$ARCH" in
    aarch64) PIPER_ARCH=linux_aarch64 ;;
    armv7l)  PIPER_ARCH=linux_armv7l ;;
    x86_64)  PIPER_ARCH=linux_x86_64 ;;
    *) echo "unsupported arch $ARCH — install piper manually"; PIPER_ARCH="" ;;
  esac
  if [ -n "$PIPER_ARCH" ]; then
    TMP="$(mktemp -d)"
    curl -fL --retry 3 -o "$TMP/piper.tar.gz" \
      "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_${PIPER_ARCH}.tar.gz"
    tar -xzf "$TMP/piper.tar.gz" -C "$TMP"
    sudo rm -rf /opt/piper
    sudo cp -r "$TMP/piper" /opt/piper
    sudo ln -sf /opt/piper/piper /usr/local/bin/piper
    rm -rf "$TMP"
  fi
fi

say "piper voice: $PIPER_VOICE"
# voice ids look like en_US-amy-medium -> .../en/en_US/amy/medium/<id>.onnx
VOICE_NAME="$(echo "$PIPER_VOICE" | cut -d- -f2)"
VOICE_QUALITY="$(echo "$PIPER_VOICE" | cut -d- -f3)"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/$VOICE_NAME/$VOICE_QUALITY"
if [ -s "$MODELS/$PIPER_VOICE.onnx" ]; then
  echo "already downloaded"
else
  curl -fL --retry 3 -o "$MODELS/$PIPER_VOICE.onnx" "$VOICE_BASE/$PIPER_VOICE.onnx"
  curl -fL --retry 3 -o "$MODELS/$PIPER_VOICE.onnx.json" "$VOICE_BASE/$PIPER_VOICE.onnx.json"
fi

# ---- 6. openwakeword models ------------------------------------------------
say "wake word models"
"$APP/.venv/bin/python" - <<'PY' || echo "⚠️  openwakeword download failed — wake word will be disabled"
import openwakeword.utils as u
u.download_models()
print("openwakeword models ready")
PY

# ---- 7. config -------------------------------------------------------------
say "config"
if [ -f "$APP/.env" ]; then
  echo ".env already exists — leaving it alone"
elif [ -f "$APP/config.example.env" ]; then
  cp "$APP/config.example.env" "$APP/.env"
  sed -i "s|/home/smaniak/voicebox-app|$APP|g" "$APP/.env"
  echo "created $APP/.env from the example"
fi

say "done"
echo "next:  $APP/.venv/bin/python $APP/preflight.py"
