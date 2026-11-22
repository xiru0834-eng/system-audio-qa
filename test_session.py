import json
import tempfile
import unittest
from pathlib import Path

from session import InterviewSession, MemoryStore
from question_review import review_question, parse_terms


class SessionTests(unittest.TestCase):
    def test_confirmed_answer_is_locked_and_requires_the_same_question(self):
        question = "讲一下你做过的 Kubernetes 发布。"
        approved = "我负责 Kubernetes 发布和回滚演练。团队用的是 10% 灰度。"
        draft = {"resume": "发布项目", "answer_cards": f"{question} => {approved}"}
        session = InterviewSession(draft)
        draft["answer_cards"] = ""
        self.assertEqual(session.quick_answer(" 讲一下你做过的 Kubernetes 发布？ "), approved)
        self.assertIsNone(session.quick_answer("为什么你做过的 Kubernetes 发布需要回滚？"))
        self.assertIsNone(session.quick_answer(question, deep=True))
        self.assertNotIn(approved, session.input_for(question))
        self.assertNotEqual(session.prefix, InterviewSession(draft).prefix)
        session.correct("只负责演练。")
        self.assertIsNone(session.quick_answer(question))

    def test_cards_reject_duplicate_questions_and_keep_technical_symbols(self):
        with self.assertRaisesRegex(ValueError, "重复"):
            InterviewSession({"answer_cards": "项目怎么做？ => 第一稿\n项目怎么做。 => 第二稿"})
        with self.assertRaisesRegex(ValueError, "=>"):
            InterviewSession({"answer_cards": "缺少分隔符"})
        session = InterviewSession({"answer_cards": "用 C++ 做了什么？ => 已确认口述稿"})
        self.assertIsNone(session.quick_answer("用 C 做了什么？"))

    def test_terms_are_review_candidates_only(self):
        aliases = parse_terms("RAG = Grok\nKubernetes = k8s")
        review = review_question("Grok 里怎么减少幻觉？", aliases)
        self.assertEqual(review.replacement, "RAG 里怎么减少幻觉？")
        self.assertIn("Grok", review.message)
        self.assertFalse(review_question("我明确问的是 Grok，怎么用？", ()).message)
        self.assertFalse(review_question("RAG 和 Grok 有什么区别？", aliases).message)

    def test_number_and_release_aliases_preserve_the_rest_of_the_question(self):
        aliases = parse_terms("100 并发=100并80;加量=加亮")
        self.assertEqual(review_question("100并80，P95从多少降到多少？", aliases).replacement,
                         "100 并发，P95从多少降到多少？")
        self.assertEqual(review_question("灰度后能不能继续加亮？", aliases).replacement,
                         "灰度后能不能继续加量？")
        self.assertFalse(review_question("灰度后能不能继续加量？", aliases).message)

    def test_incomplete_negative_clause_stops_but_standalone_question_can_pass(self):
        for question in ("回滚之后，你没有。", "发布以后你有没有……", "Why did you not?"):
            self.assertTrue(review_question(question, ()).incomplete, question)
        for question in ("你有没有做过压测？", "你做过回滚没有？", "为什么？", "Kubernetes？"):
            self.assertFalse(review_question(question, ()).message, question)

    def test_turn_identity_survives_reanswer_and_new_turn(self):
        session = InterviewSession({"resume": "资料"})
        session.record("first", "A", mode="快速")
        first = session.turns[0].id
        session.record("first fixed", "B", reuse=True, mode="深度")
        session.record("second", "C", mode="快速")
        self.assertEqual(session.turns[0].id, first)
        self.assertEqual(session.turns[0].mode, "深度")
        self.assertNotEqual(session.turns[1].id, first)
        self.assertEqual(session.history[0], ("first fixed", "B"))

    def test_sources_are_bounded_relevant_and_change_without_changing_prefix(self):
        session = InterviewSession({"resume": "[资料：缓存.md]\nRedis 用于缓存商品详情。\n\n"
                                              "[资料：交付.md]\n我用 Kubernetes 管理发布。\n\n"
                                              "[资料：无关.md]\n" + ("办公行政记录。" * 300)})
        redis_input = session.input_for("为什么使用 Redis？")
        release_input = session.input_for("Kubernetes 怎么管理发布？")
        redis_prefix, redis_suffix = redis_input.split("\n", 1)
        release_prefix, release_suffix = release_input.split("\n", 1)
        self.assertEqual(redis_prefix, release_prefix)
        self.assertNotIn("办公行政", redis_input)
        self.assertEqual(json.loads(redis_suffix)["candidate_sources"][0]["source"], "缓存.md")
        self.assertEqual(json.loads(release_suffix)["candidate_sources"][0]["source"], "交付.md")

    def test_short_follow_up_uses_previous_question_to_recover_sources(self):
        session = InterviewSession({"resume": "[资料：缓存.md]\nRedis 存储热点。\n\n"
                                              "[资料：交付.md]\nKubernetes 管理发布。"})
        session.record("讲一下 Kubernetes", "讲述发布方案")
        suffix = json.loads(session.input_for("为什么？").split("\n", 1)[1])
        self.assertEqual(suffix["candidate_sources"][0]["source"], "交付.md")

    def test_model_answers_stay_available_locally_but_are_not_reused_as_facts(self):
        session = InterviewSession({"resume": "负责发布和回滚演练。"})
        session.record("回滚怎么做？", "回滚时我们先放量 15%。")
        session.correct("这次演练没有量化放量比例。")
        prepared = session.input_for("刚才那个项目怎么介绍？")
        suffix = json.loads(prepared.split("\n", 1)[1])
        self.assertEqual(suffix["recent_questions"], ["回滚怎么做？"])
        self.assertIn("这次演练没有量化放量比例", prepared)
        self.assertNotIn("15%", prepared)
        self.assertIn("15%", session.history[0][1])

    def test_saved_preset_has_only_interview_fields_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            store = MemoryStore(path)
            self.assertIsNone(store.load_preset())
            store.save_preset({"company": "A", "role": "Dev", "resume": "Private", "key": "example"})
            store.close()
            store = MemoryStore(path)
            self.assertEqual(store.load_preset(), {"company": "A", "role": "Dev"})
            store.close()

    def test_workspace_and_region_preferences_persist_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            store = MemoryStore(path)
            store.save_preferences({"dashscope_workspace": "workspace-123", "dashscope_region": "北京",
                                    "api_key": "must not be stored here"})
            store.close()
            reopened = MemoryStore(path)
            self.assertEqual(reopened.load_preferences(), {
                "dashscope_workspace": "workspace-123", "dashscope_region": "北京",
            })
            reopened.close()

    def test_prefix_stays_identical_across_questions_feedback_and_draft_edits(self):
        draft = {"company": "Acme", "role": "AI Engineer", "round": "二面", "resume": "Built retrieval."}
        session = InterviewSession(draft)
        prefix = session.prefix
        draft["company"] = "Other"
        session.record("What did you build?", "I built retrieval.")
        session.correct("My role was integration and tests.")
        result = session.input_for("Why?", reuse=False)
        self.assertTrue(result.startswith(prefix + "\n"))
        self.assertNotIn("Other", result)
        self.assertIn("integration and tests", result)
        self.assertIn("What did you build?", result)
        self.assertNotIn("What did you build?", session.input_for("What did you build?", reuse=True).split("\n", 1)[1].split('"question_or_transcript"')[0])
        self.assertNotEqual(InterviewSession(draft).prefix, prefix)

    def test_memory_survives_reopen_and_supports_edit_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            first = MemoryStore(path)
            item = first.save("fact", "Built retrieval")
            first.close()
            second = MemoryStore(path)
            self.assertEqual(second.list(), [(item, "fact", "Built retrieval")])
            second.save("fact", "Tested retrieval", item)
            self.assertEqual(second.list(), [(item, "fact", "Tested retrieval")])
            second.delete(item)
            self.assertEqual(second.list(), [])
            second.close()
