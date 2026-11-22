import audioop
import ctypes
import copy
import json
import os
import queue
import re
import shutil
import sqlite3
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, ttk

import httpx
import pyaudiowpatch as pyaudio
from tkinterdnd2 import DND_FILES, TkinterDnD

from audio_capture import NoAudio, PlaybackCapture, playback_devices
from endpoint import default_playback_id
from hotkey import DEFAULT, Hotkey, Shortcut, load_shortcut, save_shortcut, settings_path
from deepseek_client import DeepSeekClient
from dashscope_client import DashScopeClient
from openai_client import ANSWER_LENGTH_INSTRUCTIONS, STANDARD_ANSWER_LENGTH, ApiError, OpenAIClient
from profile_text import MAX_TEXT, read_profile
from question_review import review_question
from session import InterviewSession, MemoryStore
from live_workflow import CaptionTimeline, QuestionComposer, TurnLedger, WordTiming
from realtime_asr import DashScopeRealtimeASR
from realtime_audio import BoundedAudioQueue
from secure_store import SecureStore, SecretStoreError
from window_privacy import native_window, set_privacy

BG = "#F7F5F0"
PANEL = "#FFFDF9"
FIELD = "#FFFFFF"
BORDER = "#DAD7D0"
INK = "#252A32"
MUTED = "#5D636D"
ACCENT = "#944630"
FOCUS = "#B17863"
ACTIVE = "#803A28"
NAV_BG = "#222831"
NAV_ACTIVE = "#343D49"
NAV_TEXT = "#D6DCE4"
ANSWER_BG = "#20252D"
ANSWER_TEXT = "#F2F1ED"
ANSWER_MUTED = "#B9C0CA"
ANSWER_ACCENT = "#E4AF90"


def style_window(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".", font=("Microsoft YaHei UI", 12), foreground=INK, background=PANEL,
        fieldbackground=FIELD, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
        troughcolor=FIELD, selectbackground=ACCENT, selectforeground=INK,
        insertcolor=INK, arrowcolor=MUTED,
    )
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG)
    for name in ("TButton", "Primary.TButton"):
        style.configure(name, background=PANEL, foreground=INK, padding=(10, 6), width=0)
        style.map(name,
                  background=[("disabled", BG), ("pressed", "#E7DED6"), ("active", "#EFE7E0")],
                  foreground=[("disabled", "#8E8A84")],
                  bordercolor=[("focus", FOCUS)],
                  lightcolor=[("pressed", BORDER)], darkcolor=[("pressed", BORDER)])
    style.configure("TEntry", padding=6, bordercolor="#8E8A84")
    style.configure("TCheckbutton", background=BG, indicatorbackground=FIELD, indicatorforeground=INK)
    style.map("TCheckbutton", background=[("active", ACTIVE)],
              indicatorbackground=[("selected", ACCENT), ("pressed", ACTIVE)])
    style.configure("TCombobox", padding=4)
    style.map("TCombobox",
              background=[("disabled", FIELD), ("active", PANEL), ("readonly", PANEL)],
              fieldbackground=[("readonly", FIELD), ("disabled", FIELD)],
              foreground=[("readonly", INK), ("disabled", MUTED)],
              selectbackground=[("readonly", FIELD)],
              selectforeground=[("readonly", INK)],
              bordercolor=[("focus", FOCUS)])
    style.map("TEntry", bordercolor=[("focus", FOCUS)])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(13, 6))
    style.map("TNotebook.Tab",
              background=[("selected", ACCENT), ("active", ACTIVE)],
              foreground=[("selected", INK), ("!selected", MUTED)],
              bordercolor=[("focus", FOCUS)], lightcolor=[("selected", BORDER)])
    style.layout("Hidden.TNotebook.Tab", [])
    style.configure("Hidden.TNotebook", background=BG, borderwidth=0, tabmargins=0)
    style.configure("TScrollbar", background=BORDER)
    style.map("TScrollbar", background=[("active", FOCUS), ("pressed", FOCUS)])
    style.configure("Horizontal.TProgressbar", background=FOCUS, thickness=5)
    style.configure("Nav.TButton", background=NAV_BG, foreground=NAV_TEXT,
                    anchor="w", padding=(13, 10), borderwidth=0)
    style.map("Nav.TButton", background=[("active", NAV_ACTIVE), ("pressed", NAV_ACTIVE)],
              foreground=[("disabled", "#8B929D")])
    style.configure("NavActive.TButton", background=NAV_ACTIVE, foreground="#FFFFFF",
                    anchor="w", padding=(13, 10), borderwidth=0)
    style.map("NavActive.TButton", background=[("active", NAV_ACTIVE), ("pressed", NAV_ACTIVE)])
    style.configure("NavIcon.TButton", background=NAV_BG, foreground=NAV_TEXT,
                    anchor="center", padding=(4, 10), borderwidth=0)
    style.configure("NavIconActive.TButton", background=NAV_ACTIVE, foreground="#FFFFFF",
                    anchor="center", padding=(4, 10), borderwidth=0)
    style.map("NavIcon.TButton", background=[("active", NAV_ACTIVE), ("pressed", NAV_ACTIVE)])
    style.map("NavIconActive.TButton", background=[("active", NAV_ACTIVE), ("pressed", NAV_ACTIVE)])
    style.configure("Primary.TButton", background=ACCENT, foreground="#FFFFFF",
                    padding=(16, 9), font=("Microsoft YaHei UI", 11, "bold"))
    style.map("Primary.TButton", background=[("pressed", ACTIVE), ("active", ACTIVE)],
              foreground=[("disabled", "#F1E7E2")])
    style.configure("Floating.TButton", background="#343D49", foreground=ANSWER_TEXT, padding=(6, 6),
                    width=0, font=("Microsoft YaHei UI", 9))
    style.map("Floating.TButton", background=[("active", "#485260"), ("pressed", "#485260")])
    style.configure("Floating.Primary.TButton", background=ANSWER_ACCENT, foreground="#29241F",
                    padding=(10, 7), width=0, font=("Microsoft YaHei UI", 10, "bold"))
    style.map("Floating.Primary.TButton", background=[("active", "#D39C7D"), ("pressed", "#D39C7D")])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", INK)


def style_title_bar(root, surface=BG):
    window = native_window(root)
    set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
    set_attribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    set_attribute.restype = ctypes.c_long
    dark = wintypes.BOOL(False)
    set_attribute(window, 20, ctypes.byref(dark), ctypes.sizeof(dark))
    for attribute, color in ((34, BORDER), (35, surface), (36, INK)):
        colorref = wintypes.DWORD(int.from_bytes(bytes.fromhex(color[1:]), "little"))
        set_attribute(window, attribute, ctypes.byref(colorref), ctypes.sizeof(colorref))


PROVIDERS = {"DeepSeek": (DeepSeekClient, "DEEPSEEK_API_KEY"),
             "OpenAI": (OpenAIClient, "OPENAI_API_KEY"),
             "阿里云百炼": (DashScopeClient, "DASHSCOPE_API_KEY")}


class DraftText(tk.Text):
    def get(self, index1="1.0", index2="end-1c"):
        return super().get(index1, index2)


def select_startup_provider(configured_keys, *, preferred=None, workspace_id=""):
    if preferred in PROVIDERS and configured_keys.get(preferred):
        return preferred
    if configured_keys.get("阿里云百炼") and str(workspace_id).strip():
        return "阿里云百炼"
    for provider in ("DeepSeek", "OpenAI", "阿里云百炼"):
        if configured_keys.get(provider):
            return provider
    return "阿里云百炼"


class AudioQA:
    def __init__(self, root, api_factory=None, shortcut_path=None, memory_path=None):
        self.root = root
        self.api_factory = api_factory
        self.capture = None
        self.hotkey = None
        self.shortcut_path = shortcut_path
        self.shortcut = load_shortcut(shortcut_path)
        self.recording_hotkey = False
        self.capture_change = threading.Lock()
        self.closing = False
        self.capture_attempt = 0
        self.listening_pending = False
        self.paused = False
        self.listening_panel = None
        self.default_capture = False
        self.playback_id = None
        self.next_device_poll = 0
        self.events = queue.SimpleQueue()
        self.audio_chunks = BoundedAudioQueue(max_chunks=60)
        self.realtime_asr = None
        self.asr_epoch = 0
        self.asr_state = "not_configured"
        self.asr_started_at = 0.0
        self.caption_timeline = CaptionTimeline("未开始")
        self.question_composer = QuestionComposer(self.caption_timeline)
        self.turn_ledger = TurnLedger()
        self.live_turn_id = None
        self.selected_caption_ids = set()
        self.caption_lines = {}
        self.active_request_ids = set()
        self.request_turn_map = {}
        self.request_session_tokens = {}
        self.request_modes = {}
        self.cancelled_request_ids = set()
        self.session_token = 0
        self.live_transcript = ""
        self.transcript_dirty = False
        self.asr_sentence_fallback = 0
        self.devices = {}
        self.busy = False
        self.session = None
        self.request_id = 0
        self.retry_replaces_last = False
        self.update_latency = None
        self.compact = False
        self.ui_scale = float(root.tk.call("tk", "scaling")) / (96 / 72)
        self.privacy = tk.BooleanVar(master=root, value=False)
        self.privacy_caption = tk.StringVar(master=root, value="截图保护")
        self.timing = {}
        self.source_excerpts = []
        self.selected_turn_id = None
        self.pinned_turn = None
        self.history_rows = []
        self.review_original = ""
        self.review_replacement = ""
        self.audio_check = None
        self.memory_rows = []
        self.memory = None
        self.preferences = {}
        self.secret_store = None
        try:
            self.secret_store = SecureStore(settings_path().with_name("secrets.dat"))
        except (OSError, SecretStoreError):
            pass
        try:
            self.memory = MemoryStore(memory_path or settings_path().with_name("memory.db"))
            self.preferences = self.memory.load_preferences()
        except (OSError, sqlite3.Error):
            pass

        self.personal_resume = None
        self.selected_resume = None
        self.resume_import_id = 0
        self.resume_loading = False
        self.current_saved_interview_id = None
        self.saved_turn_snapshot = ()
        self.panel_geometry_after = None
        self.session_metadata = {}
        self.settings_return_tab = None
        if self.memory is not None:
            try:
                self.personal_resume = self.memory.load_resume()
            except (ValueError, sqlite3.Error):
                self.personal_resume = None

        root.title("系统音频问答")
        self.size_window()
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style_window(root)
        try:
            TkinterDnD._require(root)
            self.dnd_ready = True
        except (tk.TclError, RuntimeError):
            self.dnd_ready = False
        root.bind("<Map>", lambda event: style_title_bar(root) if event.widget is root else None, add="+")

        shell = tk.Frame(root, bg=BG)
        shell.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(shell, bg=NAV_BG, width=210)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.brand_label = tk.Label(self.sidebar, text="系统音频问答", bg=NAV_BG, fg="#F2F1ED",
                                    font=("Microsoft YaHei UI", 13, "bold"), anchor="w")
        self.brand_label.pack(fill="x", pady=(22, 26))
        self.sidebar_main = tk.Frame(self.sidebar, bg=NAV_BG)
        self.sidebar_main.pack(fill="x", padx=8)
        self.nav_buttons = {}
        self._make_nav_button(self.sidebar_main, "prepare", "◈", "面试准备", lambda: self.navigate("prepare"))
        self._make_nav_button(self.sidebar_main, "documents", "▤", "我的资料", lambda: self.navigate("documents"))
        self._make_nav_button(self.sidebar_main, "records", "≡", "面试记录", lambda: self.navigate("records"))
        self.sidebar_settings = tk.Frame(self.sidebar, bg=NAV_BG)
        self.sidebar_settings.pack(side="bottom", fill="x", padx=8, pady=(0, 12))
        self.settings_subnav = tk.Frame(self.sidebar, bg=NAV_BG)
        self._make_nav_button(self.settings_subnav, "setting_audio", "◖", "声音与转写",
                              lambda: self.select_settings_category("audio"))
        self._make_nav_button(self.settings_subnav, "setting_hotkey", "⌘", "快捷键",
                              lambda: self.select_settings_category("hotkey"))
        self._make_nav_button(self.settings_subnav, "setting_display", "▣", "浮窗与显示",
                              lambda: self.select_settings_category("display"))
        self._make_nav_button(self.settings_subnav, "setting_model", "◇", "模型与连接",
                              lambda: self.select_settings_category("model"))
        self.settings_back_button = ttk.Button(self.settings_subnav, text="返回", style="Nav.TButton",
                                               command=self.leave_settings)
        self.settings_back_button.pack(fill="x", pady=(8, 0))
        self.sidebar_hint = tk.Label(self.sidebar, text="面试准备", bg=NAV_BG, fg="#AEB8C4",
                                     font=("Microsoft YaHei UI", 9), anchor="w")
        self.sidebar_hint.pack(side="bottom", fill="x", pady=(0, 10))
        self._make_nav_button(self.sidebar_settings, "settings", "⚙", "设置",
                              lambda: self.navigate("settings"))

        content = tk.Frame(shell, bg=BG)
        content.pack(side="right", fill="both", expand=True)
        page_heading = tk.Frame(content, bg=BG)
        page_heading.pack(fill="x", padx=30, pady=(22, 8))
        self.header = ttk.Label(page_heading, text="面试准备", font=("Microsoft YaHei UI", 21, "bold"))
        self.header.pack(anchor="w")

        toolbar = self.toolbar = ttk.Frame(content)
        self.start_button = ttk.Button(toolbar, text="开始监听", command=self.toggle_capture)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(toolbar, text="暂停听题", command=self.toggle_pause)
        self.pause_button.pack(side="left", padx=(8, 0))
        self.ask_button = ttk.Button(toolbar, text="回答这题", style="Primary.TButton",
                                     command=self.request_audio)
        self.ask_button.pack(side="left", padx=(8, 0))
        ttk.Label(toolbar, text="回听").pack(side="left", padx=(15, 5))
        self.window = tk.StringVar(value="25 秒")
        ttk.Combobox(toolbar, textvariable=self.window, values=("10 秒", "15 秒", "25 秒", "40 秒"),
                     width=7, state="readonly").pack(side="left")
        self.meter = ttk.Progressbar(toolbar, maximum=100, length=75)
        self.meter.pack(side="right")
        ttk.Label(toolbar, text="播放电平", foreground=MUTED).pack(side="right", padx=6)
        self.capture_label = ttk.Label(toolbar, text="未缓冲", foreground=MUTED, wraplength=650)

        self.tabs = ttk.Notebook(content, style="Hidden.TNotebook")
        self.qa_tab = ttk.Frame(self.tabs, padding=(22, 16))
        self.history_tab = ttk.Frame(self.tabs, padding=(22, 16))
        self.profile_tab = ttk.Frame(self.tabs, padding=(22, 16))
        self.documents_tab = ttk.Frame(self.tabs, padding=(22, 16))
        self.records_tab = ttk.Frame(self.tabs, padding=(22, 16))
        self.settings_tab = ttk.Frame(self.tabs, padding=(22, 16))
        self.memory_tab = ttk.Frame(self.tabs, padding=(22, 16))
        cards_tab = self.cards_tab = ttk.Frame(self.tabs, padding=(22, 16))
        pages = ((self.profile_tab, "面试准备"), (self.documents_tab, "我的资料"),
                 (self.records_tab, "面试记录"), (self.qa_tab, "本场面试"),
                 (self.history_tab, "本场回看"), (cards_tab, "口述卡"),
                 (self.memory_tab, "记忆与反馈"), (self.settings_tab, "设置"))
        for page, title in pages:
            self.tabs.add(page, text=title)

        self.build_qa()
        self.build_history()
        self.build_profile()
        self.build_resume_page()
        self.build_records_page()
        ttk.Label(cards_tab, text="每行填写「问题 => 你已核实、可以直接说的回答」。开始本场后生效。\n"
                  "快速模式命中同一问题时显示原稿。修改事实后，请核对稿子再开始新场。",
                  foreground=MUTED, wraplength=455).pack(anchor="w")
        self.answer_cards = self.text_box(cards_tab, 7, expand=True)
        self.build_memory()
        self.build_settings()
        self.root.update_idletasks()

        self.status = ttk.Label(content, text="请在设置里配置云服务 API Key。",
                                foreground=MUTED, wraplength=850)
        self.status.pack(side="bottom", fill="x", anchor="w", padx=30, pady=(8, 14))
        self.tabs.pack(fill="both", expand=True, padx=30, pady=(0, 8))
        self.tabs.bind("<<NotebookTabChanged>>", self.on_page_changed, add="+")
        root.bind("<Configure>", self._responsive_sidebar, add="+")
        self.refresh_devices()
        self.update_buttons()
        self.refresh_memory()
        self.navigate("prepare")
        root.bind("<Control-Alt-Up>", lambda _: self.keyboard_history(1))
        root.bind("<Control-Alt-Down>", lambda _: self.keyboard_history(-1))
        self.tick_timer = root.after(35, self.tick)

    def _make_nav_button(self, parent, key, icon, label, command):
        row = tk.Frame(parent, bg=NAV_BG)
        row.pack(fill="x", pady=2)
        marker = tk.Frame(row, bg=NAV_BG, width=3)
        marker.pack(side="left", fill="y")
        button = ttk.Button(row, text=f"{icon}   {label}", style="Nav.TButton", command=command)
        button.pack(side="right", fill="x", expand=True)
        button.bind("<Enter>", lambda _event, text=label: self._set_sidebar_hint(text), add="+")
        button.bind("<FocusIn>", lambda _event, text=label: self._set_sidebar_hint(text), add="+")
        self.nav_buttons[key] = (button, marker, icon, label)

    def _set_sidebar_hint(self, text):
        if hasattr(self, "sidebar_hint"):
            self.sidebar_hint.configure(text=text)

    def _set_navigation(self, active):
        self.active_nav = active
        for key, (button, marker, icon, label) in self.nav_buttons.items():
            selected = key == active
            if getattr(self, "narrow_sidebar", False):
                style = "NavIconActive.TButton" if selected else "NavIcon.TButton"
            else:
                style = "NavActive.TButton" if selected else "Nav.TButton"
            button.configure(style=style)
            marker.configure(bg=ACCENT if selected else NAV_BG)
        if active in self.nav_buttons:
            self._set_sidebar_hint(self.nav_buttons[active][3])

    def _responsive_sidebar(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        width = self.root.winfo_width() if event is None else event.width
        narrow = width / max(self.ui_scale, 0.1) < 840
        self.narrow_sidebar = narrow
        sidebar_width = round((60 if narrow else 210) * self.ui_scale)
        if self.sidebar.winfo_width() != sidebar_width:
            self.sidebar.configure(width=sidebar_width)
        self.brand_label.configure(
            text="音频" if narrow else "系统音频问答",
            font=("Microsoft YaHei UI", 9 if narrow else 13, "bold"),
            padx=4 if narrow else 16,
            anchor="center" if narrow else "w",
        )
        self.sidebar_hint.configure(
            wraplength=round(52 * self.ui_scale) if narrow else 0,
            anchor="center" if narrow else "w",
            font=("Microsoft YaHei UI", 8 if narrow else 9),
            padx=4 if narrow else 16,
        )
        self.settings_back_button.configure(text="↩" if narrow else "返回",
                                            style="NavIcon.TButton" if narrow else "Nav.TButton")
        self.header.master.pack_configure(padx=18 if narrow else 30)
        for button, _marker, icon, label in self.nav_buttons.values():
            button.configure(text=icon if narrow else f"{icon}   {label}")
        self._set_navigation(getattr(self, "active_nav", "prepare"))
        if hasattr(self, "resume_drop_label"):
            self.resume_drop_label.configure(wraplength=max(220, width - sidebar_width - 150))
        if hasattr(self, "saved_records_list") and self.saved_interviews and narrow != self.records_narrow:
            self.records_narrow = narrow
            if narrow:
                self.close_record_detail()
            else:
                self.saved_records_list.pack(side="left", fill="y", padx=(0, 16))
                self.show_saved_interview()
        if hasattr(self, "audio_source_label"):
            self.identity_fields[0].grid_configure(columnspan=2 if narrow else 1)
            self.identity_fields[1].grid_configure(row=1 if narrow else 0, column=0 if narrow else 1,
                                                   columnspan=2 if narrow else 1)
            if narrow:
                self.preflight_status.configure(wraplength=max(180, width - sidebar_width - round(210 * self.ui_scale)))
                self.preflight_status.grid_configure(row=0, column=1, columnspan=2, sticky="ew", padx=0)
                self.audio_check_button.grid_configure(row=1, column=0, sticky="w", padx=0, pady=(5, 0))
            else:
                self.preflight_status.configure(wraplength=round(560 * self.ui_scale))
                self.preflight_status.grid_configure(row=0, column=1, columnspan=1, sticky="ew", padx=0)
                self.audio_check_button.grid_configure(row=0, column=2, sticky="e", padx=(10, 0), pady=0)

    def navigate(self, page):
        pages = {"prepare": self.profile_tab, "documents": self.documents_tab,
                 "records": self.records_tab, "settings": self.settings_tab}
        if page != "settings":
            self.settings_subnav.pack_forget()
        if page == "settings" and self.tabs.select() != str(self.settings_tab):
            self.settings_return_tab = self.tabs.select()
        if page == "prepare" and (self.capture or self.paused) and self.session is not None:
            self.tabs.select(self.qa_tab)
        else:
            self.tabs.select(pages[page])
        if page == "settings":
            self.settings_subnav.pack(side="bottom", fill="x", padx=8, pady=(0, 8),
                                      before=self.sidebar_settings)
            self.select_settings_category(self.settings_category.get())
        self.on_page_changed()

    def leave_settings(self):
        self.tabs.select(self.settings_return_tab or self.profile_tab)
        self.on_page_changed()

    def on_page_changed(self, _event=None):
        selected = self.tabs.select()
        page_names = {
            str(self.profile_tab): ("面试准备", "prepare"),
            str(self.documents_tab): ("我的资料", "documents"),
            str(self.records_tab): ("面试记录", "records"),
            str(self.qa_tab): ("本场面试", "prepare"),
            str(self.history_tab): ("本场回看", "prepare"),
            str(self.cards_tab): ("口述卡", "prepare"),
            str(self.memory_tab): ("记忆与反馈", "documents"),
            str(self.settings_tab): ("设置", "settings"),
        }
        title, nav_key = page_names.get(selected, ("系统音频问答", "prepare"))
        active_session = self.session is not None and (self.capture is not None or self.paused)
        button, marker, icon, label = self.nav_buttons["prepare"]
        label = "本场面试" if active_session else "面试准备"
        button.configure(text=icon if getattr(self, "narrow_sidebar", False) else f"{icon}   {label}")
        self.nav_buttons["prepare"] = (button, marker, icon, label)
        if selected == str(self.settings_tab):
            category = self.settings_category.get() if hasattr(self, "settings_category") else "audio"
            title = f"设置  /  {self.settings_category_names.get(category, '声音与转写')}"
            self.settings_subnav.pack(side="bottom", fill="x", padx=8, pady=(0, 8),
                                      before=self.sidebar_settings)
        else:
            self.settings_subnav.pack_forget()
        self.header.configure(text=title)
        if selected == str(self.qa_tab):
            self.toolbar.pack(fill="x", padx=30, pady=(0, 8), before=self.tabs)
        else:
            self.toolbar.pack_forget()
            self.capture_label.pack_forget()
        self._set_navigation(nav_key)
        if nav_key == "settings" and hasattr(self, "settings_category"):
            self._set_navigation("setting_" + self.settings_category.get())

    def select_settings_category(self, category):
        self.settings_category.set(category)
        self.settings_tabs.select(self.settings_pages[category])
        self._set_navigation("setting_" + category)
        self.header.configure(text=f"设置  /  {self.settings_category_names[category]}")

    def text_box(self, parent, height, *, readonly=False, expand=False):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=expand or readonly or parent == self.profile_tab, pady=(5, 11))
        content = tk.Text(frame, width=1, height=height, wrap="char", undo=not readonly,
                          font=("Microsoft YaHei UI", 11), padx=10, pady=8,
                          bg=FIELD, fg=INK, relief="flat", spacing3=4, insertbackground=INK,
                          selectbackground=ACCENT, selectforeground=INK, inactiveselectbackground=ACCENT,
                          highlightbackground=BORDER, highlightcolor=FOCUS, highlightthickness=1)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=content.yview)
        scroll.pack(side="right", fill="y")
        content.pack(side="left", fill="both", expand=True)
        content.configure(yscrollcommand=scroll.set)
        if readonly:
            content.configure(state="disabled")
        return content

    def build_qa(self):
        self.session_label = ttk.Label(self.qa_tab, text="本场未开始", wraplength=620, foreground=MUTED)
        self.session_label.pack(anchor="w", pady=(0, 6))
        self.caption_status = ttk.Label(self.qa_tab, text="实时字幕未开始", wraplength=620,
                                        foreground=MUTED)
        self.caption_status.pack(anchor="w", pady=(0, 6))
        controls = ttk.Frame(self.qa_tab)
        controls.pack(fill="x", pady=(0, 6))
        self.mode = tk.StringVar(value="快速")
        ttk.Combobox(controls, textvariable=self.mode, values=("快速", "深度"),
                     state="readonly", width=8).pack(side="left")
        self.retry_button = ttk.Button(controls, text="用当前转写重答", command=self.retry_answer)
        self.retry_button.pack(side="left", padx=6)
        self.cancel_button = ttk.Button(controls, text="取消等待", command=self.cancel_request)
        self.cancel_button.pack(side="left")
        self.save_session_button = ttk.Button(controls, text="保存本场", command=self.save_current_session)
        self.save_session_button.pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="本场回看", command=lambda: self.tabs.select(self.history_tab)).pack(
            side="left", padx=6,
        )
        ttk.Button(controls, text="记忆与反馈", command=lambda: self.tabs.select(self.memory_tab)).pack(
            side="left",
        )
        self.open_panel_button = ttk.Button(controls, text="打开浮窗", command=self.show_listening_panel)
        self.open_panel_button.pack(side="left", padx=(6, 0))
        self.privacy_toggle = ttk.Checkbutton(controls, textvariable=self.privacy_caption,
                                             variable=self.privacy, command=self.apply_privacy)
        self.privacy_toggle.pack(side="left", padx=(6, 0))
        self.qa_body = ttk.Frame(self.qa_tab)
        self.qa_body.pack(fill="both", expand=True)
        self.question_pane = ttk.Frame(self.qa_body)
        self.answer_pane = ttk.Frame(self.qa_body)
        self.turn_list = tk.Listbox(self.question_pane, height=4, width=1, bg=PANEL, fg=INK,
                                    exportselection=False, selectbackground=ACCENT,
                                    selectforeground="#FFFFFF", highlightcolor=FOCUS)
        self.turn_list.bind("<<ListboxSelect>>", self.show_reviewed_turn)
        self.visible_turn_ids = ()
        self.reviewed_turn_id = None
        ttk.Label(self.question_pane, text="问题与转写", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.transcript = self.text_box(self.question_pane, 4, expand=True)
        self.transcript.bind("<KeyRelease>", self.mark_transcript_dirty, add="+")
        question_actions = ttk.Frame(self.question_pane)
        question_actions.pack(fill="x")
        self.text_button = ttk.Button(question_actions, text="回答手输问题", command=self.request_text)
        self.text_button.pack(side="left")
        self.review_button = ttk.Button(question_actions, text="采用候选词", command=self.toggle_review_term)
        self.review_label = ttk.Label(self.question_pane, foreground=INK, wraplength=250)
        heading = ttk.Frame(self.answer_pane)
        heading.pack(fill="x")
        ttk.Label(heading, text="建议回答", font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.answer = self.text_box(self.answer_pane, 7, readonly=True)
        self.active_question = ttk.Label(self.answer_pane, wraplength=500, justify="left",
                                         font=("Microsoft YaHei UI", 11, "bold"))
        self.active_question.pack(anchor="w", fill="x", before=self.answer.master)
        self.reviewed_answer = self.text_box(self.answer_pane, 7, readonly=True)
        self.reviewed_answer.master.pack_forget()
        self.latest_button = ttk.Button(heading, text="回到最新", command=self.show_latest_turn)
        caption_row = ttk.Frame(self.answer_pane)
        caption_row.pack(side="bottom", fill="x", before=self.answer.master)
        ttk.Separator(caption_row).pack(fill="x", pady=(0, 6))
        self.caption_preview = ttk.Label(caption_row, text="最近字幕：—", foreground=MUTED,
                                         wraplength=460, justify="left")
        self.caption_preview.pack(anchor="w")
        self.compact_button = ttk.Button(controls, text="紧凑窗口", command=self.toggle_compact)
        self.compact_button.pack(side="right")
        self.source_button = ttk.Button(heading, text="查看参考资料", command=self.show_sources,
                                       state="disabled")
        self.source_button.pack(side="right", padx=6)
        self.layout_question()

    def refresh_turn_list(self):
        ids = tuple(self.turn_ledger.turns)
        if not ids:
            self.turn_list.pack_forget()
        elif not self.turn_list.winfo_manager():
            self.turn_list.pack(fill="x", pady=(4, 8), before=self.transcript.master)
        if ids != self.visible_turn_ids:
            top = self.turn_list.yview()[0]
            self.turn_list.delete(0, "end")
            for number, item in enumerate(self.turn_ledger.turns.values(), 1):
                self.turn_list.insert("end", f"{number}. {' '.join(item.question.split())}")
            self.visible_turn_ids = ids
            self.turn_list.yview_moveto(top)
        active = self.reviewed_turn_id or self.turn_ledger.current_id
        if active in ids:
            index = ids.index(active)
            self.turn_list.selection_clear(0, "end")
            self.turn_list.selection_set(index)
            if not self.reviewed_turn_id:
                self.turn_list.see(index)
        if not self.reviewed_turn_id:
            current = self.turn_ledger.current
            self.active_question.configure(text=current.question if current else "")

    def show_reviewed_turn(self, _event=None):
        selection = self.turn_list.curselection()
        if not selection:
            return
        item = self.turn_ledger.get(self.visible_turn_ids[selection[0]])
        if item.turn_id == self.turn_ledger.current_id:
            self.show_latest_turn()
            return
        self.reviewed_turn_id = item.turn_id
        self.active_question.configure(text=item.question)
        self.replace_text(self.reviewed_answer, item.answer or item.status)
        self.answer.master.pack_forget()
        self.reviewed_answer.master.pack(fill="both", expand=True, pady=(5, 11))
        self.latest_button.pack(side="right", padx=6)

    def show_latest_turn(self):
        self.reviewed_turn_id = None
        self.reviewed_answer.master.pack_forget()
        self.answer.master.pack(fill="both", expand=True, pady=(5, 11))
        self.latest_button.pack_forget()
        self.refresh_turn_list()

    def layout_question(self):
        self.question_pane.grid_forget()
        self.answer_pane.grid_forget()
        self.qa_body.columnconfigure(0, weight=1 if self.compact else 2, minsize=0 if self.compact else 218)
        self.qa_body.columnconfigure(1, weight=0 if self.compact else 3)
        self.qa_body.rowconfigure(0, weight=0 if self.compact else 1)
        self.qa_body.rowconfigure(1, weight=1 if self.compact else 0)
        self.question_pane.grid(row=0, column=0, sticky="nsew", padx=(0, 0 if self.compact else 10))
        self.answer_pane.grid(row=1 if self.compact else 0, column=0 if self.compact else 1, sticky="nsew")
        self.review_label.configure(wraplength=470 if self.compact else 235)

    def clear_review(self):
        self.review_original = self.review_replacement = ""
        self.review_label.configure(text="")
        self.review_label.pack_forget()
        self.review_button.pack_forget()

    def mark_transcript_dirty(self, _event=None):
        self.transcript_dirty = True

    def toggle_review_term(self):
        current = self.transcript.get("1.0", "end-1c")
        if current == self.review_original:
            self.replace_text(self.transcript, self.review_replacement)
            self.review_button.configure(text="恢复原转写")
        elif current == self.review_replacement:
            self.replace_text(self.transcript, self.review_original)
            self.review_button.configure(text="采用候选词")
        else:
            self.review_label.configure(text="转写已编辑，请确认后提交。")
            self.review_button.pack_forget()

    def build_history(self):
        actions = ttk.Frame(self.history_tab)
        actions.pack(fill="x")
        ttk.Label(actions, text="本场最近 4 道问答", foreground=MUTED).pack(side="left")
        self.pin_button = ttk.Button(actions, text="固定选中", command=self.toggle_pin)
        self.pin_button.pack(side="right")
        ttk.Button(actions, text="上一题", command=lambda: self.move_history(1)).pack(side="right", padx=4)
        ttk.Button(actions, text="下一题", command=lambda: self.move_history(-1)).pack(side="right")
        self.history_list = tk.Listbox(self.history_tab, height=4, bg=FIELD, fg=INK,
                                      selectbackground=ACCENT, selectforeground=INK, relief="flat",
                                      highlightbackground=BORDER, highlightcolor=FOCUS,
                                      highlightthickness=1, exportselection=False)
        self.history_list.pack(fill="x", pady=(6, 5))
        self.history_list.bind("<<ListboxSelect>>", self.show_history)
        self.history_text = self.text_box(self.history_tab, 6, readonly=True)
        self.refresh_history()

    def refresh_history(self):
        self.history_list.delete(0, "end")
        turns = list(reversed(self.session.turns)) if self.session is not None else []
        if self.pinned_turn is not None:
            self.pinned_turn = next((turn for turn in turns if turn.id == self.pinned_turn.id), self.pinned_turn)
            turns = [self.pinned_turn, *(turn for turn in turns if turn.id != self.pinned_turn.id)]
        self.history_rows = turns
        for index, turn in enumerate(turns):
            label = " ".join(turn.question.split())[:50]
            pinned = "固定 · " if self.pinned_turn and turn.id == self.pinned_turn.id else ""
            self.history_list.insert("end", f"{pinned}{turn.mode} · {label}")
            if turn.id == self.selected_turn_id:
                self.history_list.selection_set(index)
        self.show_history()

    def show_history(self, _event=None):
        selected = self.history_list.curselection()
        if selected and selected[0] < len(self.history_rows):
            turn = self.history_rows[selected[0]]
            self.selected_turn_id = turn.id
            text = f"问题\n{turn.question}\n\n{turn.mode}回答\n{turn.answer}"
        else:
            self.selected_turn_id = None
            text = "请选择一条问答。" if self.history_rows else "本场完成的问答会显示在这里。"
        pinned = self.pinned_turn is not None and self.selected_turn_id == self.pinned_turn.id
        self.pin_button.configure(text="取消固定" if pinned else "固定选中",
                                  state="normal" if self.selected_turn_id is not None else "disabled")
        if self.history_text.get("1.0", "end-1c") != text:
            self.replace_text(self.history_text, text)

    def move_history(self, delta):
        if not self.history_rows:
            return
        selection = self.history_list.curselection()
        index = max(0, min(len(self.history_rows) - 1, selection[0] + delta)) if selection else 0
        self.history_list.selection_clear(0, "end")
        self.history_list.selection_set(index)
        self.history_list.see(index)
        self.show_history()

    def keyboard_history(self, delta):
        self.tabs.select(self.history_tab)
        self.move_history(delta)
        return "break"

    def toggle_pin(self):
        selection = self.history_list.curselection()
        if selection:
            turn = self.history_rows[selection[0]]
            self.pinned_turn = None if self.pinned_turn and turn.id == self.pinned_turn.id else turn
            self.refresh_history()

    def show_sources(self):
        self.source_view.title("本题发给模型的参考资料")
        self.source_view.update_idletasks()
        try:
            set_privacy(native_window(self.source_view), self.privacy.get())
        except OSError:
            if self.privacy.get():
                self.source_view.withdraw()
                self.status.configure(text="Windows 未确认资料窗口保护。")
                return
        self.replace_text(self.source_text, "\n\n".join(
            f"{item['source']}\n{item['text']}" for item in self.source_excerpts
        ))
        self.source_view.deiconify()
        self.source_view.lift()

    def toggle_compact(self):
        self.compact = not self.compact
        if self.compact:
            self.header.pack_forget()
        else:
            self.header.pack(anchor="w")
        self.size_window()
        self.transcript.configure(height=1 if self.compact else 4, pady=4 if self.compact else 8)
        self.answer.configure(height=4 if self.compact else 7, pady=4 if self.compact else 8)
        self.layout_question()
        self.compact_button.configure(text="恢复窗口" if self.compact else "紧凑窗口")

    def size_window(self):
        width, height, min_width, min_height = (550, 525, 520, 500) if self.compact else (1180, 760, 760, 620)
        available_width = self.root.winfo_screenwidth() - 44
        available_height = self.root.winfo_screenheight() - 100
        self.root.minsize(min(round(min_width * self.ui_scale), available_width),
                          min(round(min_height * self.ui_scale), available_height))
        self.root.geometry(f"{min(round(width * self.ui_scale), available_width)}x"
                           f"{min(round(height * self.ui_scale), available_height)}")

    def build_profile(self):
        actions = ttk.Frame(self.profile_tab)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        viewport = ttk.Frame(self.profile_tab)
        viewport.pack(fill="both", expand=True)
        canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(canvas)
        body_window = canvas.create_window(0, 0, anchor="nw", window=body)
        body.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(body_window, width=event.width))
        self.profile_tab.bind("<Map>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")), add="+")
        self.root.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units")
                       if str(event.widget).startswith(str(body)) and not isinstance(event.widget, tk.Text)
                       else None, add="+")
        fields = ttk.Frame(body)
        fields.pack(fill="x")
        fields.columnconfigure(0, weight=1, uniform="identity")
        fields.columnconfigure(1, weight=1, uniform="identity")
        self.identity_fields = []
        self.draft = {}
        for column, key, label in ((0, "company", "公司"), (1, "role", "岗位")):
            group = ttk.Frame(fields)
            group.grid(row=0, column=column, sticky="ew", padx=(0, 14), pady=(0, 14))
            ttk.Label(group, text=label).pack(anchor="w", pady=(0, 4))
            field = ttk.Entry(group, width=1)
            field.pack(fill="x")
            self.draft[key] = field
            self.identity_fields.append(group)

        ttk.Label(fields, text="面试轮次").grid(row=3, column=0, sticky="w", pady=(0, 4))
        self.draft["round"] = ttk.Combobox(
            fields, values=("一面", "二面", "三面", "终面", "HR 面"), width=20,
        )
        self.draft["round"].grid(row=4, column=0, sticky="w", pady=(0, 14))
        jd_row = ttk.Frame(fields)
        jd_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Label(jd_row, text="岗位描述").pack(side="left")
        self.draft["job_description"] = DraftText(
            fields, width=1, height=4, wrap="word", font=("Microsoft YaHei UI", 11), padx=9, pady=7,
            bg=FIELD, fg=INK, relief="flat", undo=True, spacing3=3, insertbackground=INK,
            selectbackground=ACCENT, selectforeground="#FFFFFF",
            highlightbackground=BORDER, highlightcolor=FOCUS, highlightthickness=1,
        )
        self.draft["job_description"].grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        self.jd_expand = ttk.Button(jd_row, text="展开", command=self.toggle_job_description)
        self.jd_expand.pack(side="right")
        self.preparation_details = ttk.Frame(body)
        self.draft["language"] = ttk.Combobox(
            self.preparation_details, values=("自动", "中文", "English", "中英对照"), width=18,
        )
        self.draft["language"].set("中文")
        self.draft["answer_length"] = ttk.Combobox(
            self.preparation_details, values=tuple(ANSWER_LENGTH_INSTRUCTIONS), width=18, state="readonly",
        )
        self.draft["answer_length"].set(STANDARD_ANSWER_LENGTH)
        self.draft["focus"] = ttk.Entry(self.preparation_details, width=20)
        self.draft["terms"] = ttk.Entry(self.preparation_details, width=20)
        self.draft["verified_facts"] = ttk.Entry(self.preparation_details, width=20)

        self.resume_choice = tk.StringVar(value="")
        self.resume_choice_label = ttk.Label(body, textvariable=self.resume_choice, foreground=MUTED)
        self.resume_choice_label.pack(anchor="w", pady=(2, 3))
        resume_actions = ttk.Frame(body)
        resume_actions.pack(fill="x", pady=(0, 12))
        self.use_resume_button = ttk.Button(resume_actions, text="用于本场", command=self.use_personal_resume)
        self.use_resume_button.pack(side="left")
        self.manage_resume_button = ttk.Button(resume_actions, text="上传简历 / 粘贴文字",
                                               command=lambda: self.navigate("documents"))
        self.manage_resume_button.pack(side="left", padx=(0, 8))
        self.resume_session_badge = ttk.Label(resume_actions, text="")
        self.resume_session_badge.pack(side="left", padx=(8, 0))
        project = ttk.Frame(body)
        project.pack(fill="x", pady=(0, 12))
        ttk.Label(project, text="本场主讲项目").pack(side="left", padx=(0, 12))
        self.project_choice = ttk.Combobox(project, state="readonly", width=24, postcommand=self.load_project_choices)
        self.project_choice.pack(side="left", fill="x", expand=True)
        self.project_choice.bind("<<ComboboxSelected>>", self.choose_project)

        audio_row = ttk.Frame(body)
        audio_row.pack(fill="x", pady=(1, 12))
        self.audio_source_label = ttk.Label(audio_row, text="系统播放音源")
        self.audio_source_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.preflight_status = ttk.Label(audio_row, foreground=MUTED, wraplength=560)
        self.preflight_status.grid(row=0, column=1, sticky="ew")
        self.audio_check_button = ttk.Button(audio_row, text="试一下声音", command=self.start_audio_check)
        self.audio_check_button.grid(row=0, column=2, sticky="e", padx=(10, 0))
        audio_row.columnconfigure(1, weight=1)
        self.audio_prepare_status = ttk.Label(body, foreground=MUTED, wraplength=620)
        self.audio_prepare_status.pack(anchor="w")

        self.details_toggle = ttk.Button(body, text="更多本场设置  +", command=self.toggle_preparation_details)
        self.details_toggle.pack(anchor="w", pady=(0, 5))
        detail_fields = (("language", "回答语言"), ("answer_length", "回答长度"),
                         ("focus", "关注点"), ("terms", "术语候选"),
                         ("verified_facts", "核实过的项目事实"))
        for index, (key, label) in enumerate(detail_fields):
            row, column = divmod(index, 2)
            ttk.Label(self.preparation_details, text=label).grid(
                row=row * 2, column=column, sticky="w", padx=(0, 14), pady=(5, 3))
            self.draft[key].grid(row=row * 2 + 1, column=column, sticky="ew", padx=(0, 14), pady=(0, 7))
        self.preparation_details.columnconfigure(0, weight=1)
        self.preparation_details.columnconfigure(1, weight=1)
        ttk.Label(self.preparation_details, text="本场补充资料").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(7, 3))
        self.profile = DraftText(
            self.preparation_details, height=4, wrap="word", font=("Microsoft YaHei UI", 10),
            padx=9, pady=7, bg=FIELD, fg=INK, relief="flat", undo=True, spacing3=3,
            insertbackground=INK, selectbackground=ACCENT, selectforeground="#FFFFFF",
            highlightbackground=BORDER, highlightcolor=FOCUS, highlightthickness=1,
        )
        self.profile.grid(row=7, column=0, columnspan=2, sticky="ew")
        ttk.Button(self.preparation_details, text="口述卡", command=lambda: self.tabs.select(self.cards_tab)).grid(
            row=8, column=0, sticky="w", pady=(8, 4))
        ttk.Button(self.preparation_details, text="保存面试配置", command=self.save_interview_preset).grid(
            row=8, column=1, sticky="e", pady=(8, 4))
        ttk.Button(self.preparation_details, text="加载面试配置", command=self.load_interview_preset).grid(
            row=9, column=1, sticky="e")

        self.session_return_button = ttk.Button(body, text="返回本场面试", command=self.return_to_session)
        self.session_return_button.pack(anchor="w", pady=(9, 0))
        self.session_return_button.pack_forget()
        self.begin_button = ttk.Button(actions, text="开始听题", style="Primary.TButton",
                                       command=self.begin_listening)
        self.begin_button.pack(side="right")
        self.update_resume_choice()
        self.source_view = tk.Toplevel(self.root, background=BG)
        self.source_view.withdraw()
        self.source_view.geometry("550x380")
        self.source_view.transient(self.root)
        self.source_view.protocol("WM_DELETE_WINDOW", self.source_view.withdraw)
        self.source_view.bind("<Map>", lambda event: style_title_bar(event.widget)
                              if event.widget is self.source_view else None, add="+")
        self.source_text = self.text_box(self.source_view, 10, readonly=True)

    def toggle_job_description(self):
        field = self.draft["job_description"]
        expanded = int(field.cget("height")) == 4
        field.configure(height=10 if expanded else 4)
        self.jd_expand.configure(text="折起" if expanded else "展开")

    def load_project_choices(self):
        try:
            facts = [value for _, kind, value in self.memory.list() if kind == "fact"] if self.memory else []
        except sqlite3.Error:
            facts = []
        self.project_choice.configure(values=("暂不选用", *facts))

    def choose_project(self, _event=None):
        if self.project_choice.current() == 0:
            self.project_choice.set("")

    def toggle_preparation_details(self):
        if self.preparation_details.winfo_manager():
            self.preparation_details.pack_forget()
            self.details_toggle.configure(text="更多本场设置  +")
        else:
            self.preparation_details.pack(fill="x", pady=(0, 4), after=self.details_toggle)
            self.details_toggle.configure(text="更多本场设置  −")

    def return_to_session(self):
        if self.session is not None:
            self.tabs.select(self.qa_tab)

    def update_resume_choice(self):
        self.manage_resume_button.configure(text="管理简历" if self.personal_resume else "上传简历 / 粘贴文字")
        if self.selected_resume:
            self.resume_choice.set(f"本场简历  ·  {self.selected_resume['name']}")
            self.use_resume_button.configure(text="更换本场简历")
            self.resume_session_badge.configure(text="已选用于本场")
        elif self.personal_resume:
            self.resume_choice.set(f"可选简历  ·  {self.personal_resume['name']}  ·  本机已保存")
            self.use_resume_button.configure(text="用于本场")
            self.resume_session_badge.configure(text="")
        else:
            self.resume_choice.set("本场使用的资料  ·  尚未选择简历")
            self.resume_session_badge.configure(text="")
            self.use_resume_button.pack_forget()
        if self.personal_resume:
            self.use_resume_button.pack(side="left", padx=(0, 8), before=self.manage_resume_button)
            self.use_resume_button.configure(state="normal")

    def use_personal_resume(self):
        if not self.personal_resume:
            self.navigate("documents")
            return
        self.selected_resume = dict(self.personal_resume)
        self.update_resume_choice()
        self.status.configure(text="已将本机简历选为本场资料。开始后会锁定到当前场次。")

    def build_resume_page(self):
        self.saved_resume_row = ttk.Frame(self.documents_tab)
        self.saved_resume_row.pack(fill="x", pady=(0, 14))
        self.saved_resume_label = ttk.Label(self.saved_resume_row, font=("Microsoft YaHei UI", 12, "bold"))
        self.saved_resume_label.pack(side="left")
        self.saved_resume_status = ttk.Label(self.saved_resume_row, foreground=MUTED)
        self.saved_resume_status.pack(side="left", padx=(10, 0))
        self.replace_resume_button = ttk.Button(self.saved_resume_row, text="更换简历",
                                                command=self.choose_resume_file)
        self.replace_resume_button.pack(side="right")
        self.review_saved_resume_button = ttk.Button(
            self.saved_resume_row, text="查看与编辑",
            command=lambda: self.start_resume_edit(self.personal_resume["name"], self.personal_resume["text"]),
        )
        project_row = ttk.Frame(self.documents_tab)
        project_row.pack(fill="x", pady=(0, 16))
        ttk.Label(project_row, text="项目经历", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        ttk.Label(project_row, text="已核实项目事实在记忆与反馈中管理。", foreground=MUTED).pack(
            side="left", padx=(10, 0),
        )
        ttk.Button(project_row, text="打开", command=lambda: self.tabs.select(self.memory_tab)).pack(side="right")

        self.resume_drop_zone = tk.Frame(self.documents_tab, bg=PANEL, highlightbackground=BORDER,
                                         highlightthickness=1, height=160)
        self.resume_drop_zone.pack(fill="x", pady=(0, 16))
        self.resume_drop_zone.pack_propagate(False)
        self.resume_drop_label = tk.Label(
            self.resume_drop_zone,
            text="选择简历文件\n也可以拖入 PDF、DOCX 或粘贴文字",
            bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 12), justify="center", cursor="hand2",
        )
        self.resume_drop_label.pack(fill="both", expand=True, padx=16, pady=16)
        self.resume_drop_label.bind("<Button-1>", lambda _event: self.choose_resume_file())
        for widget in (self.resume_drop_zone, self.resume_drop_label):
            widget.bind("<Enter>", lambda _event: self.resume_drop_zone.configure(highlightbackground=ACCENT), add="+")
            widget.bind("<Leave>", lambda _event: self.resume_drop_zone.configure(highlightbackground=BORDER), add="+")
        self.paste_resume_button = ttk.Button(self.documents_tab, text="粘贴简历文字", command=self.start_pasted_resume)
        self.paste_resume_button.pack(anchor="w", pady=(0, 12))
        self.resume_loading_label = ttk.Label(self.documents_tab, foreground=MUTED, wraplength=610)
        self.resume_loading_label.pack(anchor="w")
        self.resume_error = ttk.Label(self.documents_tab, foreground="#A52C2C", wraplength=760)
        self.resume_error.pack(anchor="w", pady=(0, 8))

        self.resume_editor_frame = ttk.Frame(self.documents_tab)
        self.resume_editor_frame.bind("<Escape>", lambda _: self.cancel_resume_edit())
        file_row = ttk.Frame(self.resume_editor_frame)
        file_row.pack(fill="x", pady=(0, 6))
        self.resume_candidate_label = ttk.Label(file_row, text="", font=("Microsoft YaHei UI", 11, "bold"))
        self.resume_candidate_label.pack(side="left")
        ttk.Button(file_row, text="重新选择", command=self.choose_resume_file).pack(side="right")
        ttk.Label(self.resume_editor_frame, text="核对提取内容后再保存到本机。文本可以直接编辑。",
                  foreground=MUTED).pack(anchor="w", pady=(0, 4))
        self.resume_editor = self.text_box(self.resume_editor_frame, 16, expand=True)
        self.resume_editor.bind("<Escape>", lambda _: self.cancel_resume_edit())
        editor_actions = ttk.Frame(self.resume_editor_frame)
        editor_actions.pack(side="bottom", fill="x", pady=(4, 0), before=self.resume_editor.master)
        ttk.Button(editor_actions, text="取消", command=self.cancel_resume_edit).pack(side="left")
        self.save_resume_button = ttk.Button(editor_actions, text="保存简历", style="Primary.TButton",
                                             command=self.save_resume)
        self.save_resume_button.pack(side="right")
        self.update_resume_page()
        if self.dnd_ready:
            try:
                for target in (self.resume_drop_zone, self.resume_drop_label, self.saved_resume_row):
                    target.drop_target_register(DND_FILES)
                    target.dnd_bind("<<DropEnter>>", self.on_resume_drag_enter)
                    target.dnd_bind("<<DropLeave>>", self.on_resume_drag_leave)
                    target.dnd_bind("<<Drop>>", self.on_resume_drop)
            except tk.TclError:
                self.dnd_ready = False

    def update_resume_page(self):
        if self.personal_resume:
            self.saved_resume_label.configure(text=self.personal_resume["name"])
            self.saved_resume_status.configure(text="已保存在本机")
            self.replace_resume_button.configure(text="更换简历")
            self.review_saved_resume_button.pack(side="right", padx=8)
            self.resume_drop_zone.pack_forget()
            self.paste_resume_button.configure(text="粘贴文字并更换")
        else:
            self.saved_resume_label.configure(text="还没有保存的简历")
            self.saved_resume_status.configure(text="")
            self.replace_resume_button.configure(text="上传简历")
            self.review_saved_resume_button.pack_forget()
            self.resume_drop_zone.pack(fill="x", pady=(0, 16), before=self.paste_resume_button)
            self.paste_resume_button.configure(text="粘贴简历文字")
        if not self.resume_candidate_label.cget("text"):
            self.resume_editor_frame.pack_forget()
        self.update_resume_choice()

    def start_resume_edit(self, name, text):
        if not str(text).strip():
            self.resume_error.configure(text="未读到可编辑文字，可粘贴简历内容。")
            return False
        self.resume_candidate_name = name
        self.resume_candidate_label.configure(text=name)
        self.replace_text(self.resume_editor, text)
        self.resume_error.configure(text="")
        self.resume_editor_frame.pack(fill="both", expand=True)
        self.resume_drop_zone.pack_forget()
        self.resume_editor.focus_set()
        return True

    def start_pasted_resume(self):
        self.resume_import_id += 1
        self.resume_loading = False
        self.resume_loading_label.configure(text="")
        self.save_resume_button.state(("!disabled",))
        self.resume_error.configure(text="")
        self.resume_candidate_name = "粘贴的简历"
        self.replace_text(self.resume_editor, "")
        self.resume_candidate_label.configure(text="粘贴的简历")
        self.resume_editor_frame.pack(fill="both", expand=True)
        self.resume_drop_zone.pack_forget()
        self.resume_editor.focus_set()

    def choose_resume_file(self):
        if self.privacy.get():
            self.resume_error.configure(text="截图保护已开启。请先关闭保护后选择文件，或直接粘贴简历文字。")
            return
        path = filedialog.askopenfilename(
            title="从这台电脑选择简历",
            filetypes=[("简历文件", "*.pdf *.docx *.txt *.md")],
        )
        if not path:
            return
        self.read_resume_file(path)

    def read_resume_file(self, path):
        path = Path(path)
        self.resume_import_id += 1
        import_id = self.resume_import_id
        self.resume_loading = True
        self.save_resume_button.state(("disabled",))
        self.resume_loading_label.configure(text=f"{path.name} · 正在读取…")
        self.resume_error.configure(text="")

        def read():
            try:
                result = read_profile(path)
            except ValueError as error:
                self.events.put(("resume_import", (import_id, path.name, None, str(error))))
            except PermissionError:
                self.events.put(("resume_import", (import_id, path.name, None, "无法读取此文件。请检查文件权限或占用情况。")))
            except Exception:
                self.events.put(("resume_import", (import_id, path.name, None, "简历读取失败。请重新选择文件或粘贴文字。")))
            else:
                self.events.put(("resume_import", (import_id, path.name, result, "")))

        threading.Thread(target=read, daemon=True).start()

    def finish_resume_import(self, import_id, name, result, error):
        if import_id != self.resume_import_id:
            return
        self.resume_loading = False
        self.resume_loading_label.configure(text="")
        self.save_resume_button.state(("!disabled",))
        if error:
            self.resume_error.configure(text=error)
        else:
            self.start_resume_edit(name, result)

    def on_resume_drag_enter(self, _event):
        self.resume_drop_zone.configure(highlightbackground=ACCENT)
        self.resume_drop_label.configure(text="松开以读取简历")
        return "copy"

    def on_resume_drag_leave(self, _event):
        self.resume_drop_zone.configure(highlightbackground=BORDER)
        self.resume_drop_label.configure(text="选择简历文件\n也可以拖入 PDF、DOCX 或粘贴文字")

    def on_resume_drop(self, event):
        self.on_resume_drag_leave(event)
        if self.privacy.get():
            self.resume_error.configure(text="截图保护已开启。请先关闭保护后导入文件，或直接粘贴简历文字。")
            return "refuse_drop"
        try:
            paths = self.root.tk.splitlist(event.data)
        except tk.TclError:
            paths = ()
        if len(paths) != 1:
            self.resume_error.configure(text="请一次选择一份简历。")
            return "refuse_drop"
        self.read_resume_file(paths[0])
        return "copy"

    def cancel_resume_edit(self):
        self.resume_import_id += 1
        self.resume_loading = False
        self.resume_loading_label.configure(text="")
        self.save_resume_button.state(("!disabled",))
        self.resume_candidate_label.configure(text="")
        self.replace_text(self.resume_editor, "")
        self.resume_error.configure(text="")
        self.resume_editor_frame.pack_forget()
        if not self.personal_resume:
            self.resume_drop_zone.pack(fill="x", pady=(0, 16), before=self.paste_resume_button)

    def save_resume(self):
        if self.resume_loading or not self.resume_candidate_label.cget("text"):
            return False
        text = self.resume_editor.get("1.0", "end-1c").strip()
        if not text:
            self.resume_error.configure(text="简历内容不能为空。")
            return False
        if len(text) > MAX_TEXT:
            self.resume_error.configure(text="简历超过 48,000 字符。请删减后再保存。")
            return False
        if self.memory is None:
            self.resume_error.configure(text="本机资料存储不可用，文本仍保留在编辑框中。")
            return False
        try:
            self.memory.save_resume(self.resume_candidate_name, text)
        except (OSError, sqlite3.Error, ValueError) as error:
            self.resume_error.configure(text=f"保存失败，文本仍保留在编辑框中。{error}")
            return False
        self.personal_resume = {"name": self.resume_candidate_name, "text": text}
        self.resume_candidate_label.configure(text="")
        self.resume_editor_frame.pack_forget()
        self.update_resume_page()
        self.status.configure(text=f"{self.personal_resume['name']} 已保存在本机。")
        return True

    def build_records_page(self):
        actions = ttk.Frame(self.records_tab)
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="去面试准备", command=lambda: self.navigate("prepare")).pack(side="right")
        self.records_back = ttk.Button(actions, text="返回记录", command=self.close_record_detail)
        self.records_narrow = False
        self.records_body = ttk.Frame(self.records_tab)
        self.records_body.pack(fill="both", expand=True)
        self.saved_records_list = tk.Listbox(
            self.records_body, width=34, bg=PANEL, fg=INK, selectbackground=ACCENT, exportselection=False,
            selectforeground="#FFFFFF", highlightbackground=BORDER, relief="flat", activestyle="none",
        )
        self.saved_records_list.pack(side="left", fill="y", padx=(0, 16))
        self.saved_records_list.bind("<<ListboxSelect>>", self.show_saved_interview)
        self.saved_record_detail = self.text_box(self.records_body, 15, readonly=True, expand=True)
        self.saved_record_detail_frame = self.saved_record_detail.master
        self.record_questions = ttk.Combobox(self.saved_record_detail_frame, state="readonly", width=1)
        self.record_questions.pack(side="top", fill="x", pady=(0, 8), before=self.saved_record_detail)
        self.record_questions.bind("<<ComboboxSelected>>", self.show_record_question)
        self.record_raw = ttk.Button(self.saved_record_detail_frame, text="展开原始转写", command=self.toggle_record_raw)
        self.record_raw.pack(side="bottom", anchor="w", pady=(6, 0), before=self.saved_record_detail)
        self.record_with_raw = False
        self.records_empty = ttk.Label(self.records_body, text="还没有保存的面试记录。",
                                       foreground=MUTED, font=("Microsoft YaHei UI", 12))
        self.saved_interviews = []
        self.refresh_saved_interviews()

    def refresh_saved_interviews(self):
        self.saved_interviews = []
        if self.memory is not None:
            try:
                self.saved_interviews = self.memory.list_interviews()
            except (sqlite3.Error, ValueError):
                self.records_empty.configure(text="读取面试记录失败。")
        self.saved_records_list.delete(0, "end")
        for record in self.saved_interviews:
            label = " · ".join(value for value in (record["company"], record["role"], record["round"])
                               if value) or "通用场次"
            self.saved_records_list.insert("end", f"{label}  ·  {record['saved_at'][:16].replace('T', ' ')}")
        if self.saved_interviews:
            self.records_empty.pack_forget()
            if self.records_narrow:
                self.close_record_detail()
            else:
                self.saved_records_list.pack(side="left", fill="y", padx=(0, 16))
                self.saved_records_list.selection_set(0)
                self.show_saved_interview()
        else:
            self.saved_records_list.pack_forget()
            self.saved_record_detail_frame.pack_forget()
            self.replace_text(self.saved_record_detail, "")
            self.records_empty.pack(anchor="center", expand=True)

    def show_saved_interview(self, _event=None):
        selected = self.saved_records_list.curselection()
        if not selected or selected[0] >= len(self.saved_interviews):
            return
        record = self.saved_interviews[selected[0]]
        self.viewed_record = record
        if self.records_narrow:
            self.saved_records_list.pack_forget()
            self.records_back.pack(side="left")
        else:
            self.records_back.pack_forget()
        self.saved_record_detail_frame.pack(side="left", fill="both", expand=True)
        self.record_questions.configure(values=tuple(
            f"{index}. {' '.join(turn['question'].split())}" for index, turn in enumerate(record["turns"], 1)
        ))
        if record["turns"]:
            self.record_questions.current(0)
        else:
            self.record_questions.set("")
        self.show_record_question()

    def close_record_detail(self):
        self.records_back.pack_forget()
        self.saved_record_detail_frame.pack_forget()
        self.saved_records_list.pack(fill="both", expand=True)
        self.saved_records_list.selection_clear(0, "end")

    def show_record_question(self, _event=None):
        self.record_with_raw = False
        self.render_record_question()

    def toggle_record_raw(self):
        self.record_with_raw = not self.record_with_raw
        self.render_record_question()

    def render_record_question(self):
        record = self.viewed_record
        heading = " · ".join(value for value in (record["company"], record["role"], record["round"])
                             if value) or "通用场次"
        sections = [heading, f"保存时间  {record['saved_at']}"]
        index = self.record_questions.current()
        self.record_raw.pack_forget()
        if index < 0:
            sections.append("\n本场没有已提交的问题。")
        else:
            turn = record["turns"][index]
            sections.extend((f"\n提交题干\n{turn['question']}", f"\n回答建议\n{turn['answer'] or '尚无回答'}"))
            if turn.get("raw_caption"):
                self.record_raw.pack(side="bottom", anchor="w", pady=(6, 0), before=self.saved_record_detail)
                self.record_raw.configure(text="收起原始转写" if self.record_with_raw else "展开原始转写")
                if self.record_with_raw:
                    sections.append(f"\n原始转写\n{turn['raw_caption']}")
        self.replace_text(self.saved_record_detail, "\n".join(sections))

    def save_interview_preset(self):
        if self.memory is None:
            self.status.configure(text="本机预设存储不可用。")
            return
        try:
            self.memory.save_preset({name: field.get().strip() for name, field in self.draft.items()})
        except sqlite3.Error:
            self.status.configure(text="保存面试预设失败。")
        else:
            self.status.configure(text="面试配置已保存为预设。简历由你每场选用。")

    def load_interview_preset(self):
        try:
            preset = self.memory.load_preset() if self.memory else None
        except (ValueError, sqlite3.Error):
            preset = None
        if preset is None:
            self.status.configure(text="暂无可加载的面试预设。")
            return
        for name, field in self.draft.items():
            if name == "answer_length":
                length = preset.get(name)
                if not isinstance(length, str) or length not in ANSWER_LENGTH_INSTRUCTIONS:
                    length = STANDARD_ANSWER_LENGTH
                field.set(length)
                continue
            value = preset.get(name, "")
            if isinstance(field, tk.Text):
                self.replace_text(field, value)
            else:
                field.delete(0, "end")
                field.insert(0, value)
        self.status.configure(text="已加载到草稿。开始新场后生效。")

    def update_preflight(self):
        device = self.capture.device_name if self.capture else self.device.get()
        self.preflight_status.configure(text=device)

    def start_audio_check(self):
        if self.audio_check is not None:
            return
        self.audio_check_button.configure(state="disabled")
        self.audio_settings_test.configure(state="disabled")
        message = "播放一段测试声音，约 4 秒后显示回放电平。"
        self.audio_prepare_status.configure(text=message)
        self.audio_settings_status.configure(text=message)
        self.audio_check = (time.perf_counter(), self.capture)
        if self.capture is not None:
            self.root.after(4000, self.finish_audio_check)
            return
        index = self.devices.get(self.device.get())

        def check():
            try:
                capture = PlaybackCapture(index)
                try:
                    time.sleep(4)
                    result = self.audio_probe_result(capture, 4)
                finally:
                    capture.close()
            except (OSError, ValueError):
                result = "无法连接播放设备，请刷新后重试。"
            self.events.put(("audio_test", result))

        threading.Thread(target=check, daemon=True).start()

    def finish_audio_check(self):
        started, original = self.audio_check
        capture = self.capture
        if capture is None or (original is not None and capture is not original):
            self.show_audio_check_result("检查期间播放设备未就绪或发生切换，请重试。")
            return
        self.show_audio_check_result(self.audio_probe_result(capture, min(4.0, time.perf_counter() - started)))

    @staticmethod
    def audio_probe_result(capture, seconds):
        try:
            pcm = capture.buffer.snapshot(seconds)
            frames = len(pcm) // capture.buffer.frame_bytes
            peak = audioop.max(pcm, 2)
        except NoAudio:
            frames, peak = 0, 0
        seconds = frames / capture.buffer.rate
        return (f"{capture.device_name} · {seconds:.2f} 秒回放 · 峰值 {peak / 32768:.1%}"
                if peak > 25 else f"{capture.device_name} · 未收到可用信号，请核对会议输出设备。")

    def show_audio_check_result(self, result):
        self.audio_check = None
        self.audio_check_button.configure(state="normal")
        self.audio_settings_test.configure(state="normal")
        self.audio_prepare_status.configure(text=result)
        self.audio_settings_status.configure(text=result)
        self.status.configure(text=result)

    def begin_listening(self):
        if self.listening_pending or self.start_button.instate(("disabled",)):
            return
        if self.session is not None and self.listening_panel is not None and (self.capture or self.paused):
            self.return_to_session()
            return
        if not self.start_session(allow_generic=True):
            return
        if self.capture is not None:
            self.show_listening_panel()
            return
        self.listening_pending = True
        self.begin_button.configure(state="disabled")
        self.status.configure(text="正在连接系统播放设备…")
        self.start_capture()

    def start_session(self, *, allow_generic=False):
        data = {name: field.get().strip() for name, field in self.draft.items()}
        project = self.project_choice.get().strip()
        if project:
            data["verified_facts"] = "\n".join(filter(None, (data["verified_facts"], project)))
        supplement = self.profile.get("1.0", "end-1c").strip()
        if self.selected_resume:
            resume_source = self.selected_resume
            data["resume"] = f"[资料：{resume_source['name']}]\n{resume_source['text']}"
            if supplement:
                data["resume"] += f"\n\n[资料：本场补充资料]\n{supplement}"
        else:
            data["resume"] = supplement
        if not allow_generic and not all(data[field] for field in ("company", "role", "round", "resume")):
            self.status.configure(text="请填写公司、岗位、轮次，并导入或粘贴简历。")
            return
        if len(data["resume"]) > MAX_TEXT or any(len(value) > 3000 for name, value in data.items()
                                                if name != "resume"):
            self.status.configure(text="本场资料过长，请删减后再开始。")
            return
        data["answer_cards"] = self.answer_cards.get("1.0", "end-1c").strip()
        try:
            session = InterviewSession(data)
        except ValueError as error:
            self.status.configure(text=str(error))
            return
        self.cancel_request()
        self.session = session
        self.session_metadata = {key: data[key] for key in ("company", "role", "round")}
        self.current_saved_interview_id = None
        self.session_token += 1
        self.selected_turn_id = None
        self.pinned_turn = None
        self.history_list.selection_clear(0, "end")
        self.refresh_history()
        self.retry_replaces_last = False
        self.source_excerpts = []
        self.source_button.configure(state="disabled")
        self.source_view.withdraw()
        self.replace_text(self.source_text, "")
        self.audio_chunks.clear()
        if self.realtime_asr is not None:
            self._reset_live_audio()
        self.caption_timeline = CaptionTimeline(f"session-{self.session_token}")
        self.question_composer = QuestionComposer(self.caption_timeline)
        self.turn_ledger = TurnLedger()
        self.saved_turn_snapshot = ()
        self.selected_caption_ids.clear()
        self.caption_lines.clear()
        self.show_latest_turn()
        self.live_transcript = ""
        self.transcript_dirty = False
        self.asr_state = "not_configured"
        self.caption_preview.configure(text="最近字幕：—")
        if self.capture is not None:
            self.capture.buffer.clear()
            self._start_realtime_asr()
        self.replace_text(self.transcript, "")
        self.clear_review()
        self.replace_text(self.answer, "")
        label = " · ".join(data[field] for field in ("company", "role", "round") if data[field]) or "通用场次"
        self.session_label.configure(text=f"本场已锁定 · {label}")
        self.status.configure(text=f"本场已锁定。问题播完后按 {self.shortcut_name()} 或点击回答这题。")
        self.tabs.select(self.qa_tab)
        self.session_return_button.pack(anchor="w", pady=(9, 0))
        self.update_buttons()
        return True

    def save_current_session(self):
        if self.memory is None:
            self.status.configure(text="本机面试记录存储不可用。")
            return False
        if self.session is None or not self.turn_ledger.turns:
            self.status.configure(text="本场还没有可保存的问题。")
            return False
        if self.busy or any(turn.status == "生成中" for turn in self.turn_ledger.turns.values()):
            self.status.configure(text="回答生成完成后再保存本场。")
            return False
        snapshot = tuple(self.turn_ledger.turns.values())
        if snapshot == self.saved_turn_snapshot:
            return True
        turns = []
        for turn in snapshot:
            entry = {"question": turn.question, "answer": turn.answer, "source": turn.source, "status": turn.status}
            raw_caption = "\n".join(self.caption_lines[key] for key in turn.caption_ids if key in self.caption_lines)
            if raw_caption:
                entry["raw_caption"] = raw_caption
            turns.append(entry)
        try:
            self.current_saved_interview_id = self.memory.save_interview(
                self.session_metadata, turns, item=self.current_saved_interview_id,
            )
        except (OSError, sqlite3.Error, ValueError):
            self.status.configure(text="本场保存失败。当前问答仍保留在窗口中。")
            return False
        self.saved_turn_snapshot = snapshot
        self.refresh_saved_interviews()
        self.update_buttons()
        self.status.configure(text="本场面试记录已保存在本机。")
        return True

    def build_listening_panel(self):
        panel = self.listening_panel = tk.Toplevel(self.root, background=ANSWER_BG)
        panel.withdraw()
        panel.title("系统音频")
        panel.attributes("-topmost", bool(self.preferences.get("panel_always_on_top", True)))
        panel.resizable(True, True)
        panel.protocol("WM_DELETE_WINDOW", self.restore_workbench)
        panel.bind("<Map>", lambda event: style_title_bar(panel, ANSWER_BG)
                   if event.widget is panel else None, add="+")

        body = tk.Frame(panel, bg=ANSWER_BG, padx=14, pady=12)
        body.pack(fill="both", expand=True)
        line = tk.Frame(body, bg=ANSWER_BG)
        line.pack(fill="x")
        self.listening_status = tk.Label(line, bg=ANSWER_BG, fg=ANSWER_TEXT,
                                         font=("Microsoft YaHei UI", 10, "bold"))
        self.listening_status.pack(side="left")
        self.listening_shortcut = tk.Label(line, bg=ANSWER_BG, fg=ANSWER_MUTED,
                                           font=("Microsoft YaHei UI", 9))
        self.listening_shortcut.pack(side="right")
        tk.Label(body, text="当前题", bg=ANSWER_BG, fg=ANSWER_MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(7, 0))
        self.listening_question = tk.Label(body, bg=ANSWER_BG, fg=ANSWER_TEXT, anchor="w", width=1,
                                          font=("Microsoft YaHei UI", 11, "bold"))
        self.listening_question.pack(fill="x", pady=(2, 3))
        caption_block = tk.Frame(body, bg=ANSWER_BG)
        tk.Label(caption_block, text="新字幕", bg=ANSWER_BG, fg=ANSWER_MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        self.listening_caption = tk.Label(caption_block, bg=ANSWER_BG, fg=ANSWER_MUTED,
                                         anchor="w", justify="left", width=1, height=2,
                                         font=("Microsoft YaHei UI", 9))
        self.listening_caption.pack(fill="x")
        self.listening_detail = tk.Label(body, bg=ANSWER_BG, fg=ANSWER_MUTED, anchor="w", width=1,
                                        wraplength=400,
                                        font=("Microsoft YaHei UI", 9))
        actions = tk.Frame(body, bg=ANSWER_BG)
        self.listening_answer = self.text_box(body, 8, readonly=True)
        actions.pack(side="bottom", fill="x", before=self.listening_answer.master)
        caption_block.pack(side="bottom", fill="x", pady=(3, 4), before=self.listening_answer.master)
        self.listening_detail.pack(side="bottom", fill="x", pady=(0, 2), before=self.listening_answer.master)
        self.listening_detail.configure(height=1)
        self.listening_answer.master.pack_configure(pady=(3, 3))
        self.listening_answer.configure(bg=ANSWER_BG, fg=ANSWER_TEXT, insertbackground=ANSWER_TEXT,
                                        highlightbackground="#343D49", highlightcolor=ANSWER_ACCENT)
        self.listening_answer.tag_configure("opening", font=("Microsoft YaHei UI", 13, "bold"),
                                            foreground=ANSWER_TEXT)
        self.listening_answer_display = ""
        self.listening_answer_request = None
        self.listening_ask = ttk.Button(actions, text="回答这题", style="Floating.Primary.TButton",
                                        command=self.request_audio)
        self.listening_ask.pack(side="left")
        self.listening_open = ttk.Button(actions, text="完整问答", style="Floating.TButton",
                                         command=self.restore_workbench)
        self.listening_open.pack(side="left", padx=6)
        self.listening_pause = ttk.Button(actions, text="暂停听题", style="Floating.TButton",
                                          command=self.toggle_pause)
        self.listening_pause.pack(side="left")
        self.listening_stop = ttk.Button(actions, text="停止", style="Floating.TButton",
                                         command=self.stop_listening)
        self.listening_stop.pack(side="right")
        work_area = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
            work_width = max(1, work_area.right - work_area.left)
            work_height = max(1, work_area.bottom - work_area.top)
            minimum_width = min(round(480 * self.ui_scale), work_width)
            minimum_height = min(round(420 * self.ui_scale), work_height)
            width = min(max(round(self.preferences.get("panel_width", 620) * self.ui_scale),
                            minimum_width), work_width)
            height = min(max(round(self.preferences.get("panel_height", 560) * self.ui_scale),
                             minimum_height), work_height)
            x = max(work_area.left, work_area.right - width - round(16 * self.ui_scale))
            y = max(work_area.top, work_area.bottom - height - round(16 * self.ui_scale))
            panel.minsize(minimum_width, minimum_height)
            panel.geometry(f"{width}x{height}+{x}+{y}")
        else:
            panel.geometry(f"{round(620 * self.ui_scale)}x{round(560 * self.ui_scale)}")
            panel.minsize(round(480 * self.ui_scale), round(420 * self.ui_scale))
        panel.bind("<Configure>", self.on_listening_panel_configure, add="+")

    def on_listening_panel_configure(self, event):
        if event.widget is not self.listening_panel or not self.listening_panel.winfo_viewable():
            return
        if self.panel_geometry_after is not None:
            self.root.after_cancel(self.panel_geometry_after)
        self.panel_geometry_after = self.root.after(500, self.save_listening_panel_geometry)

    def save_listening_panel_geometry(self):
        self.panel_geometry_after = None
        panel = self.listening_panel
        if panel is None or not panel.winfo_exists():
            return
        self.preferences["panel_width"] = round(panel.winfo_width() / self.ui_scale)
        self.preferences["panel_height"] = round(panel.winfo_height() / self.ui_scale)
        if self.memory is not None:
            try:
                self.memory.save_preferences({
                    "panel_width": self.preferences["panel_width"],
                    "panel_height": self.preferences["panel_height"],
                })
            except sqlite3.Error:
                self.status.configure(text="浮窗已调整，但尺寸无法保存到本机。")

    def show_listening_panel(self):
        if self.listening_panel is None:
            self.build_listening_panel()
        if self.privacy.get():
            try:
                self.listening_panel.update_idletasks()
                set_privacy(native_window(self.listening_panel), True)
            except OSError:
                self.privacy_caption.set("浮窗保护未确认")
                self.status.configure(text="Windows 未确认浮窗捕获保护。请在本场面试继续使用主窗口。")
                return
        self.update_listening_panel()
        self.listening_panel.deiconify()
        if self.root.state() == "normal":
            self.root.iconify()

    def update_listening_panel(self):
        panel = self.listening_panel
        if panel is None:
            return
        connected = self.capture is not None
        connecting = self.start_button.instate(("disabled",))
        if connected:
            title = "正在监听系统音频"
        elif self.paused:
            title = "已暂停听题"
        elif connecting:
            title = "正在重新连接"
        else:
            title = "系统音频已停止"
        self.listening_status.configure(text=title)
        self.listening_shortcut.configure(text=self.shortcut_name() if self.hotkey else "仅按钮")
        self.listening_pause.configure(text="恢复听题" if self.paused else "暂停听题")
        self.listening_ask.configure(state=self.ask_button.cget("state"))
        detail = self.status.cget("text")
        current = self.turn_ledger.current
        question = current.question if current else " ".join(self.transcript.get("1.0", "end-1c").split())
        caption = "\n".join(line[:42] + ("…" if len(line) > 42 else "")
                            for line in self.live_transcript.splitlines()[-2:])
        self.listening_question.configure(text=question or "等待下一道题")
        if not caption and self.asr_state == "text_only":
            caption = f"{self.provider.get()}：按回答时识别最近系统音频"
        elif not caption:
            caption = "等待系统播放声音"
        self.listening_caption.configure(text="字幕：" + caption)
        prompt = f"等对方说完，按 {self.shortcut_name()} 回答" if self.hotkey else "等对方说完，点击「回答这题」"
        answer = self.answer.get("1.0", "end-1c") or prompt
        previous = self.listening_answer_display
        if answer != previous or self.listening_answer_request != self.request_id:
            if self.listening_answer_request == self.request_id and previous and answer.startswith(previous):
                self.append_answer_text(self.listening_answer, answer[len(previous):])
            else:
                self.replace_text(self.listening_answer, answer)
                self.listening_answer.yview_moveto(0)
            self.listening_answer.tag_remove("opening", "1.0", "end")
            first_sentence = re.search(r"[。！？.!?\n]", answer)
            opening_end = first_sentence.end() if first_sentence else min(len(answer), 70)
            if opening_end:
                self.listening_answer.tag_add("opening", "1.0", f"1.0+{opening_end}c")
            self.listening_answer_display = answer
            self.listening_answer_request = self.request_id
        if self.listening_detail.cget("text") != detail:
            self.listening_detail.configure(text=detail)

    def restore_workbench(self):
        if self.listening_panel is not None:
            self.listening_panel.withdraw()
        if self.session is not None:
            self.tabs.select(self.qa_tab)
        self.root.deiconify()
        self.root.lift()

    def stop_listening(self):
        self.cancel_request()
        self.paused = False
        self.stop_capture()
        self.status.configure(text="已停止监听系统音频。")

    def toggle_pause(self):
        if self.paused:
            self.paused = False
            self.status.configure(text="正在恢复系统音频监听…")
            self.pause_button.configure(text="暂停听题")
            self.start_capture()
            return
        if self.capture is None:
            self.status.configure(text="当前没有正在监听的系统音频。")
            return
        self.paused = True
        self.stop_capture(preserve_panel=True)
        self.pause_button.configure(text="恢复听题")
        self.status.configure(text="已暂停听题。已收到的字幕和答案仍保留。")
        self.update_listening_panel()

    def _reset_live_audio(self):
        self.asr_epoch += 1
        self.audio_chunks.clear()
        if self.realtime_asr is not None:
            self.realtime_asr.stop()
            self.realtime_asr.join(1.5)
            self.realtime_asr = None
        self.asr_state = "stopped"

    def _enqueue_audio(self, data, frames):
        """Keep the PortAudio callback bounded and free of UI/network work."""
        self.audio_chunks.put(data, frames=frames)

    def _start_realtime_asr(self):
        if self.realtime_asr is not None or self.session is None:
            return
        if self.provider.get() != "阿里云百炼":
            self.asr_state = "text_only"
            provider = self.provider.get()
            if self.get_key():
                detail = (f"{provider} 已就绪。播放完问题后按 {self.shortcut_name()} 或点「回答这题」，"
                          "届时转写并生成回答。")
            else:
                detail = f"{provider} 尚未配置密钥。可先手输问题，或到设置里保存密钥。"
            self.caption_status.configure(text=detail)
            return
        key = self.get_key()
        workspace = self.workspace_id.get().strip()
        if not key or not workspace:
            self.asr_state = "not_configured"
            self.caption_status.configure(text="实时字幕未配置。可继续监听并使用手输问题。")
            return
        capture = self.capture
        if capture is None:
            return
        self.asr_started_at = time.perf_counter()
        epoch = self.asr_epoch
        try:
            self.realtime_asr = DashScopeRealtimeASR(
                api_key=key,
                workspace_id=workspace,
                region=self.region.get(),
                audio_queue=self.audio_chunks,
                sample_rate=capture.buffer.rate,
                channels=capture.buffer.channels,
                session_started_at=self.asr_started_at,
                emit=lambda kind, value: self.events.put(("asr_" + kind, (epoch, value))),
            )
            self.realtime_asr.start()
            self.asr_state = "connecting"
            self.caption_status.configure(text="正在连接实时字幕…")
        except (ValueError, OSError) as error:
            self.realtime_asr = None
            self.asr_state = "error"
            self.caption_status.configure(text=f"实时字幕未启动 · {error}")

    def _on_asr_status(self, value):
        state = value.get("state", "unknown") if isinstance(value, dict) else "unknown"
        self.asr_state = state
        labels = {
            "connecting": "正在连接实时字幕…",
            "connected": "实时字幕已连接 · 不使用麦克风",
            "reconnecting": "实时字幕断线，正在重连…",
            "audio_gap": "实时字幕有音频丢帧，继续追踪当前对话…",
            "error": f"实时字幕失败 · {value.get('error', '请改用手输问题。')}",
            "stopped": "实时字幕已停止",
            "finished": "实时字幕已结束",
        }
        self.caption_status.configure(text=labels.get(state, f"实时字幕 · {state}"))
        self.audio_asr_status.configure(text=self.caption_status.cget("text"))

    def _caption_to_timing(self, item, offset_ms=0):
        words = []
        for word in item.get("words", ()):
            if not isinstance(word, dict):
                continue
            try:
                words.append(WordTiming(offset_ms + int(word.get("begin_time", 0)),
                                        offset_ms + int(word.get("end_time", 0)),
                                        str(word.get("text", ""))))
            except (TypeError, ValueError):
                continue
        return words

    def _on_asr_caption(self, item):
        if self.session is None:
            return
        sentence_id = item.get("sentence_id")
        if sentence_id is None:
            self.asr_sentence_fallback += 1
            sentence_id = self.asr_sentence_fallback
        caption = self.caption_timeline.upsert(
            int(item.get("connection_id", 0)), int(sentence_id),
            int(item.get("begin_ms", 0)), int(item.get("end_ms", item.get("begin_ms", 0))),
            str(item.get("text", "")), bool(item.get("is_final")),
            offset_ms=int(item.get("offset_ms", 0)),
            words=self._caption_to_timing(item, int(item.get("offset_ms", 0))),
        )
        self.caption_lines[caption.caption_id] = caption.text
        recent = self.caption_timeline.recent(2)
        self.live_transcript = "\n".join(item.text for item in recent)
        self.caption_preview.configure(text="最近字幕：" + (self.live_transcript or "—"))
        if (not self.busy and not self.selected_caption_ids and not self.transcript_dirty
                and self.root.focus_get() != self.transcript and not self.transcript.tag_ranges("sel")):
            self.replace_text(self.transcript, self.live_transcript)

    def build_memory(self):
        ttk.Label(self.memory_tab, text="事实纠正或回答偏好。仅本场随会话清除，保存后可跨场使用。",
                  foreground=MUTED).pack(anchor="w")
        self.memory_input = ttk.Entry(self.memory_tab)
        self.memory_input.pack(fill="x", pady=6)
        controls = ttk.Frame(self.memory_tab)
        controls.pack(fill="x")
        self.memory_kind = ttk.Combobox(controls, values=("事实", "偏好"), state="readonly", width=7)
        self.memory_kind.set("事实")
        self.memory_kind.pack(side="left")
        for label, command in (("仅本场", self.add_feedback), ("保存新条目", self.save_memory),
                               ("更新所选", lambda: self.save_memory(edit=True)),
                               ("删除所选", self.delete_memory)):
            ttk.Button(controls, text=label, command=command).pack(side="left", padx=4)
        self.memory_list = tk.Listbox(self.memory_tab, bg=FIELD, fg=INK, selectbackground=ACCENT,
                                      relief="flat", height=10)
        self.memory_list.pack(fill="both", expand=True, pady=8)
        self.memory_list.bind("<<ListboxSelect>>", self.load_memory_selection)

    def refresh_memory(self):
        try:
            self.memory_rows = self.memory.list() if self.memory else []
        except sqlite3.Error:
            self.status.configure(text="本机记忆读取失败。")
        self.memory_list.delete(0, "end")
        for _, kind, value in self.memory_rows:
            self.memory_list.insert("end", f"{'事实' if kind == 'fact' else '偏好'} · {value}")

    def memory_selection(self):
        selected = self.memory_list.curselection()
        return self.memory_rows[selected[0]] if selected else None

    def load_memory_selection(self, _event):
        item = self.memory_selection()
        if item:
            self.memory_kind.set("事实" if item[1] == "fact" else "偏好")
            self.memory_input.delete(0, "end")
            self.memory_input.insert(0, item[2])

    def feedback_values(self):
        text = self.memory_input.get().strip()
        if not text or len(text) > 2000:
            self.status.configure(text="请输入 1–2,000 字的事实或偏好。")
            return None
        return ("fact" if self.memory_kind.get() == "事实" else "preference", text)

    def add_feedback(self):
        if self.session is None:
            self.status.configure(text="请先开始本场。")
            return
        values = self.feedback_values()
        if values:
            kind, text = values
            self.session.correct(text, kind)
            message = ("事实已更新。核对口述卡后，可开始新场重新启用。" if kind == "fact"
                       else "反馈会用于本场接下来的回答。")
            self.status.configure(text=message)

    def save_memory(self, edit=False):
        values = self.feedback_values()
        item = self.memory_selection() if edit else None
        if not values or (edit and item is None):
            return
        try:
            if self.memory is None:
                raise sqlite3.OperationalError()
            self.memory.save(*values, item=item[0] if item else None)
        except sqlite3.Error:
            self.status.configure(text="保存本机记忆失败。")
            return
        self.refresh_memory()
        if self.session and (values[0] == "fact" or (item and item[1] == "fact")):
            self.session.invalidate_cards()
        self.status.configure(text="已更新本机记忆，下次请求生效。")

    def delete_memory(self):
        item = self.memory_selection()
        if item and self.memory:
            try:
                self.memory.delete(item[0])
            except sqlite3.Error:
                self.status.configure(text="删除本机记忆失败。")
                return
            if self.session and item[1] == "fact":
                self.session.invalidate_cards()
            self.refresh_memory()
            self.status.configure(text="已删除所选记忆。")

    def build_settings(self):
        self.settings_category_names = {"audio": "声音与转写", "hotkey": "快捷键",
                                        "display": "浮窗与显示", "model": "模型与连接"}
        self.settings_category = tk.StringVar(value="audio")
        self.settings_tabs = ttk.Notebook(self.settings_tab, style="Hidden.TNotebook")
        self.settings_pages = {
            "audio": ttk.Frame(self.settings_tabs),
            "hotkey": ttk.Frame(self.settings_tabs),
            "display": ttk.Frame(self.settings_tabs),
            "model": ttk.Frame(self.settings_tabs),
        }
        for key, page in self.settings_pages.items():
            self.settings_tabs.add(page, text=self.settings_category_names[key])
        self.settings_tabs.pack(fill="both", expand=True)

        model_page = self.settings_pages["model"]
        service = ttk.Frame(model_page)
        service.pack(fill="x")
        ttk.Label(service, text="云服务").pack(side="left")
        self.api_keys = {name: tk.StringVar() for name in PROVIDERS}
        if self.secret_store:
            for name in PROVIDERS:
                try:
                    self.api_keys[name].set(self.secret_store.load(self.secret_name(name)))
                except SecretStoreError:
                    pass
        configured_keys = {
            name: bool(value.get().strip() or os.environ.get(PROVIDERS[name][1], "").strip())
            for name, value in self.api_keys.items()
        }
        selected_provider = select_startup_provider(
            configured_keys,
            preferred=self.preferences.get("provider"),
            workspace_id=self.preferences.get("dashscope_workspace", ""),
        )
        self.provider = tk.StringVar(value=selected_provider)
        self.provider_box = ttk.Combobox(service, textvariable=self.provider, values=tuple(PROVIDERS),
                                        state="readonly", width=15)
        self.provider_box.pack(side="left", padx=8)
        self.key = ttk.Entry(model_page, textvariable=self.api_keys[self.provider.get()],
                             show="•", width=48)
        self.key.pack(anchor="w", pady=(4, 4))
        self.key_hint = ttk.Label(model_page, foreground=MUTED)
        self.key_hint.pack(anchor="w", pady=(0, 4))
        key_actions = ttk.Frame(model_page)
        key_actions.pack(anchor="w", pady=(0, 8))
        self.save_key_button = ttk.Button(key_actions, text="保存密钥", command=self.save_api_key)
        self.save_key_button.pack(side="left")
        self.clear_key_button = ttk.Button(key_actions, text="清除已保存密钥", command=self.clear_api_key)
        self.clear_key_button.pack(side="left", padx=6)
        self.key_visibility = ttk.Button(key_actions, text="显示密钥", command=self.toggle_key_visibility)
        self.key_visibility.pack(side="left")
        self.model_test_button = ttk.Button(key_actions, text="测试文本连接", command=self.test_model_connection)
        self.model_test_button.pack(side="left", padx=6)
        self.model_test_status = ttk.Label(model_page, foreground=MUTED, wraplength=550)
        self.model_test_status.pack(anchor="w", pady=(0, 7))
        self.provider.trace_add("write", self.update_provider)
        self.provider.trace_add("write", self.save_provider_preference)
        self.workspace_row = ttk.Frame(model_page)
        ttk.Label(self.workspace_row, text="业务空间").pack(side="left")
        self.workspace_id = tk.StringVar(value=self.preferences.get("dashscope_workspace", ""))
        ttk.Entry(self.workspace_row, textvariable=self.workspace_id, width=33).pack(side="left", padx=6)
        ttk.Label(self.workspace_row, text="地域").pack(side="left", padx=(10, 4))
        self.region = tk.StringVar(value=self.preferences.get("dashscope_region", "北京"))
        ttk.Combobox(self.workspace_row, textvariable=self.region, values=("北京", "新加坡"),
                     state="readonly", width=10).pack(side="left")
        self.workspace_row.pack(anchor="w", pady=(0, 8))
        self.cloud_disclosure = ttk.Label(
            model_page,
            text="开启实时字幕会将系统播放音频发送到阿里云百炼；不连接麦克风。仅在开始听题时发送。",
            foreground=MUTED, wraplength=600,
        )
        self.cloud_disclosure.pack(anchor="w", pady=(0, 12))
        self.update_provider()

        audio_page = self.settings_pages["audio"]
        ttk.Label(audio_page, text="系统播放设备", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        row = ttk.Frame(audio_page)
        row.pack(fill="x", pady=(4, 5))
        self.device = ttk.Combobox(row, width=56, state="readonly")
        self.device.pack(side="left", fill="x", expand=True)
        self.refresh_button = ttk.Button(row, text="刷新", command=self.refresh_devices)
        self.refresh_button.pack(side="left", padx=(8, 0))
        ttk.Label(audio_page, text="只采集 Windows 播放回环，不连接麦克风。固定设备可停止缓冲后更改。",
                  foreground=MUTED).pack(anchor="w", pady=(0, 16))
        self.audio_settings_test = ttk.Button(audio_page, text="试一下声音", command=self.start_audio_check)
        self.audio_settings_test.pack(anchor="w")
        self.audio_settings_status = ttk.Label(audio_page, foreground=MUTED, wraplength=640)
        self.audio_settings_status.pack(anchor="w", pady=(6, 0))
        self.audio_asr_status = ttk.Label(audio_page, foreground=MUTED, wraplength=640)
        self.audio_asr_status.pack(anchor="w", pady=(5, 0))

        hotkey_page = self.settings_pages["hotkey"]
        ttk.Label(hotkey_page, text="回答快捷键", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        hotkey_row = ttk.Frame(hotkey_page)
        hotkey_row.pack(fill="x", pady=(4, 5))
        self.shortcut_label = ttk.Label(hotkey_row, text=self.shortcut_name(), width=19)
        self.shortcut_label.pack(side="left")
        self.record_button = ttk.Button(hotkey_row, text="录入快捷键", command=self.start_hotkey_recording)
        self.record_button.pack(side="left")
        self.record_button.bind("<KeyPress>", self.record_hotkey)
        self.record_button.bind("<FocusOut>", self.cancel_hotkey_recording)
        ttk.Button(hotkey_row, text="恢复默认", command=lambda: self.set_shortcut(DEFAULT)).pack(
            side="left", padx=6,
        )
        ttk.Button(hotkey_row, text="仅按钮", command=lambda: self.set_shortcut(None)).pack(side="left")
        ttk.Label(hotkey_page, text="可用 Ctrl、Alt、Shift 加字母、数字、空格或 F1–F11。"
                  "\n缓冲时快捷键全局生效。Shift+Z 会占用大写 Z。",
                  foreground=MUTED, wraplength=600).pack(anchor="w")
        display_page = self.settings_pages["display"]
        ttk.Label(display_page, text="浮窗与显示", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        self.panel_on_top = tk.BooleanVar(value=bool(self.preferences.get("panel_always_on_top", True)))
        ttk.Checkbutton(display_page, text="浮窗保持在最前面", variable=self.panel_on_top,
                        command=self.save_panel_display_preferences).pack(anchor="w", pady=(8, 3))
        ttk.Checkbutton(display_page, text="截图与共享保护",
                        variable=self.privacy, command=self.apply_privacy).pack(anchor="w", pady=(12, 3))
        self.privacy_label = ttk.Label(display_page, text="关闭", foreground=MUTED, wraplength=680)
        self.privacy_label.pack(anchor="w")
        self.measure = tk.BooleanVar(value=False)
        diagnostics = ttk.Frame(display_page)
        diagnostics.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(diagnostics, text="记录请求耗时", variable=self.measure).pack(side="left")
        ttk.Button(diagnostics, text="导出记录", command=self.export_timing).pack(side="left", padx=7)
        ttk.Label(diagnostics, text="问答字号").pack(side="left", padx=(8, 4))
        font_box = ttk.Combobox(diagnostics, values=("11", "13", "15", "17"), width=4, state="readonly")
        font_box.set(str(self.preferences.get("font_size", 11)))
        font_box.pack(side="left")
        font_box.bind("<<ComboboxSelected>>", lambda _: self.resize_text(int(font_box.get())))
        appearance = tk.Frame(display_page, bg=ANSWER_BG, padx=16, pady=12)
        appearance.pack(fill="x", pady=(20, 0))
        tk.Label(appearance, text="样式预览  ·  当前题", bg=ANSWER_BG, fg=ANSWER_MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        tk.Label(appearance, text="怎么确认 Agent 的动作生效了？", bg=ANSWER_BG, fg=ANSWER_TEXT,
                 font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(4, 7))
        self.display_preview_answer = tk.Label(
            appearance, text="我会读取操作后的界面状态，再和目标值核对。",
            bg=ANSWER_BG, fg=ANSWER_TEXT, wraplength=470, justify="left",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        self.display_preview_answer.pack(anchor="w")
        tk.Label(appearance, text="新字幕  ·  如果界面一直没更新呢？", bg=ANSWER_BG, fg=ANSWER_MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(7, 0))

    def toggle_key_visibility(self):
        visible = bool(self.key.cget("show"))
        self.key.configure(show="" if visible else "•")
        self.key_visibility.configure(text="隐藏密钥" if visible else "显示密钥")

    def test_model_connection(self):
        provider = self.provider.get()
        key = self.get_key()
        if not key:
            self.model_test_status.configure(text="请先填写 API Key。")
            return
        factory = self.api_client_factory(provider)
        self.model_test_button.state(("disabled",))
        self.model_test_status.configure(text=f"{provider} 文本服务测试中…")

        def test():
            try:
                with factory(key) as client:
                    stream = iter(client.answer("连接测试，请只回答“收到”。"))
                    try:
                        if not any(part.strip() for part in stream):
                            raise ApiError("文本服务未返回正文")
                    finally:
                        if hasattr(stream, "close"):
                            stream.close()
            except (ApiError, ValueError, OSError, httpx.HTTPError):
                status = "文本连接失败。请核对密钥、业务空间与网络。"
            else:
                status = "文本服务已返回正文。语音状态见「声音与转写」。"
            self.events.put(("model_test", (provider, status)))

        threading.Thread(target=test, daemon=True).start()

    def save_panel_display_preferences(self):
        enabled = self.panel_on_top.get()
        self.preferences["panel_always_on_top"] = enabled
        if self.listening_panel is not None:
            self.listening_panel.attributes("-topmost", enabled)
        if self.memory is not None:
            try:
                self.memory.save_preferences({"panel_always_on_top": enabled})
            except sqlite3.Error:
                self.status.configure(text="浮窗设置无法保存到本机。")

    @staticmethod
    def secret_name(provider):
        return {"阿里云百炼": "dashscope", "DeepSeek": "deepseek", "OpenAI": "openai"}[provider]

    def save_api_key(self):
        if not self.secret_store:
            self.status.configure(text="本机 DPAPI 密钥存储不可用。")
            return
        provider = self.provider.get()
        try:
            self.secret_store.save(self.secret_name(provider), self.api_keys[provider].get().strip())
            if self.memory:
                self.memory.save_preferences({
                    "provider": provider,
                    "dashscope_workspace": self.workspace_id.get().strip(),
                    "dashscope_region": self.region.get(),
                })
        except (SecretStoreError, OSError, sqlite3.Error, ValueError):
            self.status.configure(text="无法保存设置。请检查 Key 格式和本机用户目录权限。")
            return
        self.status.configure(text=f"{provider} 密钥已在本机加密保存。")

    def save_provider_preference(self, *_):
        if self.memory:
            try:
                self.memory.save_preferences({"provider": self.provider.get()})
            except sqlite3.Error:
                pass

    def clear_api_key(self):
        provider = self.provider.get()
        self.api_keys[provider].set("")
        try:
            if self.secret_store:
                self.secret_store.delete(self.secret_name(provider))
        except (SecretStoreError, OSError):
            self.status.configure(text="无法清除已保存密钥。请检查本机用户目录权限。")
            return
        self.status.configure(text="已清除本机保存的 API Key。")

    def resize_text(self, size):
        self.preferences["font_size"] = size
        self.display_preview_answer.configure(font=("Microsoft YaHei UI", size + 2, "bold"))
        widgets = [self.transcript, self.answer, self.source_text, self.history_text]
        if self.listening_panel is not None:
            widgets.append(self.listening_answer)
        for widget in widgets:
            widget.configure(font=("Microsoft YaHei UI", size))
        if self.listening_panel is not None:
            self.listening_answer.tag_configure("opening", font=("Microsoft YaHei UI", size + 2, "bold"))
        if self.memory is not None:
            try:
                self.memory.save_preferences({"font_size": size})
            except sqlite3.Error:
                self.status.configure(text="字号已调整，但无法保存到本机。")

    def export_timing(self):
        source = settings_path().with_name("timings.jsonl")
        if self.privacy.get():
            self.status.configure(text="请在停止共享后导出耗时记录。")
            return
        if not source.is_file():
            self.status.configure(text="尚无耗时记录。启用记录后运行一次问答。")
            return
        target = filedialog.asksaveasfilename(title="导出耗时记录", defaultextension=".jsonl",
                                             initialfile="问答耗时.jsonl",
                                             filetypes=[("JSON Lines", "*.jsonl")])
        if not target:
            return
        try:
            shutil.copyfile(source, target)
        except OSError:
            self.status.configure(text="导出失败。请选择另一处可写位置。")
        else:
            self.status.configure(text="耗时记录已导出。")

    def apply_privacy(self):
        enabled = self.privacy.get()
        listening_panel = self.__dict__.get("listening_panel")
        try:
            set_privacy(native_window(self.root), enabled)
            self.source_view.update_idletasks()
            set_privacy(native_window(self.source_view), enabled)
            if listening_panel is not None:
                listening_panel.update_idletasks()
                set_privacy(native_window(listening_panel), enabled)
        except OSError:
            self.source_view.withdraw()
            if listening_panel is not None:
                listening_panel.withdraw()
            self.privacy_caption.set("保护未确认")
            self.privacy_label.configure(text="Windows 未确认所有窗口的捕获设置。")
        else:
            self.privacy_caption.set("保护已开启" if enabled else "截图保护")
            state = ("Windows 已对主窗、参考资料窗和当前浮窗应用捕获排除。共享效果请从接收端核对。"
                     if enabled else "关闭")
            self.privacy_label.configure(text=state)

    def shortcut_name(self):
        return self.shortcut.label if self.shortcut is not None else "仅按钮"

    def start_hotkey_recording(self):
        self.recording_hotkey = True
        self.record_button.focus_set()
        self.bind_hotkey()
        self.shortcut_label.configure(text="等待按键…")
        self.status.configure(text="按下要使用的组合，或按 Esc 取消。")

    def cancel_hotkey_recording(self, _event=None):
        if self.recording_hotkey:
            self.recording_hotkey = False
            self.shortcut_label.configure(text=self.shortcut_name())
            self.bind_hotkey()
            self.status.configure(text="已取消录入。")

    def record_hotkey(self, event):
        if not self.recording_hotkey:
            return None
        if event.keysym == "Escape":
            self.cancel_hotkey_recording()
        else:
            shortcut = Shortcut.from_tk(event.keysym, event.state, event.keycode)
            if shortcut is not None:
                self.set_shortcut(shortcut)
        return "break"

    def set_shortcut(self, shortcut):
        previous = self.shortcut
        self.recording_hotkey = False
        self.shortcut = shortcut
        error = self.bind_hotkey()
        if error:
            self.shortcut = previous
            self.bind_hotkey()
            status = f"{error}。已恢复 {self.shortcut_name()}。"
        else:
            status = f"快捷键已设置为 {self.shortcut_name()}。"
            try:
                save_shortcut(shortcut, self.shortcut_path)
            except OSError:
                status = f"当前使用 {self.shortcut_name()}。本机设置保存失败。"
        self.shortcut_label.configure(text=self.shortcut_name())
        self.status.configure(text=status)

    def refresh_devices(self):
        previous = self.device.get()
        self.devices = {"Windows 默认播放设备": None}
        audio = None
        try:
            audio = pyaudio.PyAudio()
            devices, _ = playback_devices(audio)
            for index, device in devices.items():
                name = str(device["name"]).removesuffix(" [Loopback]")
                self.devices[f"{name}  ·  {index}"] = index
        except (OSError, ValueError) as error:
            self.status.configure(text=f"无法列出播放设备（{error}）。")
        finally:
            if audio is not None:
                audio.terminate()
        self.device.configure(values=tuple(self.devices))
        self.device.set(previous if previous in self.devices else next(iter(self.devices)))

    def get_key(self):
        provider = self.provider.get()
        _, env_name = PROVIDERS[provider]
        key = self.api_keys[provider].get().strip() or os.environ.get(env_name, "").strip()
        if not key and self.secret_store:
            try:
                key = self.secret_store.load(self.secret_name(provider))
                self.api_keys[provider].set(key)
            except SecretStoreError:
                self.status.configure(text="已保存的密钥无法解密。请在设置中重新保存。")
        return key

    def update_provider(self, *_):
        provider = self.provider.get()
        _, env_name = PROVIDERS[provider]
        self.key.configure(textvariable=self.api_keys[provider])
        stored = "保存在 Windows 用户配置中（DPAPI）" if provider == "阿里云百炼" else f"留空时读取 {env_name}"
        self.key_hint.configure(text=f"{provider} API Key · {stored}")
        if provider == "阿里云百炼":
            self.workspace_row.pack(anchor="w", pady=(0, 8), before=self.cloud_disclosure)
            self.cloud_disclosure.configure(
                text="开启实时字幕会将系统播放音频发送到阿里云百炼；不连接麦克风。仅在开始听题时发送。"
            )
        else:
            self.workspace_row.pack_forget()
            self.cloud_disclosure.configure(
                text="旧版 DeepSeek / OpenAI 路径仅在回答时上传当前题目音频和本题所需资料。"
            )

    def api_client_factory(self, provider):
        if self.api_factory:
            return self.api_factory
        if provider == "阿里云百炼":
            workspace, region = self.workspace_id.get().strip(), self.region.get()
            return lambda key: DashScopeClient(key, workspace, region)
        return PROVIDERS[provider][0]

    def bind_hotkey(self):
        if self.hotkey is not None:
            self.hotkey.close()
            self.hotkey = None
        if self.capture is None:
            return
        shortcut = self.shortcut
        state = "仅按钮"
        error_text = None
        if self.recording_hotkey:
            state = "正在录入快捷键"
        elif shortcut is not None:
            try:
                self.hotkey = Hotkey(shortcut.modifiers,
                                     lambda: self.events.put(("hotkey", (shortcut, time.perf_counter()))),
                                     key=shortcut.key)
                state = shortcut.label
            except OSError as error:
                detail = "已被占用" if error.winerror == 1409 else "注册失败"
                error_text = f"{shortcut.label} {detail}"
                state = f"{error_text}，可用按钮或换键"
        self.capture_label.configure(text=f"正在缓冲 · {self.capture.device_name} · {state}")
        return error_text

    def toggle_capture(self):
        if self.capture is not None:
            self.stop_capture()
            return
        self.start_capture()

    def start_capture(self):
        self.capture_attempt += 1
        attempt = self.capture_attempt
        self.start_button.configure(state="disabled")
        self.device.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.capture_label.configure(text="正在连接播放设备…")
        index = self.devices.get(self.device.get())
        self.default_capture = index is None
        self.playback_id = default_playback_id() if self.default_capture else None
        self.next_device_poll = time.monotonic() + 1.0

        def start():
            with self.capture_change:
                if self.closing:
                    return
                try:
                    self.events.put(("started", (attempt, PlaybackCapture(index, on_chunk=self._enqueue_audio))))
                except (OSError, NoAudio, ValueError) as error:
                    self.events.put(("capture_error", (attempt, str(error))))

        threading.Thread(target=start, daemon=True).start()

    def stop_capture(self, *, reconnecting=False, preserve_panel=False):
        self.capture_attempt += 1
        self.listening_pending = False
        self._reset_live_audio()
        capture, self.capture = self.capture, None
        self.bind_hotkey()
        try:
            if capture is not None:
                capture.close()
        except OSError as error:
            self.status.configure(text=f"释放播放设备时出错（{error}）。")
        self.capture_label.configure(text="未缓冲")
        self.start_button.configure(text="开始缓冲", state="normal")
        self.begin_button.configure(state="normal")
        self.device.configure(state="readonly")
        self.refresh_button.configure(state="normal")
        self.meter.configure(value=0)
        self.caption_status.configure(text="实时字幕未开始")
        self.update_buttons()
        if not reconnecting and not preserve_panel and self.listening_panel is not None and not self.closing:
            self.listening_panel.withdraw()
            if self.root.state() == "iconic":
                self.restore_workbench()

    def import_profile(self):
        if self.privacy.get():
            self.status.configure(text="共享隐私开启时，请粘贴资料。文件选择器可在共享前使用。")
            return
        self.navigate("documents")
        self.choose_resume_file()

    @staticmethod
    def replace_text(widget, text):
        state = widget.cget("state")
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state=state)

    @staticmethod
    def append_answer_text(widget, text):
        top_line = widget.index("@0,0") if widget.yview()[0] > 0 else None
        widget.configure(state="normal")
        widget.insert("end", text)
        widget.configure(state="disabled")
        if top_line is not None:
            widget.yview(top_line)

    def request_audio(self, triggered_at=None):
        triggered_at = time.perf_counter() if triggered_at is None else triggered_at
        if self.capture is None:
            return
        if self.session is None:
            self.status.configure(text="请先在「面试准备」开始本场。")
            return
        manual_draft = self.transcript.get("1.0", "end-1c").strip() if self.transcript_dirty else ""
        if self.caption_timeline.all() and self.asr_state in {"connected", "reconnecting", "audio_gap"}:
            cutoff_ms = max(0, round((triggered_at - self.asr_started_at) * 1000))
            turn = self.question_composer.submit(manual_draft, cutoff_ms=cutoff_ms, now=triggered_at)
            if turn is not None:
                self.transcript_dirty = False
                self.replace_text(self.transcript, turn.question)
                self.request(question=turn.question, turn=turn, allow_parallel=True,
                             triggered_at=triggered_at)
                return
            self.status.configure(text="按键前还没有可提交的完整字幕，请再听一句或手输问题。")
            return
        if self.provider.get() == "阿里云百炼":
            self.status.configure(text="实时字幕尚未连接。请等待字幕连接，或直接手输问题。")
            return
        if self.busy:
            return
        buffer = self.capture.buffer
        try:
            seconds = int(self.window.get().split()[0])
            audio = buffer.snapshot(seconds, ended_at=triggered_at)
        except NoAudio as error:
            self.status.configure(text=str(error))
            return
        self.request(buffer=buffer, audio=audio, triggered_at=triggered_at)

    def request_text(self):
        question = self.transcript.get("1.0", "end-1c").strip()
        if question:
            turn = self.question_composer.submit(question, now=time.perf_counter())
            if turn is None and self.busy:
                return
            self.transcript_dirty = False
            self.request(question=question, turn=turn, allow_parallel=True)
        else:
            self.status.configure(text="请先在转写框里输入一个问题。")

    def retry_answer(self):
        question = self.transcript.get("1.0", "end-1c").strip()
        if question:
            self.request(question=question, reuse=self.retry_replaces_last)

    def cancel_request(self):
        if self.busy:
            self.cancelled_request_ids.add(self.request_id)
            turn_id = self.request_turn_map.get(self.request_id)
            if turn_id:
                self.turn_ledger.update_status(turn_id, "已取消")
        self.request_id += 1
        if self.busy:
            self.busy = False
            self.update_buttons()
            self.status.configure(text="已取消本地等待。")

    def request(self, *, question=None, buffer=None, audio=None, reuse=False, triggered_at=None,
                turn=None, allow_parallel=False):
        if self.busy and not allow_parallel:
            return
        if self.session is None:
            self.status.configure(text="请先在「面试准备」开始本场。")
            return
        provider = self.provider.get()
        factory = self.api_client_factory(provider)
        key = self.get_key()
        deep = self.mode.get() == "深度"
        local_only = bool(buffer is None and question and self.session.quick_answer(question, deep=deep))
        if not local_only and not key:
            self.status.configure(text=f"请先在设置里填写 {provider} API Key。")
            return
        if not local_only and (not key.isascii() or any(char.isspace() for char in key)):
            self.status.configure(text="API Key 格式有误，请检查是否包含空格或换行。")
            return
        if question and len(question) > 5000:
            self.status.configure(text="问题超过 5,000 字符，请删减后再试。")
            return
        session = copy.deepcopy(self.session)
        try:
            memories = self.memory.list() if self.memory else []
        except sqlite3.Error:
            self.status.configure(text="本机记忆读取失败，请稍后重试。")
            return
        for _, kind, text in reversed(memories[:8]):
            session.correct(text, kind, remembered=True)
        self.retry_replaces_last = reuse
        self.request_id += 1
        request_id = self.request_id
        if turn is not None:
            turn = self.turn_ledger.create(
                turn.question, source=turn.source, caption_ids=turn.caption_ids, cutoff_ms=turn.cutoff_ms,
                needs_review=turn.needs_review, turn_id=turn.turn_id,
            )
        elif reuse and self.turn_ledger.current and question:
            turn = replace(self.turn_ledger.current, question=question, answer="", status="生成中")
            self.turn_ledger.turns[turn.turn_id] = turn
            self.visible_turn_ids = ()
        else:
            turn = self.turn_ledger.create(question or "待转写题目", source="手输" if question else "语音")
        self.request_turn_map[request_id] = turn.turn_id
        self.request_session_tokens[request_id] = self.session_token
        self.request_modes[request_id] = self.mode.get()
        self.refresh_turn_list()
        started_at = time.perf_counter() if triggered_at is None else triggered_at
        self.timing = {"mode": self.mode.get(), "source": "audio" if buffer else "text", "provider": provider,
                       "sentence_candidates": []}
        self.measure_request = self.measure.get()
        self.characters_received = 0
        self.update_latency = started_at
        self.busy = True
        self.update_buttons()
        self.clear_review()
        self.replace_text(self.answer, "")
        self.source_excerpts = []
        self.source_button.configure(state="disabled")
        self.source_view.withdraw()
        self.replace_text(self.source_text, "")
        self.status.configure(text="正在转写最近的系统音频…" if buffer else "正在生成回答…")
        self.timing["feedback_ui_ms"] = round((time.perf_counter() - started_at) * 1000, 1)

        def work():
            transcript = question
            chunks = []

            def publish(kind, value=None):
                # Queue every event. The UI consumes only the current turn, while
                # stale chunks still update their own ledger entry.
                self.events.put((kind, (request_id, value)))

            try:
                with (nullcontext() if local_only else factory(key)) as api:
                    if buffer is not None:
                        wav = buffer.encode(audio)
                        publish("encoded", len(wav))
                        transcript = api.transcribe(wav)
                        publish("transcript", transcript)
                        review = review_question(transcript, session.aliases)
                        if review.message:
                            publish("review", (transcript, review))
                            return
                    card = session.quick_answer(transcript, deep=deep)
                    if card:
                        publish("card", [{"source": "本场已确认口述卡", "text": card}])
                        parts = (card,)
                    else:
                        prepared, sources = session.prepare(transcript, reuse=reuse, deep=deep)
                        publish("generating", sources)
                        parts = api.answer(transcript, prepared_input=prepared, deep=deep)
                    for chunk in parts:
                        chunks.append(chunk)
                        publish("chunk", chunk)
            except (ApiError, NoAudio) as error:
                publish("error", str(error))
            except httpx.HTTPError:
                publish("error", f"{provider} 连接失败或超时。请检查网络后重试。")
            except Exception:
                publish("error", "处理请求失败。请重试或改用文字提问。")
            else:
                publish("done", (transcript, "".join(chunks), reuse))

        threading.Thread(target=work, daemon=True).start()

    def update_buttons(self):
        ready = self.session is not None
        self.ask_button.configure(state="normal" if self.capture and ready else "disabled")
        self.text_button.configure(state="normal" if ready else "disabled")
        self.retry_button.configure(state="normal" if ready and not self.busy else "disabled")
        self.cancel_button.configure(state="normal" if self.busy else "disabled")
        self.open_panel_button.configure(state="normal" if ready and (self.capture or self.paused) else "disabled")
        if hasattr(self, "save_session_button"):
            snapshot = tuple(self.turn_ledger.turns.values())
            saved = bool(snapshot) and snapshot == self.saved_turn_snapshot
            can_save = ready and bool(snapshot) and not saved and not self.busy and not any(
                turn.status == "生成中" for turn in snapshot
            )
            self.save_session_button.configure(state="normal" if can_save else "disabled",
                                               text="本场已保存" if saved else "保存本场")

    def response_event(self, kind, event):
        request_id, value = event
        turn_id = self.request_turn_map.get(request_id)
        if turn_id and self.request_session_tokens.get(request_id) != self.session_token:
            return
        turn = self.turn_ledger.get(turn_id) if turn_id else None
        if turn and request_id not in self.cancelled_request_ids:
            if kind == "transcript":
                self.turn_ledger.turns[turn_id] = replace(turn, question=value)
                self.visible_turn_ids = ()
                self.refresh_turn_list()
            elif kind == "chunk":
                self.turn_ledger.append_answer(turn_id, value)
            elif kind in {"review", "error", "done"}:
                self.turn_ledger.update_status(turn_id, {"review": "待确认", "error": "失败", "done": "已完成"}[kind])
        if request_id != self.request_id:
            if request_id in self.cancelled_request_ids:
                if kind in {"done", "error"} and turn_id:
                    self.turn_ledger.update_status(turn_id, "已取消")
                return
            if kind == "done" and turn_id:
                if self.request_session_tokens.get(request_id) == self.session_token:
                    question_turn = self.turn_ledger.get(turn_id)
                    transcript, answer, reuse = value
                    self.session.record(question_turn.question if question_turn else transcript,
                                        answer, reuse=reuse,
                                        mode=self.request_modes.get(request_id, "快速"))
                    self.refresh_history()
            self.update_buttons()
            return
        elapsed = round((time.perf_counter() - self.update_latency) * 1000, 1)
        if kind == "encoded":
            self.timing.update(encoded_ms=elapsed, wav_bytes=value)
        elif kind in {"generating", "card"}:
            self.timing["generation_started_ms"] = elapsed
            self.timing["answer_path"] = "confirmed_card" if kind == "card" else "cloud"
            self.source_excerpts = value
            self.source_button.configure(state="normal" if value else "disabled")
        elif kind == "transcript":
            self.replace_text(self.transcript, value)
            self.status.configure(text="已完成转写，正在生成回答…")
            self.timing["transcript_ui_ms"] = elapsed
        elif kind == "review":
            transcript, review = value
            self.busy = False
            self.update_buttons()
            self.review_original, self.review_replacement = transcript, review.replacement
            self.review_label.configure(text=review.message)
            self.review_label.pack(anchor="w", pady=(5, 0))
            if review.replacement:
                self.review_button.configure(text="采用候选词")
                self.review_button.pack(side="left", padx=4)
            self.status.configure(text="请确认转写，再提交文字问题。")
            self.timing.update(complete_ui_ms=elapsed, status="review")
            if self.measure_request:
                self.write_timing()
        elif kind == "chunk":
            self.append_answer_text(self.answer, value)
            if value.strip():
                self.timing.setdefault("first_text_ui_ms", elapsed)
            boundaries = self.timing["sentence_candidates"]
            for offset, char in enumerate(value, 1):
                if char in ".!?。！？\n" and len(boundaries) < 24:
                    boundaries.append([self.characters_received + offset, elapsed])
            self.characters_received += len(value)
        elif kind in {"done", "error"}:
            self.busy = False
            self.update_buttons()
            self.timing.update(complete_ui_ms=elapsed, status=kind)
            if kind == "done":
                transcript, answer, reuse = value
                if turn_id:
                    self.turn_ledger.update_status(turn_id, "已完成")
                self.session.record(transcript, answer, reuse=reuse, mode=self.timing["mode"])
                self.refresh_history()
                self.retry_replaces_last = True
                label = "已匹配本场口述卡" if self.timing.get("answer_path") == "confirmed_card" else "回答已生成"
                self.status.configure(text=f"{label} · {elapsed / 1000:.1f} 秒")
            else:
                self.status.configure(text=value)
            if self.measure_request:
                self.write_timing()

    def write_timing(self):
        path = settings_path().with_name("timings.jsonl")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(self.timing, ensure_ascii=False) + "\n")
        except OSError:
            self.status.configure(text=f"{self.status.cget('text')} · 耗时日志写入失败。")

    def tick(self):
        if self.tabs.select() == str(self.profile_tab):
            self.update_preflight()
        for _ in range(200):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "hotkey":
                shortcut, triggered_at = value
                if self.hotkey is not None and shortcut == self.shortcut:
                    self.request_audio(triggered_at)
            elif kind == "started":
                attempt, capture = value
                if attempt != self.capture_attempt:
                    capture.close()
                    continue
                self.capture = capture
                self.paused = False
                self.start_button.configure(state="normal", text="停止缓冲")
                self.begin_button.configure(state="normal")
                self.bind_hotkey()
                self._start_realtime_asr()
                self.update_buttons()
                self.status.configure(text="问题播完后按快捷键或点「回答这题」。")
                if self.listening_pending:
                    self.listening_pending = False
                    self.show_listening_panel()
            elif kind == "capture_error":
                attempt, error = value
                if attempt != self.capture_attempt:
                    continue
                was_pending = self.listening_pending
                self.stop_capture()
                if was_pending:
                    self.tabs.select(self.profile_tab)
                self.status.configure(text=f"无法开始缓冲 · {error}")
            elif kind == "resume_import":
                self.finish_resume_import(*value)
            elif kind == "model_test":
                self.model_test_button.state(("!disabled",))
                provider, result = value
                self.model_test_status.configure(text=f"{provider} · {result}")
            elif kind == "audio_test":
                self.show_audio_check_result(value)
            elif kind == "asr_status":
                epoch, state = value
                if epoch == self.asr_epoch:
                    self._on_asr_status(state)
            elif kind == "asr_caption":
                epoch, caption = value
                if epoch == self.asr_epoch:
                    self._on_asr_caption(caption)
            else:
                self.response_event(kind, value)

        if self.capture is not None:
            try:
                active = self.capture.is_active()
            except OSError:
                active = False
            if self.default_capture and time.monotonic() >= self.next_device_poll:
                self.next_device_poll = time.monotonic() + 1.0
                current = default_playback_id()
                if current and current != self.playback_id:
                    self.stop_capture(reconnecting=True)
                    self.start_capture()
                    self.status.configure(text="Windows 默认播放设备已更换，正在重新连接。")
                    self.update_listening_panel()
                    self.tick_timer = self.root.after(35, self.tick)
                    return
            if active:
                self.meter.configure(value=min(100, self.capture.buffer.meter() / 120))
            else:
                self.stop_capture()
                self.status.configure(text="播放设备已断开。请刷新设备后重新开始缓冲。")
        if self.listening_panel is not None and self.listening_panel.state() == "normal":
            self.update_listening_panel()
        self.tick_timer = self.root.after(35, self.tick)

    def close(self):
        self.root.after_cancel(self.tick_timer)
        if self.panel_geometry_after is not None:
            self.root.after_cancel(self.panel_geometry_after)
        self.cancel_request()
        with self.capture_change:
            self.closing = True
            self.stop_capture()
            while not self.events.empty():
                kind, value = self.events.get()
                if kind == "started":
                    value[1].close()
        if self.memory is not None:
            self.memory.close()
        self.root.destroy()


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except OSError:
        pass


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    AudioQA(root)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in {"--live-desktop-probe", "--live-card-probe"}:
        from pathlib import Path

        from benchmark_deepseek import desktop_probe

        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise SystemExit(2)
        enable_dpi_awareness()
        desktop_probe(key, Path(sys.argv[2]), confirmed_card=sys.argv[1] == "--live-card-probe")
    elif len(sys.argv) == 3 and sys.argv[1] == "--capture-receiver":
        from windows_probe import capture_receiver

        enable_dpi_awareness()
        capture_receiver(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] in {"--release-live", "--release-soak"}:
        from retest_ms import run

        enable_dpi_awareness()
        run(sys.argv[2], "live" if sys.argv[1] == "--release-live" else "soak")
    elif len(sys.argv) == 3 and sys.argv[1] in {"--self-test", "--loopback-peer", "--privacy-test", "--listening-test"}:
        from self_test import peer, run, run_listening, run_privacy

        enable_dpi_awareness()
        action = {"--self-test": run, "--loopback-peer": peer, "--privacy-test": run_privacy,
                  "--listening-test": run_listening}[sys.argv[1]]
        action(sys.argv[2])
    else:
        main()
