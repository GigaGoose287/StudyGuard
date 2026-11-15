#!/usr/bin/env python3
"""
StudyGuard - a desktop app that keeps you on task.

Start a study session and StudyGuard watches the title of the window you're
using. If you spend too long on a distracting site/app it pops a reminder on
top of everything telling you to get back to studying.

Features
  - Focus timer ring with 5-minute breaks (up to 4 per rolling hour)
  - Strict mode: anything that is not a study app counts as a distraction
  - Session checklist: small steps, with a "Now:" prompt on the Focus tab
  - Distraction parking lot: multiple tabs, export to .txt
  - Exam countdown, daily study goal and streak
  - Compact window during sessions, optional always-on-top
  - Wellness reminders: water, movement and meals
  - Calm dark and light themes
  - Habit tracker: time per app/website (focused, distracted, outside sessions,
    idle), saved as a readable report that is rewritten each hour/day/week in
    ~/.studyguard/usage/ (current_report.md, previous_report.md)

Run:   python studyguard.py        (Python 3.8+, no extra packages)

Platform notes
  Windows : works out of the box.
  macOS   : first run, allow Terminal/Python under System Settings >
            Privacy & Security > Accessibility (needed to read window titles).
  Linux   : needs xdotool   (sudo apt install xdotool). X11 only.
"""

import datetime as dt
import json
import math
import os
import platform
import re
import sys
import subprocess
import time
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog

APP_DIR = os.path.join(os.path.expanduser("~"), ".studyguard")
CFG_PATH = os.path.join(APP_DIR, "settings.json")
PARK_PATH = os.path.join(APP_DIR, "parking.json")
PROG_PATH = os.path.join(APP_DIR, "progress.json")

DEFAULTS = {
    "goal": "Study",
    "minutes": 25,
    "grace_seconds": 10,
    "nag_cooldown_seconds": 60,
    "distractions": [
        "youtube", "netflix", "twitch", "tiktok", "instagram", "facebook",
        "twitter", "x.com", "reddit", "snapchat", "discord", "steam",
        "disney+", "9gag", "spotify",
    ],
    "allowed": ["lecture", "tutorial", "course", "khan academy", "study", "notes"],
    "strict": False,
    "strict_apps": [
        "studyguard", "anki", "pdf", "word", "powerpoint", "onenote",
        "notion", "obsidian", "zotero", "kindle", "acrobat", "preview", "pages",
        "keynote", "canvas", "moodle", "blackboard", "zoom", "teams", "uptodate",
        "amboss", "osmosis", "pubmed", "quizlet", "google docs", "google slides",
        "break",
    ],
    "theme": "ink",               # "ink" (dark) or "paper" (light)
    "support_off": False,         # True = never show the support reminder again
    "support_next": "",           # date the next gentle support reminder is due
    "daily_goal_minutes": 240,
    "exams": [],
    "checklist": [],
    "always_on_top": False,
    "compact_on_start": True,
    "track_usage": True,          # habit tracker: log which apps/sites you use
    "track_outside": True,        # ...also when no study session is running
    "hide_titles": False,         # only record app/site names, not page titles
    "usage_period": "day",        # "hour", "day" or "week"
    "usage_day_start": 4,         # a "day" runs 4am to 4am, so late nights count as one day
    "water_on": True, "water_min": 30,
    "move_on": True, "move_min": 60,
    "meal_on": True, "meal_min": 180,
}

BREAK_SECONDS = 5 * 60
MAX_BREAKS_PER_HOUR = 4
DONATE_URL = "https://ko-fi.com/gigagoose79218"
SUPPORT_EVERY_DAYS = 7
SUPPORT_MESSAGE = ("Hi there! I am delighted to see that you enjoy StudyGuard! "
                   "If you can afford it, do consider supporting me!")
COMPACT_SIZE = (380, 540)
FULL_SIZE = (1040, 700)
FULL_MIN = (980, 660)
RING_SIZE = 228

# (key, title, message, default minutes)
WELLNESS = [
    ("water", "Drink some water", "Take a few sips. Staying hydrated helps you concentrate.", 30),
    ("move", "Get up and move", "Stand up, stretch and walk around for a minute or two.", 60),
    ("meal", "Time to eat", "Have a snack or a proper meal. Your brain needs fuel.", 180),
]

CHECK_PLACEHOLDER = "Add a small step, then press Enter"
PARK_PLACEHOLDER = "Park a distracting thought, then press Enter"

# ---- Habit tracker ----
USAGE_DIR = os.path.join(APP_DIR, "usage")
CUR_JSON = os.path.join(USAGE_DIR, "current.json")
CUR_MD = os.path.join(USAGE_DIR, "current_report.md")
PREV_JSON = os.path.join(USAGE_DIR, "previous.json")
PREV_MD = os.path.join(USAGE_DIR, "previous_report.md")
PERIODS = {"hour": "Hourly", "day": "Daily", "week": "Weekly"}
CATS = ("focused", "distracted", "break", "paused", "outside", "away")
CAT_LABEL = {"focused": "Focused", "distracted": "Distracted", "break": "On a break",
             "paused": "Session paused", "outside": "Outside sessions", "away": "Away (idle)"}
IDLE_LIMIT = 120              # seconds without keyboard/mouse (outside sessions) = away
MAX_PAGES = 15                # page titles kept per app/site


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path) or APP_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def load_settings():
    cfg = dict(DEFAULTS)
    saved = load_json(CFG_PATH, {})
    if isinstance(saved, dict):
        cfg.update(saved)
    return cfg


def fmt_time(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def fmt_hm(seconds):
    seconds = max(0, int(seconds))
    h, m = seconds // 3600, (seconds % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def parse_date(s):
    s = (s or "").strip()
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


# --------------------------------------------------------------------------
# Active window detection (no third-party packages)
# --------------------------------------------------------------------------
def get_active_window():
    """Return a string describing the foreground window (app + title)."""
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value or ""
        if system == "Darwin":
            script = (
                'tell application "System Events"\n'
                "  set p to first application process whose frontmost is true\n"
                "  set appName to name of p\n"
                '  set winName to ""\n'
                "  try\n"
                "    set winName to name of front window of p\n"
                "  end try\n"
                "end tell\n"
                'return appName & " - " & winName'
            )
            out = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
            return out.stdout.strip()
        out = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def get_idle_seconds():
    """Seconds since the last keyboard/mouse input, or None if it can't be read."""
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(lii)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                return None
            ms = (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
            return ms / 1000.0
        if system == "Darwin":
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=2)
            m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out.stdout)
            return int(m.group(1)) / 1e9 if m else None
        out = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=2)
        return int(out.stdout.strip()) / 1000.0
    except Exception:
        return None


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as ex:
        messagebox.showinfo("Report folder", f"Your reports are in:\n{path}\n\n({ex})")


# --------------------------------------------------------------------------
# Habit tracker: turns window titles into apps/sites and keeps the tallies
# --------------------------------------------------------------------------
BROWSERS = {
    "google chrome": "Chrome", "chrome": "Chrome", "chromium": "Chromium",
    "microsoft edge": "Edge", "edge": "Edge", "mozilla firefox": "Firefox", "firefox": "Firefox",
    "safari": "Safari", "brave": "Brave", "brave browser": "Brave", "opera": "Opera",
    "vivaldi": "Vivaldi", "arc": "Arc", "zen browser": "Zen",
}
# keyword in the title -> name used in the report
SITES = (
    ("youtube", "YouTube"), ("netflix", "Netflix"), ("reddit", "Reddit"), ("instagram", "Instagram"),
    ("facebook", "Facebook"), ("messenger", "Messenger"), ("tiktok", "TikTok"), ("x.com", "X / Twitter"),
    ("twitter", "X / Twitter"), ("twitch", "Twitch"), ("discord", "Discord"), ("whatsapp", "WhatsApp"),
    ("snapchat", "Snapchat"), ("pinterest", "Pinterest"), ("linkedin", "LinkedIn"), ("9gag", "9GAG"),
    ("disney+", "Disney+"), ("spotify", "Spotify"),
    ("amazon", "Amazon"), ("ebay", "eBay"), ("gmail", "Gmail"), ("outlook", "Outlook"),
    ("google docs", "Google Docs"), ("google slides", "Google Slides"), ("google sheets", "Google Sheets"),
    ("google drive", "Google Drive"), ("canvas", "Canvas"), ("moodle", "Moodle"),
    ("blackboard", "Blackboard"), ("amboss", "AMBOSS"), ("uptodate", "UpToDate"),
    ("osmosis", "Osmosis"), ("pubmed", "PubMed"), ("quizlet", "Quizlet"), ("lecturio", "Lecturio"),
    ("sketchy", "Sketchy"), ("passmedicine", "Passmedicine"), ("geeky medics", "Geeky Medics"),
    ("teachmeanatomy", "TeachMeAnatomy"), ("kenhub", "Kenhub"), ("khan academy", "Khan Academy"),
    ("boards and beyond", "Boards & Beyond"), ("wikipedia", "Wikipedia"), ("chatgpt", "ChatGPT"),
    ("claude", "Claude"), ("notion", "Notion"),
)
_SPLIT = re.compile(r"\s+[-–—|·]\s+")
_MORE_PAGES = re.compile(r"\s+and \d+ more pages?$", re.I)


def _find_site(text):
    text = text.lower()
    for key, name in SITES:
        if re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", text):
            return name
    return None


def describe_window(raw):
    """Turn a raw window title into (app, label). label = website for browsers, else the app."""
    t = (raw or "").replace("​", "").replace("‎", "").strip()
    if not t:
        return "", "Unknown window"
    parts = [_MORE_PAGES.sub("", p).strip() for p in _SPLIT.split(t)]
    parts = [p for p in parts if p]
    browser_i = next((i for i, p in enumerate(parts) if p.lower() in BROWSERS), None)
    if browser_i is not None:
        app = BROWSERS[parts[browser_i].lower()]
        rest = [p for i, p in enumerate(parts) if i != browser_i and p.lower() not in ("personal", "work")]
        if not rest:
            return app, app
        label = _find_site(rest[-1]) or _find_site(" ".join(rest)) or rest[-1]
        return app, label[:40]
    app = parts[0] if platform.system() == "Darwin" else parts[-1]
    return app[:40], app[:40]


def _hm(seconds):
    return fmt_hm(seconds) if seconds >= 60 else f"{int(seconds)}s"


def _pct(a, b):
    return f"{round(100 * a / b)}%" if b else "-"


def build_report(d):
    """Plain-language Markdown report for one tracking period."""
    T = d["totals"]
    in_sess = T["focused"] + T["distracted"]
    all_secs = sum(T.values())
    start = dt.datetime.fromisoformat(d["start"])
    end = dt.datetime.fromisoformat(d["end"])
    upd = dt.datetime.fromisoformat(d["updated"])
    apps = sorted(d["apps"].items(), key=lambda kv: -kv[1]["seconds"])
    outside_listed = sum(v["by_cat"].get("outside", 0) for k, v in apps if v.get("listed"))

    L = [
        "# StudyGuard habit report",
        "",
        f"**Period:** {PERIODS.get(d['period'], d['period'])}, "
        f"{start.strftime('%a %d %b %Y %H:%M')} to {end.strftime('%a %d %b %Y %H:%M')}  ",
        f"**Last updated:** {upd.strftime('%a %d %b %Y %H:%M')}",
        "",
        "_This file is rewritten every 30 seconds. When the period ends it becomes "
        "`previous_report.md` and a fresh `current_report.md` starts._",
        "",
        "## Summary",
        "",
        f"- Time tracked: **{_hm(all_secs)}**",
        f"- In study sessions: **{_hm(in_sess)}** active, of which **{_hm(T['focused'])} focused** and "
        f"**{_hm(T['distracted'])} distracted** (focus score {_pct(T['focused'], in_sess)})",
        f"- Breaks: {_hm(T['break'])}; paused: {_hm(T['paused'])}",
        f"- Outside sessions: {_hm(T['outside'])} at the computer, of which {_hm(outside_listed)} "
        f"on sites/apps from your distraction list; {_hm(T['away'])} away/idle",
        f"- Longest unbroken stretch of focus: **{_hm(d['longest_focus'])}**",
        f"- Window switches: {d['switches']} in total, {d['session_switches']} during sessions"
        + (f" (about {round(d['session_switches'] / (in_sess / 3600))} per hour in sessions)" if in_sess >= 600 else ""),
        "",
    ]

    L += ["## Study sessions", ""]
    if d["sessions"]:
        L += ["| Started | Studying | Planned | Focused | Distracted | Reminders | Finished? |",
              "|---|---|---|---|---|---|---|"]
        for s in d["sessions"]:
            st = dt.datetime.fromisoformat(s["start"]).strftime("%a %H:%M")
            L.append(f"| {st} | {s['goal']} | {s['minutes']} min | {_hm(s['focused'])} | "
                     f"{_hm(s['distracted'])} | {s['reminders']} | {'yes' if s['finished'] else 'stopped early'} |")
    else:
        L.append("No study sessions in this period yet.")
    L.append("")

    L += ["## Apps and websites (most time first)", "",
          "| App / site | Total | Focused | Distracted | Outside sessions | Distraction list? |",
          "|---|---|---|---|---|---|"]
    for name, v in apps[:30]:
        b = v["by_cat"]
        L.append(f"| {name}{' (' + v['app'] + ')' if v['app'] and v['app'] != name else ''} | {_hm(v['seconds'])} | "
                 f"{_hm(b.get('focused', 0))} | {_hm(b.get('distracted', 0))} | "
                 f"{_hm(b.get('outside', 0))} | {'yes' if v.get('listed') else ''} |")
    if len(apps) > 30:
        L.append(f"| ...and {len(apps) - 30} more | | | | | |")
    L.append("")

    tops = [(n, v) for n, v in apps[:8] if v.get("pages")]
    if tops:
        L += ["## Top pages / windows", ""]
        for name, v in tops:
            pages = sorted(v["pages"].items(), key=lambda kv: -kv[1])[:4]
            L.append(f"- **{name}**: " + "; ".join(f"{p} ({_hm(s)})" for p, s in pages))
        L.append("")

    L += ["## Hour by hour", "",
          "| Hour | Focused | Distracted | Break | Outside sessions | Away |", "|---|---|---|---|---|---|"]
    for key in sorted(d["hours"]):
        h = d["hours"][key]
        label = dt.datetime.strptime(key, "%Y-%m-%d %H").strftime("%a %H:00")
        L.append(f"| {label} | {_hm(h.get('focused', 0))} | {_hm(h.get('distracted', 0))} | "
                 f"{_hm(h.get('break', 0))} | {_hm(h.get('outside', 0))} | {_hm(h.get('away', 0))} |")
    L.append("")
    return "\n".join(L)


class UsageTracker:
    """Keeps one period of usage data in memory and on disk (current.json + current_report.md)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.data = None
        self.last_label = None
        self.focus_run = 0
        self.session = None
        d = load_json(CUR_JSON, None)
        if self._valid(d) and d["period"] == cfg["usage_period"] and \
                dt.datetime.fromisoformat(d["end"]) > dt.datetime.now():
            self.data = d
        else:
            if self._valid(d):
                self.archive(d)
            self.new_period()

    @staticmethod
    def _valid(d):
        return isinstance(d, dict) and all(k in d for k in ("start", "end", "totals", "apps", "hours", "period"))

    def bounds(self, now=None):
        now = now or dt.datetime.now()
        period = self.cfg["usage_period"]
        if period == "hour":
            start = now.replace(minute=0, second=0, microsecond=0)
            return start, start + dt.timedelta(hours=1)
        h = max(0, min(23, int(self.cfg.get("usage_day_start", 4))))
        start = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if start > now:
            start -= dt.timedelta(days=1)
        if period == "week":
            start -= dt.timedelta(days=start.weekday())      # back to Monday
            return start, start + dt.timedelta(days=7)
        return start, start + dt.timedelta(days=1)

    def new_period(self):
        start, end = self.bounds()
        self.data = {
            "version": 1, "period": self.cfg["usage_period"],
            "start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
            "updated": dt.datetime.now().isoformat(timespec="seconds"),
            "totals": {c: 0 for c in CATS}, "apps": {}, "hours": {}, "sessions": [],
            "switches": 0, "session_switches": 0, "longest_focus": 0,
        }
        self.last_label = None
        self.focus_run = 0
        self.write()

    def archive(self, d=None):
        d = d or self.data
        d["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        save_json(PREV_JSON, d)
        self._write_text(PREV_MD, build_report(d))

    def restart(self):
        """Close the current period now (e.g. the period length was changed)."""
        self.archive()
        self.new_period()

    def check_rollover(self):
        if dt.datetime.now() >= dt.datetime.fromisoformat(self.data["end"]):
            self.restart()

    def record(self, raw, cat, listed):
        d = self.data
        d["totals"][cat] = d["totals"].get(cat, 0) + 1
        if cat == "away":
            app, label = "", "Away from computer"
        else:
            app, label = describe_window(raw)
        e = d["apps"].setdefault(label, {"app": app, "seconds": 0, "by_cat": {}, "listed": False, "pages": {}})
        e["seconds"] += 1
        e["by_cat"][cat] = e["by_cat"].get(cat, 0) + 1
        if listed:
            e["listed"] = True
        if cat != "away" and not self.cfg.get("hide_titles") and raw:
            page = raw.replace("​", "").strip()[:80]
            pages = e.setdefault("pages", {})
            pages[page] = pages.get(page, 0) + 1
            if len(pages) > MAX_PAGES:
                del pages[min(pages, key=pages.get)]
        hk = dt.datetime.now().strftime("%Y-%m-%d %H")
        hour = d["hours"].setdefault(hk, {})
        hour[cat] = hour.get(cat, 0) + 1
        if self.last_label is not None and label != self.last_label:
            d["switches"] += 1
            if cat in ("focused", "distracted"):
                d["session_switches"] += 1
        self.last_label = label
        self.focus_run = self.focus_run + 1 if cat == "focused" else 0
        d["longest_focus"] = max(d["longest_focus"], self.focus_run)

    def session_start(self, goal, minutes):
        self.session = {"start": dt.datetime.now().isoformat(timespec="seconds"), "goal": goal, "minutes": minutes}

    def session_end(self, finished, focused, distracted, reminders):
        if not self.session:
            return
        s = dict(self.session, end=dt.datetime.now().isoformat(timespec="seconds"), finished=bool(finished),
                 focused=focused, distracted=distracted, reminders=reminders)
        self.data["sessions"].append(s)
        self.session = None
        self.write()

    @staticmethod
    def _write_text(path, text):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def write(self):
        self.data["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        save_json(CUR_JSON, self.data)
        self._write_text(CUR_MD, build_report(self.data))


# --------------------------------------------------------------------------
# Look & feel: two calm palettes (Dark "ink", Light "paper") and the fonts
# --------------------------------------------------------------------------
THEMES = {
    "ink": dict(BG="#111111", SIDE="#0b0b0b", CARD="#181818", FIELD="#222222", HOVER="#2d2d2d",
                BORDER="#252525", FG="#f2efe8", MUTED="#8f8a82", FAINT="#58544f",
                ACCENT="#f2efe8", ACCENT_FG="#111111", ACCENT_HOVER="#ffffff",
                GREEN="#8fc3a0", RED="#e5806f", BLUE="#8fb3dc", GOLD="#d9b56c"),
    "paper": dict(BG="#f5f2ec", SIDE="#ede9e1", CARD="#fffdf9", FIELD="#efebe4", HOVER="#e4dfd6",
                  BORDER="#e3ded4", FG="#1d1c1a", MUTED="#7b766d", FAINT="#b4aea4",
                  ACCENT="#1d1c1a", ACCENT_FG="#f8f6f1", ACCENT_HOVER="#3b3936",
                  GREEN="#3f8a5a", RED="#c4553f", BLUE="#3f72ad", GOLD="#a9802b"),
}
THEME_NAMES = {"ink": "Dark", "paper": "Light"}

_saved_cfg = load_json(CFG_PATH, {})
THEME = _saved_cfg.get("theme") if isinstance(_saved_cfg, dict) else None
if THEME not in THEMES:
    THEME = "ink"
_P = THEMES[THEME]
BG, SIDE, CARD, FIELD, HOVER, BORDER = (_P[k] for k in ("BG", "SIDE", "CARD", "FIELD", "HOVER", "BORDER"))
FG, MUTED, FAINT = _P["FG"], _P["MUTED"], _P["FAINT"]
ACCENT, ACCENT_FG, ACCENT_HOVER = _P["ACCENT"], _P["ACCENT_FG"], _P["ACCENT_HOVER"]
GREEN, RED, BLUE, GOLD = _P["GREEN"], _P["RED"], _P["BLUE"], _P["GOLD"]
BG2, BG3 = CARD, FIELD            # older names still used in a few places

# Fonts are refined in pick_fonts() once Tk is running (it checks what is installed).
FONT = SANS = "Segoe UI"
THIN = "Segoe UI Light"
SEMI = "Segoe UI Semibold"
DISPLAY = "Segoe UI Semibold"

TEXT_STYLE = dict(
    bg=CARD, fg=FG, insertbackground=FG, relief="flat", highlightthickness=1,
    highlightbackground=BORDER, highlightcolor=FAINT, selectbackground=HOVER,
    selectforeground=FG,
)


def pick_fonts(root):
    """Windows' own crisp UI fonts (Segoe UI family); close equivalents elsewhere."""
    global FONT, SANS, THIN, SEMI, DISPLAY
    have = {f.lower(): f for f in tkfont.families(root)}

    def first(*names, default):
        for n in names:
            if n.lower() in have:
                return have[n.lower()]
        return default
    SANS = first("Segoe UI", "Helvetica Neue", "Inter", "Helvetica", "DejaVu Sans", default="Helvetica")
    SEMI = first("Segoe UI Semibold", "Inter SemiBold", default=SANS)
    THIN = first("Segoe UI Light", "Helvetica Neue Light", "Inter Display Light", "Inter Light", default=SANS)
    DISPLAY = first("Segoe UI Semibold", "Inter Display Medium", "Inter Display", default=SEMI)
    FONT = SANS


def semi(size):
    """A semibold font tuple (falls back to bold)."""
    return (SEMI, size) if SEMI != SANS else (SANS, size, "bold")


def caps(text):
    """Small capitals, used for labels."""
    return text.upper()


def mix(a, b, f):
    """Blend hex colour a towards hex colour b by fraction f (0..1)."""
    f = max(0.0, min(1.0, f))
    a, b = a.lstrip("#"), b.lstrip("#")
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(int(ca[i] + (cb[i] - ca[i]) * f) for i in range(3))


# --------------------------------------------------------------------------
# Custom widgets
# --------------------------------------------------------------------------
def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r,
           x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Btn(tk.Canvas):
    """A pill-shaped button. kind: accent (filled), ghost (soft), quiet (text only), danger."""

    def __init__(self, parent, text, command=None, kind="ghost", bg=None, height=40,
                 width=None, state="normal", size=10):
        self._text, self._cmd, self._kind, self._state = text, command, kind, state
        self._bg = bg or BG
        self._hover = False
        self._size = size
        f = semi(size) if kind == "accent" else (SANS, size)
        self._font = tkfont.Font(family=f[0], size=f[1], weight=f[2] if len(f) > 2 else "normal")
        w = width or (self._font.measure(text) + int(height * 1.1) + 8)
        tk.Canvas.__init__(self, parent, width=w, height=height, bg=self._bg,
                           highlightthickness=0, bd=0, cursor="hand2" if state == "normal" else "")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _colors(self):
        k = self._kind
        if self._state == "disabled":
            return (self._bg if k == "quiet" else FIELD), FAINT
        if k == "accent":
            return (ACCENT_HOVER if self._hover else ACCENT), ACCENT_FG
        if k == "quiet":
            return (FIELD if self._hover else self._bg), (FG if self._hover else MUTED)
        if k == "danger":
            return (HOVER if self._hover else FIELD), RED
        return (HOVER if self._hover else FIELD), FG

    def _wh(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            w = int(float(self.cget("width")))
        if h < 10:
            h = int(float(self.cget("height")))
        return w, h

    def _draw(self):
        self.delete("all")
        w, h = self._wh()
        fill, fg = self._colors()
        round_rect(self, 1, 1, w - 1, h - 1, (h - 2) / 2, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)

    def _enter(self, e):
        self._hover = True
        self._draw()

    def _leave(self, e):
        self._hover = False
        self._draw()

    def _release(self, e):
        w, h = self._wh()
        if self._state == "normal" and self._cmd and 0 <= e.x <= w and 0 <= e.y <= h:
            self._cmd()

    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        redraw = False
        for key, attr in (("text", "_text"), ("state", "_state"),
                          ("command", "_cmd"), ("kind", "_kind")):
            if key in kw:
                setattr(self, attr, kw.pop(key))
                redraw = True
        if kw:
            tk.Canvas.configure(self, **kw)
        if redraw:
            tk.Canvas.configure(self, cursor="hand2" if self._state == "normal" else "")
            self._draw()

    config = configure


class Toggle(tk.Frame):
    """An on/off switch bound to a BooleanVar."""

    def __init__(self, parent, text, variable, command=None, bg=None):
        bg = bg or BG
        tk.Frame.__init__(self, parent, bg=bg)
        self.var, self.cmd, self.bgc = variable, command, bg
        self.cv = tk.Canvas(self, width=38, height=22, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.cv.pack(side="left")
        self.lbl = tk.Label(self, text=text, bg=bg, fg=FG, font=(SANS, 10), cursor="hand2")
        self.lbl.pack(side="left", padx=(10, 0))
        for w in (self.cv, self.lbl):
            w.bind("<Button-1>", self.flip)
        variable.trace_add("write", lambda *a: self.draw())
        self.draw()

    def flip(self, e=None):
        self.var.set(not self.var.get())
        if self.cmd:
            self.cmd()

    def draw(self):
        on = bool(self.var.get())
        c = self.cv
        c.delete("all")
        if on:
            round_rect(c, 1, 1, 37, 21, 10, fill=ACCENT, outline="")
            c.create_oval(19, 4, 33, 18, fill=ACCENT_FG, outline="")
        else:
            round_rect(c, 1, 1, 37, 21, 10, fill=FIELD, outline=BORDER)
            c.create_oval(5, 4, 19, 18, fill=MUTED, outline="")


class Bar(tk.Canvas):
    """A slim rounded progress bar."""

    def __init__(self, parent, bg=None, color=None, height=6):
        tk.Canvas.__init__(self, parent, height=height, bg=bg or CARD, highlightthickness=0, bd=0)
        self.pct, self.color = 0.0, color or GREEN
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, pct):
        pct = max(0.0, min(100.0, pct))
        if abs(pct - self.pct) > 0.05:
            self.pct = pct
            self.draw()

    def draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = int(float(self.cget("height")))
        if w < 4:
            return
        round_rect(self, 0, 0, w, h, h / 2, fill=FIELD, outline="")
        if self.pct > 0:
            round_rect(self, 0, 0, max(h, w * self.pct / 100), h, h / 2, fill=self.color, outline="")


class Ring(tk.Canvas):
    """Circular timer: a thin progress ring with the time in the middle."""

    def __init__(self, parent, size=220, bg=None):
        tk.Canvas.__init__(self, parent, width=size, height=size, bg=bg or BG, highlightthickness=0, bd=0)
        self.size = size
        self.state = None
        self.set("25:00", 0, FG, "Ready")

    def resize(self, size):
        if size != self.size:
            self.size = size
            self.configure(width=size, height=size)
            st, self.state = self.state, None
            self.set(*st)

    def set(self, text, pct, color, sub):
        pct = round(max(0.0, min(100.0, pct)), 1)
        state = (text, pct, color, sub)
        if state == self.state:
            return
        self.state = state
        self.delete("all")
        s = self.size
        wd = max(4, s // 42)
        pad = wd + 3
        r = s / 2 - pad
        self.create_oval(pad, pad, s - pad, s - pad, outline=FIELD, width=wd)
        if pct > 0:
            ext = 359.9 * pct / 100
            self.create_arc(pad, pad, s - pad, s - pad, start=90, extent=-ext,
                            style="arc", outline=color, width=wd)
            for a in (90, 90 - ext):          # rounded ends
                t = math.radians(a)
                x, y = s / 2 + r * math.cos(t), s / 2 - r * math.sin(t)
                self.create_oval(x - wd / 2, y - wd / 2, x + wd / 2, y + wd / 2, fill=color, outline="")
        big = int(s * (0.165 if len(text) <= 5 else 0.12))
        self.create_text(s / 2, s / 2 - s * 0.03, text=text, fill=FG, font=(THIN, big))
        self.create_text(s / 2, s / 2 + s * 0.15, text=caps(sub),
                         fill=MUTED if sub == "Ready" else color, font=semi(8))


class Chip(tk.Canvas):
    """A small rounded label that follows a StringVar."""

    def __init__(self, parent, variable, bg=None, size=9):
        self.var, self._bg = variable, bg or BG
        self._font = tkfont.Font(family=SANS, size=size)
        tk.Canvas.__init__(self, parent, height=28, width=60, bg=self._bg, highlightthickness=0, bd=0)
        variable.trace_add("write", lambda *a: self.draw())
        self.draw()

    def draw(self):
        text = self.var.get()
        pad, dot, gap = 14, 6, 8                  # same space on both ends
        w = pad + dot + gap + self._font.measure(text) + pad
        self.configure(width=w)
        self.delete("all")
        round_rect(self, 0, 0, w, 28, 14, fill=FIELD, outline="")
        self.create_oval(pad, 11, pad + dot, 17, fill=GOLD, outline="")
        self.create_text(pad + dot + gap, 14, text=text, fill=MUTED, font=self._font, anchor="w")


class NavItem(tk.Canvas):
    def __init__(self, parent, text, command):
        tk.Canvas.__init__(self, parent, height=38, bg=SIDE, highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.command = text, command
        self.selected = self.hover = False
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self._h(True))
        self.bind("<Leave>", lambda e: self._h(False))
        self.bind("<Button-1>", lambda e: self.command())

    def _h(self, on):
        self.hover = on
        self.draw()

    def draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 60)
        if self.selected:
            round_rect(self, 0, 1, w, 37, 12, fill=mix(SIDE, FG, 0.08), outline="")
            self.create_oval(14, 16, 20, 22, fill=ACCENT, outline="")
        elif self.hover:
            round_rect(self, 0, 1, w, 37, 12, fill=mix(SIDE, FG, 0.04), outline="")
        self.create_text(30, 19, text=self.text, anchor="w",
                         fill=FG if (self.selected or self.hover) else MUTED,
                         font=semi(10) if self.selected else (SANS, 10))


class Sidebar(tk.Frame):
    """Brand, page list and a small 'today' footer."""

    def __init__(self, parent, names, command, icon=None):
        tk.Frame.__init__(self, parent, bg=SIDE, width=214)
        self.pack_propagate(False)
        self.command = command
        brand = tk.Frame(self, bg=SIDE)
        brand.pack(fill="x", padx=22, pady=(26, 28))
        if icon is not None:
            tk.Label(brand, image=icon, bg=SIDE).pack(side="left")
        tk.Label(brand, text="StudyGuard", bg=SIDE, fg=FG, font=(DISPLAY, 16)).pack(
            side="left", padx=(10 if icon is not None else 0, 0))
        tk.Label(self, text=caps("Menu"), bg=SIDE, fg=FAINT, font=semi(7)).pack(
            anchor="w", padx=26, pady=(0, 6))
        self.items = []
        for i, name in enumerate(names):
            it = NavItem(self, name, lambda i=i: self.select(i))
            it.pack(fill="x", padx=14, pady=1)
            self.items.append(it)
        self.footer = tk.Frame(self, bg=SIDE)
        self.footer.pack(side="bottom", fill="x", padx=26, pady=(0, 26))
        self.select(0, notify=False)

    def select(self, index, notify=True):
        for j, it in enumerate(self.items):
            it.selected = j == index
            it.draw()
        if notify:
            self.command(index)


# --------------------------------------------------------------------------
# Theme for the ttk widgets (entries, tables, labels...)
# --------------------------------------------------------------------------
def apply_theme(root):
    pick_fonts(root)
    s = ttk.Style(root)
    s.theme_use("clam")
    root.configure(bg=BG)
    s.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD,
                troughcolor=BG, focuscolor=BG, font=(SANS, 10))

    # frames & labels
    s.configure("TFrame", background=BG)
    s.configure("Card.TFrame", background=CARD)
    s.configure("TLabel", background=BG, foreground=FG)
    s.configure("Card.TLabel", background=CARD, foreground=FG)
    s.configure("CardBig.TLabel", background=CARD, foreground=FG, font=(SANS, 12))
    s.configure("Muted.TLabel", background=BG, foreground=MUTED)
    s.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=(SANS, 9))
    s.configure("CardCaps.TLabel", background=CARD, foreground=MUTED, font=semi(7))
    s.configure("Caps.TLabel", background=BG, foreground=MUTED, font=semi(7))
    s.configure("H1.TLabel", background=BG, foreground=FG, font=(DISPLAY, 24))
    s.configure("Status.TLabel", background=BG, foreground=MUTED, font=(SANS, 11))
    s.configure("StatNum.TLabel", background=CARD, foreground=FG, font=(THIN, 17))
    s.configure("StatCap.TLabel", background=CARD, foreground=MUTED, font=semi(7))

    # inputs
    s.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG,
                bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, padding=(10, 8))
    s.map("TEntry", bordercolor=[("focus", FAINT)], lightcolor=[("focus", FIELD)],
          darkcolor=[("focus", FIELD)])
    s.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, background=FIELD,
                arrowcolor=MUTED, insertcolor=FG, bordercolor=FIELD, lightcolor=FIELD,
                darkcolor=FIELD, padding=(8, 6), arrowsize=11)
    s.map("TSpinbox", bordercolor=[("focus", FAINT)], lightcolor=[("focus", FIELD)],
          darkcolor=[("focus", FIELD)], background=[("active", HOVER)])

    # sub-tabs of the parking lot
    s.configure("Park.TNotebook", background=BG, borderwidth=0, bordercolor=BG,
                lightcolor=BG, darkcolor=BG, tabmargins=[0, 0, 0, 10])
    s.configure("Park.TNotebook.Tab", background=BG, foreground=MUTED, padding=[16, 7],
                borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG, font=(SANS, 10))
    s.map("Park.TNotebook.Tab",
          background=[("selected", FIELD), ("!selected", BG)],
          foreground=[("selected", FG), ("!selected", MUTED)],
          bordercolor=[("!disabled", BG)], lightcolor=[("!disabled", BG)],
          darkcolor=[("!disabled", BG)])

    # tables
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                rowheight=36, borderwidth=0, font=(SANS, 10))
    s.map("Treeview", background=[("selected", FIELD)], foreground=[("selected", FG)])
    s.configure("Treeview.Heading", background=CARD, foreground=MUTED, relief="flat",
                padding=(8, 10), font=semi(7), borderwidth=0)
    s.map("Treeview.Heading", background=[("active", CARD)])
    s.layout("Check.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    s.configure("Check.Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                rowheight=44, borderwidth=0, font=(SANS, 11))
    s.map("Check.Treeview", background=[("selected", FIELD)], foreground=[("selected", FG)])

    # slim scrollbar without arrows
    s.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
        "sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    s.configure("Vertical.TScrollbar", background=FIELD, troughcolor=CARD, bordercolor=CARD,
                lightcolor=FIELD, darkcolor=FIELD, width=7)
    s.map("Vertical.TScrollbar",
          background=[("pressed", HOVER), ("active", HOVER), ("!active", FIELD)],
          lightcolor=[("!disabled", FIELD)], darkcolor=[("!disabled", FIELD)])


# --------------------------------------------------------------------------
# App icon (PNG, base64)
# --------------------------------------------------------------------------
ICON_64 = (
    "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"
)
ICON_32 = (
    "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"
)



class StudyGuard:
    PAGES = ["Focus", "Checklist", "Parking Lot", "Goals", "Habits", "Wellness", "Settings"]

    def __init__(self, root):
        self.root = root
        self.cfg = load_settings()
        self.progress = load_json(PROG_PATH, {"days": {}})
        if not isinstance(self.progress.get("days"), dict):
            self.progress = {"days": {}}
        self.parking = load_json(PARK_PATH, {})
        if not isinstance(self.parking.get("tabs"), list) or not self.parking["tabs"]:
            self.parking = {"tabs": [{"name": "Thoughts", "text": ""}]}

        # session state
        self.running = False
        self.paused = False
        self.remaining = 0
        self.focused = 0
        self.distracted = 0
        self.nag_count = 0
        self.drift_run = 0
        self.last_nag = 0.0
        self.current_title = ""
        self.tick_n = 0
        self.nag_window = None
        self.on_break = False
        self.break_remaining = 0
        self.break_times = []
        self.unsaved = 0              # focused seconds not yet written to progress.json
        self.goal_notified = None     # date string when "goal reached" was shown
        self.park_texts = []
        self.compact = False
        self.full_size = FULL_SIZE
        self.well_secs = {k: 0 for k, *_ in WELLNESS}
        self.well_window = None
        self.last_active = 0.0
        self.sec_n = 0                # every second, session or not
        self.idle_secs = 0.0
        self.tracker = UsageTracker(self.cfg)
        self.top_var = tk.BooleanVar(value=bool(self.cfg["always_on_top"]))

        root.title("StudyGuard")
        root.geometry(f"{FULL_SIZE[0]}x{FULL_SIZE[1]}")
        root.minsize(*FULL_MIN)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        try:
            root.attributes("-topmost", bool(self.cfg["always_on_top"]))
        except Exception:
            pass

        self.support_window = None
        if not self.cfg.get("support_next"):      # first run: first gentle reminder in a week
            self.cfg["support_next"] = (dt.date.today() + dt.timedelta(days=SUPPORT_EVERY_DAYS)).isoformat()
            save_json(CFG_PATH, self.cfg)

        self.set_icon()
        self.build_ui()
        self.refresh_exams()
        self.refresh_checklist()
        self.update_daily()
        self.update_stats()
        self.update_ring()
        self.update_greeting()
        self.tick()
        self.root.after(30000, self.autosave)

    # ======================================================================
    # UI construction
    # ======================================================================
    def set_icon(self):
        self.icon_img = self.icon_small = None
        try:
            self.icon_img = tk.PhotoImage(master=self.root, data=ICON_64)
            self.icon_small = tk.PhotoImage(master=self.root, data=ICON_32)
            self.root.iconphoto(True, self.icon_img, self.icon_small)
        except Exception:
            pass

    def card(self, parent, pady=(0, 14), padding=18, expand=False):
        outer = tk.Frame(parent, bg=BORDER)          # 1px hairline border
        outer.pack(fill="both" if expand else "x", expand=expand, pady=pady)
        inner = ttk.Frame(outer, style="Card.TFrame", padding=padding)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return inner

    def page_header(self, p, title, sub=None, wrap=660):
        ttk.Label(p, text=title, style="H1.TLabel").pack(anchor="w")
        if sub:
            ttk.Label(p, text=sub, style="Muted.TLabel", wraplength=wrap, justify="left").pack(
                anchor="w", pady=(6, 20))
        else:
            tk.Frame(p, bg=BG, height=18).pack()

    def build_ui(self):
        self.side = Sidebar(self.root, self.PAGES, self.show_tab, icon=self.icon_small)
        self.side.pack(side="left", fill="y")
        self.nav = self.side
        self.vsep = tk.Frame(self.root, bg=BORDER, width=1)
        self.vsep.pack(side="left", fill="y")

        f = self.side.footer
        tk.Label(f, text=caps("Today"), bg=SIDE, fg=FAINT, font=semi(7)).pack(anchor="w")
        self.side_today = tk.StringVar(value="0m")
        tk.Label(f, textvariable=self.side_today, bg=SIDE, fg=FG, font=(SANS, 10)).pack(anchor="w", pady=(4, 8))
        self.side_bar = Bar(f, bg=SIDE, height=4)
        self.side_bar.pack(fill="x")
        Btn(f, "Support on Ko-fi", self.open_donate, kind="ghost", bg=SIDE, height=32, size=9).pack(
            anchor="w", pady=(18, 0))

        self.stack = tk.Frame(self.root, bg=BG)
        self.stack.pack(side="left", fill="both", expand=True, padx=36, pady=(30, 24))
        self.stack.grid_rowconfigure(0, weight=1)
        self.stack.grid_columnconfigure(0, weight=1)
        self.tabs = []
        for _ in self.PAGES:
            fr = ttk.Frame(self.stack)
            fr.grid(row=0, column=0, sticky="nsew")
            self.tabs.append(fr)
        (self.tab_focus, self.tab_check, self.tab_park, self.tab_goals,
         self.tab_habits, self.tab_well, self.tab_set) = self.tabs
        self.build_focus()
        self.build_checklist()
        self.build_parking()
        self.build_goals()
        self.build_habits()
        self.build_wellness()
        self.build_settings()
        self.show_tab(0)

    def show_tab(self, index):
        self.tabs[index].tkraise()

    def update_greeting(self):
        now = dt.datetime.now()
        h = now.hour
        part = "morning" if 5 <= h < 12 else ("afternoon" if 12 <= h < 17 else "evening")
        self.greet_var.set(f"Good {part.capitalize()}")
        self.date_var.set(f"{now.strftime('%A')} {now.day} {now.strftime('%B')}")

    # ---- window modes ----------------------------------------------------
    def set_compact(self, on):
        """Compact = just the timer card in a small window."""
        if on == self.compact:
            return
        self.compact = on
        r = self.root
        body = self.focus_body
        if on:
            r.update_idletasks()
            self.full_size = (max(r.winfo_width(), FULL_MIN[0]), max(r.winfo_height(), FULL_MIN[1]))
            self.side.select(0)
            self.side.pack_forget()
            self.vsep.pack_forget()
            self.focus_top.pack_forget()
            self.focus_right.grid_remove()
            body.columnconfigure(0, weight=1, uniform="")
            body.columnconfigure(1, weight=0, uniform="")
            body.pack_configure(pady=0)
            self.focus_left.grid_configure(padx=0)
            self.ring.resize(196)
            self.stack.pack_configure(padx=16, pady=16)
            r.minsize(340, 440)
            r.geometry(f"{COMPACT_SIZE[0]}x{COMPACT_SIZE[1]}")
        else:
            r.minsize(*FULL_MIN)
            self.side.pack(side="left", fill="y", before=self.stack)
            self.vsep.pack(side="left", fill="y", before=self.stack)
            self.focus_top.pack(fill="x", before=body)
            body.pack_configure(pady=(24, 0))
            self.focus_right.grid()
            body.columnconfigure(0, weight=6, uniform="cols")
            body.columnconfigure(1, weight=5, uniform="cols")
            self.focus_left.grid_configure(padx=(0, 14))
            self.ring.resize(RING_SIZE)
            self.stack.pack_configure(padx=36, pady=(30, 24))
            r.geometry(f"{self.full_size[0]}x{self.full_size[1]}")
        self.view_btn.configure(text="Full view" if on else "Compact view")

    # ---- Focus page ------------------------------------------------------
    def build_focus(self):
        p = self.tab_focus
        top = self.focus_top = tk.Frame(p, bg=BG)
        top.pack(fill="x")
        greet = tk.Frame(top, bg=BG)
        greet.pack(side="left")
        self.greet_var, self.date_var = tk.StringVar(), tk.StringVar()
        tk.Label(greet, textvariable=self.greet_var, bg=BG, fg=FG, font=(DISPLAY, 26)).pack(anchor="w")
        tk.Label(greet, textvariable=self.date_var, bg=BG, fg=MUTED, font=(SANS, 10)).pack(anchor="w", pady=(2, 0))
        self.exam_banner = tk.StringVar()
        self.exam_lbl = Chip(top, self.exam_banner, bg=BG)
        self.exam_lbl.pack(side="right", anchor="n", pady=(10, 0))

        body = self.focus_body = tk.Frame(p, bg=BG)
        body.pack(fill="both", expand=True, pady=(24, 0))
        body.columnconfigure(0, weight=6, uniform="cols")
        body.columnconfigure(1, weight=5, uniform="cols")
        body.rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        self.focus_left, self.focus_right = left, right

        # ---- left: the timer card ----
        tc = self.card(left, pady=0, padding=(26, 22), expand=True)
        view = tk.Frame(tc, bg=CARD)
        view.pack(side="bottom", fill="x", pady=(14, 0))
        Toggle(view, "Always on top", self.top_var, command=self.apply_topmost, bg=CARD).pack(side="left")
        self.view_btn = Btn(view, "Compact view", self.toggle_compact, height=30, size=9, width=118, bg=CARD)
        self.view_btn.pack(side="right")

        mid = tk.Frame(tc, bg=CARD)              # keeps the timer block vertically centred
        mid.pack(expand=True, fill="x")
        self.ring = Ring(mid, size=RING_SIZE, bg=CARD)
        self.ring.pack(pady=(0, 10))
        self.status_var = tk.StringVar(value="Ready when you are")
        self.status_lbl = tk.Label(mid, textvariable=self.status_var, bg=CARD, fg=MUTED, font=(SANS, 11))
        self.status_lbl.pack()
        self.next_var = tk.StringVar()
        tk.Label(mid, textvariable=self.next_var, bg=CARD, fg=MUTED, font=(SANS, 10),
                 wraplength=340, justify="center").pack(pady=(4, 16))

        row = tk.Frame(mid, bg=CARD)
        row.pack(fill="x")
        row.columnconfigure(0, weight=2, uniform="b")
        row.columnconfigure(1, weight=1, uniform="b")
        row.columnconfigure(2, weight=1, uniform="b")
        self.start_btn = Btn(row, "Start session", self.start, kind="accent", bg=CARD, height=44)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.pause_btn = Btn(row, "Pause", self.toggle_pause, state="disabled", bg=CARD, height=44)
        self.pause_btn.grid(row=0, column=1, sticky="ew", padx=5)
        self.stop_btn = Btn(row, "Stop", self.stop, state="disabled", bg=CARD, height=44)
        self.stop_btn.grid(row=0, column=2, sticky="ew", padx=(5, 0))
        self.break_btn = Btn(mid, "Take a 5-min break", self.toggle_break, state="disabled",
                             kind="quiet", bg=CARD, height=36)
        self.break_btn.pack(fill="x", pady=(8, 0))

        # ---- right: session setup, session stats, quick parking ----
        setup = self.card(right)
        setup.columnconfigure(0, weight=1)
        ttk.Label(setup, text=caps("Studying"), style="CardCaps.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.goal_var = tk.StringVar(value=self.cfg["goal"])
        ttk.Entry(setup, textvariable=self.goal_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 10))
        lenrow = ttk.Frame(setup, style="Card.TFrame")
        lenrow.grid(row=2, column=0, sticky="w")
        self.min_var = tk.IntVar(value=self.cfg["minutes"])
        sp = ttk.Spinbox(lenrow, from_=1, to=240, textvariable=self.min_var, width=4, command=self.update_ring)
        sp.pack(side="left")
        sp.bind("<KeyRelease>", lambda e: self.update_ring())
        ttk.Label(lenrow, text="minutes", style="CardMuted.TLabel").pack(side="left", padx=(8, 0))
        self.strict_var = tk.BooleanVar(value=self.cfg["strict"])
        Toggle(setup, "Strict", self.strict_var, command=self.on_strict, bg=CARD).grid(row=2, column=1, sticky="e")

        stats = self.card(right)
        ttk.Label(stats, text=caps("Session"), style="CardCaps.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        self.stat_vars = {}
        for i, (key, cap) in enumerate([("focused", "Focused"), ("distracted", "Drifted"),
                                        ("reminders", "Nudges"), ("breaks", "Breaks")]):
            stats.columnconfigure(i, weight=1)
            v = tk.StringVar(value="0")
            ttk.Label(stats, textvariable=v, style="StatNum.TLabel").grid(row=1, column=i, sticky="w", pady=(8, 0))
            ttk.Label(stats, text=caps(cap), style="StatCap.TLabel").grid(row=2, column=i, sticky="w")
            self.stat_vars[key] = v
        self.daily_var = tk.StringVar()
        ttk.Label(stats, textvariable=self.daily_var, style="CardMuted.TLabel").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(14, 6))
        self.daily_bar = Bar(stats, bg=CARD)
        self.daily_bar.grid(row=4, column=0, columnspan=4, sticky="ew")
        self.window_var = tk.StringVar(value="")
        ttk.Label(stats, textvariable=self.window_var, style="CardMuted.TLabel", wraplength=330).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))


        park = tk.Frame(right, bg=BG)
        park.pack(fill="x")
        self.quick_var = tk.StringVar()
        e = ttk.Entry(park, textvariable=self.quick_var)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.quick_park())
        self.placeholder(e, self.quick_var, PARK_PLACEHOLDER)
        self.park_btn = Btn(park, "Park it", self.quick_park, height=36, bg=BG)
        self.park_btn.pack(side="left", padx=(8, 0))

    # ---- Checklist page --------------------------------------------------
    def build_checklist(self):
        p = self.tab_check
        self.page_header(p, "Checklist", "Break the session into small, doable steps. Double-click a step to "
                                         "tick it off. The first open step shows as “Now” on the Focus page.")
        row = tk.Frame(p, bg=BG)
        row.pack(fill="x")
        self.check_var = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.check_var)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.add_check())
        self.placeholder(e, self.check_var, CHECK_PLACEHOLDER)
        Btn(row, "Add step", self.add_check, kind="accent", height=38, bg=BG).pack(side="left", padx=(10, 0))

        bar = tk.Frame(p, bg=BG)
        bar.pack(side="bottom", fill="x")
        Btn(bar, "Tick / untick", self.toggle_selected, height=32, size=9, bg=BG).pack(side="left")
        Btn(bar, "Remove", self.remove_check, height=32, size=9, bg=BG).pack(side="left", padx=8)
        Btn(bar, "Clear ticked", self.clear_done, height=32, size=9, bg=BG).pack(side="left")
        self.check_summary = tk.StringVar()
        ttk.Label(bar, textvariable=self.check_summary, style="Muted.TLabel").pack(side="right")

        wrap = self.card(p, pady=(14, 14), padding=(10, 6), expand=True)
        self.check_tree = ttk.Treeview(wrap, columns=("step",), show="", style="Check.Treeview",
                                       selectmode="browse")
        self.check_tree.column("step", anchor="w")
        self.check_tree.tag_configure("done", foreground=FAINT)
        self.check_tree.tag_configure("open", foreground=FG)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.check_tree.yview)
        self.check_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.check_tree.pack(side="left", fill="both", expand=True)
        self.check_tree.bind("<Double-Button-1>", self.on_check_double)
        self.check_tree.bind("<Delete>", lambda ev: self.remove_check())

    # ---- Parking lot page ------------------------------------------------
    def build_parking(self):
        p = self.tab_park
        self.page_header(p, "Parking lot", "Jot down stray thoughts, questions and to-dos so you can deal with "
                                           "them later. Use tabs to sort them, e.g. Thoughts, To look up, Admin.")
        bar = tk.Frame(p, bg=BG)
        bar.pack(fill="x", pady=(0, 12))
        Btn(bar, "+  New tab", self.new_park_tab, height=32, size=9, bg=BG).pack(side="left")
        Btn(bar, "Rename", self.rename_park_tab, height=32, size=9, bg=BG).pack(side="left", padx=8)
        Btn(bar, "Delete", self.delete_park_tab, height=32, size=9, bg=BG, kind="quiet").pack(side="left")
        Btn(bar, "Export all", self.save_all_txt, height=32, size=9, bg=BG).pack(side="right")
        Btn(bar, "Export tab", self.save_tab_txt, height=32, size=9, bg=BG).pack(side="right", padx=8)

        self.park_status = tk.StringVar(value="Your notes save automatically.")
        ttk.Label(p, textvariable=self.park_status, style="Muted.TLabel", wraplength=640).pack(
            side="bottom", anchor="w", pady=(10, 0))
        self.park_nb = ttk.Notebook(p, style="Park.TNotebook")
        self.park_nb.pack(fill="both", expand=True)
        for t in self.parking["tabs"]:
            self.add_park_tab(t.get("name", "Tab"), t.get("text", ""))

    def add_park_tab(self, name, text=""):
        frame = ttk.Frame(self.park_nb)
        txt = tk.Text(frame, wrap="word", font=(SANS, 11), undo=True, padx=18, pady=14,
                      spacing1=2, spacing3=4, **TEXT_STYLE)
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", text)
        self.park_nb.add(frame, text=name)
        self.park_texts.append(txt)

    # ---- Goals page ------------------------------------------------------
    def build_goals(self):
        p = self.tab_goals
        self.page_header(p, "Goals", "A daily focus target, your streak, and the exams you're working towards.")
        card = self.card(p)
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=caps("Daily focus goal"), style="CardCaps.TLabel").grid(row=0, column=0, sticky="w")
        gl = ttk.Frame(card, style="Card.TFrame")
        gl.grid(row=0, column=1, rowspan=2, sticky="e")
        self.dgoal_var = tk.IntVar(value=self.cfg["daily_goal_minutes"])
        sp = ttk.Spinbox(gl, from_=10, to=900, increment=10, textvariable=self.dgoal_var, width=5,
                         command=self.update_daily)
        sp.pack(side="left")
        sp.bind("<KeyRelease>", lambda ev: self.update_daily())
        ttk.Label(gl, text="min a day", style="CardMuted.TLabel").pack(side="left", padx=(8, 0))
        self.daily_var2 = tk.StringVar()
        ttk.Label(card, textvariable=self.daily_var2, style="CardBig.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 12))
        self.daily_bar2 = Bar(card, bg=CARD, height=8)
        self.daily_bar2.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.streak_var = tk.StringVar()
        ttk.Label(card, textvariable=self.streak_var, style="CardMuted.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Label(p, text=caps("Exam countdown"), style="Caps.TLabel").pack(anchor="w", pady=(10, 8))
        add = tk.Frame(p, bg=BG)
        add.pack(side="bottom", fill="x", pady=(14, 0))
        add.columnconfigure(0, weight=2)
        add.columnconfigure(1, weight=1)
        self.exam_name_var = tk.StringVar()
        self.exam_date_var = tk.StringVar()
        ttk.Entry(add, textvariable=self.exam_name_var).grid(row=0, column=0, sticky="ew")
        ed = ttk.Entry(add, textvariable=self.exam_date_var)
        ed.grid(row=0, column=1, sticky="ew", padx=10)
        ed.bind("<Return>", lambda ev: self.add_exam())
        Btn(add, "Add exam", self.add_exam, kind="accent", height=38, bg=BG).grid(row=0, column=2)
        Btn(add, "Remove", self.remove_exam, height=38, bg=BG, kind="quiet").grid(row=0, column=3, padx=(8, 0))
        ttk.Label(add, text="Exam name", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(add, text="Date, e.g. 2026-11-20 or 20/11/2026", style="Muted.TLabel").grid(
            row=1, column=1, columnspan=3, sticky="w", padx=10, pady=(5, 0))

        tw = self.card(p, pady=0, padding=(10, 4), expand=True)
        self.exam_tree = ttk.Treeview(tw, columns=("exam", "date", "left"), show="headings",
                                      height=4, selectmode="browse")
        self.exam_tree.heading("exam", text=caps("Exam"), anchor="w")
        self.exam_tree.heading("date", text=caps("Date"), anchor="w")
        self.exam_tree.heading("left", text=caps("Time left"), anchor="e")
        self.exam_tree.column("exam", width=240, anchor="w")
        self.exam_tree.column("date", width=150, anchor="w")
        self.exam_tree.column("left", width=110, anchor="e")
        self.exam_tree.pack(fill="both", expand=True)

    # ---- Habits page -----------------------------------------------------
    def build_habits(self):
        p = self.tab_habits
        self.page_header(p, "Habits", "Which apps and websites you use, and for how long: focused, drifting "
                                      "or outside sessions. Everything stays on this computer.")
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x")
        top.columnconfigure(0, weight=1, uniform="h")
        top.columnconfigure(1, weight=1, uniform="h")

        lf = tk.Frame(top, bg=BG)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        opts = self.card(lf, pady=0, expand=True)
        self.track_var = tk.BooleanVar(value=self.cfg["track_usage"])
        self.track_out_var = tk.BooleanVar(value=self.cfg["track_outside"])
        self.hide_titles_var = tk.BooleanVar(value=self.cfg["hide_titles"])
        Toggle(opts, "Track app & website usage", self.track_var, command=self.persist, bg=CARD).pack(anchor="w")
        Toggle(opts, "Also outside study sessions", self.track_out_var, command=self.persist,
               bg=CARD).pack(anchor="w", pady=(10, 0))
        Toggle(opts, "Names only, no page titles", self.hide_titles_var,
               command=self.persist, bg=CARD).pack(anchor="w", pady=(10, 0))
        per = ttk.Frame(opts, style="Card.TFrame")
        per.pack(fill="x", pady=(16, 0))
        self.period_btns = {}
        for key, name in PERIODS.items():
            b = Btn(per, name, lambda k=key: self.set_period(k), height=30, size=9, bg=CARD,
                    kind="accent" if self.cfg["usage_period"] == key else "ghost")
            b.pack(side="left", padx=(0, 6))
            self.period_btns[key] = b
        ds = ttk.Frame(opts, style="Card.TFrame")
        ds.pack(fill="x", pady=(12, 0))
        ttk.Label(ds, text="Days start at", style="CardMuted.TLabel").pack(side="left")
        self.day_start_var = tk.IntVar(value=self.cfg["usage_day_start"])
        ttk.Spinbox(ds, from_=0, to=23, textvariable=self.day_start_var, width=3,
                    command=self.persist).pack(side="left", padx=6)
        ttk.Label(ds, text=":00", style="CardMuted.TLabel").pack(side="left")

        rf = tk.Frame(top, bg=BG)
        rf.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        summ = self.card(rf, pady=0, expand=True)
        self.habit_period = tk.StringVar()
        ttk.Label(summ, textvariable=self.habit_period, style="CardCaps.TLabel").pack(anchor="w")
        self.habit_rows = {}
        grid = ttk.Frame(summ, style="Card.TFrame")
        grid.pack(fill="x", pady=(10, 0))
        grid.columnconfigure(1, weight=1)
        for i, (key, cap) in enumerate([("focused", "Focused"), ("distracted", "Drifted in sessions"),
                                        ("score", "Focus score"), ("outside", "Outside sessions"),
                                        ("away", "Away / idle"), ("streak", "Longest focus stretch"),
                                        ("switches", "Window switches in sessions")]):
            ttk.Label(grid, text=cap, style="CardMuted.TLabel").grid(row=i, column=0, sticky="w", pady=2)
            v = tk.StringVar(value="-")
            ttk.Label(grid, textvariable=v, style="Card.TLabel").grid(row=i, column=1, sticky="e", pady=2)
            self.habit_rows[key] = v

        bar = tk.Frame(p, bg=BG)
        bar.pack(side="bottom", fill="x", pady=(12, 0))
        Btn(bar, "Save report now", self.save_report_now, height=32, size=9, bg=BG).pack(side="left")
        Btn(bar, "Open report folder", lambda: open_folder(USAGE_DIR), height=32, size=9,
            bg=BG).pack(side="left", padx=8)
        dot = tk.Canvas(bar, width=8, height=8, bg=BG, highlightthickness=0)
        ttk.Label(bar, text="On your distraction list", style="Muted.TLabel").pack(side="right")
        dot.create_oval(0, 0, 8, 8, fill=RED, outline="")
        dot.pack(side="right", padx=(0, 6))

        tw = self.card(p, pady=(14, 0), padding=(10, 4), expand=True)
        cols = ("name", "total", "focused", "distracted", "outside")
        self.habit_tree = ttk.Treeview(tw, columns=cols, show="headings", height=4, selectmode="none")
        for c, head, w, anchor in (("name", "App / site", 250, "w"), ("total", "Total", 80, "e"),
                                   ("focused", "Focused", 80, "e"), ("distracted", "Drifted", 80, "e"),
                                   ("outside", "Outside", 90, "e")):
            self.habit_tree.heading(c, text=caps(head), anchor=anchor)
            self.habit_tree.column(c, width=w, anchor=anchor)
        self.habit_tree.tag_configure("listed", foreground=RED)
        sb = ttk.Scrollbar(tw, orient="vertical", command=self.habit_tree.yview)
        self.habit_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.habit_tree.pack(side="left", fill="both", expand=True)
        self.refresh_habits()

    # ---- Wellness page ---------------------------------------------------
    def build_wellness(self):
        p = self.tab_well
        self.page_header(p, "Wellness", "Gentle nudges while a session is running. The clocks keep going "
                                        "during breaks and carry over between sessions.")
        card = self.card(p)
        card.columnconfigure(0, minsize=230)
        card.columnconfigure(4, weight=1)
        self.well_on, self.well_min, self.well_next = {}, {}, {}
        for r, (kind, label, msg, default) in enumerate(WELLNESS):
            self.well_on[kind] = tk.BooleanVar(value=self.cfg[f"{kind}_on"])
            self.well_min[kind] = tk.IntVar(value=self.cfg[f"{kind}_min"])
            self.well_next[kind] = tk.StringVar()
            pad = (0 if r == 0 else 18, 0)
            Toggle(card, label, self.well_on[kind], bg=CARD).grid(row=r, column=0, sticky="w", pady=pad)
            ttk.Label(card, text="every", style="CardMuted.TLabel").grid(row=r, column=1, padx=(0, 8), pady=pad)
            ttk.Spinbox(card, from_=5, to=480, increment=5, textvariable=self.well_min[kind], width=4).grid(
                row=r, column=2, pady=pad)
            ttk.Label(card, text="min", style="CardMuted.TLabel").grid(row=r, column=3, padx=(8, 0), pady=pad)
            ttk.Label(card, textvariable=self.well_next[kind], style="CardMuted.TLabel").grid(
                row=r, column=4, sticky="e", pady=pad)
        Btn(p, "Preview a reminder", self.preview_wellness, height=32, size=9, bg=BG).pack(anchor="w", pady=(4, 0))
        self.update_well_labels()

    # ---- Settings page ---------------------------------------------------
    def build_settings(self):
        p = self.tab_set
        self.page_header(p, "Settings")
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x")
        top.columnconfigure(0, weight=1, uniform="s")
        top.columnconfigure(1, weight=1, uniform="s")
        lc = tk.Frame(top, bg=BG)
        lc.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        rc = tk.Frame(top, bg=BG)
        rc.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        t = self.card(lc, pady=0, expand=True)
        t.columnconfigure(0, weight=1)
        ttk.Label(t, text=caps("Reminders"), style="CardCaps.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(t, text="Nudge me after drifting for (sec)", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.grace_var = tk.IntVar(value=self.cfg["grace_seconds"])
        ttk.Spinbox(t, from_=3, to=300, textvariable=self.grace_var, width=4).grid(row=1, column=1, sticky="e", pady=(12, 0))
        ttk.Label(t, text="Then again every (sec)", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.cool_var = tk.IntVar(value=self.cfg["nag_cooldown_seconds"])
        ttk.Spinbox(t, from_=10, to=900, textvariable=self.cool_var, width=4).grid(row=2, column=1, sticky="e", pady=(10, 0))
        Toggle(t, "Keep StudyGuard on top", self.top_var, command=self.apply_topmost, bg=CARD).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(16, 0))
        self.compact_var = tk.BooleanVar(value=self.cfg["compact_on_start"])
        Toggle(t, "Compact view during sessions", self.compact_var, bg=CARD).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        a = self.card(rc, pady=0, expand=True)
        ttk.Label(a, text=caps("Appearance"), style="CardCaps.TLabel").pack(anchor="w")
        row = ttk.Frame(a, style="Card.TFrame")
        row.pack(fill="x", pady=(12, 0))
        self.theme_btns = {}
        for key, name in THEME_NAMES.items():
            b = Btn(row, name, lambda k=key: self.set_theme(k), height=32, size=9, bg=CARD,
                    kind="accent" if self.cfg.get("theme", THEME) == key else "ghost", width=84)
            b.pack(side="left", padx=(0, 6))
            self.theme_btns[key] = b
        self.theme_note = tk.StringVar(value="Dark is easy on the eyes at night; Light suits daytime.")
        ttk.Label(a, textvariable=self.theme_note, style="CardMuted.TLabel", wraplength=300,
                  justify="left").pack(anchor="w", pady=(12, 0))
        self.restart_btn = Btn(a, "Restart now", self.restart_app, height=30, size=9, bg=CARD)
        sup = ttk.Frame(a, style="Card.TFrame")
        sup.pack(side="bottom", fill="x", pady=(16, 0))
        tk.Frame(sup, bg=BORDER, height=1).pack(fill="x", pady=(0, 14))
        ttk.Label(sup, text=caps("Support"), style="CardCaps.TLabel").pack(anchor="w")
        ttk.Label(sup, text="StudyGuard is free. If it helps you study, you can buy me a coffee.",
                  style="CardMuted.TLabel", wraplength=300, justify="left").pack(anchor="w", pady=(6, 10))
        Btn(sup, "Support on Ko-fi", self.open_donate, kind="accent", height=32, size=9, bg=CARD).pack(anchor="w")
        self.support_var = tk.BooleanVar(value=not self.cfg.get("support_off", False))
        Toggle(sup, "Weekly support reminder", self.support_var, command=self.on_support_toggle,
               bg=CARD).pack(anchor="w", pady=(12, 0))

        ttk.Label(p, text=caps("Word lists"), style="Caps.TLabel").pack(anchor="w", pady=(18, 8))
        lists = self.card(p, pady=0, expand=True)
        self.list_widgets = {}
        for col, (title, key) in enumerate([("Distracting", "distractions"),
                                            ("Always OK", "allowed"),
                                            ("Strict mode: study apps", "strict_apps")]):
            lists.columnconfigure(col, weight=1)
            lists.rowconfigure(1, weight=1)
            ttk.Label(lists, text=caps(title), style="CardCaps.TLabel").grid(
                row=0, column=col, sticky="w", padx=(0 if col == 0 else 10, 0))
            tx = tk.Text(lists, height=4, width=14, wrap="none", font=(SANS, 10), padx=8, pady=6,
                         **dict(TEXT_STYLE, bg=FIELD, highlightbackground=FIELD))
            tx.grid(row=1, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0), pady=(8, 0))
            tx.insert("1.0", "\n".join(self.cfg[key]))
            self.list_widgets[key] = tx
        ttk.Label(
            lists, style="CardMuted.TLabel", wraplength=660, justify="left",
            text="One word per line. A window counts as a distraction if its title contains a distracting "
                 "word, unless it also has an “Always OK” word (e.g. a YouTube lecture). In strict "
                 "mode, any window without a study-app or “Always OK” word counts as a distraction.",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def set_theme(self, key):
        self.cfg["theme"] = key
        self.persist()
        for k, b in self.theme_btns.items():
            b.configure(kind="accent" if k == key else "ghost")
        if key == THEME:
            self.theme_note.set("This is the look you're using now.")
            self.restart_btn.pack_forget()
        else:
            self.theme_note.set(f"{THEME_NAMES[key]} mode starts next time StudyGuard opens.")
            self.restart_btn.pack(anchor="w", pady=(10, 0))

    def open_donate(self):
        self.set_support_off(True)                # they've donated (or meant to): no more reminders
        try:
            webbrowser.open(DONATE_URL)
        except Exception:
            messagebox.showinfo("Support StudyGuard", f"Thank you! You can donate at:\n{DONATE_URL}")

    # ---- gentle support reminder -----------------------------------------
    def set_support_off(self, off):
        self.cfg["support_off"] = bool(off)
        if not off:
            self.cfg["support_next"] = (dt.date.today() + dt.timedelta(days=SUPPORT_EVERY_DAYS)).isoformat()
        if hasattr(self, "support_var") and self.support_var.get() == bool(off):
            self.support_var.set(not off)
        save_json(CFG_PATH, self.cfg)

    def on_support_toggle(self):
        self.set_support_off(not self.support_var.get())

    def maybe_show_support(self):
        """Only when it's due, never during a session, and never twice at once."""
        if self.cfg.get("support_off") or self.running or self.support_window is not None:
            return
        due = parse_date(self.cfg.get("support_next", ""))
        if due is None or dt.date.today() >= due:
            self.show_support()

    def close_support(self, later=True):
        if later:
            self.cfg["support_next"] = (dt.date.today() + dt.timedelta(days=SUPPORT_EVERY_DAYS)).isoformat()
            save_json(CFG_PATH, self.cfg)
        if self.support_window is not None:
            try:
                self.support_window.destroy()
            except Exception:
                pass
            self.support_window = None

    def show_support(self):
        """A quiet corner card: no sound, doesn't steal focus, not forced on top."""
        self.close_support(later=False)
        win = self.support_window = tk.Toplevel(self.root)
        win.title("Support StudyGuard")
        win.configure(bg=BORDER)
        win.resizable(False, False)
        if self.icon_img is not None:
            try:
                win.iconphoto(False, self.icon_img)
            except Exception:
                pass
        w, h = 420, 282
        x = win.winfo_screenwidth() - w - 24
        y = win.winfo_screenheight() - h - 90
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.protocol("WM_DELETE_WINDOW", lambda: self.close_support(later=True))
        outer = tk.Frame(win, bg=CARD)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        body = tk.Frame(outer, bg=CARD)
        body.pack(fill="both", expand=True, padx=26, pady=22)
        head = tk.Frame(body, bg=CARD)
        head.pack(fill="x")
        dot = tk.Canvas(head, width=9, height=9, bg=CARD, highlightthickness=0)
        dot.create_oval(0, 0, 9, 9, fill=GOLD, outline="")
        dot.pack(side="left")
        tk.Label(head, text=caps("Support"), bg=CARD, fg=MUTED, font=semi(7)).pack(side="left", padx=(8, 0))
        tk.Label(body, text="Enjoying StudyGuard?", bg=CARD, fg=FG, font=(DISPLAY, 19), anchor="w").pack(fill="x", pady=(10, 4))
        tk.Label(body, text=SUPPORT_MESSAGE, bg=CARD, fg=FG, font=(SANS, 10), anchor="w", justify="left",
                 wraplength=w - 60).pack(fill="x")
        row = tk.Frame(body, bg=CARD)

        def donate():
            self.close_support(later=False)
            self.open_donate()

        def never():
            self.close_support(later=False)
            self.set_support_off(True)
        Btn(row, "Support on Ko-fi", donate, kind="accent", bg=CARD, height=38, size=10).pack(side="left")
        Btn(row, "Maybe later", lambda: self.close_support(later=True), kind="ghost", bg=CARD,
            height=38, size=10).pack(side="left", padx=(8, 0))
        never_lbl = tk.Label(body, text="Don't show this again", bg=CARD, fg=MUTED, font=(SANS, 9, "underline"),
                             cursor="hand2")
        never_lbl.pack(side="bottom", anchor="w", pady=(12, 0))
        never_lbl.bind("<Button-1>", lambda e: never())
        never_lbl.bind("<Enter>", lambda e: never_lbl.configure(fg=FG))
        never_lbl.bind("<Leave>", lambda e: never_lbl.configure(fg=MUTED))
        row.pack(side="bottom", fill="x", pady=(14, 0))
        win.after(60, win.lift)                    # visible, but no focus grab, sound or always-on-top

    def restart_app(self):
        if self.running and not messagebox.askyesno(
                "Restart StudyGuard", "This will end your current session. Restart anyway?"):
            return
        self.save_all()
        try:
            if getattr(sys, "frozen", False):
                args = [sys.executable]
            else:
                args = [sys.executable, os.path.abspath(sys.argv[0])]
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
            subprocess.Popen(args, env=env, close_fds=True)
        except Exception as ex:
            messagebox.showinfo("Restart", f"Please close and reopen StudyGuard.\n\n({ex})")
            return
        self.root.destroy()

    # ---- small styled dialog (replaces the plain grey one) ---------------
    def ask_text(self, title, prompt, initial=""):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=CARD)
        win.transient(self.root)
        win.resizable(False, False)
        result = {"v": None}
        f = tk.Frame(win, bg=CARD)
        f.pack(fill="both", expand=True, padx=26, pady=22)
        tk.Label(f, text=title, bg=CARD, fg=FG, font=(DISPLAY, 17)).pack(anchor="w")
        tk.Label(f, text=prompt, bg=CARD, fg=MUTED, font=(SANS, 10)).pack(anchor="w", pady=(4, 12))
        var = tk.StringVar(value=initial)
        e = ttk.Entry(f, textvariable=var, width=36)
        e.pack(fill="x")
        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x", pady=(16, 0))

        def ok():
            result["v"] = var.get()
            win.destroy()
        Btn(row, "Save", ok, kind="accent", bg=CARD, height=36).pack(side="right")
        Btn(row, "Cancel", win.destroy, kind="quiet", bg=CARD, height=36).pack(side="right", padx=(0, 8))
        e.bind("<Return>", lambda ev: ok())
        win.bind("<Escape>", lambda ev: win.destroy())
        win.update_idletasks()
        r = self.root
        x = r.winfo_rootx() + (r.winfo_width() - win.winfo_reqwidth()) // 2
        y = r.winfo_rooty() + (r.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        e.focus_set()
        e.select_range(0, "end")
        try:
            win.grab_set()
        except Exception:
            pass
        self.root.wait_window(win)
        return result["v"]

    def new_park_tab(self):
        name = self.ask_text("New tab", "Name for the new tab:")
        if name and name.strip():
            self.add_park_tab(name.strip())
            self.park_nb.select(len(self.park_texts) - 1)
            self.save_parking()

    def rename_park_tab(self):
        i = self.current_park_index()
        old = self.park_nb.tab(i, "text")
        name = self.ask_text("Rename tab", "New name:", initial=old)
        if name and name.strip():
            self.park_nb.tab(i, text=name.strip())
            self.save_parking()

    # ---- ring ------------------------------------------------------------
    def update_ring(self):
        if self.on_break:
            done = BREAK_SECONDS - self.break_remaining
            self.ring.set(fmt_time(self.break_remaining), 100 * done / BREAK_SECONDS, BLUE, "Break")
        elif self.running:
            total = max(1, self.cfg["minutes"] * 60)
            pct = 100 * (total - self.remaining) / total
            if self.paused:
                sub, col = "Paused", MUTED
            elif self.drift_run > 0:
                sub, col = "Drifting", RED
            else:
                sub, col = "Focus", GREEN
            self.ring.set(fmt_time(self.remaining), pct, col, sub)
        else:
            self.ring.set(fmt_time(self.safe_int(self.min_var, 25) * 60), 0, FG, "Ready")

    # ---- checklist -------------------------------------------------------
    def selected_check(self):
        sel = self.check_tree.selection()
        return int(sel[0]) if sel else None

    def on_check_double(self, event):
        iid = self.check_tree.identify_row(event.y)
        if iid:
            self.toggle_index(int(iid))

    def toggle_selected(self):
        i = self.selected_check()
        if i is not None:
            self.toggle_index(i)

    def toggle_index(self, idx):
        items = self.cfg["checklist"]
        if not 0 <= idx < len(items):
            return
        item = items[idx]
        item["done"] = not item["done"]
        save_json(CFG_PATH, self.cfg)
        self.refresh_checklist()
        self.check_tree.selection_set(str(idx))
        if item["done"] and all(i["done"] for i in items):
            self.notice("Checklist complete", "Every step is ticked off. Well done.")

    def remove_check(self):
        i = self.selected_check()
        if i is not None and i < len(self.cfg["checklist"]):
            del self.cfg["checklist"][i]
            save_json(CFG_PATH, self.cfg)
            self.refresh_checklist()

    def clear_done(self):
        self.cfg["checklist"] = [i for i in self.cfg["checklist"] if not i["done"]]
        save_json(CFG_PATH, self.cfg)
        self.refresh_checklist()

    def refresh_checklist(self):
        tree = self.check_tree
        sel = self.selected_check()
        tree.delete(*tree.get_children())
        items = self.cfg["checklist"]
        for i, it in enumerate(items):
            mark = "✓" if it["done"] else "○"
            tree.insert("", "end", iid=str(i), values=(f"  {mark}    {it['text']}",),
                        tags=("done" if it["done"] else "open",))
        if sel is not None and sel < len(items):
            tree.selection_set(str(sel))
        done = sum(1 for i in items if i["done"])
        self.check_summary.set(f"{done} of {len(items)} done" if items else "No steps yet")
        nxt = next((i["text"] for i in items if not i["done"]), None)
        if nxt:
            self.next_var.set(f"Now: {nxt}")
        elif items:
            self.next_var.set("All steps done. Nice work.")
        else:
            self.next_var.set("Add small steps on the Checklist page")

    # ======================================================================
    # Pop-ups
    # ======================================================================
    def popup(self, title, lines, color, button="OK", timeout=10000, size=(460, 220),
              slot="nag", second=None, tag="StudyGuard"):
        """slot 'nag' = centred pop-up; slot 'well' = toast in the top-right corner."""
        closer = self.close_well if slot == "well" else self.close_nag
        closer()
        win = tk.Toplevel(self.root)
        if slot == "well":
            self.well_window = win
        else:
            self.nag_window = win
        win.title(title)
        win.configure(bg=BORDER)
        win.attributes("-topmost", True)
        w, h = size[0], size[1] + 30
        if slot == "well":
            x, y = win.winfo_screenwidth() - w - 24, 56
        else:
            x = (win.winfo_screenwidth() - w) // 2
            y = (win.winfo_screenheight() - h) // 3
        win.geometry(f"{w}x{h}+{x}+{y}")
        outer = tk.Frame(win, bg=CARD)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        body = tk.Frame(outer, bg=CARD)
        body.pack(fill="both", expand=True, padx=30, pady=24)
        head = tk.Frame(body, bg=CARD)
        head.pack(fill="x")
        dot = tk.Canvas(head, width=9, height=9, bg=CARD, highlightthickness=0)
        dot.create_oval(0, 0, 9, 9, fill=color, outline="")
        dot.pack(side="left")
        tk.Label(head, text=caps(tag), bg=CARD, fg=MUTED, font=semi(7)).pack(side="left", padx=(8, 0))
        tk.Label(body, text=title, bg=CARD, fg=FG, font=(DISPLAY, 21), anchor="w").pack(fill="x", pady=(10, 2))
        for text, tone in lines:
            tk.Label(body, text=text, bg=CARD, fg=FG if tone == "main" else MUTED, font=(SANS, 11),
                     anchor="w", justify="left", wraplength=w - 70).pack(fill="x", pady=(6, 0))
        row = tk.Frame(body, bg=CARD)
        row.pack(side="bottom", anchor="w", pady=(16, 0))
        Btn(row, button, closer, kind="accent", bg=CARD, height=40).pack(side="left")
        if second:
            def do_second():
                second[1]()
                closer()
            Btn(row, second[0], do_second, kind="ghost", bg=CARD, height=40).pack(side="left", padx=(8, 0))
        win.lift()
        if slot != "well":
            win.focus_force()
        self.beep()

        def auto_close():
            current = self.well_window if slot == "well" else self.nag_window
            if current is win:
                closer()
        win.after(timeout, auto_close)

    def save_all(self):
        self.persist()
        if self.running:
            self.tracker.session_end(False, self.focused, self.distracted, self.nag_count)
            self.running = False
        self.tracker.write()
        self.flush_progress()
        self.save_parking()

    def on_close(self):
        self.save_all()
        self.root.destroy()

    # ======================================================================
    # Logic (unchanged from the previous version)
    # ======================================================================
    def apply_topmost(self):
        on = bool(self.top_var.get())
        try:
            self.root.attributes("-topmost", on)
        except Exception:
            pass
        self.cfg["always_on_top"] = on
        save_json(CFG_PATH, self.cfg)

    def toggle_compact(self):
        self.set_compact(not self.compact)

    def set_period(self, key):
        if key == self.cfg["usage_period"]:
            return
        self.cfg["usage_period"] = key
        self.persist()
        for k, b in self.period_btns.items():
            b.configure(kind="accent" if k == key else "ghost")
        self.tracker.restart()          # finished data moves to previous_report.md
        self.refresh_habits()

    def save_report_now(self):
        self.persist()
        self.tracker.write()
        messagebox.showinfo("Report saved", f"Saved to:\n{CUR_MD}")

    def refresh_habits(self):
        if not hasattr(self, "habit_tree"):
            return
        d = self.tracker.data
        T = d["totals"]
        start = dt.datetime.fromisoformat(d["start"])
        end = dt.datetime.fromisoformat(d["end"])
        self.habit_period.set(caps(f"{PERIODS[d['period']]} report") + "     "
                              + f"{start.strftime('%a %d %b %H:%M')} – {end.strftime('%a %d %b %H:%M')}")
        insess = T["focused"] + T["distracted"]
        r = self.habit_rows
        r["focused"].set(_hm(T["focused"]))
        r["distracted"].set(_hm(T["distracted"]))
        r["score"].set(_pct(T["focused"], insess))
        r["outside"].set(_hm(T["outside"]))
        r["away"].set(_hm(T["away"]))
        r["streak"].set(_hm(d["longest_focus"]))
        r["switches"].set(str(d["session_switches"]))
        tree = self.habit_tree
        tree.delete(*tree.get_children())
        apps = sorted(d["apps"].items(), key=lambda kv: -kv[1]["seconds"])[:40]
        for name, v in apps:
            b = v["by_cat"]
            tree.insert("", "end", values=(name, _hm(v["seconds"]), _hm(b.get("focused", 0)),
                                           _hm(b.get("distracted", 0)), _hm(b.get("outside", 0))),
                        tags=("listed",) if v.get("listed") else ())

    def well_interval(self, kind):
        default = next(d for k, _, _, d in WELLNESS if k == kind)
        return self.safe_int(self.well_min[kind], default) * 60

    def reset_wellness(self):
        for kind in self.well_secs:
            self.well_secs[kind] = 0

    def wellness_tick(self):
        self.last_active = time.time()
        due = []
        for kind, label, msg, default in WELLNESS:
            if not self.well_on[kind].get():
                continue
            self.well_secs[kind] += 1
            if self.well_secs[kind] >= self.well_interval(kind):
                due.append(kind)
        if due and self.well_window is None:
            self.show_wellness(due)

    def show_wellness(self, kinds, preview=False):
        info = {k: (label, msg) for k, label, msg, d in WELLNESS}
        title = info[kinds[0]][0] if len(kinds) == 1 else "Time for a quick reset"
        lines = [((info[k][1] if len(kinds) == 1 else info[k][0]), "main") for k in kinds]
        second = None
        if not preview:
            for k in kinds:
                self.well_secs[k] = 0

            def snooze(kinds=tuple(kinds)):
                for k in kinds:
                    self.well_secs[k] = max(0, self.well_interval(k) - 300)
            second = ("Remind me in 5 min", snooze)
        h = 190 + 26 * (len(lines) - 1)
        self.popup(title, lines, BLUE, button="Done", timeout=90000, size=(400, h + 20),
                   slot="well", second=second, tag="Wellness")

    def preview_wellness(self):
        self.show_wellness([k for k, *_ in WELLNESS], preview=True)

    def update_well_labels(self):
        for kind, label, msg, default in WELLNESS:
            if not self.well_on[kind].get():
                txt = "off"
            elif self.running:
                txt = "next in " + fmt_time(max(0, self.well_interval(kind) - self.well_secs[kind]))
            else:
                txt = "starts with your session"
            self.well_next[kind].set(txt)

    def placeholder(self, entry, var, text):
        def on_in(e):
            if var.get() == text:
                var.set("")
                entry.configure(foreground=FG)

        def on_out(e):
            if not var.get().strip():
                var.set(text)
                entry.configure(foreground=MUTED)
        entry.bind("<FocusIn>", on_in)
        entry.bind("<FocusOut>", on_out)
        var.set(text)
        entry.configure(foreground=MUTED)

    def current_park_index(self):
        try:
            return self.park_nb.index(self.park_nb.select())
        except Exception:
            return 0

    def delete_park_tab(self):
        if len(self.park_texts) <= 1:
            messagebox.showinfo("Parking lot", "You need to keep at least one tab.")
            return
        i = self.current_park_index()
        name = self.park_nb.tab(i, "text")
        if messagebox.askyesno("Delete tab", f"Delete the tab \"{name}\" and everything in it?"):
            self.park_nb.forget(i)
            del self.park_texts[i]
            self.save_parking()

    def collect_parking(self):
        tabs = []
        for i, txt in enumerate(self.park_texts):
            tabs.append({"name": self.park_nb.tab(i, "text"), "text": txt.get("1.0", "end-1c")})
        return {"tabs": tabs}

    def save_parking(self):
        try:
            self.parking = self.collect_parking()
            save_json(PARK_PATH, self.parking)
        except Exception:
            pass

    def save_tab_txt(self):
        i = self.current_park_index()
        name = self.park_nb.tab(i, "text")
        safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_").strip() or "parking-lot"
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save tab as .txt", defaultextension=".txt",
            filetypes=[("Text file", "*.txt")], initialfile=f"{safe}.txt")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.park_texts[i].get("1.0", "end-1c") + "\n")
            self.park_status.set(f"Saved: {path}")
        except Exception as ex:
            messagebox.showerror("Could not save", str(ex))

    def save_all_txt(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save all tabs as .txt", defaultextension=".txt",
            filetypes=[("Text file", "*.txt")], initialfile="parking-lot.txt")
        if not path:
            return
        try:
            data = self.collect_parking()
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"StudyGuard parking lot - {dt.date.today().isoformat()}\n\n")
                for t in data["tabs"]:
                    f.write(f"=== {t['name']} ===\n{t['text']}\n\n")
            self.park_status.set(f"Saved: {path}")
        except Exception as ex:
            messagebox.showerror("Could not save", str(ex))

    def quick_park(self):
        s = self.quick_var.get().strip()
        if not s or s == PARK_PLACEHOLDER:
            return
        txt = self.park_texts[self.current_park_index()]
        content = txt.get("1.0", "end-1c")
        prefix = "\n" if content and not content.endswith("\n") else ""
        txt.insert("end", f"{prefix}- [{time.strftime('%H:%M')}] {s}\n")
        txt.see("end")
        self.quick_var.set("")
        self.park_btn.configure(text="Parked")
        self.root.after(1500, lambda: self.park_btn.configure(text="Park it"))
        self.save_parking()

    def read_lists(self):
        def lines(widget):
            return [l.strip().lower() for l in widget.get("1.0", "end").splitlines() if l.strip()]
        w = self.list_widgets
        return lines(w["distractions"]), lines(w["allowed"]), lines(w["strict_apps"])

    def safe_int(self, var, default, lo=1):
        try:
            return max(lo, int(var.get()))
        except Exception:
            return default

    def goal_secs(self):
        return self.safe_int(self.dgoal_var, 240) * 60

    def persist(self):
        dist, allow, strict_apps = self.read_lists()
        self.cfg.update({
            "goal": self.goal_var.get().strip() or "Study",
            "minutes": self.safe_int(self.min_var, 25),
            "grace_seconds": self.safe_int(self.grace_var, 10),
            "nag_cooldown_seconds": self.safe_int(self.cool_var, 60),
            "distractions": dist,
            "allowed": allow,
            "strict_apps": strict_apps,
            "strict": bool(self.strict_var.get()),
            "daily_goal_minutes": self.safe_int(self.dgoal_var, 240),
            "always_on_top": bool(self.top_var.get()),
            "compact_on_start": bool(self.compact_var.get()),
            "track_usage": bool(self.track_var.get()),
            "track_outside": bool(self.track_out_var.get()),
            "hide_titles": bool(self.hide_titles_var.get()),
            "usage_day_start": self.safe_int(self.day_start_var, 4, lo=0) % 24,
        })
        for kind, label, msg, default in WELLNESS:
            self.cfg[f"{kind}_on"] = bool(self.well_on[kind].get())
            self.cfg[f"{kind}_min"] = self.safe_int(self.well_min[kind], default)
        save_json(CFG_PATH, self.cfg)

    def on_strict(self):
        self.cfg["strict"] = bool(self.strict_var.get())
        save_json(CFG_PATH, self.cfg)

    def set_status(self, text, color):
        self.status_var.set(text)
        self.status_lbl.config(foreground=color)

    def focus_text(self):
        return f"Focus: {self.cfg['goal']}" + ("  (strict)" if self.strict_var.get() else "")

    def add_check(self):
        s = self.check_var.get().strip()
        if not s or s == CHECK_PLACEHOLDER:
            return
        self.cfg["checklist"].append({"text": s, "done": False})
        self.check_var.set("")
        save_json(CFG_PATH, self.cfg)
        self.refresh_checklist()

    def add_exam(self):
        name = self.exam_name_var.get().strip()
        d = parse_date(self.exam_date_var.get())
        if not name or d is None:
            messagebox.showinfo("Exam countdown",
                                "Enter an exam name and a date like 2026-11-20 or 20/11/2026.")
            return
        self.cfg["exams"].append({"name": name, "date": d.isoformat()})
        self.cfg["exams"].sort(key=lambda e: e["date"])
        self.exam_name_var.set("")
        self.exam_date_var.set("")
        save_json(CFG_PATH, self.cfg)
        self.refresh_exams()

    def remove_exam(self):
        sel = self.exam_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.cfg["exams"]):
            del self.cfg["exams"][idx]
            save_json(CFG_PATH, self.cfg)
            self.refresh_exams()

    @staticmethod
    def days_text(days):
        if days < 0:
            return "Passed"
        if days == 0:
            return "Today!"
        return "1 day" if days == 1 else f"{days} days"

    def refresh_exams(self):
        for r in self.exam_tree.get_children():
            self.exam_tree.delete(r)
        today = dt.date.today()
        self.cfg["exams"].sort(key=lambda e: e["date"])
        upcoming = None
        for i, e in enumerate(self.cfg["exams"]):
            try:
                d = dt.date.fromisoformat(e["date"])
            except Exception:
                continue
            days = (d - today).days
            self.exam_tree.insert("", "end", iid=str(i),
                                  values=(e["name"], d.strftime("%a %d %b %Y"), self.days_text(days)))
            if days >= 0 and upcoming is None:
                upcoming = (e["name"], days, d)
        if upcoming:
            name, days, d = upcoming
            when = "today" if days == 0 else f"in {self.days_text(days)}"
            self.exam_banner.set(f"Next exam: {name} {when}  ({d.strftime('%a %d %b')})")
        else:
            self.exam_banner.set("Add your exam dates/deadlines on the Goals page")

    def today_total(self):
        return self.progress["days"].get(dt.date.today().isoformat(), 0) + self.unsaved

    def streak(self):
        goal = self.goal_secs()
        days = self.progress["days"]
        d = dt.date.today()
        n = 1 if self.today_total() >= goal else 0
        d -= dt.timedelta(days=1)
        while days.get(d.isoformat(), 0) >= goal:
            n += 1
            d -= dt.timedelta(days=1)
        return n

    def update_daily(self):
        goal = self.goal_secs()
        total = self.today_total()
        pct = min(100, 100 * total / goal) if goal else 0
        txt = f"{fmt_hm(total)} of your {fmt_hm(goal)} goal"
        self.daily_var.set(txt)
        self.daily_var2.set(txt)
        self.daily_bar.set(pct)
        self.daily_bar2.set(pct)
        self.side_today.set(f"{fmt_hm(total)} of {fmt_hm(goal)}")
        self.side_bar.set(pct)
        n = self.streak()
        self.streak_var.set(f"Goal streak: {n} day{'s' if n != 1 else ''}" +
                            ("  -  goal reached today!" if total >= goal else ""))

    def flush_progress(self):
        if self.unsaved:
            key = dt.date.today().isoformat()
            self.progress["days"][key] = self.progress["days"].get(key, 0) + self.unsaved
            self.unsaved = 0
        save_json(PROG_PATH, self.progress)

    def autosave(self):
        try:
            self.persist()
            self.flush_progress()
            self.save_parking()
            self.refresh_exams()
            self.tracker.write()
        finally:
            self.root.after(30000, self.autosave)

    def start(self):
        self.persist()
        self.remaining = self.cfg["minutes"] * 60
        self.focused = self.distracted = self.nag_count = self.drift_run = 0
        self.last_nag = 0.0
        self.running, self.paused = True, False
        self.on_break = False
        self.break_times = []
        if self.today_total() >= self.goal_secs():
            self.goal_notified = dt.date.today().isoformat()   # already met earlier today
        self.break_btn.configure(state="normal", text="Take a 5-min break")
        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Pause")
        self.stop_btn.configure(state="normal")
        self.set_status(self.focus_text(), GREEN)
        self.update_stats()
        self.update_ring()
        self.tracker.session_start(self.cfg["goal"], self.cfg["minutes"])
        if time.time() - self.last_active > 1800:
            self.reset_wellness()      # you've been away: start the clocks fresh
        if self.cfg.get("compact_on_start", True):
            self.set_compact(True)

    def toggle_pause(self):
        if not self.running or self.on_break:
            return
        self.paused = not self.paused
        self.pause_btn.configure(text="Resume" if self.paused else "Pause")
        if self.paused:
            self.set_status("Paused", MUTED)
        else:
            self.set_status(self.focus_text(), GREEN)
        self.drift_run = 0
        self.update_ring()

    def breaks_left(self):
        now = time.time()
        self.break_times = [b for b in self.break_times if now - b < 3600]
        return max(0, MAX_BREAKS_PER_HOUR - len(self.break_times))

    def toggle_break(self):
        if not self.running or self.paused:
            return
        if self.on_break:
            self.end_break()
            return
        if self.breaks_left() <= 0:
            wait = 3600 - (time.time() - min(self.break_times))
            messagebox.showinfo(
                "No breaks left",
                f"You've used all {MAX_BREAKS_PER_HOUR} breaks for this hour.\n"
                f"Your next break unlocks in {fmt_time(wait)}.",
            )
            return
        self.break_times.append(time.time())
        self.on_break = True
        self.break_remaining = BREAK_SECONDS
        self.drift_run = 0
        self.close_nag()
        self.break_btn.configure(text="End break early")
        self.pause_btn.configure(state="disabled")
        self.update_stats()
        self.update_ring()

    def end_break(self, auto=False):
        self.on_break = False
        self.drift_run = 0
        self.break_btn.configure(text="Take a 5-min break")
        self.pause_btn.configure(state="normal")
        self.set_status(self.focus_text(), GREEN)
        if auto:
            self.notice("Break's over!", f"Back to: {self.cfg['goal']}")
        self.update_stats()
        self.update_ring()

    def stop(self, finished=False):
        was_running = self.running
        self.running = self.paused = False
        self.on_break = False
        self.break_btn.configure(state="disabled", text="Take a 5-min break")
        self.close_nag()
        self.set_compact(False)
        self.flush_progress()
        if was_running:
            self.tracker.session_end(finished, self.focused, self.distracted, self.nag_count)
        self.start_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="Pause")
        self.stop_btn.configure(state="disabled")
        self.set_status("Session complete!" if finished else "Stopped. Ready when you are.", MUTED)
        self.update_daily()
        self.update_ring()
        if was_running and finished:
            total = self.focused + self.distracted
            pct = int(100 * self.focused / total) if total else 100
            items = self.cfg["checklist"]
            done = sum(1 for i in items if i["done"])
            goal = self.goal_secs()
            today = self.today_total()
            lines = [
                "Session finished.\n",
                f"Focused: {fmt_time(self.focused)}",
                f"Distracted: {fmt_time(self.distracted)}",
                f"Reminders: {self.nag_count}",
                f"Focus score: {pct}%",
            ]
            if items:
                lines.append(f"Checklist: {done}/{len(items)} steps done")
            lines.append(f"Daily goal: {fmt_hm(today)} of {fmt_hm(goal)} "
                         f"({min(100, int(100 * today / goal)) if goal else 0}%)")
            messagebox.showinfo("Nice work!", "\n".join(lines))

    def tick(self):
        self.sec_n += 1
        tracking = bool(self.track_var.get())
        track_now = tracking and (self.running or self.track_out_var.get())
        if (self.running or track_now) and self.sec_n % 2 == 1:
            self.current_title = get_active_window()
        cat = "outside"

        if self.running and self.on_break:
            cat = "break"
            # the session timer is frozen while you're on a break
            self.break_remaining -= 1
            left = fmt_time(self.break_remaining)
            self.set_status(f"On a break  ·  {left}", BLUE)
            self.update_stats()
            if self.break_remaining <= 0:
                self.end_break(auto=True)
            else:
                self.update_ring()
        elif self.running and not self.paused:
            self.tick_n += 1
            self.remaining -= 1
            title = self.current_title
            self.window_var.set(f"Current window: {title[:70] if title else '(unknown)'}")

            if self.is_distraction(title):
                cat = "distracted"
                self.distracted += 1
                self.drift_run += 1
                self.set_status(f"Drifting...  ({self.drift_run}s)", RED)
                if (self.drift_run >= self.cfg["grace_seconds"]
                        and time.time() - self.last_nag >= self.cfg["nag_cooldown_seconds"]):
                    self.nag(title)
            else:
                cat = "focused"
                self.focused += 1
                self.unsaved += 1
                self.drift_run = 0
                self.set_status(self.focus_text(), GREEN)
                self.update_daily()
                today = dt.date.today().isoformat()
                if self.goal_notified != today and self.today_total() >= self.goal_secs():
                    self.goal_notified = today
                    self.notice("Daily goal reached!",
                                f"You've focused for {fmt_hm(self.today_total())} today. Brilliant.")

            self.update_stats()
            if self.remaining <= 0:
                self.stop(finished=True)
            else:
                self.update_ring()
        elif self.paused:
            cat = "paused"

        if self.running and not self.paused:
            self.wellness_tick()
        self.update_well_labels()
        if self.sec_n % 60 == 0:
            self.update_greeting()
        if self.sec_n == 8 or self.sec_n % 600 == 0:
            self.maybe_show_support()

        if track_now:
            self.track_second(cat)
        if self.sec_n % 5 == 0:
            self.refresh_habits()

        self.root.after(1000, self.tick)

    def track_second(self, cat):
        """Add this second to the habit tracker."""
        try:
            self.tracker.check_rollover()
            if cat == "outside":
                if self.sec_n % 5 == 0:
                    self.idle_secs = get_idle_seconds() or 0.0
                if self.idle_secs >= IDLE_LIMIT:
                    cat = "away"
            title = self.current_title
            dist, allow, _ = self.read_lists()
            t = (title or "").lower()
            listed = bool(t) and not any(a in t for a in allow) and any(d in t for d in dist)
            self.tracker.record(title, cat, listed)
        except Exception:
            pass

    def is_distraction(self, title):
        t = (title or "").lower().strip()
        if not t:
            return False
        dist, allow, strict_apps = self.read_lists()
        if self.strict_var.get():
            return not any(w in t for w in strict_apps + allow)
        if any(a in t for a in allow):
            return False
        return any(d in t for d in dist)

    def update_stats(self):
        v = self.stat_vars
        v["focused"].set(fmt_time(self.focused))
        v["distracted"].set(fmt_time(self.distracted))
        v["reminders"].set(str(self.nag_count))
        v["breaks"].set(f"{self.breaks_left()}/{MAX_BREAKS_PER_HOUR}")

    def close_well(self):
        if self.well_window is not None:
            try:
                self.well_window.destroy()
            except Exception:
                pass
            self.well_window = None

    def nag(self, title):
        self.last_nag = time.time()
        self.nag_count += 1
        lines = [(f"Your goal: {self.cfg['goal']}", "main")]
        nxt = next((i["text"] for i in self.cfg["checklist"] if not i["done"]), None)
        if nxt:
            lines.append((f"Next step: {nxt[:70]}", "main"))
        lines.append((f"You drifted to: {title[:70]}", "muted"))
        self.popup("Back to studying", lines, RED, button="OK, back to it",
                   timeout=15000, size=(500, 270 if nxt else 240), tag="Focus check")

    def notice(self, title, line, color=GREEN):
        self.popup(title, [(line, "main")], color, size=(460, 200))

    def close_nag(self):
        if self.nag_window is not None:
            try:
                self.nag_window.destroy()
            except Exception:
                pass
            self.nag_window = None

    def beep(self):
        try:
            if platform.system() == "Windows":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            else:
                self.root.bell()
        except Exception:
            pass


def main():
    if platform.system() == "Windows":
        try:   # own taskbar icon instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("StudyGuard.App")
        except Exception:
            pass
    root = tk.Tk()
    apply_theme(root)
    StudyGuard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
