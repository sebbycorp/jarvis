# Hardware Notes

Live observations from the actual device (`172.16.10.117`, user `smaniak`).

## Current role (2026-07-20)

The PiCrawler's legs broke. The robotics stack was removed and the device became a
voice box. What remains in use:

| Part | Status | Used for |
|---|---|---|
| Raspberry Pi 4 | ✅ | everything |
| Robot HAT (I2C `0x14`) | ✅ | speaker amp enable pin, battery ADC |
| hifiberry DAC (PCM5102A) | ✅ | speaker output |
| USB PnP Sound Device | ✅ | microphone |
| Camera `/dev/video0` | ❌ see below | disabled in `.env` |
| 8× leg servos | ❌ broken | — |
| 2S 18650 pack | ⚠️ | optional; wall power is fine now that nothing moves |

A future drone build would reuse the Pi + camera; the old servo/gait code is in git
history before the `voicebox` branch.

### Open hardware issues (2026-07-20)

**Camera — no frames.** The OV5647 is detected on I2C and libcamera configures
streams, then: `Dequeue timer of 1000000.00us has expired` → `Camera frontend
has timed out`. Classic loose/failed CSI ribbon. Currently
`VOICEBOX_CAMERA_ENABLED=0`. Worse, picamera2 **blocks forever** rather than
raising here — it hung preflight indefinitely — so `camera.py` time-bounds every
call and latches a broken flag. Re-seat the ribbon at both ends to retry.

**Speaker — output level unverified.** Every software layer reports success
(`aplay` exits 0, Piper synthesizes, softvol and Master both at 100%, HAT amp
enable returns True), and a mic loopback *does* show the speaker being heard —
but only at **peak ~900/32767 (−31 dBFS)**, about 3x the noise floor. Either the
speaker is quiet/damaged or the amp isn't driving properly. **Nobody has yet
confirmed by ear whether it is audible in the room.** Do that before chasing it
in software. Note this does not affect the mic→STT path, which is verified good.

## Environment
- OS: Debian 13 "Trixie", aarch64
- Python: 3.13.5 (PEP 668 externally-managed)
- App venv: `~/voicebox-app/.venv`, created with `--system-site-packages` so it
  inherits system `picamera2` and `robot_hat`.

## Audio

Cards as of 2026-07-20:

| Card | Device | Role |
|---|---|---|
| 0, 1 | `vc4hdmi0`, `vc4hdmi1` | HDMI, unused |
| 2 | `bcm2835 Headphones` | 3.5mm jack, unused |
| 3 | `sndrpihifiberry` (PCM5102A) | **speaker out** |
| 4 | `USB PnP Sound Device` | **microphone** |

- `i2samp.sh` applied `dtoverlay=hifiberry-dac` + `/etc/asound.conf`. Its
  interactive reboot prompt hangs headless — the config still gets written;
  reboot manually.
- `/etc/asound.conf` sets `pcm.!default robothat`, a plug → softvol → dmix chain
  onto the hifiberry. So plain `aplay` (no `-D`) already hits the right speaker.
- **The mixer control is named `robot-hat speaker Playback Volume`** — a softvol,
  not a hardware control. There is no `Master`, `PCM`, or `Digital` on this box,
  so code that guesses those names silently fails to change volume.
  `music.mixer_controls()` discovers it via `amixer scontrols` instead.
- The mic was **card 3 in the 2026-07-18 notes and card 4 today** — USB card
  indices drift across reboots. Pin `VOICEBOX_MIC_DEVICE` to the device *name*
  (a substring match works) rather than an index.
- The HAT amp needs an explicit enable (`robot_hat.utils.enable_speaker`) or the
  DAC plays into a muted amp — silence with no error. `audio.enable_speaker()`
  handles this and tolerates the older `robot_hat.tts` import path.

### Microphone sample rate (bit us hard)
The USB PnP mic offers **44100 and 48000 only — opening it at 16000 fails** with
`PortAudioError: Invalid sample rate [PaErrorCode -9997]`. But whisper,
openWakeWord and webrtcvad all *require* 16 kHz. ALSA's `default` device will
accept 16 kHz (it resamples via Pulse), but we capture from the device directly
at 48 kHz and downsample in `audio.resample()` — 48000/16000 is an exact 3:1,
polyphase via scipy, and it keeps Pulse out of the hot path.
`audio.pick_capture_rate()` probes 16000 → 48000 → 32000 → 44100 in that order.

### Measured latency (Pi 4, 2026-07-20)
Per turn, from end of speech: **STT ~2.0s + model ~0.7s ≈ 2.7s.** Local intents
(music/volume) answer in ~0.05s since they never leave the box.

| Model | audio_ctx | Time for 1.9s clip | Transcript |
|---|---|---|---|
| base.en | 1500 (default) | 10.6s (5.5x realtime) | correct |
| tiny.en | 1500 (default) | 4.1s (2.2x) | identical |
| tiny.en | **768** | **2.1s (1.0x)** | identical |
| tiny.en | 512 | 1.4s (0.7x) | identical, but only ~10s coverage |

Two findings worth keeping: **tiny.en matches base.en** on short commands here,
and **`audio_ctx` is the single biggest win** — whisper otherwise pads every
clip to a 30s window. 768 ≈ 15.4s of coverage, matching `MAX_UTTERANCE_S=15`.
Don't drop to 512 without lowering `MAX_UTTERANCE_S` to match.

`vcgencmd get_throttled` reported `0xe0000` — under-voltage//capping *has*
occurred historically (not active). If STT times regress, check this first.

## Speech stack (all local)
- **STT:** whisper.cpp via `pywhispercpp`, model `ggml-base.en.bin`. The binding
  keeps the model resident — a Pi 4 spends ~1s per turn just reloading `base.en`
  from disk with the CLI. Drop to `ggml-tiny.en.bin` if latency matters more than
  accuracy.
- **TTS:** piper, voice `en_US-amy-medium`. Outputs raw s16 on stdout at the
  voice's own sample rate — read it from the sidecar `.onnx.json`, don't assume
  22050.
- **Wake word:** openWakeWord (`hey_jarvis`) on onnxruntime, 1280-sample chunks.
- Mic frames are 20 ms / 320 samples: a valid webrtcvad frame size, and exactly
  1/4 of openWakeWord's chunk.

### Python 3.13 caveats
This box runs Python 3.13, which is newer than much of the ML packaging
ecosystem. **`tflite-runtime` has no 3.13 wheel**, and that has now bitten twice:

- **vilib** skipped mediapipe/tflite, losing its local face/hand/pose
  recognition. Irrelevant now — the voice box uses `picamera2` directly for
  stills and sends them to a cloud vision model.
- **openwakeword** declares `tflite-runtime>=2.8` as a hard dependency on Linux,
  so a plain `pip install openwakeword` **aborts the whole requirements
  install**. It only actually needs tflite for the tflite backend; we run
  `inference_framework="onnx"`, so `setup_pi.sh` installs it with `--no-deps`
  and pins its real deps (scipy, scikit-learn, tqdm, onnxruntime) in
  `requirements.txt`. Don't "tidy" this back into requirements.txt.

vilib and the `picrawler` package are no longer dependencies at all.

## Model gateway (AgentGateway @ `172.16.10.155`)

Verified working 2026-07-20, all unauthenticated on the LAN:

| Route | Port | Model | Notes |
|---|---|---|---|
| `/spark/v1` | 31944 | `Qwen/Qwen3.6-35B-A3B-FP8` | vLLM on DGX Spark; **default** |
| `/openai/v1` | 30160 | (empty → `gpt-5.5-2026-04-23`) | gateway pins the model |
| `/grok/v1` | 31397 | `grok-4.5` | |

**Only `/chat/completions` is routed.** `/v1/models`, `/audio/transcriptions`,
`/audio/speech`, `/embeddings` and `/responses` all return **503** — this is why
STT and TTS run on the Pi instead of going through the gateway.

Response-shape gotchas handled in `llm.py`:
- Qwen (vLLM) and Grok are reasoning models: they put thinking in `reasoning` /
  `reasoning_content`, and `content` can be **empty** when the answer is truncated
  at `max_tokens`. The router falls back to the reasoning field.
- Qwen's route is text-only; images are only attached for backends flagged
  `vision` in `config.BACKENDS`.

## Access
- SSH is **key auth** (the old alias was `pi-crawler` with
  `~/.ssh/picrawler_ed25519`); the voice box scripts expect an alias named
  `voicebox`. Password auth throttles under rapid sudo calls — keep using keys.
- Note the deploy key lives on whichever workstation set the Pi up; a fresh
  machine needs the key copied or a new one authorized before `make deploy` works.
