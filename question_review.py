import re
from dataclasses import dataclass


INCOMPLETE = re.compile(
    r"(?:^|[\s，,。；;：:]|(?:之后|以后|之前|以前))"
    r"(?:(?:你们|你|您|我|我们|他|他们)\s*(?:没有|有没有|是否|能不能|如果)|"
    r"when you|why did you not|if you|how would you)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuestionReview:
    message: str = ""
    replacement: str = ""
    incomplete: bool = False


def parse_terms(value):
    aliases = []
    for line in re.split(r"[\n;；]+", value):
        if not line.strip():
            continue
        preferred, separator, heard = (part.strip() for part in line.partition("="))
        if (not separator or not preferred or not heard or "=" in heard
                or preferred.casefold() == heard.casefold() or max(len(preferred), len(heard)) > 40):
            raise ValueError("术语格式为「正确术语=可能听成的词」，多个用分号隔开。")
        aliases.append((preferred, heard))
    if len(aliases) > 24:
        raise ValueError("本场最多设置 24 条术语候选。")
    return tuple(aliases)


def term_pattern(value):
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(value) + r"(?![A-Za-z0-9_])",
                      re.IGNORECASE)


def review_question(question, aliases):
    stripped = question.rstrip("。？?！!….,，；;\u3000 \t\r\n")
    if INCOMPLETE.search(stripped):
        return QuestionReview("这句话似乎没说完。请补完整问题后再提交。", incomplete=True)
    for preferred, heard in aliases:
        if term_pattern(preferred).search(question):
            continue
        pattern = term_pattern(heard)
        if pattern.search(question):
            replacement = pattern.sub(lambda _: preferred, question)
            return QuestionReview(f"听到了「{heard}」。本场术语表也有「{preferred}」，请确认题意。",
                                  replacement)
    return QuestionReview()
