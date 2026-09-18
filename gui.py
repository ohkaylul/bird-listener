"""Tkinter GUI: species list with Mute / Always Alert toggles, search, status."""
import io
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from PIL import Image, ImageTk

import config
import species_info


class BirdApp:
    def __init__(self, root, listener_controls):
        self.root = root
        self.listener_controls = listener_controls  # dict: start, stop, list_devices
        self.state = config.load_state()

        self.species_rows = {}  # common_name -> row widgets
        self.species_order = []  # keeps insertion order, newest first
        self.species_sci = {}  # common_name -> scientific_name

        root.title("Bird Listener")
        root.geometry("560x640")
        root.protocol("WM_DELETE_WINDOW", self.on_close_request)

        self._build_layout()
        self._refresh_species_list()

        self.on_minimize_to_tray = None  # set externally by main.py

    # ---------- layout ----------

    def _build_layout(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        self.status_var = tk.StringVar(value="stopped")
        ttk.Label(top, textvariable=self.status_var).pack(side="left")

        self.toggle_btn = ttk.Button(top, text="Start Listening", command=self.toggle_listening)
        self.toggle_btn.pack(side="right")

        search_frame = ttk.Frame(self.root, padding=(8, 0))
        search_frame.pack(fill="x")
        ttk.Label(search_frame, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_species_list())
        ttk.Entry(search_frame, textvariable=self.search_var).pack(side="left", fill="x", expand=True, padx=6)

        settings_frame = ttk.LabelFrame(self.root, text="Settings", padding=8)
        settings_frame.pack(fill="x", padx=8, pady=6)

        ttk.Label(settings_frame, text="Min confidence:").grid(row=0, column=0, sticky="w")
        self.conf_var = tk.DoubleVar(value=self.state.get("min_confidence", 0.7))
        ttk.Scale(settings_frame, from_=0.1, to=0.99, variable=self.conf_var,
                  orient="horizontal", command=lambda v: self._save_confidence()).grid(row=0, column=1, sticky="ew", padx=6)
        settings_frame.columnconfigure(1, weight=1)

        ttk.Label(settings_frame, text="Re-alert after (minutes of silence):").grid(row=1, column=0, sticky="w")
        self.quiet_var = tk.IntVar(value=self.state.get("quiet_hours_minutes", 180))
        quiet_entry = ttk.Entry(settings_frame, textvariable=self.quiet_var, width=8)
        quiet_entry.grid(row=1, column=1, sticky="w", padx=6)
        quiet_entry.bind("<FocusOut>", lambda e: self._save_quiet_minutes())

        ttk.Label(settings_frame, text="Input device:").grid(row=2, column=0, sticky="w")
        self.device_var = tk.StringVar()
        devices = self.listener_controls["list_devices"]()
        self.device_map = {f"{idx}: {name}": idx for idx, name in devices}
        device_names = ["(system default)"] + list(self.device_map.keys())
        self.device_combo = ttk.Combobox(settings_frame, textvariable=self.device_var, values=device_names, state="readonly")
        current_device = self.state.get("device")
        if current_device is None:
            self.device_combo.set("(system default)")
        else:
            match = next((k for k, v in self.device_map.items() if v == current_device), "(system default)")
            self.device_combo.set(match)
        self.device_combo.grid(row=2, column=1, sticky="ew", padx=6)
        self.device_combo.bind("<<ComboboxSelected>>", lambda e: self._save_device())

        list_frame = ttk.LabelFrame(self.root, text="Species heard", padding=8)
        list_frame.pack(fill="both", expand=True, padx=8, pady=6)

        canvas = tk.Canvas(list_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self.list_inner = ttk.Frame(canvas)
        self.list_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.list_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        header = ttk.Frame(self.list_inner)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(header, text="Species", width=30, font=("", 9, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Muted", width=8, font=("", 9, "bold")).grid(row=0, column=1)
        ttk.Label(header, text="Always alert", width=12, font=("", 9, "bold")).grid(row=0, column=2)

    # ---------- state persistence ----------

    def _save_confidence(self):
        self.state["min_confidence"] = round(self.conf_var.get(), 2)
        config.save_state(self.state)

    def _save_quiet_minutes(self):
        try:
            self.state["quiet_hours_minutes"] = int(self.quiet_var.get())
            config.save_state(self.state)
        except (tk.TclError, ValueError):
            pass

    def _save_device(self):
        selected = self.device_var.get()
        self.state["device"] = self.device_map.get(selected)  # None if "(system default)"
        config.save_state(self.state)

    # ---------- listening control ----------

    def toggle_listening(self):
        if self.toggle_btn["text"] == "Start Listening":
            self.listener_controls["start"]()
            self.toggle_btn.config(text="Stop Listening")
        else:
            self.listener_controls["stop"]()
            self.toggle_btn.config(text="Start Listening")
            self.status_var.set("stopped")

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    # ---------- species list ----------

    def register_species(self, common_name, scientific_name=None):
        """Called when a species is heard for the first time in this session (thread-safe)."""
        self.root.after(0, lambda: self._register_species_ui(common_name, scientific_name))

    def _register_species_ui(self, common_name, scientific_name):
        self.state = config.load_state()
        if scientific_name:
            self.species_sci[common_name] = scientific_name
        if common_name not in self.species_rows:
            self.species_order.insert(0, common_name)
            self._refresh_species_list()

    def _refresh_species_list(self):
        for child in list(self.list_inner.grid_slaves()):
            info = child.grid_info()
            if int(info["row"]) > 0:
                child.destroy()
        self.species_rows.clear()

        query = self.search_var.get().strip().lower()
        muted = set(self.state.get("muted", []))
        always = set(self.state.get("always_alert", []))

        row_idx = 1
        for name in self.species_order:
            if query and query not in name.lower():
                continue
            row = ttk.Frame(self.list_inner)
            row.grid(row=row_idx, column=0, sticky="ew", pady=1)
            name_label = ttk.Label(row, text=name, width=30, foreground="#1a5fb4", cursor="hand2")
            name_label.grid(row=0, column=0, sticky="w")
            name_label.bind("<Button-1>", lambda e, n=name: self._open_species_detail(n))

            mute_var = tk.BooleanVar(value=name in muted)
            always_var = tk.BooleanVar(value=name in always)

            def on_mute_toggle(n=name, v=mute_var, av=always_var):
                self._set_species_flag(n, "muted", v.get())
                if v.get() and av.get():
                    av.set(False)
                    self._set_species_flag(n, "always_alert", False)

            def on_always_toggle(n=name, v=always_var, mv=mute_var):
                self._set_species_flag(n, "always_alert", v.get())
                if v.get() and mv.get():
                    mv.set(False)
                    self._set_species_flag(n, "muted", False)

            ttk.Checkbutton(row, variable=mute_var, command=on_mute_toggle, width=6).grid(row=0, column=1)
            ttk.Checkbutton(row, variable=always_var, command=on_always_toggle, width=10).grid(row=0, column=2)

            self.species_rows[name] = row
            row_idx += 1

    def _set_species_flag(self, common_name, key, value):
        self.state = config.load_state()
        current = set(self.state.get(key, []))
        if value:
            current.add(common_name)
        else:
            current.discard(common_name)
        self.state[key] = sorted(current)
        config.save_state(self.state)

    # ---------- species detail popup ----------

    def _open_species_detail(self, common_name):
        scientific_name = self.species_sci.get(common_name)

        win = tk.Toplevel(self.root)
        win.title(common_name)
        win.geometry("420x480")
        win.resizable(False, False)

        header = ttk.Label(win, text=common_name, font=("", 14, "bold"))
        header.pack(pady=(12, 0), padx=12, anchor="w")
        if scientific_name:
            ttk.Label(win, text=scientific_name, font=("", 10, "italic")).pack(padx=12, anchor="w")

        image_label = ttk.Label(win)
        image_label.pack(pady=8)

        text = tk.Text(win, wrap="word", height=12, borderwidth=0, background=win.cget("background"))
        text.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        text.insert("1.0", "Loading..." if scientific_name else "No scientific name known for this detection.")
        text.config(state="disabled")

        link_var = tk.StringVar(value="")
        link_label = ttk.Label(win, textvariable=link_var, foreground="#1a5fb4", cursor="hand2")
        link_label.pack(pady=(0, 12))

        if not scientific_name:
            return

        def fetch():
            try:
                info = species_info.get_species_info(scientific_name, common_name)
                win.after(0, lambda: populate(info, None))
            except Exception as e:
                win.after(0, lambda: populate(None, e))

        def populate(info, error):
            if not win.winfo_exists():
                return
            text.config(state="normal")
            text.delete("1.0", "end")
            if error:
                text.insert("1.0", f"Couldn't load details ({error}).")
            else:
                text.insert("1.0", info["extract"])
                if info["image_bytes"]:
                    try:
                        img = Image.open(io.BytesIO(info["image_bytes"]))
                        img.thumbnail((300, 220))
                        photo = ImageTk.PhotoImage(img)
                        image_label.configure(image=photo)
                        image_label.image = photo  # keep a reference
                    except Exception:
                        pass
                if info["page_url"]:
                    link_var.set("View full article on Wikipedia")
                    link_label.bind("<Button-1>", lambda e, url=info["page_url"]: webbrowser.open(url))
            text.config(state="disabled")

        threading.Thread(target=fetch, daemon=True).start()

    # ---------- window close behavior ----------

    def on_close_request(self):
        if self.on_minimize_to_tray:
            self.on_minimize_to_tray()
        else:
            self.root.destroy()
