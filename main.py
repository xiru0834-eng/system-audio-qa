import audioop
import ctypes
import copy
import json
import os
import queue
import shutil
import sqlite3
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from contextlib import nullcontext
from tkinter import filedialog, ttk

import httpx
import pyaudiowpatch as pyaudio

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

BG = "#191D24"
PANEL = "#212831"
FIELD = "#252D38"
BORDER = "#3E4A5A"
INK = "#EDF0F4"
MUTED = "#B1BAC7"
ACCENT = "#9EB6D6"
FOCUS = "#BDD0E8"
ACTIVE = "#8098B8"


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
    for name, normal in (("TButton", PANEL), ("Primary.TButton", ACCENT)):
        style.configure(name, background=normal, foreground=INK, padding=(10, 6))
        style.map(name,
                  background=[("disabled", BG), ("pressed", ACCENT), ("active", ACTIVE)],
                  foreground=[("disabled", "#62695f")],
                  bordercolor=[("focus", FOCUS)],
                  lightcolor=[("pressed", BORDER)], darkcolor=[("pressed", BORDER)])
    style.configure("TEntry", padding=5)
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
    style.configure("TScrollbar", background=BORDER)
    style.map("TScrollbar", background=[("active", FOCUS), ("pressed", FOCUS)])
    style.configure("Horizontal.TProgressbar", background=FOCUS, thickness=5)
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

        root.title("系统音频问答")
        self.size_window()
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style_window(root)
        root.bind("<Map>", lambda event: style_title_bar(root) if event.widget is root else None, add="+")

        shell = ttk.Frame(root, padding=(18, 13))
        shell.pack(fill="both", expand=True)
        self.header = ttk.Label(shell, text="系统音频问答", font=("Microsoft YaHei UI", 17, "bold"))
        self.header.pack(
            anchor="w", pady=(0, 9),
        )

        toolbar = self.toolbar = ttk.Frame(shell)
        toolbar.pack(fill="x")
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
        self.capture_label = ttk.Label(shell, text="未缓冲", foreground=MUTED, wraplength=650)
        self.capture_label.pack(anchor="w", pady=(8, 11))

        self.tabs = ttk.Notebook(shell)
        self.qa_tab = ttk.Frame(self.tabs, padding=(10, 12))
        self.history_tab = ttk.Frame(self.tabs, padding=(10, 12))
        self.profile_tab = ttk.Frame(self.tabs, padding=(10, 12))
        self.settings_tab = ttk.Frame(self.tabs, padding=(10, 12))
        self.memory_tab = ttk.Frame(self.tabs, padding=(10, 12))
        cards_tab = ttk.Frame(self.tabs, padding=(10, 12))
        self.tabs.add(self.profile_tab, text="面试准备")
        self.tabs.add(cards_tab, text="口述卡")
        self.tabs.add(self.qa_tab, text="问答")
        self.tabs.add(self.history_tab, text="本场回看")
        self.tabs.add(self.memory_tab, text="记忆与反馈")
        self.tabs.add(self.settings_tab, text="设置")
        self.build_qa()
        self.build_history()
        self.build_profile()
        ttk.Label(cards_tab, text="每行填写「问题 => 你已核实、可以直接说的回答」。开始本场后生效。\n"
                  "快速模式命中同一问题时显示原稿。修改事实后，请核对稿子再开始新场。",
                  foreground=MUTED, wraplength=455).pack(anchor="w")
        self.answer_cards = self.text_box(cards_tab, 7, expand=True)
        self.build_memory()
        self.build_settings()

        self.status = ttk.Label(shell, text="请在设置里配置云服务 API Key。",
                                foreground=MUTED, wraplength=650)
        self.status.pack(side="bottom", anchor="w", pady=(9, 1))
        self.tabs.pack(fill="both", expand=True)
        self.refresh_devices()
        self.update_buttons()
        self.refresh_memory()
        self.tabs.select(self.profile_tab)
        root.bind("<Control-Alt-Up>", lambda _: self.keyboard_history(1))
        root.bind("<Control-Alt-Down>", lambda _: self.keyboard_history(-1))
        self.tick_timer = root.after(35, self.tick)

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
        self.privacy_toggle = ttk.Checkbutton(controls, textvariable=self.privacy_caption,
                                             variable=self.privacy, command=self.apply_privacy)
        self.privacy_toggle.pack(side="left", padx=(6, 0))
        self.qa_body = ttk.Frame(self.qa_tab)
        self.qa_body.pack(fill="both", expand=True)
        self.question_pane = ttk.Frame(self.qa_body)
        self.answer_pane = ttk.Frame(self.qa_body)
        ttk.Label(self.question_pane, text="问题与转写", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.transcript = self.text_box(self.question_pane, 4, expand=True)
        self.transcript.bind("<KeyRelease>", self.mark_transcript_dirty, add="+")
        self.caption_preview = ttk.Label(self.question_pane, text="最近字幕：—", foreground=MUTED,
                                         wraplength=300, justify="left")
        self.caption_preview.pack(anchor="w", pady=(0, 6))
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
        self.compact_button = ttk.Button(controls, text="紧凑窗口", command=self.toggle_compact)
        self.compact_button.pack(side="right")
        self.source_button = ttk.Button(heading, text="查看参考资料", command=self.show_sources,
                                       state="disabled")
        self.source_button.pack(side="right", padx=6)
        self.layout_question()

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
            self.header.pack(before=self.toolbar, anchor="w", pady=(0, 9))
        self.size_window()
        self.transcript.configure(height=1 if self.compact else 4, pady=4 if self.compact else 8)
        self.answer.configure(height=4 if self.compact else 7, pady=4 if self.compact else 8)
        self.layout_question()
        self.compact_button.configure(text="恢复窗口" if self.compact else "紧凑窗口")

    def size_window(self):
        width, height, min_width, min_height = (550, 525, 520, 500) if self.compact else (730, 620, 680, 570)
        available_width = self.root.winfo_screenwidth() - 44
        available_height = self.root.winfo_screenheight() - 100
        self.root.minsize(min(round(min_width * self.ui_scale), available_width),
                          min(round(min_height * self.ui_scale), available_height))
        self.root.geometry(f"{min(round(width * self.ui_scale), available_width)}x"
                           f"{min(round(height * self.ui_scale), available_height)}")

    def build_profile(self):
        fields = ttk.Frame(self.profile_tab)
        fields.pack(fill="x")
        fields.columnconfigure(1, weight=1)
        fields.columnconfigure(3, weight=1)
        self.draft = {}
        items = (("company", "公司", ()), ("role", "岗位", ()),
                 ("round", "轮次", ("一面", "二面", "三面", "终面", "HR 面")),
                 ("language", "语言", ("自动", "中文", "English", "中英对照")),
                 ("answer_length", "回答长度", tuple(ANSWER_LENGTH_INSTRUCTIONS)),
                 ("job_description", "岗位描述", ()), ("focus", "关注点", ()),
                 ("terms", "术语候选", ()), ("verified_facts", "核实过的项目事实", ()))
        for index, (key, label, choices) in enumerate(items):
            row, col = divmod(index, 2)
            ttk.Label(fields, text=label).grid(row=row, column=col * 2, sticky="w", padx=(0, 5), pady=3)
            field = ttk.Combobox(fields, values=choices, width=15) if choices else ttk.Entry(fields, width=17)
            field.grid(row=row, column=col * 2 + 1, sticky="ew", padx=(0, 9), pady=3)
            self.draft[key] = field
        self.draft["language"].set("中文")
        self.draft["answer_length"].configure(state="readonly")
        self.draft["answer_length"].set(STANDARD_ANSWER_LENGTH)
        self.preflight_status = ttk.Label(self.profile_tab, foreground=MUTED, wraplength=605)
        self.preflight_status.pack(anchor="w", pady=(6, 3))
        self.audio_check_button = ttk.Button(self.profile_tab, text="检查系统回放（4 秒）",
                                             command=self.start_audio_check)
        self.audio_check_button.pack(anchor="w")
        toolbar = ttk.Frame(self.profile_tab)
        toolbar.pack(fill="x", pady=(6, 0))
        ttk.Button(toolbar, text="导入文件", command=self.import_profile).pack(side="left")
        ttk.Button(toolbar, text="清空", command=lambda: self.replace_text(self.profile, "")).pack(
            side="left", padx=8,
        )
        ttk.Button(toolbar, text="保存预设", command=self.save_interview_preset).pack(side="left")
        ttk.Button(toolbar, text="加载预设", command=self.load_interview_preset).pack(side="left", padx=6)
        self.begin_button = ttk.Button(toolbar, text="开始监听", style="Primary.TButton",
                                       command=self.begin_listening)
        self.begin_button.pack(side="right")
        ttk.Label(self.profile_tab, text="术语格式示例 RAG=Grok。本人职责、指标和试验条件可以补在核实事实中。"
                  "\n本场问答会使用题目、所选资料和已核实事实。",
                  foreground=MUTED, wraplength=620).pack(anchor="w", pady=(9, 3))
        self.profile = self.text_box(self.profile_tab, 7)
        self.source_view = tk.Toplevel(self.root, background=BG)
        self.source_view.withdraw()
        self.source_view.geometry("550x380")
        self.source_view.transient(self.root)
        self.source_view.protocol("WM_DELETE_WINDOW", self.source_view.withdraw)
        self.source_view.bind("<Map>", lambda event: style_title_bar(event.widget)
                              if event.widget is self.source_view else None, add="+")
        self.source_text = self.text_box(self.source_view, 10, readonly=True)

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
            field.delete(0, "end")
            field.insert(0, preset.get(name, ""))
        self.status.configure(text="已加载到草稿。开始新场后生效。")

    def update_preflight(self):
        key_state = "已填 Key" if self.get_key() else "待填 Key"
        shortcut = self.shortcut_name()
        if self.shortcut is not None:
            shortcut += " 已注册" if self.hotkey is not None else (" 未注册" if self.capture else " 待启动")
        device = self.capture.device_name if self.capture else self.device.get()
        self.preflight_status.configure(text=f"{device} · {shortcut} · {self.provider.get()} {key_state}")

    def start_audio_check(self):
        now = time.perf_counter()
        self.audio_check = (now, self.capture)
        self.audio_check_button.configure(state="disabled")
        self.status.configure(text="现在播放一段会议测试声音。4 秒后查看收到的回放。")
        if self.capture is None and not self.start_button.instate(("disabled",)):
            self.start_capture()
        self.root.after(4000, self.finish_audio_check)

    def finish_audio_check(self):
        started, original = self.audio_check
        self.audio_check = None
        self.audio_check_button.configure(state="normal")
        capture = self.capture
        if capture is None or (original is not None and capture is not original):
            self.status.configure(text="检查期间播放设备未就绪或发生切换，请重试。")
            return
        try:
            pcm = capture.buffer.snapshot(min(4.0, time.perf_counter() - started))
            frames = len(pcm) // capture.buffer.frame_bytes
            peak = audioop.max(pcm, 2)
        except NoAudio:
            frames, peak = 0, 0
        seconds = frames / capture.buffer.rate
        result = (f"{capture.device_name} · {seconds:.2f} 秒回放 · 峰值 {peak / 32768:.1%}"
                  if peak > 25 else f"{capture.device_name} · 未收到可用信号，请核对会议输出设备。")
        self.status.configure(text=result)

    def begin_listening(self):
        if self.listening_pending or self.start_button.instate(("disabled",)):
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
        data["resume"] = self.profile.get("1.0", "end-1c").strip()
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
        self.update_buttons()
        return True

    def build_listening_panel(self):
        panel = self.listening_panel = tk.Toplevel(self.root, background=BG)
        panel.withdraw()
        panel.title("系统音频")
        panel.attributes("-topmost", True)
        panel.resizable(False, False)
        panel.protocol("WM_DELETE_WINDOW", self.stop_listening)
        panel.bind("<Map>", lambda event: style_title_bar(panel, BG)
                   if event.widget is panel else None, add="+")

        body = tk.Frame(panel, bg=BG, padx=14, pady=12)
        body.pack(fill="both", expand=True)
        line = tk.Frame(body, bg=BG)
        line.pack(fill="x")
        self.listening_status = tk.Label(line, bg=BG, fg=INK,
                                         font=("Microsoft YaHei UI", 10, "bold"))
        self.listening_status.pack(side="left")
        self.listening_shortcut = tk.Label(line, bg=BG, fg=MUTED,
                                           font=("Microsoft YaHei UI", 9))
        self.listening_shortcut.pack(side="right")
        self.listening_question = tk.Label(body, bg=BG, fg=INK, anchor="w", width=1,
                                          font=("Microsoft YaHei UI", 11, "bold"))
        self.listening_question.pack(fill="x", pady=(7, 3))
        self.listening_caption = tk.Label(body, bg=BG, fg=MUTED, anchor="w", justify="left", width=1, height=2,
                                         font=("Microsoft YaHei UI", 9))
        self.listening_caption.pack(side="bottom", fill="x", pady=(4, 0))
        self.listening_detail = tk.Label(body, bg=BG, fg=MUTED, anchor="w", width=1,
                                        font=("Microsoft YaHei UI", 9))
        actions = tk.Frame(body, bg=BG)
        actions.pack(side="bottom", fill="x")
        self.listening_detail.pack(side="bottom", fill="x", pady=(2, 3))
        self.listening_answer = self.text_box(body, 3, readonly=True)
        self.listening_answer.configure(fg=INK)
        self.listening_answer_display = ""
        self.listening_answer_request = None
        self.listening_ask = ttk.Button(actions, text="回答这题", command=self.request_audio)
        self.listening_ask.pack(side="left")
        self.listening_open = ttk.Button(actions, text="完整问答", command=self.restore_workbench)
        self.listening_open.pack(side="left", padx=6)
        self.listening_pause = ttk.Button(actions, text="暂停听题", command=self.toggle_pause)
        self.listening_pause.pack(side="left")
        self.listening_stop = ttk.Button(actions, text="停止", command=self.stop_listening)
        self.listening_stop.pack(side="right")
        panel.geometry(f"{round(430 * self.ui_scale)}x{round(260 * self.ui_scale)}")
        panel.minsize(round(430 * self.ui_scale), round(260 * self.ui_scale))
        work_area = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
            x = max(work_area.left, work_area.right - round(432 * self.ui_scale))
            y = max(work_area.top, work_area.bottom - round(303 * self.ui_scale))
            panel.geometry(f"+{x}+{y}")

    def show_listening_panel(self):
        if self.listening_panel is None:
            self.build_listening_panel()
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
        elif connecting:
            title = "正在重新连接"
        else:
            title = "系统音频已停止"
        self.listening_status.configure(text=title)
        self.listening_shortcut.configure(text=self.shortcut_name() if self.hotkey else "仅按钮")
        self.listening_ask.configure(state=self.ask_button.cget("state"))
        detail = self.status.cget("text")
        question = " ".join(self.transcript.get("1.0", "end-1c").split())
        caption = "\n".join(line[:42] + ("…" if len(line) > 42 else "")
                            for line in self.live_transcript.splitlines()[-2:])
        self.listening_question.configure(text=question or "等待下一道题")
        if not caption and self.asr_state == "text_only":
            caption = f"{self.provider.get()}：按回答时识别最近系统音频"
        elif not caption:
            caption = "等待系统播放声音"
        self.listening_caption.configure(text="字幕：" + caption)
        answer = self.answer.get("1.0", "end-1c") or "按快捷键或点「回答这题」"
        previous = self.listening_answer_display
        if answer != previous or self.listening_answer_request != self.request_id:
            if self.listening_answer_request == self.request_id and previous and answer.startswith(previous):
                self.append_answer_text(self.listening_answer, answer[len(previous):])
            else:
                self.replace_text(self.listening_answer, answer)
                self.listening_answer.yview_moveto(0)
            self.listening_answer_display = answer
            self.listening_answer_request = self.request_id
        if self.listening_detail.cget("text") != detail:
            self.listening_detail.configure(text=detail)

    def restore_workbench(self):
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
            self.start_capture()
            return
        if self.capture is None:
            self.status.configure(text="当前没有正在监听的系统音频。")
            return
        self.paused = True
        self.stop_capture(preserve_panel=True)
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
        if not self.busy and not self.selected_caption_ids and not self.transcript_dirty:
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
        service = ttk.Frame(self.settings_tab)
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
        self.key = ttk.Entry(self.settings_tab, textvariable=self.api_keys[self.provider.get()],
                             show="•", width=48)
        self.key.pack(anchor="w", pady=(4, 4))
        self.key_hint = ttk.Label(self.settings_tab, foreground=MUTED)
        self.key_hint.pack(anchor="w", pady=(0, 4))
        key_actions = ttk.Frame(self.settings_tab)
        key_actions.pack(anchor="w", pady=(0, 8))
        self.save_key_button = ttk.Button(key_actions, text="保存密钥", command=self.save_api_key)
        self.save_key_button.pack(side="left")
        self.clear_key_button = ttk.Button(key_actions, text="清除已保存密钥", command=self.clear_api_key)
        self.clear_key_button.pack(side="left", padx=6)
        self.provider.trace_add("write", self.update_provider)
        self.provider.trace_add("write", self.save_provider_preference)
        self.workspace_row = ttk.Frame(self.settings_tab)
        ttk.Label(self.workspace_row, text="业务空间").pack(side="left")
        self.workspace_id = tk.StringVar(value=self.preferences.get("dashscope_workspace", ""))
        ttk.Entry(self.workspace_row, textvariable=self.workspace_id, width=33).pack(side="left", padx=6)
        ttk.Label(self.workspace_row, text="地域").pack(side="left", padx=(10, 4))
        self.region = tk.StringVar(value=self.preferences.get("dashscope_region", "北京"))
        ttk.Combobox(self.workspace_row, textvariable=self.region, values=("北京", "新加坡"),
                     state="readonly", width=10).pack(side="left")
        self.workspace_row.pack(anchor="w", pady=(0, 8))
        self.cloud_disclosure = ttk.Label(
            self.settings_tab,
            text="开启实时字幕会将系统播放音频发送到阿里云百炼；不连接麦克风。仅在开始听题时发送。",
            foreground=MUTED, wraplength=600,
        )
        self.cloud_disclosure.pack(anchor="w", pady=(0, 12))
        self.update_provider()

        ttk.Label(self.settings_tab, text="播放设备").pack(anchor="w")
        row = ttk.Frame(self.settings_tab)
        row.pack(fill="x", pady=(4, 5))
        self.device = ttk.Combobox(row, width=56, state="readonly")
        self.device.pack(side="left", fill="x", expand=True)
        self.refresh_button = ttk.Button(row, text="刷新", command=self.refresh_devices)
        self.refresh_button.pack(side="left", padx=(8, 0))
        ttk.Label(self.settings_tab, text="默认选项跟随 Windows 播放设备。固定设备可停止缓冲后更改。",
                  foreground=MUTED).pack(anchor="w", pady=(0, 16))

        ttk.Label(self.settings_tab, text="全局快捷键").pack(anchor="w")
        hotkey_row = ttk.Frame(self.settings_tab)
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
        ttk.Label(self.settings_tab, text="可用 Ctrl、Alt、Shift 加字母、数字、空格或 F1–F11。"
                  "\n缓冲时快捷键全局生效。Shift+Z 会占用大写 Z。",
                  foreground=MUTED, wraplength=600).pack(anchor="w")
        ttk.Checkbutton(self.settings_tab, text="截图与共享保护",
                        variable=self.privacy, command=self.apply_privacy).pack(anchor="w", pady=(12, 3))
        self.privacy_label = ttk.Label(self.settings_tab, text="关闭", foreground=MUTED)
        self.privacy_label.pack(anchor="w")
        self.measure = tk.BooleanVar(value=False)
        diagnostics = ttk.Frame(self.settings_tab)
        diagnostics.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(diagnostics, text="记录请求耗时", variable=self.measure).pack(side="left")
        ttk.Button(diagnostics, text="导出记录", command=self.export_timing).pack(side="left", padx=7)
        ttk.Label(diagnostics, text="问答字号").pack(side="left", padx=(8, 4))
        font_box = ttk.Combobox(diagnostics, values=("11", "13", "15", "17"), width=4, state="readonly")
        font_box.set("11")
        font_box.pack(side="left")
        font_box.bind("<<ComboboxSelected>>", lambda _: self.resize_text(int(font_box.get())))

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
        for widget in (self.transcript, self.answer, self.source_text, self.history_text):
            widget.configure(font=("Microsoft YaHei UI", size))

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
        try:
            set_privacy(native_window(self.root), enabled)
            self.source_view.update_idletasks()
            set_privacy(native_window(self.source_view), enabled)
        except OSError:
            self.source_view.withdraw()
            self.privacy_caption.set("保护未确认")
            self.privacy_label.configure(text="Windows 未确认所有窗口的捕获设置。")
        else:
            self.privacy_caption.set("保护已开启" if enabled else "截图保护")
            state = ("Windows 已对主窗和参考资料窗应用捕获排除。共享效果请从接收端核对。"
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
        path = filedialog.askopenfilename(
            title="导入简历或项目经历",
            filetypes=[("资料文件", "*.txt *.md *.docx *.pdf")],
        )
        if not path:
            return
        try:
            imported = f"[资料：{os.path.basename(path)}]\n{read_profile(path)}"
            current = self.profile.get("1.0", "end-1c").strip()
            merged = f"{current}\n\n{imported}" if current else imported
            if len(merged) > MAX_TEXT:
                raise ValueError("合并后的资料超过 48,000 字符。请先删减文本。")
        except ValueError as error:
            self.status.configure(text=str(error))
        except Exception:
            self.status.configure(text="资料读取失败。请检查文件是否损坏，或直接粘贴文字。")
        else:
            self.replace_text(self.profile, merged)
            self.status.configure(text=f"已导入资料 · 当前 {len(merged):,} 字符")

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
        if turn is None:
            turn = self.turn_ledger.create(question or "待转写题目", source="手输" if question else "语音")
        self.request_turn_map[request_id] = turn.turn_id
        self.request_session_tokens[request_id] = self.session_token
        self.request_modes[request_id] = self.mode.get()
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
                        if turn.question == "待转写题目":
                            self.turn_ledger.update_status(turn.turn_id, "生成中")
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
                        self.turn_ledger.append_answer(turn.turn_id, chunk)
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

    def response_event(self, kind, event):
        request_id, value = event
        turn_id = self.request_turn_map.get(request_id)
        if request_id != self.request_id:
            if request_id in self.cancelled_request_ids:
                if kind in {"done", "error"} and turn_id:
                    self.turn_ledger.update_status(turn_id, "已取消")
                return
            if kind == "chunk" and turn_id:
                self.turn_ledger.append_answer(turn_id, value)
            elif kind == "done" and turn_id:
                self.turn_ledger.update_status(turn_id, "已完成")
                if self.request_session_tokens.get(request_id) == self.session_token:
                    question_turn = self.turn_ledger.get(turn_id)
                    transcript, answer, reuse = value
                    self.session.record(question_turn.question if question_turn else transcript,
                                        answer, reuse=reuse,
                                        mode=self.request_modes.get(request_id, "快速"))
                    self.refresh_history()
            elif kind == "error" and turn_id:
                self.turn_ledger.update_status(turn_id, "失败")
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
