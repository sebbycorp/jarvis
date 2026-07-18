# PiCrawler Hardware Notes

Live observations from the actual device (`172.16.10.117`, user `smaniak`).

## Environment (2026-07-18)
- OS: Debian 13 "Trixie", aarch64
- Python: 3.13.5 (PEP 668 externally-managed)
- Camera: `/dev/video0`, `rpicam-hello` present
- Audio: USB PnP Sound Device = card 3 (mic); onboard bcm2835 headphones = card 2

## Phase 0 — I2C enablement
- `dtparam=i2c_arm=on` was commented out; enabled it in `/boot/firmware/config.txt`
  (backup saved as `config.txt.bak.<epoch>`).
- Added `i2c-dev` to `/etc/modules`.
- After reboot, `/dev/i2c-1` present.
- `i2cdetect -y 1` bus scan:

```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
10: -- -- -- -- 14 -- -- -- -- -- -- -- -- -- -- --
```

- **Robot HAT detected at 0x14.** (No separate 0x40 PWM device visible — the
  PiCrawler Robot HAT drives servos via its onboard MCU at 0x14.)

## Hardware facts
- Kit: PiCrawler = 4 legs × 2 servos = **8 servos, no pan/tilt head** (camera fixed).
  `move_head` intentionally omitted from `picrawler_ctl`.

## Phase 1 — SunFounder stack install (2026-07-18)
Installed via the **hybrid** approach (deviation from the pure-venv plan): SunFounder's
official system-wide installers (which also do hardware setup), then a venv with
`--system-site-packages` that inherits them, holding only our pip deps.

- SSH: switched to **key auth** (`~/.ssh/picrawler_ed25519`, alias `pi-crawler`)
  after password auth throttled under rapid sudo calls.
- Versions: robot-hat **2.5.x**, vilib **0.3.18**, picrawler **2.1.4**,
  picamera2 **0.3.36**, fastmcp **3.4.4**.
- robot-hat `install.py` also installed: espeak, sox, libttspico-utils, pygame,
  smbus2, gpiozero, pyaudio, spidev; turned on I2C + SPI; copied dtoverlays;
  installed `sunfounder-voice-assistant`.

### Trixie / Python 3.13 caveat (IMPORTANT)
- vilib skipped **mediapipe** and **tflite-runtime** — not supported on py3.13.
  => vilib's *built-in local ML recognition* (face/hand/pose/object detection)
  is UNAVAILABLE. Basic camera capture / `take_photo` / `Vilib.img` still work.
  The AI assistant "sees" via OpenAI GPT-4o cloud vision instead, so this does
  not block the assistant.

### API facts pinned from the device
- **TTS classes** live in `robot_hat.tts`: `Espeak`, `Pico2Wave`, `EdgeTTS`,
  `OpenAI_TTS`, `Piper` (there is NO bare `robot_hat.TTS`). `Music` is top-level.
  => `picrawler_ctl.speak()` will use `robot_hat.tts.Espeak` (offline) — verify
  exact constructor/`say()` signature on hardware in Phase 2.
- **Picrawler.do_action** valid motions (from source):
  `forward`, `backward`, `turn left`, `turn right`, `turn left angle`,
  `turn right angle`; plus `move_list` keys (e.g. `stand`, `sit`) populated per
  instance — enumerate on hardware in Phase 2.
- Picrawler has `calibration` + `cali_helper_web` (web calibration helper) and
  `OFFSET_FILE` / `set_offset` for servo trim.

### Audio
- i2samp applied `dtoverlay=hifiberry-dac` + `/etc/asound.conf`; onboard speaker
  DAC = **card 3 `sndrpihifiberry`** (PCM5102A). (i2samp's interactive reboot
  prompt hung headless, but the config was written; a manual reboot activated it.)
- USB PnP Sound Device (seen pre-reboot as card 3) = intended microphone; verify
  its card index for Phase 6 mic capture.

## Servo calibration offsets
- (to be recorded during Phase 2 calibration)
