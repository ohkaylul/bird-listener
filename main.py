"""Entry point: wires listener -> alert gating -> notifier -> GUI, with a tray icon."""
import threading
import tkinter as tk
from datetime import datetime, timedelta

import pystray
from PIL import Image, ImageDraw

import config
from gui import BirdApp
from listener import Listener, list_input_devices
from notifier import notify

_state_lock = threading.Lock()


def make_tray_image():
    img = Image.new("RGB", (64, 64), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill="#3b7a3b")
    d.polygon([(56, 32), (64, 26), (64, 38)], fill="#e0a020")
    return img


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.listener = Listener(
            on_detection=self.handle_detection,
            on_error=self.handle_error,
            on_status=self.handle_status,
        )
        self.gui = BirdApp(self.root, {
            "start": self.listener.start,
            "stop": self.listener.stop,
            "list_devices": list_input_devices,
        })
        self.gui.on_minimize_to_tray = self.minimize_to_tray
        self.tray_icon = None
        self.known_species = set()

    def handle_status(self, text):
        self.gui.set_status(text)

    def handle_error(self, text):
        self.gui.set_status(f"error: {text}")
        print(f"[error] {text}")

    def handle_detection(self, common_name, scientific_name, confidence):
        with _state_lock:
            state = config.load_state()
            muted = set(state.get("muted", []))
            always = set(state.get("always_alert", []))
            last_heard = state.get("last_heard", {})
            quiet_minutes = state.get("quiet_hours_minutes", 180)

            if common_name not in self.known_species:
                self.known_species.add(common_name)
                self.gui.register_species(common_name, scientific_name)
            self.gui.flash_species(common_name)

            should_alert = False
            if common_name in muted:
                should_alert = False
            elif common_name in always:
                should_alert = True
            else:
                last_str = last_heard.get(common_name)
                if not last_str:
                    should_alert = True
                else:
                    try:
                        last_time = datetime.fromisoformat(last_str)
                        should_alert = datetime.now() - last_time > timedelta(minutes=quiet_minutes)
                    except ValueError:
                        should_alert = True

            last_heard[common_name] = datetime.now().isoformat()
            state["last_heard"] = last_heard
            config.save_state(state)

        if should_alert:
            notify(common_name, confidence)
        self.handle_status(f"heard: {common_name} ({confidence:.0%})" + ("" if should_alert else " (muted/recent)"))

    def minimize_to_tray(self):
        self.root.withdraw()
        if self.tray_icon is None:
            menu = pystray.Menu(
                pystray.MenuItem("Show", self.show_from_tray, default=True),
                pystray.MenuItem("Quit", self.quit_app),
            )
            self.tray_icon = pystray.Icon("birdlistener", make_tray_image(), "Bird Listener", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_from_tray(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def quit_app(self, icon=None, item=None):
        self.listener.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.destroy)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
