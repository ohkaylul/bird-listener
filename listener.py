"""Continuous mic capture + BirdNET analysis loop, running in a background thread."""
import queue
import threading
import time
from datetime import datetime, timedelta

import numpy as np
import sounddevice as sd
from birdnetlib import RecordingBuffer
from birdnetlib.analyzer import Analyzer

import config

SAMPLE_RATE = 48000          # BirdNET's expected sample rate
CHUNK_SECONDS = 3.0          # BirdNET analyzes 3s windows
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_SECONDS)


class Listener:
    def __init__(self, on_detection, on_error=None, on_status=None):
        """
        on_detection(common_name, scientific_name, confidence) is called for every
        raw detection above min_confidence -- alert-gating happens by the caller.
        on_status(str) receives human-readable status updates.
        """
        self.on_detection = on_detection
        self.on_error = on_error or (lambda e: print(f"[listener] error: {e}"))
        self.on_status = on_status or (lambda s: print(f"[listener] {s}"))
        self._stop = threading.Event()
        self._thread = None
        self._audio_q: "queue.Queue[np.ndarray]" = queue.Queue()
        self.analyzer = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            self.on_status(f"audio status: {status}")
        self._audio_q.put(indata[:, 0].copy())

    def _run(self):
        try:
            self.on_status("loading BirdNET model...")
            self.analyzer = Analyzer()
            self.on_status("model loaded")
        except Exception as e:
            self.on_error(f"failed to load analyzer: {e}")
            return

        state = config.load_state()
        device = state.get("device")

        buffer = np.zeros(0, dtype=np.float32)
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=device,
                callback=self._audio_callback,
                blocksize=int(SAMPLE_RATE * 0.5),
            ):
                self.on_status("listening...")
                while not self._stop.is_set():
                    try:
                        chunk = self._audio_q.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    buffer = np.concatenate([buffer, chunk])
                    while len(buffer) >= CHUNK_SAMPLES:
                        window = buffer[:CHUNK_SAMPLES]
                        buffer = buffer[CHUNK_SAMPLES:]
                        self._analyze(window)
        except Exception as e:
            self.on_error(f"audio stream error: {e}")

    def _analyze(self, window):
        state = config.load_state()
        min_conf = state.get("min_confidence", 0.7)
        lat = state.get("latitude")
        lon = state.get("longitude")
        try:
            rec = RecordingBuffer(
                self.analyzer,
                window,
                SAMPLE_RATE,
                lat=lat,
                lon=lon,
                min_conf=min_conf,
                date=datetime.now(),
            )
            rec.analyze()
        except Exception as e:
            self.on_error(f"analysis error: {e}")
            return

        for d in rec.detections:
            common = d.get("common_name")
            sci = d.get("scientific_name")
            conf = d.get("confidence", 0.0)
            if common:
                self.on_detection(common, sci, conf)


def list_input_devices():
    devices = sd.query_devices()
    result = []
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0:
            result.append((idx, dev["name"]))
    return result
