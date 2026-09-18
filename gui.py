"""Tkinter GUI: species list with Mute / Always Alert toggles, search, status,
plus an inline detail panel and a Discord-inspired dark theme."""
import io
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from PIL import Image, ImageTk

import config
import species_info

# ---------- palette ----------
BG = "#313338"            # main content background
BG_SECONDARY = "#2b2d31"  # panels / sidebar
BG_TERTIARY = "#1e1f22"   # inputs / recessed areas
TEXT = "#f2f3f5"
TEXT_MUTED = "#b5bac1"
ACCENT = "#5865f2"        # blurple
ACCENT_HOVER = "#4752c4"
HEARD = "#23a55a"         # flash color for a species currently being heard
BORDER = "#1e1f22"

HIGHLIGHT_DURATION_MS = 4000


class BirdApp:
    def __init__(self, root, listener_controls):
        self.root = root
        self.listener_controls = listener_controls  # dict: start, stop, list_devices
        self.state = config.load_state()

        self.species_rows = {}  # common_name -> (row_frame, name_label)
        self.species_order = []  # keeps insertion order, newest first
        self.species_sci = {}  # common_name -> scientific_name
        self._flash_jobs = {}  # common_name -> after() id, to debounce rapid re-detections

        self.selected_species = None

        root.title("Bird Listener")
        root.geometry("980x640")
        root.minsize(760, 480)
        root.configure(background=BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close_request)

        self._apply_theme()
        self._build_layout()
        self._refresh_species_list()
        self._show_detail_placeholder()

        self.on_minimize_to_tray = None  # set externally by main.py

    # ---------- theme ----------

    def _apply_theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(".", background=BG, foreground=TEXT, fieldbackground=BG_TERTIARY,
                         bordercolor=BORDER, lightcolor=BG, darkcolor=BG, focuscolor=ACCENT)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=BG_SECONDARY)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=TEXT_MUTED)
        style.configure("Panel.TLabel", background=BG_SECONDARY, foreground=TEXT)
        style.configure("Header.TLabel", background=BG_SECONDARY, foreground=TEXT, font=("Segoe UI", 9, "bold"))

        style.configure("TLabelframe", background=BG, bordercolor=BORDER)
        style.configure("TLabelframe.Label", background=BG, foreground=TEXT_MUTED)

        style.configure("TEntry", fieldbackground=BG_TERTIARY, foreground=TEXT, insertcolor=TEXT,
                         bordercolor=BORDER)
        style.configure("TCombobox", fieldbackground=BG_TERTIARY, foreground=TEXT, background=BG_TERTIARY,
                         arrowcolor=TEXT, bordercolor=BORDER)
        style.map("TCombobox", fieldbackground=[("readonly", BG_TERTIARY)],
                   foreground=[("readonly", TEXT)])

        style.configure("TScale", background=BG, troughcolor=BG_TERTIARY)

        style.configure("TButton", background=ACCENT, foreground=TEXT, borderwidth=0,
                         focusthickness=0, padding=(12, 6))
        style.map("TButton", background=[("active", ACCENT_HOVER)])

        style.configure("Vertical.TScrollbar", background=BG_SECONDARY, troughcolor=BG,
                         bordercolor=BG, arrowcolor=TEXT_MUTED)

    # ---------- layout ----------

    def _build_layout(self):
        paned = tk.PanedWindow(self.root, orient="horizontal", background=BORDER,
                                sashwidth=4, sashrelief="flat", borderwidth=0)
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=8)
        right = ttk.Frame(paned, style="Panel.TFrame")
        paned.add(left, minsize=420, stretch="always")
        paned.add(right, minsize=280, width=340)

        self._build_left(left)
        self._build_detail_panel(right)

    def _build_left(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x")

        self.status_var = tk.StringVar(value="stopped")
        ttk.Label(top, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")

        self.toggle_btn = ttk.Button(top, text="Start Listening", command=self.toggle_listening)
        self.toggle_btn.pack(side="right")

        search_frame = ttk.Frame(parent, padding=(0, 8, 0, 0))
        search_frame.pack(fill="x")
        ttk.Label(search_frame, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_species_list())
        ttk.Entry(search_frame, textvariable=self.search_var).pack(side="left", fill="x", expand=True, padx=6)

        settings_frame = ttk.LabelFrame(parent, text="Settings", padding=8)
        settings_frame.pack(fill="x", pady=8)

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

        list_frame = ttk.LabelFrame(parent, text="Species heard", padding=8)
        list_frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(list_frame, highlightthickness=0, background=BG)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self.list_inner = tk.Frame(canvas, background=BG)
        self.list_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.list_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        header = tk.Frame(self.list_inner, background=BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        tk.Label(header, text="Species", width=28, font=("Segoe UI", 9, "bold"),
                 background=BG, foreground=TEXT_MUTED, anchor="w").grid(row=0, column=0, sticky="w")
        tk.Label(header, text="Muted", width=7, font=("Segoe UI", 9, "bold"),
                 background=BG, foreground=TEXT_MUTED).grid(row=0, column=1)
        tk.Label(header, text="Always", width=8, font=("Segoe UI", 9, "bold"),
                 background=BG, foreground=TEXT_MUTED).grid(row=0, column=2)

    def _build_detail_panel(self, parent):
        parent.pack_propagate(True)
        self.detail_frame = parent

        self.detail_placeholder = ttk.Label(
            parent, text="Click a species to view details", style="Panel.TLabel",
            wraplength=280, justify="left")

        self.detail_header = ttk.Label(parent, text="", style="Header.TLabel", font=("Segoe UI", 14, "bold"))
        self.detail_scientific = ttk.Label(parent, text="", style="Panel.TLabel", font=("Segoe UI", 10, "italic"))
        self.detail_image_label = tk.Label(parent, background=BG_SECONDARY)
        self.detail_text = tk.Text(parent, wrap="word", borderwidth=0, highlightthickness=0,
                                    background=BG_SECONDARY, foreground=TEXT, font=("Segoe UI", 10),
                                    height=10)
        self.detail_map_caption = ttk.Label(parent, text="Range map", style="Header.TLabel")
        self.detail_map_label = tk.Label(parent, background=BG_SECONDARY)
        self.detail_link = ttk.Label(parent, text="", style="Panel.TLabel", foreground=ACCENT, cursor="hand2")

        self._show_detail_placeholder()

    def _clear_detail_panel(self):
        for w in (self.detail_placeholder, self.detail_header, self.detail_scientific,
                  self.detail_image_label, self.detail_text, self.detail_map_caption,
                  self.detail_map_label, self.detail_link):
            w.pack_forget()

    def _show_detail_placeholder(self):
        self._clear_detail_panel()
        self.detail_placeholder.pack(padx=16, pady=16, anchor="w")

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

    def flash_species(self, common_name):
        """Briefly highlights a species row as 'currently being heard' (thread-safe).
        Timing is best-effort, tied to when the detection callback fires rather
        than the exact audio moment."""
        self.root.after(0, lambda: self._flash_species_ui(common_name))

    def _flash_species_ui(self, common_name):
        row = self.species_rows.get(common_name)
        if not row:
            return
        row_frame, name_label = row
        row_frame.configure(background=HEARD)
        name_label.configure(background=HEARD)

        existing_job = self._flash_jobs.get(common_name)
        if existing_job:
            self.root.after_cancel(existing_job)

        def revert():
            self._flash_jobs.pop(common_name, None)
            current = self.species_rows.get(common_name)
            if current:
                r, n = current
                r.configure(background=BG)
                n.configure(background=BG)

        self._flash_jobs[common_name] = self.root.after(HIGHLIGHT_DURATION_MS, revert)

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
            row = tk.Frame(self.list_inner, background=BG)
            row.grid(row=row_idx, column=0, sticky="ew", pady=1)

            name_label = tk.Label(row, text=name, width=28, background=BG, foreground=ACCENT,
                                   cursor="hand2", anchor="w", font=("Segoe UI", 10, "underline"))
            name_label.grid(row=0, column=0, sticky="w")
            name_label.bind("<Button-1>", lambda e, n=name: self._show_species_detail(n))

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

            tk.Checkbutton(row, variable=mute_var, command=on_mute_toggle, width=6,
                           background=BG, foreground=TEXT, selectcolor=BG_TERTIARY,
                           activebackground=BG, highlightthickness=0, borderwidth=0).grid(row=0, column=1)
            tk.Checkbutton(row, variable=always_var, command=on_always_toggle, width=8,
                           background=BG, foreground=TEXT, selectcolor=BG_TERTIARY,
                           activebackground=BG, highlightthickness=0, borderwidth=0).grid(row=0, column=2)

            self.species_rows[name] = (row, name_label)
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

    # ---------- inline species detail panel ----------

    def _show_species_detail(self, common_name):
        self.selected_species = common_name
        scientific_name = self.species_sci.get(common_name)

        self._clear_detail_panel()
        self.detail_header.configure(text=common_name)
        self.detail_header.pack(padx=16, pady=(16, 0), anchor="w")

        if scientific_name:
            self.detail_scientific.configure(text=scientific_name)
            self.detail_scientific.pack(padx=16, anchor="w")

        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "Loading..." if scientific_name else "No scientific name known for this detection.")
        self.detail_text.configure(state="disabled")
        self.detail_text.pack(fill="both", expand=True, padx=16, pady=8)

        if not scientific_name:
            return

        def fetch():
            try:
                info = species_info.get_species_info(scientific_name, common_name)
                self.root.after(0, lambda: self._populate_detail(common_name, info, None))
            except Exception as e:
                self.root.after(0, lambda: self._populate_detail(common_name, None, e))

        threading.Thread(target=fetch, daemon=True).start()

    def _populate_detail(self, common_name, info, error):
        # The user may have clicked a different species while this was loading.
        if self.selected_species != common_name or not self.detail_frame.winfo_exists():
            return

        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        if error:
            self.detail_text.insert("1.0", f"Couldn't load details ({error}).")
            self.detail_text.configure(state="disabled")
            return

        self.detail_text.insert("1.0", info["extract"])
        self.detail_text.configure(state="disabled")

        if info["image_bytes"]:
            photo = self._load_thumbnail(info["image_bytes"], (300, 200))
            if photo:
                self.detail_image_label.configure(image=photo)
                self.detail_image_label.image = photo
                self.detail_image_label.pack(padx=16, pady=(0, 8), before=self.detail_text)

        if info["map_bytes"]:
            map_photo = self._load_thumbnail(info["map_bytes"], (300, 200))
            if map_photo:
                self.detail_map_caption.pack(padx=16, pady=(8, 0), anchor="w")
                self.detail_map_label.configure(image=map_photo)
                self.detail_map_label.image = map_photo
                self.detail_map_label.pack(padx=16, pady=(4, 8))

        if info["page_url"]:
            self.detail_link.configure(text="View full article on Wikipedia")
            self.detail_link.bind("<Button-1>", lambda e, url=info["page_url"]: webbrowser.open(url))
            self.detail_link.pack(padx=16, pady=(0, 16), anchor="w")

    @staticmethod
    def _load_thumbnail(image_bytes, max_size):
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.thumbnail(max_size)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None  # e.g. an SVG range map, which Pillow can't rasterize

    # ---------- window close behavior ----------

    def on_close_request(self):
        if self.on_minimize_to_tray:
            self.on_minimize_to_tray()
        else:
            self.root.destroy()
