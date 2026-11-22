import json
import hashlib
import re
import sqlite3
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from question_review import parse_terms


PRESET_FIELDS = ("company", "role", "round", "language", "answer_length", "job_description", "focus", "terms")
PREFERENCE_FIELDS = {"provider", "dashscope_workspace", "dashscope_region", "font_size", "panel_x", "panel_y",
                     "panel_always_on_top", "window_opacity", "audio_device"}
CJK = re.compile(r"[\u3400-\u9fff]+")
ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9_+#.-]*", re.IGNORECASE)
COMMON = {"怎么", "什么", "如何", "为何", "为什么", "一下", "这个", "那个", "介绍",
          "what", "why", "how", "the", "about", "describe", "tell", "explain", "you", "your"}


def terms(text):
    result = {word.lower().rstrip(".") for word in ASCII_WORD.findall(text) if len(word) > 1}
    for phrase in CJK.findall(text):
        result.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    return result - COMMON


def profile_excerpt(text, size=750):
    for start in range(0, len(text), size - 100):
        excerpt = text[start:start + size].strip()
        if excerpt:
            yield excerpt
        if start + size >= len(text):
            break


def card_question(text):
    return " ".join(text.strip().rstrip("。？?！!").split())


def parse_answer_cards(text):
    cards = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        question, separator, answer = line.partition("=>")
        question, answer = card_question(question), answer.strip()
        if not separator or not question or not answer or len(question) > 160 or len(answer) > 600:
            raise ValueError(f"口述卡第 {line_number} 行请用「问题 => 口述稿」，限 160 字问题和 600 字回答。")
        if question in cards:
            raise ValueError(f"口述卡第 {line_number} 行的问题重复。")
        cards[question] = answer
        if len(cards) > 20:
            raise ValueError("每场最多锁定 20 张口述卡。")
    return cards


@dataclass(frozen=True)
class Turn:
    id: int
    question: str
    answer: str
    mode: str


class InterviewSession:
    def __init__(self, draft):
        profile = draft.get("resume", "")
        cards = draft.get("answer_cards", "")
        details = {key: value for key, value in draft.items()
                   if key not in {"resume", "terms", "verified_facts", "answer_cards"} and value}
        profile_content = profile + "\0" + draft.get("verified_facts", "")
        if cards:
            profile_content += "\0" + cards
        details["profile_version"] = hashlib.sha256(
            profile_content.encode("utf-8")
        ).hexdigest()[:16]
        self.aliases = parse_terms(draft.get("terms", ""))
        self.answer_cards = parse_answer_cards(cards)
        self.sources = []
        # A split at a document header alternates title and body.
        parts = re.split(r"(?m)^\[资料：([^\]\r\n]+)\]\r?\n", profile)
        documents = [("粘贴的资料", parts[0]), *zip(parts[1::2], parts[2::2])]
        for title, body in documents:
            for excerpt in profile_excerpt(body):
                item = {"source": title, "text": excerpt}
                self.sources.append((item, terms(title + "\n" + excerpt)))
        self.prefix = json.dumps({"locked_interview": details}, ensure_ascii=False, sort_keys=True)
        self.verified_facts = draft.get("verified_facts", "")
        self.turns = deque(maxlen=4)
        self.last_turn_id = 0
        self.feedback = deque(maxlen=32)
        self.remembered = []

    @property
    def history(self):
        return [(turn.question, turn.answer) for turn in self.turns]

    def selected_sources(self, question, history):
        query = terms(question)
        scores = [len(query & words) for _, words in self.sources]
        if not any(scores) and history:
            previous = terms(history[-1][0])
            scores = [len(previous & words) for _, words in self.sources]
        ranked = sorted(range(len(scores)), key=lambda index: -scores[index])
        if not any(scores):
            return [item for item, _ in self.sources[:2]]
        return [self.sources[index][0] for index in ranked[:4] if scores[index]]

    def prepare(self, question, *, reuse=False, deep=False):
        history = list(self.history)
        if reuse and history:
            history.pop()
        sources = self.selected_sources(question, history)
        suffix = {
            "answer_mode": "deep" if deep else "quick",
            "candidate_sources": sources,
            "verified_session_facts": self.verified_facts,
            "confirmed_feedback_oldest_to_newest": [*self.remembered, *self.feedback],
            "recent_questions": [previous_question for previous_question, _ in history],
            "question_or_transcript": question,
        }
        return self.prefix + "\n" + json.dumps(suffix, ensure_ascii=False), sources

    def input_for(self, question, *, reuse=False, deep=False):
        return self.prepare(question, reuse=reuse, deep=deep)[0]

    def quick_answer(self, question, *, deep=False):
        return None if deep else self.answer_cards.get(card_question(question))

    def invalidate_cards(self):
        self.answer_cards.clear()

    def record(self, question, answer, *, reuse=False, mode="快速"):
        if reuse and self.turns:
            turn_id = self.turns.pop().id
        else:
            self.last_turn_id += 1
            turn_id = self.last_turn_id
        self.turns.append(Turn(turn_id, question[:2500], answer[:3500], mode))

    def correct(self, text, kind="fact", *, remembered=False):
        (self.remembered if remembered else self.feedback).append({"kind": kind, "text": text})
        if kind == "fact" and not remembered:
            self.invalidate_cards()


class MemoryStore:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=2)
        self.db.execute("CREATE TABLE IF NOT EXISTS memory ("
                        "id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS interview_preset ("
                        "id INTEGER PRIMARY KEY CHECK(id = 1), fields TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS preference ("
                        "key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()

    def list(self):
        return self.db.execute("SELECT id, kind, text FROM memory ORDER BY id DESC").fetchall()

    def save(self, kind, text, item=None):
        with self.db:
            if item is None:
                return self.db.execute("INSERT INTO memory(kind, text) VALUES (?, ?)",
                                       (kind, text)).lastrowid
            self.db.execute("UPDATE memory SET kind = ?, text = ? WHERE id = ?", (kind, text, item))
        return item

    def delete(self, item):
        with self.db:
            self.db.execute("DELETE FROM memory WHERE id = ?", (item,))

    def load_preset(self):
        row = self.db.execute("SELECT fields FROM interview_preset WHERE id = 1").fetchone()
        return json.loads(row[0]) if row is not None else None

    def save_preset(self, fields):
        payload = {key: value for key, value in fields.items() if key in PRESET_FIELDS}
        with self.db:
            self.db.execute("INSERT INTO interview_preset(id, fields) VALUES (1, ?) "
                            "ON CONFLICT(id) DO UPDATE SET fields = excluded.fields",
                            (json.dumps(payload, ensure_ascii=False),))

    def load_preferences(self):
        rows = self.db.execute("SELECT key, value FROM preference").fetchall()
        return {key: json.loads(value) for key, value in rows}

    def save_preferences(self, values):
        payload = {key: value for key, value in values.items() if key in PREFERENCE_FIELDS}
        with self.db:
            self.db.executemany(
                "INSERT INTO preference(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in payload.items()],
            )

    def close(self):
        self.db.close()
