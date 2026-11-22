import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from pypdf import PdfWriter

from main import AudioQA
from profile_text import WORD
from self_test import wait_for


class InterviewWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "memory.db"
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = AudioQA(
            self.root,
            memory_path=self.database,
            shortcut_path=Path(self.temp.name) / "hotkey.json",
        )
        self.addCleanup(self.app.close)

    def import_resume(self, path):
        self.app.read_resume_file(path)
        wait_for(lambda: not self.app.resume_loading, self.root.update)

    def test_sidebar_routes_hidden_notebook_pages_without_replacing_draft(self):
        self.app.draft["company"].insert(0, "Test company")
        self.app.navigate("documents")
        self.app.navigate("prepare")
        self.assertEqual(self.app.draft["company"].get(), "Test company")
        self.assertEqual(self.app.tabs.cget("style"), "Hidden.TNotebook")
        self.assertTrue(self.app.dnd_ready)

        self.app.navigate("settings")
        self.app.select_settings_category("model")
        self.assertEqual(self.app.settings_tabs.select(), str(self.app.settings_pages["model"]))
        self.app.leave_settings()
        self.assertEqual(self.app.tabs.select(), str(self.app.profile_tab))

    def test_resume_file_review_save_cancel_and_failure_keep_saved_copy(self):
        text_file = Path(self.temp.name) / "reviewed resume.txt"
        text_file.write_text("Built a local retrieval tool.", encoding="utf-8")
        self.import_resume(text_file)
        self.assertIsNone(self.app.personal_resume)
        self.assertEqual(self.app.resume_editor.get("1.0", "end-1c"), "Built a local retrieval tool.")
        self.assertTrue(self.app.save_resume())
        saved = dict(self.app.personal_resume)

        with self.subTest("file picker cancel"):
            with patch("main.filedialog.askopenfilename", return_value=""):
                self.app.choose_resume_file()
            self.assertEqual(self.app.personal_resume, saved)

        scanned_pdf = Path(self.temp.name) / "scanned.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=120, height=120)
        with scanned_pdf.open("wb") as output:
            writer.write(output)
        self.import_resume(scanned_pdf)
        self.assertIn("扫描版 PDF", self.app.resume_error.cget("text"))
        self.assertEqual(self.app.personal_resume, saved)

        self.app.start_resume_edit("replacement.txt", "Unsaved replacement")
        self.app.cancel_resume_edit()
        self.assertEqual(self.app.personal_resume, saved)

    def test_windows_tkdnd_routes_drop_to_editable_confirmation(self):
        namespace = WORD[1:-1]
        document = Path(self.temp.name) / "resume with spaces.docx"
        with ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", (
                f'<w:document xmlns:w="{namespace}"><w:body><w:p>'
                "<w:r><w:t>Designed a test harness.</w:t></w:r>"
                "</w:p></w:body></w:document>"
            ))
        target = str(self.app.resume_drop_zone)
        file_list = self.root.tk.call("list", str(document))
        tcl = self.root.tk
        action = tcl.call("::tkdnd::olednd::HandleDragEnter", target, ("CF_HDROP",), ("copy",),
                          (), 0, 0, (15,), file_list)
        self.assertEqual(action, "copy")
        action = tcl.call("::tkdnd::olednd::HandleDrop", target, (), 0, 0, "CF_HDROP", file_list)
        self.assertEqual(action, "copy")
        wait_for(lambda: not self.app.resume_loading, self.root.update)
        self.assertEqual(self.app.resume_candidate_label.cget("text"), document.name)
        self.assertIsNone(self.app.personal_resume)
        self.assertEqual(self.app.resume_editor.get("1.0", "end-1c"), "Designed a test harness.")

    def test_older_import_cannot_replace_the_newer_editor_snapshot(self):
        self.app.start_pasted_resume()
        self.app.replace_text(self.app.resume_editor, "New draft")
        self.app.finish_resume_import(self.app.resume_import_id - 1, "old.docx", "Old draft", "")
        self.assertEqual(self.app.resume_editor.get("1.0", "end-1c"), "New draft")

    def test_session_only_enters_records_after_explicit_save(self):
        for key, value in (("company", "Test Co"), ("role", "AI Engineer"), ("round", "二面")):
            self.app.draft[key].insert(0, value)
        self.app.replace_text(self.app.profile, "Validated project context")
        self.assertTrue(self.app.start_session())
        self.app.turn_ledger.create("怎么验证效果？", source="手输")
        self.app.turn_ledger.append_answer("turn-1", "先看目标状态，再检查结果。")
        self.app.turn_ledger.update_status("turn-1", "已完成")
        self.assertEqual(self.app.memory.list_interviews(), [])

        self.assertTrue(self.app.save_current_session())
        records = self.app.memory.list_interviews()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["company"], "Test Co")
        self.assertEqual(records[0]["turns"][0]["question"], "怎么验证效果？")
        self.assertIn("先看目标状态", self.app.saved_record_detail.get("1.0", "end-1c"))
        second = self.app.turn_ledger.create("如果状态没有变化？", source="手输")
        self.app.turn_ledger.update_status(second.turn_id, "已完成")
        self.assertTrue(self.app.save_current_session())
        self.assertEqual(len(self.app.memory.list_interviews()), 1)
        self.assertEqual(len(self.app.memory.list_interviews()[0]["turns"]), 2)

    def test_return_from_settings_keeps_selected_question_when_new_turn_arrives(self):
        self.app.start_session(allow_generic=True)
        first = self.app.turn_ledger.create("旧题", source="手输")
        self.app.turn_ledger.append_answer(first.turn_id, "旧题回答")
        self.app.turn_ledger.create("新题", source="手输")
        self.app.refresh_turn_list()
        self.app.turn_list.selection_clear(0, "end")
        self.app.turn_list.selection_set(0)
        self.app.show_reviewed_turn()
        self.app.navigate("settings")
        self.app.turn_ledger.create("更新的题", source="语音")
        self.app.refresh_turn_list()
        self.app.leave_settings()
        self.assertEqual(self.app.tabs.select(), str(self.app.qa_tab))
        self.assertEqual(self.app.reviewed_turn_id, first.turn_id)
        self.assertEqual(self.app.reviewed_answer.get("1.0", "end-1c"), "旧题回答")


if __name__ == "__main__":
    unittest.main()
