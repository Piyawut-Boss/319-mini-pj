import os
import sys
import json
import time
import fcntl
import signal
import hashlib
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import cv2
from PIL import Image, ImageTk

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

BASE = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE, "dataset")
DB_PATH = os.path.join(BASE, "people.json")
CASCADE_PATH = os.path.join(BASE, "haarcascade_frontalface_default.xml")
TRAINER_PATH = os.path.join(BASE, "trainer.yml")
LABELS_PATH = os.path.join(BASE, "labels.json")
LOCK_PATH = os.path.join(BASE, ".app.lock")
ADMIN_PATH = os.path.join(BASE, "admin.json")
SETTINGS_PATH = os.path.join(BASE, "settings.json")

# keep in sync with capture_faces.py / recognize.py
ROTATE = None

FACES_PER_PERSON = 20
CONFIDENCE_THRESHOLD = 70  # LBPH distance: lower = better match
DEFAULT_ADMIN_PASSWORD = "1234"

ROLE_NORMAL = "ผู้ใช้ทั่วไป"
ROLE_ADMIN = "ผู้ดูแลระบบ"

IDLE_TIMEOUT_MS = 20_000  # auto-return to standby after this much inactivity

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]

COLOR_BG = "#1e1f26"
COLOR_PANEL = "#262832"
COLOR_ACCENT = "#4f8cff"
COLOR_TEXT = "#e8e9ee"
COLOR_MUTED = "#8b8d98"
COLOR_OK = "#2fbf71"
COLOR_WARN = "#e0a52c"
COLOR_ERR = "#e0524c"


def load_db():
    if os.path.exists(DB_PATH):
        with open(DB_PATH) as f:
            return json.load(f)
    return {}


def save_db(db):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def _fit_image(img, target_w, target_h):
    """Resize img to completely cover target_w x target_h keeping its
    aspect ratio (cropping the excess) — no black bars."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = img.resize((new_w, new_h))
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def load_admin_password_hash():
    if os.path.exists(ADMIN_PATH):
        with open(ADMIN_PATH) as f:
            return json.load(f).get("password_hash")
    password_hash = _hash_password(DEFAULT_ADMIN_PASSWORD)
    with open(ADMIN_PATH, "w") as f:
        json.dump({"password_hash": password_hash}, f, indent=2)
    return password_hash


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    return {"standby_enabled": True}


def save_settings(settings):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def next_user_id(db):
    used = [int(info["user_id"]) for info in db.values() if str(info.get("user_id", "")).isdigit()]
    return f"{(max(used) + 1) if used else 1:04d}"


class OnScreenKeyboard(tk.Toplevel):
    """A simple QWERTY touch keyboard that types into whichever Entry
    called it. Not exhaustive (English layout only) but enough for a
    kiosk touchscreen where no physical keyboard is attached."""

    ROWS = [
        list("1234567890"),
        list("qwertyuiop"),
        list("asdfghjkl"),
        list("zxcvbnm"),
    ]

    def __init__(self, parent, entry_var, title="แป้นพิมพ์"):
        super().__init__(parent)
        self.entry_var = entry_var
        self.shift = False
        self.title(title)
        self.configure(bg=COLOR_PANEL)
        self.transient(parent)
        self.resizable(False, False)

        self.key_buttons = []
        body = tk.Frame(self, bg=COLOR_PANEL, padx=10, pady=10)
        body.pack()

        for row in self.ROWS:
            row_frame = tk.Frame(body, bg=COLOR_PANEL)
            row_frame.pack(pady=2)
            for ch in row:
                btn = tk.Button(
                    row_frame, text=ch, width=3, height=1, font=("Noto Sans", 12),
                    bg="#33364a", fg=COLOR_TEXT, activebackground="#3d4160",
                    relief="flat", command=lambda c=ch: self._press(c)
                )
                btn.pack(side="left", padx=2)
                self.key_buttons.append(btn)

        bottom = tk.Frame(body, bg=COLOR_PANEL)
        bottom.pack(pady=(6, 0), fill="x")
        tk.Button(
            bottom, text="Shift", width=6, bg="#33364a", fg=COLOR_TEXT,
            relief="flat", command=self._toggle_shift
        ).pack(side="left", padx=2)
        tk.Button(
            bottom, text="เว้นวรรค", width=14, bg="#33364a", fg=COLOR_TEXT,
            relief="flat", command=lambda: self._press(" ")
        ).pack(side="left", padx=2)
        tk.Button(
            bottom, text="⌫ ลบ", width=8, bg="#4a2d2d", fg=COLOR_ERR,
            relief="flat", command=self._backspace
        ).pack(side="left", padx=2)
        tk.Button(
            bottom, text="เสร็จ", width=8, bg=COLOR_ACCENT, fg="white",
            relief="flat", command=self.destroy
        ).pack(side="left", padx=2)

    def _toggle_shift(self):
        self.shift = not self.shift
        for btn in self.key_buttons:
            ch = btn["text"]
            btn.configure(text=ch.upper() if self.shift else ch.lower())

    def _press(self, ch):
        if self.shift and ch.isalpha():
            ch = ch.upper()
        self.entry_var.set(self.entry_var.get() + ch)

    def _backspace(self):
        self.entry_var.set(self.entry_var.get()[:-1])


class App:
    def __init__(self, root):
        self.root = root
        root.title("ระบบสแกนใบหน้า - Access Control")
        root.geometry("1024x600")
        root.minsize(700, 400)
        root.configure(bg=COLOR_BG)

        self._setup_style()

        self.cascade = cv2.CascadeClassifier(CASCADE_PATH)
        self.db = load_db()
        self.settings = load_settings()
        self.admin_password_hash = load_admin_password_hash()
        self.admin_authenticated = False
        self._person_order = []
        self.captured_count = 0
        self.capturing = False
        self.current_name = None
        self._last_capture_time = 0.0

        self.recognizer = None
        self.label_map = {}
        self._load_recognizer()

        self._idle_after_id = None

        self.picam2 = None
        if Picamera2 is not None:
            try:
                self.picam2 = Picamera2()
                self.picam2.configure(self.picam2.create_video_configuration(main={"size": (640, 480), "format": "RGB888"}))
                self.picam2.start()
                time.sleep(1)
            except Exception as e:
                print("camera init failed:", e)
                self.picam2 = None

        self._build_ui()
        self._build_standby_screen()
        self._update_clock()
        self._update_preview()
        self.root.bind_all("<Button-1>", self._on_activity, add="+")

    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT, font=("Noto Sans", 11))
        style.configure("TFrame", background=COLOR_BG)
        style.configure("Panel.TFrame", background=COLOR_PANEL)
        style.configure("TLabelframe", background=COLOR_PANEL, foreground=COLOR_TEXT, bordercolor="#3a3d4a")
        style.configure("TLabelframe.Label", background=COLOR_PANEL, foreground=COLOR_ACCENT, font=("Noto Sans", 11, "bold"))
        style.configure("TLabel", background=COLOR_PANEL, foreground=COLOR_TEXT)
        style.configure("Muted.TLabel", background=COLOR_PANEL, foreground=COLOR_MUTED, font=("Noto Sans", 9))
        style.configure("Heading.TLabel", background=COLOR_BG, foreground=COLOR_TEXT, font=("Noto Sans", 16, "bold"))
        style.configure(
            "TButton", background="#33364a", foreground=COLOR_TEXT,
            borderwidth=0, focusthickness=0, padding=8, font=("Noto Sans", 10)
        )
        style.map("TButton", background=[("active", "#3d4160")])
        style.configure(
            "Accent.TButton", background=COLOR_ACCENT, foreground="white",
            borderwidth=0, padding=8, font=("Noto Sans", 10, "bold")
        )
        style.map("Accent.TButton", background=[("active", "#3f76e0")])
        style.configure(
            "Danger.TButton", background="#4a2d2d", foreground=COLOR_ERR,
            borderwidth=0, padding=6, font=("Noto Sans", 9)
        )
        style.map("Danger.TButton", background=[("active", "#5c3535")])
        style.configure("TEntry", fieldbackground="#1a1b22", foreground=COLOR_TEXT, insertcolor=COLOR_TEXT, borderwidth=0, padding=6)
        style.configure("TSeparator", background="#3a3d4a")
        style.configure("TCheckbutton", background=COLOR_PANEL, foreground=COLOR_TEXT, font=("Noto Sans", 10))
        style.map("TCheckbutton", background=[("active", COLOR_PANEL)])
        style.configure(
            "Capture.Horizontal.TProgressbar", troughcolor="#1a1b22",
            background=COLOR_ACCENT, bordercolor=COLOR_PANEL, lightcolor=COLOR_ACCENT, darkcolor=COLOR_ACCENT
        )

    def _build_ui(self):
        self.main_container = tk.Frame(self.root, bg=COLOR_BG)
        self.main_container.pack(fill="both", expand=True)
        self.current_screen = "scan"

        self._build_scan_screen()
        self._build_admin_screen()

        # scan screen is the only thing visible behind standby by default
        self.scan_screen.place(relx=0, rely=0, relwidth=1, relheight=1)

    # ---------------------------------------------------------------
    # SCAN screen — the only thing a non-admin user ever sees (besides
    # standby). Just the live camera feed and a menu button top-right.
    # ---------------------------------------------------------------
    def _build_scan_screen(self):
        self.scan_screen = tk.Frame(self.main_container, bg="black")

        self.video_label = tk.Label(
            self.scan_screen, text="กล้องไม่ได้เชื่อมต่อ", anchor="center",
            background="black", foreground=COLOR_MUTED, font=("Noto Sans", 14)
        )
        self.video_label.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.menu_btn = ttk.Button(self.scan_screen, text="⚙", width=3, command=self._on_menu_pressed)
        self.menu_btn.place(relx=1.0, rely=0.0, x=-14, y=14, anchor="ne")

    # ---------------------------------------------------------------
    # ADMIN screen — only reachable after a successful password login.
    # Scrollable single column: preview, add-user form, user list.
    # ---------------------------------------------------------------
    def _build_admin_screen(self):
        self.admin_screen = tk.Frame(self.main_container, bg=COLOR_BG)

        topbar = tk.Frame(self.admin_screen, bg=COLOR_BG)
        topbar.pack(fill="x", padx=16, pady=(14, 6))
        ttk.Label(topbar, text="โหมดผู้ดูแลระบบ", style="Heading.TLabel").pack(side="left")
        ttk.Button(topbar, text="🔒 กลับหน้าสแกน", style="Danger.TButton", command=self._lock_admin).pack(side="right")

        self.admin_status_var = tk.StringVar(value="โหมด: ผู้ดูแลระบบ")
        ttk.Label(self.admin_screen, textvariable=self.admin_status_var, style="Muted.TLabel").pack(
            anchor="w", padx=16, pady=(0, 8)
        )

        # scrollable content column (portrait screens can get taller than the window)
        canvas = tk.Canvas(self.admin_screen, bg=COLOR_BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(self.admin_screen, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=(0, 16))
        vscroll.pack(side="right", fill="y", pady=(0, 16))

        content = tk.Frame(canvas, bg=COLOR_BG)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(content_window, width=e.width))

        # small live preview (for the face-capture step)
        preview_wrap = tk.Frame(content, bg="black", height=260)
        preview_wrap.pack(fill="x", pady=(0, 14))
        preview_wrap.pack_propagate(False)
        self.admin_video_label = tk.Label(
            preview_wrap, text="กล้องไม่ได้เชื่อมต่อ", anchor="center",
            background="black", foreground=COLOR_MUTED, font=("Noto Sans", 11)
        )
        self.admin_video_label.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="พร้อม")
        self.status_label = tk.Label(
            content, textvariable=self.status_var, anchor="w",
            bg=COLOR_PANEL, fg=COLOR_TEXT, font=("Noto Sans", 10), padx=12, pady=8
        )
        self.status_label.pack(fill="x", pady=(0, 14))

        form = ttk.Labelframe(content, text="ลงทะเบียนผู้ใช้ใหม่", padding=14)
        form.pack(fill="x", pady=(0, 14))
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="User ID").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Label(form, text="สิทธิ์ผู้ใช้").grid(row=0, column=1, sticky="w")

        self.user_id_var = tk.StringVar()
        id_entry = ttk.Entry(form, textvariable=self.user_id_var)
        id_entry.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(2, 10))
        id_entry.bind("<Button-1>", lambda e: self._open_keyboard(self.user_id_var, "User ID"))

        self.role_var = tk.StringVar(value=ROLE_NORMAL)
        ttk.Combobox(
            form, textvariable=self.role_var, state="readonly",
            values=[ROLE_NORMAL, ROLE_ADMIN]
        ).grid(row=1, column=1, sticky="ew", pady=(2, 10))

        ttk.Label(form, text="ชื่อ").grid(row=2, column=0, columnspan=2, sticky="w")
        self.name_var = tk.StringVar()
        name_entry = ttk.Entry(form, textvariable=self.name_var)
        name_entry.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 10))
        name_entry.bind("<Button-1>", lambda e: self._open_keyboard(self.name_var, "ชื่อ"))

        ttk.Label(form, text="RFID UID").grid(row=4, column=0, columnspan=2, sticky="w")
        self.rfid_var = tk.StringVar()
        rfid_entry = ttk.Entry(form, textvariable=self.rfid_var)
        rfid_entry.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 2))
        rfid_entry.bind("<Button-1>", lambda e: self._open_keyboard(self.rfid_var, "RFID UID"))
        ttk.Label(form, text="ยังไม่ได้ต่อเครื่องอ่าน RFID — กรอกเองไปก่อน", style="Muted.TLabel", wraplength=400).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )

        self.capture_btn = ttk.Button(
            form, text=f"📷  ถ่ายรูปใบหน้า ({FACES_PER_PERSON} รูป)", command=self.start_capture
        )
        self.capture_btn.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 4))

        self.progress = ttk.Progressbar(
            form, style="Capture.Horizontal.TProgressbar", maximum=FACES_PER_PERSON, value=0
        )
        self.progress.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(2, 10))

        ttk.Button(form, text="บันทึกผู้ใช้", style="Accent.TButton", command=self.save_person).grid(
            row=9, column=0, columnspan=2, sticky="ew", pady=(0, 4)
        )
        ttk.Button(form, text="เทรนโมเดลใหม่", command=self.retrain).grid(row=10, column=0, columnspan=2, sticky="ew")

        list_frame = ttk.Labelframe(content, text="ผู้ใช้ที่ลงทะเบียนแล้ว", padding=10)
        list_frame.pack(fill="x", pady=(0, 20))

        list_container = tk.Frame(list_frame, bg=COLOR_PANEL)
        list_container.pack(fill="x")

        self.people_list = tk.Listbox(
            list_container, bg="#1a1b22", fg=COLOR_TEXT, selectbackground=COLOR_ACCENT,
            borderwidth=0, highlightthickness=0, activestyle="none", font=("Noto Sans", 10), height=8
        )
        self.people_list.pack(side="left", fill="both", expand=True)
        list_scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=self.people_list.yview)
        list_scrollbar.pack(side="right", fill="y")
        self.people_list.configure(yscrollcommand=list_scrollbar.set)

        ttk.Button(list_frame, text="ลบผู้ใช้ที่เลือก", style="Danger.TButton", command=self.delete_selected).pack(
            fill="x", pady=(8, 0)
        )

        settings_frame = ttk.Labelframe(content, text="ตั้งค่าระบบ", padding=14)
        settings_frame.pack(fill="x", pady=(0, 20))
        self.standby_enabled_var = tk.BooleanVar(value=self.settings.get("standby_enabled", True))
        ttk.Checkbutton(
            settings_frame, text="เปิดใช้งานหน้าพักหน้าจอ (Standby)",
            variable=self.standby_enabled_var, command=self._on_toggle_standby_setting
        ).pack(anchor="w")

        self._refresh_people_list()

    def _show_scan_screen(self):
        self.admin_screen.place_forget()
        self.scan_screen.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.scan_screen.lift()
        self.current_screen = "scan"

    def _show_admin_screen(self):
        self.scan_screen.place_forget()
        self.admin_screen.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.admin_screen.lift()
        self.current_screen = "admin"

    def _build_standby_screen(self):
        self.standby_frame = tk.Frame(self.main_container, bg=COLOR_BG)

        center = tk.Frame(self.standby_frame, bg=COLOR_BG)
        center.place(relx=0.5, rely=0.5, anchor="center")

        self.clock_var = tk.StringVar(value="--:--:--")
        self.date_var = tk.StringVar(value="")

        tk.Label(center, textvariable=self.clock_var, font=("Noto Sans", 64, "bold"), bg=COLOR_BG, fg=COLOR_TEXT).pack()
        tk.Label(center, textvariable=self.date_var, font=("Noto Sans", 16), bg=COLOR_BG, fg=COLOR_MUTED).pack(pady=(4, 40))
        tk.Label(center, text="👋  แตะหน้าจอเพื่อสแกนใบหน้า", font=("Noto Sans", 18), bg=COLOR_BG, fg=COLOR_ACCENT).pack()

        # tapping anywhere on the standby screen wakes it — bind the frame
        # and every child widget so a tap on the labels also counts
        for widget in (self.standby_frame, center, *center.winfo_children()):
            widget.bind("<Button-1>", lambda e: self._wake_from_standby())

        self.standby_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.standby_frame.lift()

    def _update_clock(self):
        now = datetime.now()
        self.clock_var.set(now.strftime("%H:%M:%S"))
        buddhist_year = now.year + 543
        self.date_var.set(f"{now.day} {THAI_MONTHS[now.month]} {buddhist_year}")
        self.root.after(1000, self._update_clock)

    def _wake_from_standby(self):
        self.standby_frame.place_forget()
        self._arm_idle_timer()

    def _go_standby(self):
        if not self.settings.get("standby_enabled", True):
            return  # standby disabled in admin settings — stay on the scan screen
        if self.admin_authenticated or self.capturing:
            self._arm_idle_timer()  # don't interrupt admin work or an in-progress capture
            return
        self.standby_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.standby_frame.lift()

    def _arm_idle_timer(self):
        if self._idle_after_id is not None:
            self.root.after_cancel(self._idle_after_id)
        self._idle_after_id = self.root.after(IDLE_TIMEOUT_MS, self._go_standby)

    def _on_activity(self, event):
        self._arm_idle_timer()

    def _open_keyboard(self, entry_var, title):
        OnScreenKeyboard(self.root, entry_var, title=title)

    def _on_menu_pressed(self):
        if self.admin_authenticated:
            return
        self._open_admin_auth_dialog()

    def _open_admin_auth_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("ยืนยันสิทธิ์ผู้ดูแลระบบ")
        dialog.configure(bg=COLOR_PANEL)
        dialog.transient(self.root)

        tk.Label(
            dialog, text="ใส่รหัสผ่านผู้ดูแลระบบ", font=("Noto Sans", 12, "bold"),
            bg=COLOR_PANEL, fg=COLOR_TEXT
        ).pack(padx=20, pady=(16, 6))

        password_var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=password_var, show="•", width=24, justify="center")
        entry.pack(padx=20, pady=4)
        entry.bind("<Button-1>", lambda e: self._open_keyboard(password_var, "รหัสผ่าน"))
        entry.bind("<Return>", lambda e: try_login())
        entry.focus_set()

        error_var = tk.StringVar(value="")
        tk.Label(dialog, textvariable=error_var, bg=COLOR_PANEL, fg=COLOR_ERR, font=("Noto Sans", 9)).pack()

        btn_row = tk.Frame(dialog, bg=COLOR_PANEL)
        btn_row.pack(pady=(10, 16))

        def try_login():
            if _hash_password(password_var.get()) == self.admin_password_hash:
                dialog.destroy()
                self._unlock_admin()
            else:
                error_var.set("รหัสผ่านไม่ถูกต้อง")
                password_var.set("")

        ttk.Button(btn_row, text="ยกเลิก", command=dialog.destroy).pack(side="left", padx=6)
        ttk.Button(btn_row, text="เข้าสู่ระบบ", style="Accent.TButton", command=try_login).pack(side="left", padx=6)

    def _unlock_admin(self):
        self.admin_authenticated = True
        self.admin_status_var.set("โหมด: ผู้ดูแลระบบ (ปลดล็อกแล้ว)")
        self.user_id_var.set(next_user_id(self.db))
        self._show_admin_screen()
        self._set_status("เข้าสู่เมนูผู้ดูแลระบบแล้ว", "ok")

    def _lock_admin(self):
        self.admin_authenticated = False
        self._show_scan_screen()
        self._arm_idle_timer()

    def _on_toggle_standby_setting(self):
        self.settings["standby_enabled"] = self.standby_enabled_var.get()
        save_settings(self.settings)
        self._set_status(
            "เปิดใช้งานหน้าพักหน้าจอแล้ว" if self.settings["standby_enabled"] else "ปิดหน้าพักหน้าจอแล้ว",
            "ok",
        )

    def _load_recognizer(self):
        if os.path.exists(TRAINER_PATH) and os.path.exists(LABELS_PATH):
            try:
                recognizer = cv2.face.LBPHFaceRecognizer_create()
                recognizer.read(TRAINER_PATH)
                with open(LABELS_PATH) as f:
                    self.label_map = {int(k): v for k, v in json.load(f).items()}
                self.recognizer = recognizer
                print(f"[recognizer] loaded, {len(self.label_map)} people: {list(self.label_map.values())}", flush=True)
            except Exception as e:
                print(f"[recognizer] failed to load: {e}", flush=True)
                self.recognizer = None
                self.label_map = {}
        else:
            self.recognizer = None
            self.label_map = {}

    def _set_status(self, text, kind="normal"):
        # only two status colors: green for success, red for failure —
        # anything else (in-progress, informational) stays plain text
        colors = {"normal": COLOR_TEXT, "ok": COLOR_OK, "warn": COLOR_TEXT, "err": COLOR_ERR}
        self.status_var.set(text)
        self.status_label.configure(fg=colors.get(kind, COLOR_TEXT))

    def _refresh_people_list(self):
        self.people_list.delete(0, tk.END)
        self._person_order = list(self.db.keys())
        for name in self._person_order:
            info = self.db[name]
            rfid = info.get("rfid") or "-"
            uid = info.get("user_id", "----")
            role = info.get("role", ROLE_NORMAL)
            self.people_list.insert(
                tk.END,
                f"  [{uid}] {name}  ({role})   •   RFID: {rfid}   •   รูป: {info.get('face_count', 0)}"
            )

    def _update_preview(self):
        if self.picam2 is not None:
            try:
                frame = self.picam2.capture_array()  # BGR order (Picamera2's "RGB888" format)
                if ROTATE is not None:
                    frame = cv2.rotate(frame, ROTATE)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self.cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(80, 80))

                if self.capturing and len(faces) > 0:
                    now = time.time()
                    if now - self._last_capture_time >= 0.3:
                        self._last_capture_time = now
                        x, y, w, h = faces[0]
                        face = cv2.resize(gray[y:y + h, x:x + w], (200, 200))
                        self.captured_count += 1
                        out_dir = os.path.join(DATASET_DIR, self.current_name)
                        os.makedirs(out_dir, exist_ok=True)
                        cv2.imwrite(os.path.join(out_dir, f"{self.captured_count:03d}.jpg"), face)
                        print(f"[capture] saved {self.captured_count}/{FACES_PER_PERSON} -> {out_dir}", flush=True)
                        self.progress.configure(value=self.captured_count)
                        self._set_status(f"กำลังถ่าย... {self.captured_count}/{FACES_PER_PERSON}", "warn")
                        if self.captured_count >= FACES_PER_PERSON:
                            self.capturing = False
                            self.capture_btn.configure(state="normal")
                            self._set_status(f"ถ่ายครบ {FACES_PER_PERSON} รูปแล้ว กด 'บันทึกผู้ใช้' ต่อได้เลย", "ok")

                for (x, y, w, h) in faces:
                    label_text = None
                    if self.recognizer is not None and not self.capturing:
                        face = cv2.resize(gray[y:y + h, x:x + w], (200, 200))
                        pred_label, confidence = self.recognizer.predict(face)
                        if confidence < CONFIDENCE_THRESHOLD:
                            label_text = self.label_map.get(pred_label, "?")
                        else:
                            label_text = "stranger"

                    # green = recognized (access granted), red = stranger / not
                    # recognized yet (access denied)
                    recognized = label_text is not None and label_text != "stranger"
                    box_color = (79, 255, 140) if recognized else (60, 60, 235)

                    cv2.rectangle(frame, (x, y), (x + w, y + h), box_color, 2)
                    if label_text:
                        cv2.rectangle(frame, (x, y - 26), (x + w, y), box_color, -1)
                        cv2.putText(
                            frame, label_text, (x + 4, y - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 30, 20), 2, cv2.LINE_AA
                        )

                # only the currently visible screen's preview label needs updating
                target = self.video_label if self.current_screen == "scan" else self.admin_video_label
                img_rgb = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                label_w = target.winfo_width()
                label_h = target.winfo_height()
                if label_w > 10 and label_h > 10:
                    img_rgb = _fit_image(img_rgb, label_w, label_h)
                imgtk = ImageTk.PhotoImage(image=img_rgb)
                target.imgtk = imgtk
                target.configure(image=imgtk, text="")
            except Exception as e:
                print(f"[preview] error: {e}", flush=True)
                self.video_label.configure(text=f"กล้องมีปัญหา: {e}", image="")
                self.admin_video_label.configure(text=f"กล้องมีปัญหา: {e}", image="")
        self.root.after(150, self._update_preview)

    def start_capture(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("แจ้งเตือน", "กรุณากรอกชื่อก่อนถ่ายรูป")
            return
        if self.picam2 is None:
            messagebox.showerror("ผิดพลาด", "กล้องไม่ได้เชื่อมต่อ")
            return
        self.current_name = name
        self.captured_count = 0
        self._last_capture_time = 0.0
        self.progress.configure(value=0)
        self.capturing = True
        self.capture_btn.configure(state="disabled")
        self._set_status("กำลังถ่าย... หันหน้าเข้ากล้อง", "warn")
        print(f"[capture] start_capture pressed for name={name!r}", flush=True)

    def save_person(self):
        if not self.admin_authenticated:
            messagebox.showerror("ผิดพลาด", "ต้องเข้าสู่เมนูผู้ดูแลระบบก่อนจึงจะเพิ่มผู้ใช้ได้")
            return
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("แจ้งเตือน", "กรุณากรอกชื่อ")
            return
        user_id = self.user_id_var.get().strip() or next_user_id(self.db)
        out_dir = os.path.join(DATASET_DIR, name)
        face_count = len(os.listdir(out_dir)) if os.path.isdir(out_dir) else 0
        self.db[name] = {
            "user_id": user_id,
            "role": self.role_var.get(),
            "rfid": self.rfid_var.get().strip() or None,
            "face_count": face_count,
            "registered_at": datetime.now().isoformat(timespec="seconds"),
        }
        save_db(self.db)
        self._refresh_people_list()
        self.name_var.set("")
        self.rfid_var.set("")
        self.role_var.set(ROLE_NORMAL)
        self.user_id_var.set(next_user_id(self.db))
        self.progress.configure(value=0)

        if face_count > 0:
            self._set_status(f"บันทึก {name} แล้ว — กำลังเทรนโมเดล...", "warn")
            self.retrain()
        else:
            self._set_status(f"บันทึก {name} แล้ว (ยังไม่มีรูปหน้า — ยังไม่เทรนโมเดล)", "warn")

    def delete_selected(self):
        sel = self.people_list.curselection()
        if not sel:
            messagebox.showwarning("แจ้งเตือน", "กรุณาเลือกผู้ใช้ที่ต้องการลบจากรายการ")
            return
        name = self._person_order[sel[0]]
        if not messagebox.askyesno("ยืนยันการลบ", f"ลบผู้ใช้ '{name}' ออกจากระบบ?"):
            return
        self.db.pop(name, None)
        save_db(self.db)
        self._refresh_people_list()
        self._set_status(f"ลบ {name} แล้ว", "warn")

    def retrain(self):
        self._set_status("กำลังเทรนโมเดล...", "warn")
        self.root.update()
        result = subprocess.run(
            [os.path.join(BASE, "venv", "bin", "python3"), os.path.join(BASE, "train_model.py")],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            self._load_recognizer()
            messagebox.showinfo("สำเร็จ", result.stdout)
            self._set_status("เทรนโมเดลเสร็จแล้ว — พร้อมจดจำใบหน้าแล้ว", "ok")
        else:
            messagebox.showerror("ผิดพลาด", result.stderr)
            self._set_status("เทรนโมเดลล้มเหลว", "err")

    def on_close(self):
        if self.picam2 is not None:
            try:
                self.picam2.stop()
            except Exception:
                pass
        self.root.destroy()


def _try_lock(lock_file):
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def acquire_single_instance_lock():
    """Takes over the lock even if another instance already holds it: the
    old instance (pid recorded in the lock file) is terminated first, then
    this process claims the lock. Keep the returned handle alive for the
    lifetime of the process (closing/GC releases the lock)."""
    lock_file = open(LOCK_PATH, "a+")
    if not _try_lock(lock_file):
        lock_file.seek(0)
        old_pid_text = lock_file.read().strip()
        lock_file.close()
        old_pid = int(old_pid_text) if old_pid_text.isdigit() else None

        if old_pid:
            print(f"[lock] another instance (pid {old_pid}) is running, closing it", flush=True)
            try:
                os.kill(old_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        acquired = False
        for _ in range(25):  # ~5s to let the old instance shut down and release the camera
            time.sleep(0.2)
            lock_file = open(LOCK_PATH, "a+")
            if _try_lock(lock_file):
                acquired = True
                break
            lock_file.close()

        if not acquired and old_pid:
            print(f"[lock] pid {old_pid} did not exit in time, forcing kill -9", flush=True)
            try:
                os.kill(old_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            time.sleep(0.5)
            lock_file = open(LOCK_PATH, "a+")
            acquired = _try_lock(lock_file)
            if not acquired:
                lock_file.close()

        if not acquired:
            return None

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def main():
    lock = acquire_single_instance_lock()
    if lock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "เปิดโปรแกรมไม่ได้",
            "ไม่สามารถปิดโปรแกรมเดิมและเปิดใหม่ได้ กรุณาปิดหน้าต่างเดิมด้วยตัวเองแล้วลองใหม่",
        )
        sys.exit(1)

    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
