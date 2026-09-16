"""Config load/save and per-species alert state, persisted to state.json."""
import json
import os
import sys
import threading
from pathlib import Path


def _state_dir():
    # Under PyInstaller, __file__ lives in a temp extraction dir that's wiped
    # on exit, so state has to live somewhere stable instead.
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("APPDATA", Path.home())) / "BirdListener"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(__file__).parent


STATE_PATH = _state_dir() / "state.json"

_lock = threading.Lock()

DEFAULT_STATE = {
    "device": None,          # sounddevice input device index, None = system default
    "latitude": None,        # optional, improves BirdNET species filtering
    "longitude": None,
    "min_confidence": 0.7,
    "quiet_hours_minutes": 180,   # "new species" alert re-fires after this many minutes of silence
    "muted": [],              # common names never alerted
    "always_alert": [],       # common names alerted on every detection
    "last_heard": {},         # common name -> ISO timestamp of last alert-eligible detection
}


def load_state():
    with _lock:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            merged = {**DEFAULT_STATE, **data}
            return merged
        return dict(DEFAULT_STATE)


def save_state(state):
    with _lock:
        STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
