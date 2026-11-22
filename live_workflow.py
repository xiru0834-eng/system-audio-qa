"""Local state for live captions and manually submitted interview questions."""

from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class WordTiming:
    begin_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Caption:
    session_id: str
    connection_id: int
    sentence_id: int
    caption_id: str
    begin_ms: int
    end_ms: int
    text: str
    is_final: bool
    revision: int
    words: tuple[WordTiming, ...] = ()


@dataclass(frozen=True)
class QuestionTurn:
    turn_id: str
    question: str
    source: str
    caption_ids: tuple[str, ...]
    cutoff_ms: int | None
    needs_review: bool
    answer: str = ""
    status: str = "等待回答"


class CaptionTimeline:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._captions: dict[tuple[int, int], Caption] = {}

    def upsert(self, connection_id: int, sentence_id: int, begin_ms: int, end_ms: int,
               text: str, is_final: bool, *, offset_ms: int = 0,
               words: Sequence[WordTiming] = ()) -> Caption:
        key = (connection_id, sentence_id)
        previous = self._captions.get(key)
        if previous and previous.is_final:
            return previous

        caption = Caption(
            session_id=self.session_id,
            connection_id=connection_id,
            sentence_id=sentence_id,
            caption_id=f"{self.session_id}:{connection_id}:{sentence_id}",
            begin_ms=offset_ms + max(0, begin_ms),
            end_ms=offset_ms + max(begin_ms, end_ms),
            text=text,
            is_final=is_final,
            revision=1 if previous is None else previous.revision + 1,
            words=tuple(words),
        )
        self._captions[key] = caption
        return caption

    def get(self, caption_id: str) -> Caption | None:
        return next((caption for caption in self._captions.values()
                     if caption.caption_id == caption_id), None)

    def all(self) -> tuple[Caption, ...]:
        return tuple(sorted(self._captions.values(), key=lambda item: (item.begin_ms, item.connection_id,
                                                                       item.sentence_id)))

    def recent(self, count: int = 2) -> tuple[Caption, ...]:
        return self.all()[-max(0, count):] if count else ()


class QuestionComposer:
    def __init__(self, captions: CaptionTimeline, *, duplicate_window_s: float = 0.65):
        self.captions = captions
        self.duplicate_window_s = duplicate_window_s
        self.voice_cursor_ms = 0
        self._next_turn = 1
        self._last_fingerprint: tuple | None = None
        self._last_submit_at = float("-inf")

    def submit(self, draft: str = "", *, selected_ids: Sequence[str] = (),
               cutoff_ms: int | None = None, now: float) -> QuestionTurn | None:
        draft = draft.strip()
        if draft:
            question, source, caption_ids, review = draft, "手输", (), False
        elif selected_ids:
            selected = tuple(self.captions.get(item) for item in selected_ids)
            selected = tuple(item for item in selected if item is not None and item.text.strip())
            if not selected:
                return None
            question = "\n".join(item.text.strip() for item in selected)
            source, caption_ids, review = "已选字幕", tuple(item.caption_id for item in selected), False
        else:
            if cutoff_ms is None:
                return None
            question, caption_ids, review = self._voice_question(cutoff_ms)
            if not question:
                return None
            source = "语音"

        fingerprint = (source, question, caption_ids, cutoff_ms if source == "语音" else None)
        if (fingerprint == self._last_fingerprint
                and now - self._last_submit_at <= self.duplicate_window_s):
            return None

        turn = QuestionTurn(
            turn_id=f"{self.captions.session_id}:{self._next_turn}",
            question=question,
            source=source,
            caption_ids=caption_ids,
            cutoff_ms=cutoff_ms if source == "语音" else None,
            needs_review=review,
            status="生成中",
        )
        self._next_turn += 1
        self._last_fingerprint = fingerprint
        self._last_submit_at = now
        if source == "语音":
            self.voice_cursor_ms = cutoff_ms
        return turn

    def _voice_question(self, cutoff_ms: int) -> tuple[str, tuple[str, ...], bool]:
        fragments: list[str] = []
        caption_ids: list[str] = []
        needs_review = False
        for caption in self.captions.all():
            if caption.begin_ms >= cutoff_ms or caption.end_ms <= self.voice_cursor_ms:
                continue
            if caption.begin_ms < self.voice_cursor_ms:
                timed_text = self._text_between(caption.words, self.voice_cursor_ms, cutoff_ms)
                if timed_text:
                    fragments.append(timed_text)
                    caption_ids.append(caption.caption_id)
                else:
                    needs_review = True
                continue

            if caption.end_ms > cutoff_ms and caption.words:
                text = self._text_between(caption.words, caption.begin_ms, cutoff_ms)
            else:
                text = caption.text.strip()
            if text:
                fragments.append(text)
                caption_ids.append(caption.caption_id)
            if caption.end_ms > cutoff_ms or not caption.is_final:
                needs_review = True
        return "\n".join(fragments), tuple(caption_ids), needs_review

    @staticmethod
    def _text_between(words: Sequence[WordTiming], begin_ms: int, end_ms: int) -> str:
        return "".join(word.text for word in words
                       if word.begin_ms >= begin_ms and word.end_ms <= end_ms).strip()


class TurnLedger:
    """Keeps each response stream attached to the question that created it."""

    def __init__(self):
        self.turns: dict[str, QuestionTurn] = {}
        self.current_id: str | None = None

    @property
    def current(self) -> QuestionTurn | None:
        return self.turns.get(self.current_id) if self.current_id else None

    def get(self, turn_id: str) -> QuestionTurn | None:
        return self.turns.get(turn_id)

    def create(self, question: str, *, source: str, caption_ids: Sequence[str] = (),
               cutoff_ms: int | None = None, needs_review: bool = False,
               turn_id: str | None = None) -> QuestionTurn:
        identifier = turn_id or f"turn-{len(self.turns) + 1}"
        if identifier in self.turns:
            raise ValueError("题目 ID 已存在。")
        turn = QuestionTurn(identifier, question, source, tuple(caption_ids), cutoff_ms, needs_review,
                            status="生成中")
        self.turns[identifier] = turn
        self.current_id = identifier
        return turn

    def append_answer(self, turn_id: str, chunk: str) -> bool:
        turn = self.turns.get(turn_id)
        if turn is None:
            return False
        self.turns[turn_id] = QuestionTurn(
            turn.turn_id, turn.question, turn.source, turn.caption_ids, turn.cutoff_ms, turn.needs_review,
            answer=turn.answer + chunk, status=turn.status,
        )
        return True

    def update_status(self, turn_id: str, status: str) -> bool:
        turn = self.turns.get(turn_id)
        if turn is None:
            return False
        self.turns[turn_id] = QuestionTurn(
            turn.turn_id, turn.question, turn.source, turn.caption_ids, turn.cutoff_ms, turn.needs_review,
            answer=turn.answer, status=status,
        )
        return True

    def select(self, turn_id: str) -> QuestionTurn | None:
        if turn_id not in self.turns:
            return None
        self.current_id = turn_id
        return self.turns[turn_id]
