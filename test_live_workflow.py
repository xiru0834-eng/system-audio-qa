import unittest

from live_workflow import CaptionTimeline, QuestionComposer, TurnLedger


class CaptionTimelineTests(unittest.TestCase):
    def test_interim_result_replaces_the_same_sentence_until_final(self):
        captions = CaptionTimeline("session-a")
        first = captions.upsert("connection-a", 1, 0, 900, "两个请求同时写入", False)
        revised = captions.upsert("connection-a", 1, 0, 1000, "两个请求同时写入会怎样", False)
        final = captions.upsert("connection-a", 1, 0, 1100, "两个请求同时写入会怎样？", True)

        self.assertEqual(first.caption_id, revised.caption_id)
        self.assertEqual(revised.caption_id, final.caption_id)
        self.assertEqual(final.revision, 3)
        self.assertEqual(captions.all(), (final,))
        self.assertTrue(final.is_final)

    def test_reconnect_offsets_keep_caption_times_monotonic(self):
        captions = CaptionTimeline("session-a")
        captions.upsert("connection-1", 1, 400, 900, "上一题", True)
        current = captions.upsert("connection-2", 1, 0, 350, "下一题", True, offset_ms=900)

        self.assertEqual(current.begin_ms, 900)
        self.assertEqual(current.end_ms, 1250)


class QuestionComposerTests(unittest.TestCase):
    def setUp(self):
        self.captions = CaptionTimeline("session-a")
        self.composer = QuestionComposer(self.captions)

    def add_caption(self, sentence_id, begin, end, text, final=True):
        return self.captions.upsert("connection-a", sentence_id, begin, end, text, final)

    def test_multiple_sentences_since_the_last_submit_form_one_question(self):
        self.add_caption(1, 0, 2200, "两个请求同时写入会怎样？")
        self.add_caption(2, 3200, 5200, "如果其中一次超时重试呢？")

        turn = self.composer.submit(cutoff_ms=5400, now=1.0)

        self.assertEqual(turn.question, "两个请求同时写入会怎样？\n如果其中一次超时重试呢？")
        self.assertEqual(turn.source, "语音")
        self.assertEqual(self.composer.voice_cursor_ms, 5400)

    def test_sentence_after_the_frozen_cutoff_is_not_in_the_question(self):
        self.add_caption(1, 0, 2000, "这次请求会超时", False)
        self.add_caption(2, 1800, 2600, "下一题怎么验证？")

        turn = self.composer.submit(cutoff_ms=1750, now=1.0)

        self.assertEqual(turn.question, "这次请求会超时")
        self.assertTrue(turn.needs_review)
        self.assertEqual(self.composer.voice_cursor_ms, 1750)

    def test_manual_question_has_priority_and_does_not_move_voice_cursor(self):
        self.add_caption(1, 0, 1400, "语音问题？")

        turn = self.composer.submit("幂等键保存多久？", cutoff_ms=1500, now=1.0)

        self.assertEqual(turn.question, "幂等键保存多久？")
        self.assertEqual(turn.source, "手输")
        self.assertEqual(self.composer.voice_cursor_ms, 0)

    def test_explicit_caption_selection_does_not_move_voice_cursor(self):
        selected = self.add_caption(2, 1500, 2400, "那你怎么验证？")
        self.add_caption(1, 0, 900, "旧字幕")

        turn = self.composer.submit(cutoff_ms=3000, selected_ids=(selected.caption_id,), now=1.0)

        self.assertEqual(turn.question, "那你怎么验证？")
        self.assertEqual(turn.source, "已选字幕")
        self.assertEqual(self.composer.voice_cursor_ms, 0)

    def test_empty_submission_does_not_advance_the_voice_cursor(self):
        self.add_caption(1, 2000, 2600, "", False)

        result = self.composer.submit(cutoff_ms=2600, now=1.0)

        self.assertIsNone(result)
        self.assertEqual(self.composer.voice_cursor_ms, 0)

    def test_duplicate_trigger_snapshot_is_deduplicated_but_later_same_question_is_new(self):
        self.add_caption(1, 0, 1400, "缓存怎么验证？")
        first = self.composer.submit(cutoff_ms=1500, now=1.0)
        duplicate = self.composer.submit(cutoff_ms=1500, now=1.2)
        self.add_caption(2, 1600, 2500, "缓存怎么验证？")
        next_turn = self.composer.submit(cutoff_ms=2600, now=2.0)

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        self.assertIsNotNone(next_turn)
        self.assertNotEqual(first.turn_id, next_turn.turn_id)


class TurnLedgerTests(unittest.TestCase):
    def test_late_answer_delta_stays_with_the_original_turn(self):
        ledger = TurnLedger()
        first = ledger.create("第一题？", source="语音")
        second = ledger.create("第二题？", source="语音")

        ledger.append_answer(first.turn_id, "第一题答案")
        ledger.append_answer(second.turn_id, "第二题答案")

        self.assertEqual(ledger.get(first.turn_id).answer, "第一题答案")
        self.assertEqual(ledger.get(second.turn_id).answer, "第二题答案")
        self.assertEqual(ledger.current.turn_id, second.turn_id)



if __name__ == "__main__":
    unittest.main()
