"""PiCrawler preflight health-check.

Run on the Pi BEFORE operating to catch problems (dead battery, no I2C, camera,
missing libs, services) before they bite mid-operation:

    ~/picrawler-app/.venv/bin/python ~/picrawler-app/preflight.py

Exit code 0 if all CRITICAL checks pass, 1 otherwise. WARN checks never fail
the run. Designed to be safe: it does NOT move any servos.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import sys

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

_critical_failed = False


def _line(status: str, name: str, detail: str = ""):
    color = {"OK": GREEN, "FAIL": RED, "WARN": YELLOW}.get(status, "")
    tag = f"{color}{status:>4}{RESET}"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))


def check(name: str, fn, critical: bool = True):
    global _critical_failed
    try:
        ok, detail = fn()
    except Exception as e:  # a check itself blowing up counts as a failure
        ok, detail = False, f"{type(e).__name__}: {e}"
    if ok:
        _line("OK", name, detail)
    elif critical:
        _critical_failed = True
        _line("FAIL", name, detail)
    else:
        _line("WARN", name, detail)


# ---- individual checks ----------------------------------------------------
def c_imports():
    import robot_hat, vilib, picrawler  # noqa: F401
    import cv2  # noqa: F401
    from picrawler import Picrawler  # noqa: F401
    return True, "robot_hat, vilib, picrawler, cv2"


def c_i2c_hat():
    if not os.path.exists("/dev/i2c-1"):
        return False, "/dev/i2c-1 missing (I2C not enabled)"
    exe = shutil.which("i2cdetect") or "/usr/sbin/i2cdetect"
    out = subprocess.run(["sudo", "-n", exe, "-y", "1"],
                         capture_output=True, text=True)
    if " 14 " in out.stdout:
        return True, "Robot HAT present at 0x14"
    return False, "HAT (0x14) not on bus — check power/seating\n" + out.stdout


def c_battery():
    from robot_hat import get_battery_voltage
    v = round(float(get_battery_voltage()), 2)
    min_v = float(os.environ.get("PICRAWLER_MIN_BATTERY_V", "6.8"))
    warn_v = float(os.environ.get("PICRAWLER_WARN_BATTERY_V", "7.2"))
    if v < min_v:
        return False, f"{v}V < {min_v}V minimum — CHARGE before moving"
    if v < warn_v:
        return True, f"{v}V (low-ish; below {warn_v}V warn threshold)"
    return True, f"{v}V"


def c_camera():
    from vilib import Vilib
    import time
    Vilib.camera_start(vflip=False, hflip=False)
    Vilib.display(local=False, web=False)
    time.sleep(1.5)
    frame = Vilib.img
    try:
        ok = frame is not None and getattr(frame, "size", 0) > 0
    finally:
        try:
            Vilib.camera_close()
        except Exception:
            pass
    return (ok, "captured a frame") if ok else (False, "no frame from camera")


def c_speaker_card():
    out = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
    if "hifiberry" in out.stdout.lower() or "sndrpihifiberry" in out.stdout.lower():
        return True, "hifiberry DAC present"
    return False, "onboard speaker card not found (check i2samp/asound.conf)"


def c_service():
    out = subprocess.run(["systemctl", "is-active", "picrawler-mcp"],
                         capture_output=True, text=True)
    state = out.stdout.strip()
    return (state == "active", f"picrawler-mcp is {state or 'unknown'}")


def main():
    print("=== PiCrawler preflight ===")
    check("Python libraries", c_imports, critical=True)
    check("I2C / Robot HAT", c_i2c_hat, critical=True)
    check("Battery voltage", c_battery, critical=True)
    check("Camera", c_camera, critical=False)
    check("Speaker card", c_speaker_card, critical=False)
    check("MCP service", c_service, critical=False)
    print("===========================")
    if _critical_failed:
        print(f"{RED}PREFLIGHT FAILED — do not operate until fixed.{RESET}")
        sys.exit(1)
    print(f"{GREEN}Preflight OK — safe to operate.{RESET}")
    sys.exit(0)


if __name__ == "__main__":
    main()
