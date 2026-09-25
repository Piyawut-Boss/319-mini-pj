import os
import sys
import json
import time
import fcntl
import signal
import hashlib
import threading
import queue
import urllib.request
import urllib.parse
import urllib.error
import uuid
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

import cv2
from PIL import Image, ImageTk

from face_engine import FaceDetector, FaceIdentifier, load_embeddings, save_embeddings, MATCH_THRESHOLD

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

try:
    import serial
except ImportError:
    serial = None

BASE = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE, "dataset")
DB_PATH = os.path.join(BASE, "people.json")
LOCK_PATH = os.path.join(BASE, ".app.lock")
ADMIN_PATH = os.path.join(BASE, "admin.json")
SETTINGS_PATH = os.path.join(BASE, "settings.json")

# keep in sync with capture_faces.py / recognize.py
ROTATE = None

ARDUINO_PORT = "/dev/arduino_rfid"  # stable symlink from udev rule, survives ttyACM0/1/2... reassignment
ARDUINO_BAUD = 9600

FACES_PER_PERSON = 10
DEFAULT_ADMIN_PASSWORD = "1234"

ROLE_NORMAL = "ผู้ใช้ทั่วไป"
ROLE_ADMIN = "ผู้ดูแลระบบ"

IDLE_TIMEOUT_MS = 20_000  # auto-return to standby after this much inactivity
FACE_UNLOCK_COOLDOWN_S = 5.0  # min seconds between auto-unlocks from face recognition
STRANGER_LINGER_S = 3.0  # unrecognized face must be continuously present this long before the first alert (avoids alerting on someone just passing by)
STRANGER_NOTIFY_COOLDOWN_S = 10.0  # min seconds between repeat Telegram alerts while the same stranger keeps lingering

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]

COLOR_BG = "#f4f5f9"
COLOR_PANEL = "#ffffff"
COLOR_SURFACE = "#eef0f5"
COLOR_SURFACE_HOVER = "#e2e5ee"
COLOR_BORDER = "#e0e2ea"
COLOR_ACCENT = "#3d5ce0"
COLOR_ACCENT_HOVER = "#3049c2"
COLOR_ACCENT_SOFT = "#e8ecfd"
COLOR_TEXT = "#1b1e29"
COLOR_MUTED = "#6b7080"
COLOR_OK = "#189c6b"
COLOR_OK_SOFT = "#e2f6ee"
COLOR_WARN = "#b9790f"
COLOR_ERR = "#d63b30"
COLOR_ERR_SOFT = "#fbe7e5"

BUTTON_RADIUS = 10


class RoundedButton(tk.Frame):
    """A flat, rounded-corner button drawn on a Canvas — ttk can't do real
    corner radii, and a business-modern look needs them. Behaves enough like
    ttk.Button for existing call sites: .configure(state=...) / (text=...)
    keeps working via the config()/configure() override below."""

    _PALETTES = {
        "accent": (COLOR_ACCENT, COLOR_ACCENT_HOVER, "#ffffff"),
        "normal": (COLOR_SURFACE, COLOR_SURFACE_HOVER, COLOR_TEXT),
        "danger": (COLOR_ERR_SOFT, "#3a2224", COLOR_ERR),
    }

    def __init__(self, parent, text, command=None, style="normal", height=42,
                 font=("Noto Sans", 10), radius=BUTTON_RADIUS, panel_bg=None):
        panel_bg = panel_bg or (parent["bg"] if "bg" in parent.keys() else COLOR_PANEL)
        super().__init__(parent, bg=panel_bg, highlightthickness=0)
        self.command = command
        self.enabled = True
        self.radius = radius
        self.text = text
        self.font = font
        self.bg_color, self.hover_color, self.fg_color = self._PALETTES[style]
        self._current_bg = self.bg_color

        self.canvas = tk.Canvas(self, height=height, bg=panel_bg, highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._redraw)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)

    def _round_rect_points(self, x1, y1, x2, y2, r):
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    def _redraw(self, event=None):
        self.canvas.delete("all")
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 2 or h < 2:
            return
        r = min(self.radius, h / 2)
        fill = self._current_bg if self.enabled else COLOR_SURFACE
        self.canvas.create_polygon(self._round_rect_points(0, 0, w, h, r), smooth=True, fill=fill, outline=fill)
        self.canvas.create_text(
            w / 2, h / 2, text=self.text, fill=self.fg_color if self.enabled else COLOR_MUTED,
            font=self.font
        )

    def _on_enter(self, event):
        if self.enabled:
            self._current_bg = self.hover_color
            self._redraw()

    def _on_leave(self, event):
        self._current_bg = self.bg_color
        self._redraw()

    def _on_click(self, event):
        if self.enabled and self.command:
            self.command()

    def configure(self, **kwargs):
        redraw = False
        if "state" in kwargs:
            self.enabled = kwargs.pop("state") != "disabled"
            redraw = True
        if "text" in kwargs:
            self.text = kwargs.pop("text")
            redraw = True
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if redraw:
            self._redraw()
        if kwargs:
            super().configure(**kwargs)

    config = configure


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


def send_telegram_message(bot_token, chat_id, text):
    """Blocking call to the Telegram Bot API — always run this off the
    Tkinter mainloop thread (see App._notify_telegram_async) since a slow
    or unreachable network would otherwise freeze the whole UI, same
    reasoning as FrameGrabber/ArduinoLink running on their own threads."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5) as resp:
            if resp.status == 200:
                return True, "ok"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, str(e)


def send_telegram_photo(bot_token, chat_id, image_bytes, caption=""):
    """Blocking call to the Telegram Bot API's sendPhoto — same off-thread
    requirement as send_telegram_message. Builds the multipart/form-data
    body by hand since urllib has no built-in multipart encoder."""
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    boundary = "----doorlockphoto" + uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode("utf-8"),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"snapshot.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode("utf-8"),
        image_bytes,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    body = b"".join(parts)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=10) as resp:
            if resp.status == 200:
                return True, "ok"
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "replace")
        return False, f"HTTP {e.code}: {body_txt}"
    except Exception as e:
        return False, str(e)


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
                    row_frame, text=ch, width=3, height=2, font=("Noto Sans", 12),
                    bg=COLOR_SURFACE, fg=COLOR_TEXT, activebackground=COLOR_SURFACE_HOVER, activeforeground=COLOR_TEXT,
                    relief="flat", bd=0, command=lambda c=ch: self._press(c)
                )
                btn.pack(side="left", padx=2)
                self.key_buttons.append(btn)

        bottom = tk.Frame(body, bg=COLOR_PANEL)
        bottom.pack(pady=(6, 0), fill="x")
        tk.Button(
            bottom, text="Shift", width=6, height=2, bg=COLOR_SURFACE, fg=COLOR_TEXT,
            activebackground=COLOR_SURFACE_HOVER, activeforeground=COLOR_TEXT,
            relief="flat", bd=0, command=self._toggle_shift
        ).pack(side="left", padx=2)
        tk.Button(
            bottom, text="เว้นวรรค", width=14, height=2, bg=COLOR_SURFACE, fg=COLOR_TEXT,
            activebackground=COLOR_SURFACE_HOVER, activeforeground=COLOR_TEXT,
            relief="flat", bd=0, command=lambda: self._press(" ")
        ).pack(side="left", padx=2)
        tk.Button(
            bottom, text="⌫ ลบ", width=8, height=2, bg=COLOR_ERR_SOFT, fg=COLOR_ERR,
            activebackground="#3a2224", activeforeground=COLOR_ERR,
            relief="flat", bd=0, command=self._backspace
        ).pack(side="left", padx=2)
        tk.Button(
            bottom, text="เสร็จ", width=8, height=2, bg=COLOR_ACCENT, fg="white",
            activebackground=COLOR_ACCENT_HOVER, activeforeground="white",
            relief="flat", bd=0, command=self.destroy
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


class FrameGrabber(threading.Thread):
    """Runs picam2.capture_array() in its own thread instead of the Tkinter
    mainloop calling it directly. A real deadlock was observed in production
    (found with py-spy): the main thread got stuck forever inside
    capture_array()'s internal Future.result(), which has no timeout —
    freezing the entire UI since Tkinter is single-threaded. Isolating the
    capture call here means a Picamera2-internal stall only stops this
    thread's frame updates; the rest of the app (buttons, menus) stays
    responsive no matter what the camera pipeline does."""

    def __init__(self, picam2):
        super().__init__(daemon=True)
        self.picam2 = picam2
        self._lock = threading.Lock()
        self._frame = None
        self._stopped = False

    def run(self):
        while not self._stopped:
            try:
                frame = self.picam2.capture_array()
                with self._lock:
                    self._frame = frame
            except Exception as e:
                print(f"[camera] capture error: {e}", flush=True)
                time.sleep(0.5)

    def get_frame(self):
        with self._lock:
            return self._frame

    def stop(self):
        self._stopped = True


class ArduinoLink:
    """Non-blocking serial link to the door-guard Arduino. Connect failure
    is silent (sets self.connected = False) — the app must work fine with
    no Arduino attached, same as the camera. Reads happen on a background
    thread and land in a queue so the Tk mainloop never blocks on I/O."""

    def __init__(self, port=ARDUINO_PORT, baud=ARDUINO_BAUD):
        self.connected = False
        self._serial = None
        self._lines = queue.Queue()
        self._port_hint = port
        self._baud = baud
        if serial is None:
            print("[arduino] pyserial not installed", flush=True)
            return
        threading.Thread(target=self._connection_loop, daemon=True).start()

    @staticmethod
    def _find_port():
        # USB re-enumeration can rename /dev/ttyACM0 -> ttyACM1 (etc.) if the
        # board resets/reconnects — look for an Arduino by USB vendor ID
        # instead of trusting a fixed path.
        try:
            from serial.tools import list_ports
        except ImportError:
            return None
        for p in list_ports.comports():
            if p.vid == 0x2341:  # Arduino SA
                return p.device
        return None

    def _connection_loop(self):
        """Runs for the lifetime of the app: connects, reads lines while
        connected, and — if the cable drops mid-session (this has actually
        happened: USB disconnects seen in dmesg during testing) — notices
        the failed read, marks self.connected False so the UI stops
        pretending it can talk to the board, and keeps retrying the
        connection in the background so it recovers on its own once the
        cable/board comes back."""
        while True:
            if self._serial is None:
                port = self._find_port() or self._port_hint
                try:
                    self._serial = serial.Serial(port, self._baud, timeout=1)
                    time.sleep(2)  # Uno resets itself when the serial port opens
                    self.connected = True
                    print(f"[arduino] connected on {port}", flush=True)
                except Exception as e:
                    self._serial = None
                    time.sleep(3)
                    continue

            try:
                raw = self._serial.readline()
            except Exception as e:
                print(f"[arduino] read error: {e} — will retry connecting", flush=True)
                self.connected = False
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
                time.sleep(1)
                continue

            line = raw.decode(errors="ignore").strip()
            if line:
                self._lines.put(line)

    def send(self, command):
        if not self.connected:
            return
        try:
            self._serial.write((command + "\n").encode())
        except Exception as e:
            print(f"[arduino] send failed: {e} — will retry connecting", flush=True)
            self.connected = False  # the read loop will close it and reconnect

    def poll_lines(self):
        lines = []
        while not self._lines.empty():
            lines.append(self._lines.get_nowait())
        return lines


class App:
    def __init__(self, root):
        self.root = root
        root.title("ระบบสแกนใบหน้า - Access Control")
        root.geometry("1024x600")
        root.minsize(700, 400)
        root.configure(bg=COLOR_BG)

        self._setup_style()

        self.detector = FaceDetector()
        self.identifier = FaceIdentifier()
        self.db = load_db()
        self.settings = load_settings()
        self.admin_password_hash = load_admin_password_hash()
        self.admin_authenticated = False
        self._person_order = []
        self.captured_count = 0
        self.capturing = False
        self.current_name = None
        self._last_capture_time = 0.0
        self._editing_original_name = None

        self.gallery = {}
        self._load_gallery()

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

        self.frame_grabber = None
        if self.picam2 is not None:
            self.frame_grabber = FrameGrabber(self.picam2)
            self.frame_grabber.start()

        self.arduino = ArduinoLink()
        self.registering_rfid = False
        self.pending_rfids = []
        self.arduino_confirmed_total = None
        self.syncing_cards = False
        self._awaiting_clear = False
        self._sync_queue = []
        self._sync_total = 0
        self._sync_progress = 0
        self.door_held_open = False
        self._last_face_unlock_time = 0.0
        self._last_stranger_notify_time = 0.0
        self._stranger_since = None
        self.telegram_token_var = tk.StringVar(value=self.settings.get("telegram_bot_token", ""))
        self.telegram_chatid_var = tk.StringVar(value=self.settings.get("telegram_chat_id", ""))

        self._build_ui()
        self._build_standby_screen()
        self._update_clock()
        self._update_preview()
        self._poll_arduino()
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
        style.configure("TLabelframe", background=COLOR_PANEL, foreground=COLOR_TEXT, bordercolor=COLOR_BORDER, borderwidth=1)
        style.configure("TLabelframe.Label", background=COLOR_PANEL, foreground=COLOR_MUTED, font=("Noto Sans", 10, "bold"))
        style.configure("TLabel", background=COLOR_PANEL, foreground=COLOR_TEXT)
        style.configure("Muted.TLabel", background=COLOR_PANEL, foreground=COLOR_MUTED, font=("Noto Sans", 9))
        style.configure("Heading.TLabel", background=COLOR_BG, foreground=COLOR_TEXT, font=("Noto Sans", 17, "bold"))
        style.configure("TEntry", fieldbackground=COLOR_SURFACE, foreground=COLOR_TEXT, insertcolor=COLOR_TEXT, borderwidth=0, padding=8)
        style.map("TEntry", fieldbackground=[("focus", COLOR_SURFACE_HOVER)])
        style.configure("TCombobox", fieldbackground=COLOR_SURFACE, background=COLOR_SURFACE, foreground=COLOR_TEXT, borderwidth=0, padding=6, arrowcolor=COLOR_MUTED)
        style.map("TCombobox", fieldbackground=[("readonly", COLOR_SURFACE)])
        style.configure("TSeparator", background=COLOR_BORDER)
        style.configure("TCheckbutton", background=COLOR_PANEL, foreground=COLOR_TEXT, font=("Noto Sans", 10))
        style.map("TCheckbutton", background=[("active", COLOR_PANEL)])
        style.configure("TScrollbar", background=COLOR_SURFACE, troughcolor=COLOR_PANEL, bordercolor=COLOR_PANEL, arrowcolor=COLOR_MUTED)
        style.configure(
            "Capture.Horizontal.TProgressbar", troughcolor=COLOR_SURFACE,
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

        self.menu_btn = tk.Button(
            self.scan_screen, text="⚙", font=("Noto Sans", 16),
            bg=COLOR_SURFACE, fg=COLOR_TEXT, activebackground=COLOR_SURFACE_HOVER, activeforeground=COLOR_TEXT,
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            command=self._on_menu_pressed
        )
        self.menu_btn.place(relx=1.0, rely=0.0, x=-16, y=16, anchor="ne", width=44, height=44)

    # ---------------------------------------------------------------
    # ADMIN screen — only reachable after a successful password login.
    # Scrollable single column: preview, add-user form, user list.
    # ---------------------------------------------------------------
    def _build_admin_screen(self):
        self.admin_screen = tk.Frame(self.main_container, bg=COLOR_BG)

        topbar = tk.Frame(self.admin_screen, bg=COLOR_BG)
        topbar.pack(fill="x", padx=20, pady=(18, 4))
        ttk.Label(topbar, text="โหมดผู้ดูแลระบบ", style="Heading.TLabel").pack(side="left")
        lock_btn = RoundedButton(topbar, text="🔒 กลับหน้าสแกน", command=self._lock_admin, style="danger", height=36, font=("Noto Sans", 9))
        lock_btn.pack(side="right")
        lock_btn.canvas.configure(width=140)

        self.admin_status_var = tk.StringVar(value="โหมด: ผู้ดูแลระบบ")
        ttk.Label(self.admin_screen, textvariable=self.admin_status_var, style="Muted.TLabel").pack(
            anchor="w", padx=20, pady=(2, 4)
        )
        tk.Frame(self.admin_screen, bg=COLOR_BORDER, height=1).pack(fill="x", padx=20, pady=(0, 12))

        # status bar lives directly on admin_screen (not inside either sub-page below)
        # so it stays visible and shows feedback no matter which sub-page is active
        status_row = tk.Frame(self.admin_screen, bg=COLOR_PANEL)
        status_row.pack(fill="x", padx=20, pady=(0, 12))
        self.status_accent = tk.Frame(status_row, bg=COLOR_MUTED, width=4)
        self.status_accent.pack(side="left", fill="y")
        self.status_var = tk.StringVar(value="พร้อม")
        self.status_label = tk.Label(
            status_row, textvariable=self.status_var, anchor="w",
            bg=COLOR_PANEL, fg=COLOR_TEXT, font=("Noto Sans", 10), padx=14, pady=10
        )
        self.status_label.pack(side="left", fill="both", expand=True)

        # body area swaps between the user list (home) and the add/edit form —
        # two separate sub-pages, entered by explicit navigation rather than one
        # long page where selecting a user silently repopulated an inline form
        self.admin_body = tk.Frame(self.admin_screen, bg=COLOR_BG)
        self.admin_body.pack(fill="both", expand=True)

        self._build_admin_list_page()
        self._build_admin_form_page()
        self.admin_list_page.place(relx=0, rely=0, relwidth=1, relheight=1)

    # ---------------------------------------------------------------
    # ADMIN LIST page — the admin "home": user list, add-new button,
    # system settings. Tapping a user navigates to the form page below.
    # ---------------------------------------------------------------
    def _build_admin_list_page(self):
        self.admin_list_page = tk.Frame(self.admin_body, bg=COLOR_BG)

        canvas = tk.Canvas(self.admin_list_page, bg=COLOR_BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(self.admin_list_page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=(0, 16))
        vscroll.pack(side="right", fill="y", pady=(0, 16))

        content = tk.Frame(canvas, bg=COLOR_BG)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(content_window, width=e.width))

        RoundedButton(
            content, text="➕  เพิ่มผู้ใช้ใหม่", command=lambda: self._show_admin_form(None),
            style="accent", panel_bg=COLOR_BG, font=("Noto Sans", 10, "bold")
        ).pack(fill="x", pady=(4, 16))

        list_frame = ttk.Labelframe(content, text="ผู้ใช้ที่ลงทะเบียนแล้ว", padding=10)
        list_frame.pack(fill="x", pady=(0, 20))

        list_container = tk.Frame(list_frame, bg=COLOR_PANEL)
        list_container.pack(fill="x")

        self.people_list = tk.Listbox(
            list_container, bg=COLOR_SURFACE, fg=COLOR_TEXT, selectbackground=COLOR_ACCENT, selectforeground="white",
            borderwidth=0, highlightthickness=0, activestyle="none", font=("Noto Sans", 10), height=8
        )
        self.people_list.pack(side="left", fill="both", expand=True)
        self.people_list.bind("<<ListboxSelect>>", self._on_person_selected)
        list_scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=self.people_list.yview)
        list_scrollbar.pack(side="right", fill="y")
        self.people_list.configure(yscrollcommand=list_scrollbar.set)

        RoundedButton(
            list_frame, text="ลบผู้ใช้ที่เลือก", command=self.delete_selected, style="danger", panel_bg=COLOR_PANEL, height=38
        ).pack(fill="x", pady=(8, 0))

        settings_frame = ttk.Labelframe(content, text="ตั้งค่าระบบ", padding=14)
        settings_frame.pack(fill="x", pady=(0, 20))
        ttk.Label(settings_frame, text="หน้าพักหน้าจอ (Standby)").pack(anchor="w", pady=(0, 8))

        toggle_row = tk.Frame(settings_frame, bg=COLOR_PANEL)
        toggle_row.pack(anchor="w")
        self.standby_on_btn = tk.Button(
            toggle_row, text="เปิด", width=8, font=("Noto Sans", 10, "bold"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            command=lambda: self._set_standby_enabled(True)
        )
        self.standby_on_btn.pack(side="left", ipady=6)
        self.standby_off_btn = tk.Button(
            toggle_row, text="ปิด", width=8, font=("Noto Sans", 10, "bold"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            command=lambda: self._set_standby_enabled(False)
        )
        self.standby_off_btn.pack(side="left", ipady=6, padx=(2, 0))
        self._refresh_standby_toggle()

        ttk.Label(settings_frame, text="เปิดประตู").pack(anchor="w", pady=(16, 8))
        self.door_open_btn = tk.Button(
            settings_frame, text="เปิดประตูตอนนี้", width=8, font=("Noto Sans", 10, "bold"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            bg=COLOR_ACCENT, fg="white", activebackground=COLOR_ACCENT_HOVER, activeforeground="white",
            command=self._open_door
        )
        self.door_open_btn.pack(anchor="w", ipady=6, ipadx=10)

        ttk.Label(settings_frame, text="เปิดประตูค้าง (ไม่ล็อกอัตโนมัติ)").pack(anchor="w", pady=(16, 8))
        door_hold_row = tk.Frame(settings_frame, bg=COLOR_PANEL)
        door_hold_row.pack(anchor="w")
        self.door_hold_on_btn = tk.Button(
            door_hold_row, text="เปิด", width=8, font=("Noto Sans", 10, "bold"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            command=lambda: self._set_door_hold(True)
        )
        self.door_hold_on_btn.pack(side="left", ipady=6)
        self.door_hold_off_btn = tk.Button(
            door_hold_row, text="ปิด", width=8, font=("Noto Sans", 10, "bold"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            command=lambda: self._set_door_hold(False)
        )
        self.door_hold_off_btn.pack(side="left", ipady=6, padx=(2, 0))
        self._refresh_door_hold_toggle()

        RoundedButton(
            settings_frame, text="🔄 รีเซ็ตและซิงค์บัตร RFID กับ Arduino", command=self._sync_all_cards_to_arduino,
            style="normal", panel_bg=COLOR_PANEL, height=38
        ).pack(fill="x", pady=(16, 0))

        telegram_frame = ttk.Labelframe(content, text="แจ้งเตือน Telegram", padding=14)
        telegram_frame.pack(fill="x", pady=(0, 20))
        ttk.Label(telegram_frame, text="ส่งข้อความ+รูปแจ้งเตือนเมื่อคนแปลกหน้ายืนค้างหน้าประตู 3 วิขึ้นไป").pack(anchor="w", pady=(0, 8))

        telegram_toggle_row = tk.Frame(telegram_frame, bg=COLOR_PANEL)
        telegram_toggle_row.pack(anchor="w")
        self.telegram_on_btn = tk.Button(
            telegram_toggle_row, text="เปิด", width=8, font=("Noto Sans", 10, "bold"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            command=lambda: self._set_telegram_enabled(True)
        )
        self.telegram_on_btn.pack(side="left", ipady=6)
        self.telegram_off_btn = tk.Button(
            telegram_toggle_row, text="ปิด", width=8, font=("Noto Sans", 10, "bold"),
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            command=lambda: self._set_telegram_enabled(False)
        )
        self.telegram_off_btn.pack(side="left", ipady=6, padx=(2, 0))
        self._refresh_telegram_toggle()

        ttk.Label(telegram_frame, text="Bot Token").pack(anchor="w", pady=(16, 4))
        telegram_token_entry = ttk.Entry(telegram_frame, textvariable=self.telegram_token_var, show="•")
        telegram_token_entry.pack(fill="x")
        telegram_token_entry.bind("<Button-1>", lambda e: self._open_keyboard(self.telegram_token_var, "Bot Token"))

        ttk.Label(telegram_frame, text="Chat ID").pack(anchor="w", pady=(10, 4))
        telegram_chatid_entry = ttk.Entry(telegram_frame, textvariable=self.telegram_chatid_var)
        telegram_chatid_entry.pack(fill="x")
        telegram_chatid_entry.bind("<Button-1>", lambda e: self._open_keyboard(self.telegram_chatid_var, "Chat ID"))

        telegram_btn_row = tk.Frame(telegram_frame, bg=COLOR_PANEL)
        telegram_btn_row.pack(fill="x", pady=(10, 0))
        RoundedButton(
            telegram_btn_row, text="บันทึก", command=self._save_telegram_settings,
            style="accent", panel_bg=COLOR_PANEL, height=38
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        RoundedButton(
            telegram_btn_row, text="ทดสอบส่งข้อความ", command=self._test_telegram,
            style="normal", panel_bg=COLOR_PANEL, height=38
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        self._refresh_people_list()

    # ---------------------------------------------------------------
    # ADMIN FORM page — add a new user OR edit an existing one (same
    # fields either way); which mode depends on how you navigated here.
    # ---------------------------------------------------------------
    def _build_admin_form_page(self):
        self.admin_form_page = tk.Frame(self.admin_body, bg=COLOR_BG)

        back_row = tk.Frame(self.admin_form_page, bg=COLOR_BG)
        back_row.pack(fill="x", padx=16, pady=(10, 0))
        back_btn = RoundedButton(back_row, text="← กลับ", command=self._show_admin_list, style="normal", height=34, font=("Noto Sans", 9), panel_bg=COLOR_BG)
        back_btn.pack(side="left")
        back_btn.canvas.configure(width=90)

        canvas = tk.Canvas(self.admin_form_page, bg=COLOR_BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(self.admin_form_page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=(8, 16))
        vscroll.pack(side="right", fill="y", pady=(8, 16))

        content = tk.Frame(canvas, bg=COLOR_BG)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(content_window, width=e.width))

        self.user_form_frame = ttk.Labelframe(content, text="เพิ่มผู้ใช้ใหม่", padding=14)
        form = self.user_form_frame
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

        arduino_row = ttk.Frame(form)
        arduino_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Label(arduino_row, text="บัตร RFID").pack(side="left")
        self.arduino_status_var = tk.StringVar(value="Arduino: กำลังเชื่อมต่อ...")
        self.arduino_status_label = tk.Label(
            arduino_row, textvariable=self.arduino_status_var,
            bg=COLOR_PANEL, fg=COLOR_MUTED, font=("Noto Sans", 9)
        )
        self.arduino_status_label.pack(side="right")

        self.rfid_rows_frame = tk.Frame(form, bg=COLOR_PANEL)
        self.rfid_rows_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        self.rfid_register_btn = RoundedButton(
            form, text="🔖 ลงทะเบียนบัตร RFID (แตะบัตรที่เครื่องอ่าน)", command=self._start_rfid_registration,
            style="normal", panel_bg=COLOR_PANEL
        )
        self.rfid_register_btn.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        self.capture_btn = RoundedButton(
            form, text=f"📷  ถ่ายรูปใบหน้า ({FACES_PER_PERSON} รูป)", command=self.start_capture,
            style="normal", panel_bg=COLOR_PANEL
        )
        self.capture_btn.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 4))

        self.progress = ttk.Progressbar(
            form, style="Capture.Horizontal.TProgressbar", maximum=FACES_PER_PERSON, value=0
        )
        self.progress.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(2, 10))

        RoundedButton(
            form, text="บันทึกผู้ใช้", command=self.save_person, style="accent", panel_bg=COLOR_PANEL, font=("Noto Sans", 10, "bold")
        ).grid(row=9, column=0, columnspan=2, sticky="ew")

    def _show_scan_screen(self):
        self.admin_screen.place_forget()
        self.scan_screen.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.scan_screen.lift()
        self.current_screen = "scan"

    def _show_admin_screen(self):
        self.scan_screen.place_forget()
        self.admin_screen.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.admin_screen.lift()

    def _show_admin_list(self):
        self.admin_form_page.place_forget()
        self.admin_list_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.admin_list_page.lift()
        self.current_screen = "admin_list"
        self._refresh_people_list()

    def _show_admin_form(self, editing_name):
        self._editing_original_name = editing_name
        self.pending_rfids = []
        self.arduino_confirmed_total = None
        self.registering_rfid = False
        self.capturing = False
        self.captured_count = 0  # tracks whether *this* visit captured new photos, see save_person
        self.progress.configure(value=0)
        self.capture_btn.configure(state="normal")

        if editing_name:
            info = self.db[editing_name]
            self.name_var.set(editing_name)
            self.user_id_var.set(info.get("user_id", ""))
            self.role_var.set(info.get("role", ROLE_NORMAL))
            rfid_raw = info.get("rfid") or []
            self.pending_rfids = [rfid_raw] if isinstance(rfid_raw, str) else list(rfid_raw)
            self.user_form_frame.configure(text=f"แก้ไขผู้ใช้: {editing_name}")
            self._set_status(f"กำลังแก้ไขผู้ใช้ '{editing_name}' — แก้ข้อมูลแล้วกด 'บันทึกผู้ใช้' เพื่ออัปเดต", "normal")
        else:
            self.name_var.set("")
            self.user_id_var.set(next_user_id(self.db))
            self.role_var.set(ROLE_NORMAL)
            self.user_form_frame.configure(text="เพิ่มผู้ใช้ใหม่")
            self._set_status("กรอกข้อมูลผู้ใช้ใหม่ ถ่ายรูป และลงทะเบียนบัตรได้เลย", "normal")

        self._refresh_pending_rfid_label()
        self.admin_list_page.place_forget()
        self.admin_form_page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.admin_form_page.lift()
        self.current_screen = "admin_form"

    def _build_standby_screen(self):
        self.standby_frame = tk.Frame(self.main_container, bg=COLOR_BG)

        center = tk.Frame(self.standby_frame, bg=COLOR_BG)
        center.place(relx=0.5, rely=0.5, anchor="center")

        self.clock_var = tk.StringVar(value="--:--:--")
        self.date_var = tk.StringVar(value="")

        tk.Label(center, textvariable=self.clock_var, font=("Noto Sans", 64, "bold"), bg=COLOR_BG, fg=COLOR_TEXT).pack()
        tk.Label(center, textvariable=self.date_var, font=("Noto Sans", 15), bg=COLOR_BG, fg=COLOR_MUTED).pack(pady=(4, 44))
        hint_pill = tk.Frame(center, bg=COLOR_ACCENT_SOFT)
        hint_pill.pack()
        tk.Label(
            hint_pill, text="แตะหน้าจอเพื่อสแกนใบหน้า", font=("Noto Sans", 13), bg=COLOR_ACCENT_SOFT, fg=COLOR_ACCENT,
            padx=22, pady=10
        ).pack()

        # tapping anywhere on the standby screen wakes it — bind the frame
        # and every child/grandchild widget so a tap on the labels or the
        # hint pill also counts
        widgets_to_bind = [self.standby_frame, center, *center.winfo_children()]
        widgets_to_bind += hint_pill.winfo_children()
        for widget in widgets_to_bind:
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

    def _start_rfid_registration(self):
        if not self.arduino.connected:
            messagebox.showerror("ผิดพลาด", "ยังไม่ได้เชื่อมต่อ Arduino")
            return
        if self.syncing_cards:
            messagebox.showerror("ผิดพลาด", "กำลังซิงค์บัตรทั้งหมดไป Arduino อยู่ รอให้เสร็จก่อน")
            return
        self.pending_rfids = []
        self.arduino_confirmed_total = None
        self.registering_rfid = True
        self._refresh_pending_rfid_label()
        self.arduino.send("REGISTER")
        self._set_status("โหมดลงทะเบียนบัตร — แตะบัตรที่เครื่องอ่าน RFID (แตะได้หลายใบ)", "normal")

    def _find_rfid_owner(self, uid, exclude_name=None):
        for name, info in self.db.items():
            if name == exclude_name:
                continue
            owned = info.get("rfid") or []
            if isinstance(owned, str):
                owned = [owned]
            if uid in owned:
                return name
        return None

    def _refresh_pending_rfid_label(self):
        for widget in self.rfid_rows_frame.winfo_children():
            widget.destroy()

        if not self.pending_rfids:
            tk.Label(
                self.rfid_rows_frame, text="ยังไม่มีบัตรที่ลงทะเบียน",
                bg=COLOR_PANEL, fg=COLOR_MUTED, font=("Noto Sans", 9)
            ).pack(anchor="w")
            return

        for uid in self.pending_rfids:
            row = tk.Frame(self.rfid_rows_frame, bg=COLOR_SURFACE)
            row.pack(fill="x", pady=2)
            tk.Label(
                row, text=uid, bg=COLOR_SURFACE, fg=COLOR_TEXT, font=("Noto Sans", 9),
                anchor="w", padx=10, pady=6
            ).pack(side="left", fill="x", expand=True)
            tk.Button(
                row, text="✕", bg=COLOR_ERR_SOFT, fg=COLOR_ERR, relief="flat", bd=0,
                font=("Noto Sans", 9, "bold"), width=3, cursor="hand2",
                command=lambda u=uid: self._remove_pending_rfid(u)
            ).pack(side="right", padx=4, pady=2)

        if self.arduino_confirmed_total is not None:
            tk.Label(
                self.rfid_rows_frame, text=f"ยืนยันจาก Arduino: มีบัตรอยู่ในระบบทั้งหมด {self.arduino_confirmed_total} ใบ",
                bg=COLOR_PANEL, fg=COLOR_MUTED, font=("Noto Sans", 8)
            ).pack(anchor="w", pady=(4, 0))

    def _remove_pending_rfid(self, uid):
        if uid in self.pending_rfids:
            self.pending_rfids.remove(uid)
            self._refresh_pending_rfid_label()
            self._set_status(f"เอาบัตร {uid} ออกจากรายการแล้ว — กด 'บันทึกผู้ใช้' เพื่อยืนยัน", "warn")

    def _poll_arduino(self):
        if self.arduino.connected:
            self.arduino_status_var.set("Arduino: เชื่อมต่ออยู่")
            self.arduino_status_label.configure(fg=COLOR_OK)
        else:
            self.arduino_status_var.set("Arduino: ไม่ได้เชื่อมต่อ (กำลังลองเชื่อมต่อใหม่...)")
            self.arduino_status_label.configure(fg=COLOR_ERR)
            if self.registering_rfid:
                self.registering_rfid = False
                self._set_status("การเชื่อมต่อ Arduino หลุดระหว่างลงทะเบียนบัตร — เชื่อมต่อใหม่แล้วลองอีกครั้ง", "err")

        for line in self.arduino.poll_lines():
            print(f"[arduino] {line}", flush=True)
            parts = line.split(":")

            # handled regardless of registering_rfid — a card removal (from
            # deleting a user) can come back at any time, not just mid-registration
            if parts[0] == "REMOVED" and len(parts) == 3:
                uid, total = parts[1], parts[2]
                self._set_status(f"Arduino ยืนยันลบบัตร {uid} แล้ว (เหลือในระบบ {total} ใบ)", "ok")
                continue
            if parts[0] == "NOTFOUND":
                self._set_status(f"ไม่พบบัตร {parts[1] if len(parts) > 1 else ''} ใน Arduino (อาจลบไปแล้ว)", "err")
                continue

            # bulk "reset & sync" (see _sync_all_cards_to_arduino): CLEAR wipes
            # the Arduino's EEPROM first so no unowned/orphaned card can
            # survive, then every owned card from people.json is pushed back
            if self.syncing_cards and self._awaiting_clear and parts[0] == "CLEARED":
                self._awaiting_clear = False
                self._set_status(f"ล้างบัตรเดิมทั้งหมดแล้ว — กำลังเขียนบัตรใหม่... (0/{self._sync_total})", "warn")
                self._send_next_sync_card()
                continue

            # bulk "sync all cards to Arduino" (see _sync_all_cards_to_arduino) —
            # kept entirely separate from the interactive single-user
            # registration flow below so it never touches pending_rfids for
            # whichever user's form happens to be open
            if self.syncing_cards and parts[0] in ("REGISTERED", "DUPLICATE", "FULL"):
                self._sync_progress += 1
                if parts[0] == "FULL":
                    self._set_status(f"หน่วยความจำ Arduino เต็มระหว่างซิงค์ ({self._sync_progress}/{self._sync_total}) — หยุดซิงค์", "err")
                    self._sync_queue = []
                self._send_next_sync_card()
                continue

            if not self.registering_rfid:
                continue
            if parts[0] == "REGISTERED" and len(parts) == 3:
                uid, total = parts[1], parts[2]
                if uid not in self.pending_rfids:
                    self.pending_rfids.append(uid)
                self.arduino_confirmed_total = total
                self._refresh_pending_rfid_label()
                self._set_status(
                    f"Arduino ยืนยันบันทึกบัตร {uid} แล้ว (รวมทั้งหมดในระบบตอนนี้ {total} ใบ) — แตะใบต่อไป หรือกด 'บันทึกผู้ใช้' เมื่อเสร็จ",
                    "ok",
                )
            elif parts[0] == "DUPLICATE" and len(parts) == 3:
                # Arduino already has this UID in its EEPROM (e.g. registered
                # before this feature existed, or before a Pi crash). If no
                # Pi user owns it yet, link it to whoever we're editing now
                # instead of just refusing — that's how an "orphaned" card
                # gets reconnected to a name.
                uid, total = parts[1], parts[2]
                self.arduino_confirmed_total = total
                owner = self._find_rfid_owner(uid, exclude_name=self.name_var.get().strip())
                if owner:
                    self._set_status(f"บัตร {uid} ผูกกับผู้ใช้ '{owner}' อยู่แล้ว ใช้ซ้ำกับคนอื่นไม่ได้", "err")
                else:
                    if uid not in self.pending_rfids:
                        self.pending_rfids.append(uid)
                    self._refresh_pending_rfid_label()
                    self._set_status(
                        f"บัตร {uid} มีอยู่ในระบบ Arduino แล้วแต่ยังไม่ผูกชื่อ — เชื่อมกับผู้ใช้นี้แล้ว (รวมทั้งหมด {total} ใบ)",
                        "ok",
                    )
            elif parts[0] == "FULL":
                self._set_status("หน่วยความจำ Arduino เต็ม ลงทะเบียนบัตรเพิ่มไม่ได้แล้ว", "err")
        self.root.after(200, self._poll_arduino)

    def _sync_all_cards_to_arduino(self):
        """Wipe the Arduino's EEPROM (CLEAR) and rewrite it from scratch with
        exactly the cards on file in people.json — guarantees every card left
        on the Arduino has a real owner (any orphaned/unknown UID is dropped
        by the wipe) and also covers the old recovery case (Arduino replaced
        / EEPROM already blank, where CLEAR is just a no-op). Independent of
        MODE_IDLE/MODE_REGISTER on the Arduino (ADD writes directly, no mode
        switch needed) and kept separate from the interactive registration
        flow on the Pi side (see the syncing_cards branch in _poll_arduino)."""
        if not self.arduino.connected:
            messagebox.showerror("ผิดพลาด", "ยังไม่ได้เชื่อมต่อ Arduino")
            return
        if self.registering_rfid:
            messagebox.showerror("ผิดพลาด", "กำลังลงทะเบียนบัตรของผู้ใช้อยู่ รอให้เสร็จก่อน")
            return

        uids = []
        for info in self.db.values():
            rfid_raw = info.get("rfid") or []
            uids.extend([rfid_raw] if isinstance(rfid_raw, str) else rfid_raw)

        if not messagebox.askyesno(
            "ยืนยันการรีเซ็ตและซิงค์",
            "จะลบบัตรทั้งหมดที่มีอยู่ใน Arduino ตอนนี้ก่อน (รวมบัตรที่ไม่มีเจ้าของใน people.json) "
            f"แล้วเขียนบัตรที่มีเจ้าของกลับเข้าไปใหม่ทั้งหมด ({len(uids)} ใบ) — บัตรที่ไม่มีเจ้าของจะใช้เปิดประตูไม่ได้อีก "
            "ดำเนินการต่อ?",
        ):
            return

        self._sync_queue = uids
        self._sync_total = len(uids)
        self._sync_progress = 0
        self.syncing_cards = True
        self._awaiting_clear = True
        self._set_status("กำลังล้างบัตรเดิมทั้งหมดใน Arduino...", "warn")
        self.arduino.send("CLEAR")

    def _send_next_sync_card(self):
        if not self._sync_queue:
            self.syncing_cards = False
            self._set_status(f"รีเซ็ตและซิงค์บัตรเสร็จแล้ว ({self._sync_progress}/{self._sync_total} ใบ มีเจ้าของครบ)", "ok")
            return
        uid = self._sync_queue.pop(0)
        self._set_status(f"กำลังซิงค์บัตรไป Arduino... ({self._sync_progress}/{self._sync_total})", "warn")
        self.arduino.send(f"ADD:{uid}")

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

        cancel_btn = RoundedButton(btn_row, text="ยกเลิก", command=dialog.destroy, style="normal", panel_bg=COLOR_PANEL, height=38)
        cancel_btn.pack(side="left", padx=6)
        cancel_btn.canvas.configure(width=110)
        login_btn = RoundedButton(btn_row, text="เข้าสู่ระบบ", command=try_login, style="accent", panel_bg=COLOR_PANEL, height=38, font=("Noto Sans", 10, "bold"))
        login_btn.pack(side="left", padx=6)
        login_btn.canvas.configure(width=110)

    def _unlock_admin(self):
        self.admin_authenticated = True
        self.admin_status_var.set("โหมด: ผู้ดูแลระบบ (ปลดล็อกแล้ว)")
        # while any admin screen is open, card-based unlock is disabled on the
        # Arduino (EXIT button still always works) — see ADMIN_LOCK in the .ino
        self.arduino.send("ADMIN_LOCK")
        self._show_admin_screen()
        self._show_admin_list()
        self._set_status("เข้าสู่เมนูผู้ดูแลระบบแล้ว", "ok")

    def _lock_admin(self):
        self.admin_authenticated = False
        self.registering_rfid = False
        self.arduino.send("IDLE")
        self.arduino.send("ADMIN_UNLOCK")
        self._show_scan_screen()
        self._arm_idle_timer()

    def _set_standby_enabled(self, enabled):
        self.settings["standby_enabled"] = enabled
        save_settings(self.settings)
        self._refresh_standby_toggle()
        self._set_status("เปิดใช้งานหน้าพักหน้าจอแล้ว" if enabled else "ปิดหน้าพักหน้าจอแล้ว", "ok")

    def _refresh_standby_toggle(self):
        enabled = self.settings.get("standby_enabled", True)
        on_style = dict(bg=COLOR_ACCENT, fg="white", activebackground=COLOR_ACCENT_HOVER, activeforeground="white")
        off_style = dict(bg=COLOR_SURFACE, fg=COLOR_MUTED, activebackground=COLOR_SURFACE_HOVER, activeforeground=COLOR_MUTED)
        self.standby_on_btn.configure(**(on_style if enabled else off_style))
        self.standby_off_btn.configure(**(off_style if enabled else on_style))

    def _open_door(self):
        """Momentary open — same relay pulse as an authorized card tap or
        the physical EXIT button, just triggered from the admin UI. Sent
        regardless of ADMIN_LOCK: that flag only blocks *card* unlocks, not
        an explicit admin action."""
        if not self.arduino.connected:
            messagebox.showerror("ผิดพลาด", "ยังไม่ได้เชื่อมต่อ Arduino")
            return
        self.arduino.send("OPEN")
        self._set_status("สั่งเปิดประตูแล้ว", "ok")

    def _set_door_hold(self, hold):
        """Hold the door unlocked indefinitely (HOLD_ON) or release it back
        to normal locked operation (HOLD_OFF) — same เปิด/ปิด segmented-
        toggle pattern as the standby setting. State is tracked locally on
        the Pi (door_held_open) since the Arduino has no "query current
        state" command — if app.py restarts while a hold is still active on
        the Arduino, this toggle's shown state can drift until pressed again."""
        if not self.arduino.connected:
            messagebox.showerror("ผิดพลาด", "ยังไม่ได้เชื่อมต่อ Arduino")
            return
        if hold == self.door_held_open:
            return
        if hold:
            if not messagebox.askyesno(
                "ยืนยันเปิดประตูค้าง",
                "จะเปิดประตูค้างไว้จนกว่าจะกดปิดอีกครั้ง (ไม่ล็อกอัตโนมัติ) ดำเนินการต่อ?",
            ):
                return
            self.arduino.send("HOLD_ON")
            self.door_held_open = True
            self._set_status("เปิดประตูค้างไว้แล้ว", "warn")
        else:
            self.arduino.send("HOLD_OFF")
            self.door_held_open = False
            self._set_status("ปล่อยประตูค้าง — กลับสู่การล็อกปกติแล้ว", "ok")
        self._refresh_door_hold_toggle()

    def _refresh_door_hold_toggle(self):
        on_style = dict(bg=COLOR_ERR, fg="white", activebackground="#b8302a", activeforeground="white")
        off_style = dict(bg=COLOR_SURFACE, fg=COLOR_MUTED, activebackground=COLOR_SURFACE_HOVER, activeforeground=COLOR_MUTED)
        self.door_hold_on_btn.configure(**(on_style if self.door_held_open else off_style))
        self.door_hold_off_btn.configure(**(off_style if self.door_held_open else on_style))

    def _refresh_telegram_toggle(self):
        enabled = self.settings.get("telegram_enabled", False)
        on_style = dict(bg=COLOR_ACCENT, fg="white", activebackground=COLOR_ACCENT_HOVER, activeforeground="white")
        off_style = dict(bg=COLOR_SURFACE, fg=COLOR_MUTED, activebackground=COLOR_SURFACE_HOVER, activeforeground=COLOR_MUTED)
        self.telegram_on_btn.configure(**(on_style if enabled else off_style))
        self.telegram_off_btn.configure(**(off_style if enabled else on_style))

    def _set_telegram_enabled(self, enabled):
        self.settings["telegram_enabled"] = enabled
        save_settings(self.settings)
        self._refresh_telegram_toggle()
        self._set_status("เปิดใช้งานแจ้งเตือน Telegram แล้ว" if enabled else "ปิดแจ้งเตือน Telegram แล้ว", "ok")

    def _save_telegram_settings(self):
        self.settings["telegram_bot_token"] = self.telegram_token_var.get().strip()
        self.settings["telegram_chat_id"] = self.telegram_chatid_var.get().strip()
        save_settings(self.settings)
        self._set_status("บันทึกการตั้งค่า Telegram แล้ว", "ok")

    def _test_telegram(self):
        bot_token = self.telegram_token_var.get().strip()
        chat_id = self.telegram_chatid_var.get().strip()
        if not bot_token or not chat_id:
            messagebox.showerror("ผิดพลาด", "กรอก Bot Token และ Chat ID ก่อนทดสอบ")
            return
        self._set_status("กำลังส่งข้อความทดสอบ...", "normal")
        threading.Thread(
            target=self._send_telegram_bg, args=(bot_token, chat_id, "🔔 ทดสอบการแจ้งเตือนจากระบบ Door Access"),
            daemon=True
        ).start()

    def _send_telegram_bg(self, bot_token, chat_id, text):
        ok, detail = send_telegram_message(bot_token, chat_id, text)
        if ok:
            print(f"[telegram] sent: {text!r}", flush=True)
            self.root.after(0, lambda: self._set_status("ส่งข้อความ Telegram สำเร็จ", "ok"))
        else:
            print(f"[telegram] failed: {detail}", flush=True)
            self.root.after(0, lambda: self._set_status(f"ส่งข้อความ Telegram ไม่สำเร็จ: {detail}", "err"))

    def _notify_telegram_photo_async(self, frame, caption):
        """Same enabled/token/chat_id gate as _notify_telegram_async, plus
        the JPEG encode — both cheap, so done on the caller's thread (the
        Tkinter mainloop, via _update_preview); only the network call runs
        in the background thread."""
        if not self.settings.get("telegram_enabled", False):
            return
        bot_token = self.settings.get("telegram_bot_token", "")
        chat_id = self.settings.get("telegram_chat_id", "")
        if not bot_token or not chat_id:
            return
        ok, jpg = cv2.imencode(".jpg", frame)
        if not ok:
            return
        threading.Thread(
            target=self._send_telegram_photo_bg, args=(bot_token, chat_id, jpg.tobytes(), caption),
            daemon=True
        ).start()

    def _send_telegram_photo_bg(self, bot_token, chat_id, image_bytes, caption):
        ok, detail = send_telegram_photo(bot_token, chat_id, image_bytes, caption)
        if ok:
            print(f"[telegram] sent photo: {caption!r}", flush=True)
            self.root.after(0, lambda: self._set_status("ส่งรูป Telegram สำเร็จ", "ok"))
        else:
            print(f"[telegram] photo failed: {detail}", flush=True)
            self.root.after(0, lambda: self._set_status(f"ส่งรูป Telegram ไม่สำเร็จ: {detail}", "err"))

    def _load_gallery(self):
        self.gallery = load_embeddings()
        print(f"[gallery] loaded, {len(self.gallery)} people: {list(self.gallery.keys())}", flush=True)

    def _set_status(self, text, kind="normal"):
        # only two status colors: green for success, red for failure —
        # anything else (in-progress, informational) stays plain text
        colors = {"normal": COLOR_TEXT, "ok": COLOR_OK, "warn": COLOR_TEXT, "err": COLOR_ERR}
        accents = {"normal": COLOR_MUTED, "ok": COLOR_OK, "warn": COLOR_WARN, "err": COLOR_ERR}
        self.status_var.set(text)
        self.status_label.configure(fg=colors.get(kind, COLOR_TEXT))
        self.status_accent.configure(bg=accents.get(kind, COLOR_MUTED))

    def _refresh_people_list(self):
        self.people_list.delete(0, tk.END)
        self._person_order = list(self.db.keys())
        for name in self._person_order:
            info = self.db[name]
            rfid_raw = info.get("rfid")
            if isinstance(rfid_raw, list):
                rfid = ", ".join(rfid_raw) if rfid_raw else "-"
            else:
                rfid = rfid_raw or "-"
            uid = info.get("user_id", "----")
            role = info.get("role", ROLE_NORMAL)
            row_idx = self.people_list.size()
            self.people_list.insert(
                tk.END,
                f"  [{uid}] {name}  ({role})   •   RFID: {rfid}   •   รูป: {info.get('face_count', 0)}"
            )
            row_bg = COLOR_SURFACE if row_idx % 2 == 0 else COLOR_SURFACE_HOVER
            self.people_list.itemconfig(row_idx, bg=row_bg)

    def _on_person_selected(self, event):
        sel = self.people_list.curselection()
        if not sel:
            return
        name = self._person_order[sel[0]]
        self._show_admin_form(name)

    def _update_preview(self):
        if self.picam2 is not None:
            frame = self.frame_grabber.get_frame()  # BGR order (Picamera2's "RGB888" format)
            if frame is None:
                self.root.after(150, self._update_preview)
                return
            try:
                if ROTATE is not None:
                    frame = cv2.rotate(frame, ROTATE)
                frame_h, frame_w = frame.shape[:2]
                faces = self.detector.detect(frame)

                if self.capturing and len(faces) > 0:
                    now = time.time()
                    if now - self._last_capture_time >= 0.3:
                        self._last_capture_time = now
                        x, y, w, h, _score = faces[0]
                        x0, y0 = max(0, x), max(0, y)
                        x1, y1 = min(frame_w, x + w), min(frame_h, y + h)
                        face = cv2.resize(frame[y0:y1, x0:x1], (200, 200))
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

                frame_has_stranger = False
                for (x, y, w, h, _score) in faces:
                    label_text = None
                    if self.gallery and not self.capturing:
                        x0, y0 = max(0, x), max(0, y)
                        x1, y1 = min(frame_w, x + w), min(frame_h, y + h)
                        face = frame[y0:y1, x0:x1]
                        if face.size > 0:
                            embedding = self.identifier.embed(face)
                            name, _sim = self.identifier.best_match(embedding, self.gallery)
                            label_text = name if name is not None else "stranger"

                    # green = recognized (access granted), red = stranger / not
                    # recognized yet (access denied)
                    recognized = label_text is not None and label_text != "stranger"
                    box_color = (107, 156, 24) if recognized else (48, 59, 214)  # BGR of COLOR_OK / COLOR_ERR

                    # auto-unlock on a recognized face — only from the actual scan
                    # screen (never while any admin screen is open, so this can't
                    # fire during registration/editing regardless of doorLockedByPi)
                    # and rate-limited so a person standing in frame doesn't spam
                    # the relay with an OPEN every ~150ms tick
                    if recognized and self.current_screen == "scan":
                        now = time.time()
                        if now - self._last_face_unlock_time >= FACE_UNLOCK_COOLDOWN_S:
                            self._last_face_unlock_time = now
                            print(f"[access] recognized {label_text} — opening door", flush=True)
                            self.arduino.send("OPEN")

                    if label_text == "stranger":
                        frame_has_stranger = True

                    cv2.rectangle(frame, (x, y), (x + w, y + h), box_color, 2)
                    if label_text:
                        cv2.rectangle(frame, (x, y - 28), (x + w, y), box_color, -1)
                        cv2.putText(
                            frame, label_text, (x + 6, y - 7),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA
                        )

                # alert on an unrecognized face that keeps lingering at the
                # door — only once it's been continuously present for
                # STRANGER_LINGER_S (so someone just passing through frame
                # doesn't trigger it), then repeats at most every
                # STRANGER_NOTIFY_COOLDOWN_S while they keep standing there.
                # The "since" timer resets the moment no stranger is in
                # frame, so a fresh linger period is required each time.
                if self.current_screen == "scan":
                    now = time.time()
                    if frame_has_stranger:
                        if self._stranger_since is None:
                            self._stranger_since = now
                        elif (now - self._stranger_since >= STRANGER_LINGER_S
                                and now - self._last_stranger_notify_time >= STRANGER_NOTIFY_COOLDOWN_S):
                            self._last_stranger_notify_time = now
                            timestamp = datetime.now().strftime("%H:%M:%S %d/%m/%Y")
                            self._notify_telegram_photo_async(frame, f"🚨 พบคนแปลกหน้ายืนอยู่หน้าประตู — {timestamp}")
                    else:
                        self._stranger_since = None

                # only the scan screen shows a live camera preview — admin
                # screens (list or form) have none, capture still works via
                # progress bar / status text alone, just without a live view
                target = self.video_label if self.current_screen == "scan" else None

                if target is not None:
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

        existing = self.db.get(self._editing_original_name) if self._editing_original_name else None
        registered_at = existing["registered_at"] if existing else datetime.now().isoformat(timespec="seconds")

        # a card removed from the form (✕ button) while editing must also be
        # removed from the Arduino's EEPROM, not just dropped from people.json
        if existing:
            old_rfid_raw = existing.get("rfid") or []
            old_rfids = [old_rfid_raw] if isinstance(old_rfid_raw, str) else list(old_rfid_raw)
            for uid in old_rfids:
                if uid not in self.pending_rfids:
                    self.arduino.send(f"REMOVE:{uid}")

        self.db[name] = {
            "user_id": user_id,
            "role": self.role_var.get(),
            "rfid": list(self.pending_rfids),
            "face_count": face_count,
            "registered_at": registered_at,
        }
        renamed = self._editing_original_name and self._editing_original_name != name
        if renamed:
            self.db.pop(self._editing_original_name, None)  # renamed — drop the old key
            self.gallery.pop(self._editing_original_name, None)  # drop stale embeddings under the old name
        self._editing_original_name = None

        save_db(self.db)

        # push every card currently assigned to this person to the Arduino's
        # EEPROM — harmless/idempotent for cards already there from the tap
        # flow (Arduino just replies DUPLICATE), but guarantees the EEPROM
        # matches people.json for this person without a separate manual sync
        for uid in self.pending_rfids:
            self.arduino.send(f"ADD:{uid}")

        self.pending_rfids = []
        self.arduino_confirmed_total = None
        self.registering_rfid = False
        self.arduino.send("IDLE")

        # only recompute embeddings for THIS person, and only if this visit
        # actually captured new photos — re-training on every save (even a
        # plain name/role edit) recomputed every other registered person's
        # embeddings too for nothing
        if self.captured_count > 0:
            self._set_status(f"บันทึก {name} แล้ว — กำลังอัปเดตข้อมูลใบหน้า...", "warn")
            self._update_person_embeddings(name)
            self._set_status(f"บันทึก {name} แล้ว — อัปเดตข้อมูลใบหน้าเรียบร้อย", "ok")
        else:
            self._set_status(f"บันทึก {name} แล้ว", "ok")

        self._show_admin_list()

    def _update_person_embeddings(self, name):
        """Recompute embeddings for just this one person from their dataset
        photos and update only their entry in embeddings.json — see the
        captured_count check in save_person for why this replaced always
        calling retrain() (a full rebuild of every registered person)."""
        out_dir = os.path.join(DATASET_DIR, name)
        if not os.path.isdir(out_dir):
            return
        embeddings = []
        for fname in sorted(os.listdir(out_dir)):
            img = cv2.imread(os.path.join(out_dir, fname))
            if img is None:
                continue
            embeddings.append(self.identifier.embed(img))
        if embeddings:
            self.gallery[name] = embeddings
            save_embeddings(self.gallery)

    def delete_selected(self):
        sel = self.people_list.curselection()
        if not sel:
            messagebox.showwarning("แจ้งเตือน", "กรุณาเลือกผู้ใช้ที่ต้องการลบจากรายการ")
            return
        name = self._person_order[sel[0]]
        if not messagebox.askyesno("ยืนยันการลบ", f"ลบผู้ใช้ '{name}' ออกจากระบบ?"):
            return

        rfid_raw = self.db[name].get("rfid") or []
        uids = [rfid_raw] if isinstance(rfid_raw, str) else list(rfid_raw)
        for uid in uids:
            self.arduino.send(f"REMOVE:{uid}")  # keep the Arduino's own access list in sync

        self.db.pop(name, None)
        save_db(self.db)
        self._refresh_people_list()
        if uids:
            self._set_status(f"ลบ {name} แล้ว — สั่งลบบัตร {len(uids)} ใบออกจาก Arduino ด้วย", "warn")
        else:
            self._set_status(f"ลบ {name} แล้ว", "warn")

    def on_close(self):
        if self.frame_grabber is not None:
            self.frame_grabber.stop()
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
