"""Fake SunFounder hardware modules so picrawler_ctl can be imported and tested
off-robot. Call install() BEFORE importing picrawler_ctl.

Exposes the fakes so tests can inspect recorded calls and set battery voltage.
"""
import sys
import types


class FakePicrawler:
    """Records do_action calls; mimics the picrawler.Picrawler surface we use."""
    instances = []

    def __init__(self, *a, **k):
        self.calls = []
        self.offset = [0.0] * 12
        FakePicrawler.instances.append(self)

    def do_action(self, motion_name, step=1, speed=50):
        self.calls.append((motion_name, step, speed))

    def set_offset(self, offset_list):
        self.offset = [min(max(o, -20), 20) for o in offset_list]


class FakeEspeak:
    said = []

    def say(self, words):
        FakeEspeak.said.append(words)


class FakeVilib:
    started = False
    photos = []
    img = None  # set to a numpy array by tests that need capture

    @classmethod
    def camera_start(cls, vflip=False, hflip=False):
        cls.started = True

    @classmethod
    def display(cls, local=False, web=False):
        pass

    @classmethod
    def take_photo(cls, name, path):
        cls.photos.append((name, path))

    @classmethod
    def camera_close(cls):
        cls.started = False


# module-level battery voltage the fake reports; tests mutate this
BATTERY_V = 8.0


def set_battery(v):
    global BATTERY_V
    BATTERY_V = v


def install():
    # picrawler
    m_pic = types.ModuleType("picrawler")
    m_pic.Picrawler = FakePicrawler
    sys.modules["picrawler"] = m_pic

    # robot_hat (+ robot_hat.tts submodule)
    m_rh = types.ModuleType("robot_hat")
    m_rh.get_battery_voltage = lambda: BATTERY_V
    m_rh.Music = object
    sys.modules["robot_hat"] = m_rh
    m_tts = types.ModuleType("robot_hat.tts")
    m_tts.Espeak = FakeEspeak
    m_tts.enable_speaker = lambda: None
    sys.modules["robot_hat.tts"] = m_tts
    m_rh.tts = m_tts

    # vilib
    m_vil = types.ModuleType("vilib")
    m_vil.Vilib = FakeVilib
    sys.modules["vilib"] = m_vil

    # cv2 (only imencode is used)
    m_cv = types.ModuleType("cv2")
    m_cv.imencode = lambda ext, frame: (True, _FakeBuf())
    sys.modules["cv2"] = m_cv


class _FakeBuf:
    def tobytes(self):
        return b"\xff\xd8\xff\xe0JPEGBYTES"  # fake JPEG-ish bytes
