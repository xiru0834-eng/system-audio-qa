"""Capture synthetic UI states and measure screenshot brightness and layout."""

import ctypes
import json
import sys
import tkinter as tk
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
    image = ImageGrab.grab(window=hwnd).convert("RGB")
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
                scale = percent / 100
                root.geometry(f"+38+62")
                prevent_test_activation(root)
                for key, value in (("company", "模拟零售团队"), ("role", "AI 应用工程师"), ("round", "二面")):
                    app.draft[key].insert(0, value)
                app.personal_resume = {"name": "样例简历.docx", "text": "构建检索服务并验证发布流程。"}
                app.use_personal_resume()
                root.deiconify()

                app.tabs.select(app.profile_tab)
                root.update()
                result.append(capture(root, folder, f"dpi{percent}-prepare"))

                app.start_resume_edit("样例简历.docx", "构建检索服务并验证发布流程。\n\n负责接口设计、回归测试和发布检查。")
                app.resume_error.configure(text="")
                app.tabs.select(app.documents_tab)
                root.update()
                result.append(capture(root, folder, f"dpi{percent}-resume-review"))

                app.resume_error.configure(text="未读到可编辑文字，可粘贴简历内容。")
                root.update()
                result.append(capture(root, folder, f"dpi{percent}-resume-error"))

                app.navigate("settings")
                app.select_settings_category("audio")
                root.update()
                result.append(capture(root, folder, f"dpi{percent}-settings-audio"))

                app.select_settings_category("display")
                root.update()
                result.append(capture(root, folder, f"dpi{percent}-settings-display"))

                app.memory.save_interview(
                    {"company": "模拟零售团队", "role": "AI 应用工程师", "round": "二面"},
                    [{"question": "检索接口突然慢下来，你会先看什么？",
                      "answer": "我会先拆开模型、索引和缓存这三段的耗时。\n\n先看哪一段的 p95 和错误率上升，再沿着这段查原因。"}],
                )
                app.refresh_saved_interviews()
                app.tabs.select(app.records_tab)
                root.update()
                result.append(capture(root, folder, f"dpi{percent}-records"))

                app.start_session(allow_generic=True)
                app.replace_text(app.transcript, "怎么确认 Agent 的动作生效了？")
                app.replace_text(app.answer, "我会读取操作后的界面状态，再和目标值核对。\n\n先读状态，执行后复核。")
                app.live_transcript = "如果界面一直没更新呢？"
                app.status.configure(text="回答已生成 · 测试资料")
                app.tabs.select(app.qa_tab)
                root.update()
                details = capture(root, folder, f"dpi{percent}-answer")
                details.update(protection_button=bool(visible(app.privacy_toggle)),
                               answer_height=app.answer.winfo_height(), question_height=app.transcript.winfo_height())
                result.append(details)
                app.build_listening_panel()
                app.update_listening_panel()
                app.listening_panel.deiconify()
                panel_hwnd = prevent_test_activation(app.listening_panel)
                raise_test_window(panel_hwnd)
                ctypes.windll.dwmapi.DwmFlush()
                floating = capture(app.listening_panel, folder, f"dpi{percent}-floating")
                floating.update(answer_height=app.listening_answer.winfo_height(),
                                answer_text=app.listening_answer.get("1.0", "end-1c"),
                                logical_size=[round(app.listening_panel.winfo_width() / scale),
                                              round(app.listening_panel.winfo_height() / scale)])
                result.append(floating)

                app.listening_panel.withdraw()
                root.geometry(f"{round(760 * scale)}x{round(660 * scale)}+38+62")
                root.update()
                app._responsive_sidebar()
                app.navigate("prepare")
                root.update()
                narrow = capture(root, folder, f"dpi{percent}-narrow-760")
                narrow.update(sidebar_width=app.sidebar.winfo_width(),
                              logical_width=round(root.winfo_width() / scale))
                result.append(narrow)
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
