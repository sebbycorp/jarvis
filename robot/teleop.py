"""Keyboard teleop for manual testing. Run on the Pi:
   ~/picrawler-app/.venv/bin/python ~/picrawler-app/teleop.py
Keys: w/s forward/back, a/d turn left/right, space=stand, r=rest, q=quit."""
import sys
import tty
import termios
from picrawler_ctl import get_controller


def getch():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    c = get_controller()
    print("teleop ready — w/s/a/d, space=stand, r=rest, q=quit")
    keymap = {"w": c.forward, "s": c.backward, "a": c.turn_left,
              "d": c.turn_right, " ": c.stand, "r": c.rest}
    while True:
        k = getch().lower()
        if k == "q":
            c.stop()
            print("bye")
            break
        fn = keymap.get(k)
        if fn:
            print(fn())


if __name__ == "__main__":
    main()
