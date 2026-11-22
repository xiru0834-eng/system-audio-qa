"""Capture synthetic UI states and measure screenshot brightness and layout."""

import ctypes
import json
import sys
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tempfile import TemporaryDirectory

from main import AudioQA, enable_dpi_awareness
from windows_probe import prevent_test_activation, raise_test_window


def capture(window, folder, name):
    from PIL import ImageGrab

    window.update()
    hwnd = prevent_test_activation(window)
    raise_test_window(hwnd)
    ctypes.windll.dwmapi.DwmFlush()
    rectangle = wintypes.RECT()
    dwm_get = ctypes.windll.dwmapi.DwmGetWindowAttribute
    dwm_get.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    if dwm_get(hwnd, 9, ctypes.byref(rectangle), ctypes.sizeof(rectangle)):
        raise RuntimeError("Could not find the application window bounds")
    image = ImageGrab.grab((rectangle.left, rectangle.top, rectangle.right, rectangle.bottom)).convert("RGB")
    image.save(folder / (name + ".png"))
    channels = [((i / 255 + 0.055) / 1.055) ** 2.4 if i > 10 else i / 255 / 12.92 for i in range(256)]
    pixels = image.width * image.height
    levels = [(count, 0.2126 * channels[r] + 0.7152 * channels[g] + 0.0722 * channels[b])
              for count, (r, g, b) in image.getcolors(pixels)]
    bright = sum(count for count, luminance in levels if luminance > 0.55)
    return {"name": name, "pixels": image.size, "bright_percent": round(bright * 100 / pixels, 4),
            "mean_luminance": round(sum(count * luminance for count, luminance in levels) / pixels, 4)}


def visible(widget):
    return widget.winfo_ismapped() and widget.winfo_width() >= widget.winfo_reqwidth() - 2


def measure(folder, scales):
    result = []
    for percent in scales:
        root = tk.Tk()
        root.withdraw()
        root.tk.call("tk", "scaling", percent / 75)
        with TemporaryDirectory() as scratch:
            app = AudioQA(root, shortcut_path=Path(scratch) / "hotkey.json", memory_path=":memory:")
            try:
                root.geometry("+38+62")
                prevent_test_activation(root)
                app.session_label.configure(text="本场已锁定 · 模拟零售团队 · AI 应用工程师 · 二面")
                app.replace_text(app.transcript, "检索接口突然慢下来，你会先看哪些指标？")
                app.replace_text(app.answer, "我会先拆开模型、索引和缓存这三段的耗时。\n\n"
                                 "先看哪一段的 p95 和错误率明显上升，再沿着这段查具体原因。"
                                 "\n\n如果 Redis 命中率掉了，我会先核对失效策略和热点请求。")
                app.status.configure(text="回答已生成 · 测试资料")
                root.deiconify()
                for tab, name in ((app.profile_tab, "prepare"), (app.settings_tab, "settings"), (app.qa_tab, "answer")):
                    app.tabs.select(tab)
                    details = capture(root, folder, f"dpi{percent}-{name}")
                    if name == "answer":
                        details.update(protection_button=bool(visible(app.privacy_toggle)),
                                       answer_height=app.answer.winfo_height(), question_height=app.transcript.winfo_height())
                    result.append(details)
                app.toggle_compact()
                details = capture(root, folder, f"dpi{percent}-compact")
                details.update(protection_button=bool(visible(app.privacy_toggle)),
                               answer_height=app.answer.winfo_height(), question_height=app.transcript.winfo_height())
                result.append(details)
            finally:
                app.close()
    return result


if __name__ == "__main__":
    base = Path(sys.argv[1]).resolve()
    base.mkdir(parents=True, exist_ok=True)
    enable_dpi_awareness()
    scales = [int(value) for value in sys.argv[2:]] or [100, 125, 150, 200]
    results = measure(base, scales)
    (base / "visual.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))
