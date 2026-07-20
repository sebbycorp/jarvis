"""Local music playback: a directory of files, one ffplay child at a time."""
from __future__ import annotations
import os
import random
import re
import shutil
import subprocess
import threading

import audio
import config

EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus"}


class Player:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._queue: list[str] = []
        self._current: str | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- library -----------------------------------------------------------
    def library(self) -> list[str]:
        root = config.MUSIC_DIR
        if not os.path.isdir(root):
            return []
        found = []
        for base, _dirs, files in os.walk(root):
            for f in files:
                if os.path.splitext(f)[1].lower() in EXTENSIONS:
                    found.append(os.path.join(base, f))
        return sorted(found)

    def search(self, query: str) -> list[str]:
        """Match on filename, ignoring case, punctuation and separators — the
        query arrives via speech recognition, so it won't match exactly."""
        def norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

        q = norm(query)
        if not q:
            return []
        terms = q.split()
        hits = []
        root = config.MUSIC_DIR
        for path in self.library():
            # match against the path relative to the library root, not just the
            # filename: artist and album are usually directory names, and
            # "play daft punk" should find Daft Punk/Around the World.flac
            rel = os.path.relpath(path, root) if path.startswith(root) else path
            hay = norm(os.path.splitext(rel)[0])
            if all(t in hay for t in terms):
                hits.append(path)
        return hits

    # ---- transport ---------------------------------------------------------
    def play(self, query: str | None = None, shuffle: bool = False) -> dict:
        tracks = self.search(query) if query else self.library()
        if not tracks:
            return {"playing": None,
                    "error": f"nothing found for {query!r}" if query
                             else f"no music in {config.MUSIC_DIR}"}
        if shuffle:
            random.shuffle(tracks)
        with self._lock:
            self._queue = tracks[1:]
            self._start(tracks[0])
        return {"playing": os.path.basename(tracks[0]),
                "queued": len(self._queue)}

    def _start(self, path: str) -> None:
        """Caller holds the lock."""
        self._kill()
        self._stop.clear()
        self._current = path
        self._proc = audio.play_file(path)
        self._thread = threading.Thread(target=self._watch, args=(self._proc,),
                                        daemon=True)
        self._thread.start()

    def _watch(self, proc: subprocess.Popen) -> None:
        proc.wait()
        with self._lock:
            if self._stop.is_set() or proc is not self._proc:
                return  # stopped or superseded by another track
            if self._queue:
                self._start(self._queue.pop(0))
            else:
                self._proc = None
                self._current = None

    def _kill(self) -> None:
        """Caller holds the lock."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def stop(self) -> dict:
        with self._lock:
            self._stop.set()
            self._queue.clear()
            self._kill()
            self._current = None
        return {"playing": None}

    def skip(self) -> dict:
        with self._lock:
            if not self._queue:
                self._stop.set()
                self._kill()
                self._current = None
                return {"playing": None}
            self._start(self._queue.pop(0))
            return {"playing": os.path.basename(self._current or "")}

    @property
    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        return {"playing": os.path.basename(self._current) if self._current else None,
                "queued": len(self._queue),
                "is_playing": self.is_playing,
                "library_size": len(self.library())}

    # ---- volume ------------------------------------------------------------
    # On this box the speaker's control is 'robot-hat speaker' — a softvol on
    # the hifiberry card (3). Bare `amixer scontrols` reports card 0's
    # Master/Capture instead, so both the control name AND the card have to be
    # discovered; setting "Master" silently adjusts the wrong device.
    PREFERRED = ("robot-hat", "speaker", "master", "pcm", "digital")

    @staticmethod
    def mixer_controls() -> list[tuple[int, str]]:
        """(card, control) for every playback mixer control on the system."""
        if not shutil.which("amixer"):
            return []
        found: list[tuple[int, str]] = []
        for card in range(8):
            out = subprocess.run(["amixer", "-c", str(card), "scontrols"],
                                 capture_output=True, text=True)
            if out.returncode != 0:
                continue
            for name in re.findall(r"Simple mixer control '(.+?)',\d+",
                                   out.stdout):
                found.append((card, name))
        return found

    @classmethod
    def _rank(cls, entry: tuple[int, str]) -> int:
        name = entry[1].lower()
        for i, want in enumerate(cls.PREFERRED):
            if want in name:
                return i
        return len(cls.PREFERRED)

    @classmethod
    def set_volume(cls, percent: int) -> dict:
        percent = max(0, min(100, int(percent)))
        if not shutil.which("amixer"):
            return {"error": "amixer not available"}
        controls = sorted(cls.mixer_controls(), key=cls._rank)
        for card, control in controls:
            if "capture" in control.lower() or "mic" in control.lower():
                continue  # never turn the microphone down
            out = subprocess.run(
                ["amixer", "-c", str(card), "-M", "sset", control, f"{percent}%"],
                capture_output=True, text=True)
            if out.returncode == 0:
                return {"volume": percent, "control": control, "card": card}
        return {"error": "no usable mixer control found"}


_player: Player | None = None
_lock = threading.Lock()


def get_player() -> Player:
    global _player
    with _lock:
        if _player is None:
            _player = Player()
    return _player
