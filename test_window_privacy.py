import unittest
from unittest.mock import Mock, patch

from main import AudioQA


class CapturePrivacyTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock()
        self.app.privacy.get.return_value = True
        self.app.source_excerpts = [{"source": "测试资料", "text": "测试标记"}]

    def test_reference_window_sets_affinity_before_its_first_show(self):
        events = []
        self.app.source_view.deiconify.side_effect = lambda: events.append("show")
        self.app.replace_text.side_effect = lambda *args: events.append("fill")
        with patch("main.native_window", return_value=25), patch(
            "main.set_privacy", side_effect=lambda *args: events.append("protect")
        ) as affinity:
            AudioQA.show_sources(self.app)

        affinity.assert_called_once_with(25, True)
        self.assertLess(events.index("protect"), events.index("show"))
        self.assertLess(events.index("protect"), events.index("fill"))

    def test_reference_window_stays_hidden_if_windows_rejects_protection(self):
        with patch("main.native_window", return_value=25), patch("main.set_privacy", side_effect=OSError):
            AudioQA.show_sources(self.app)

        self.app.source_view.deiconify.assert_not_called()
        self.app.source_view.withdraw.assert_called_once()
        self.app.replace_text.assert_not_called()

    def test_enabling_applies_affinity_even_to_the_unopened_reference_window(self):
        self.app.source_view.winfo_viewable.return_value = False
        with patch("main.native_window", side_effect=(24, 25)), patch("main.set_privacy") as affinity:
            AudioQA.apply_privacy(self.app)

        self.assertEqual([call.args for call in affinity.call_args_list], [(24, True), (25, True)])

    def test_native_file_dialogs_do_not_open_while_protection_is_selected(self):
        with patch("main.filedialog.askopenfilename") as open_file, patch(
            "main.filedialog.asksaveasfilename"
        ) as save_file:
            AudioQA.import_profile(self.app)
            AudioQA.export_timing(self.app)

        open_file.assert_not_called()
        save_file.assert_not_called()
