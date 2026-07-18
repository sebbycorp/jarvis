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

## Servo calibration offsets
- (to be recorded during Phase 2 calibration)
