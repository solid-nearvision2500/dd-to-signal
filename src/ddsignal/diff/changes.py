"""The change model, and the word-level diff that fills it in."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum

_PUNCT = re.compile(r"[^\w\s]+")
_WS = re.compile(r"\s+")
#: Words, numbers and single punctuation marks. Punctuation is its own token so
#: "customers." and "customers," diff as one shared word plus a changed
#: separator. Numbers keep their decimal points and thousands separators, so a
#: revenue figure moving from 1.2 to 3.4 billion reads as one substitution
#: rather than as four fiddly edits around an unchanged full stop.
_TOKEN = re.compile(r"\d[\d,.]*\d|\w+(?:'\w+)?|[^\w\s]")


class Kind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    UNCHANGED = "unchanged"


def normalise(text: str) -> str:
    """The comparison key for a block.

    Case, punctuation and whitespace are dropped, because a filer who re-wraps a
    paragraph or swaps a semicolon for a full stop has not told you anything.
    """
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def token_spans(text: str) -> list[tuple[str, int, int]]:
    """Tokens with their offsets in the original string."""
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(text)]


@dataclass
class Span:
    """A run of words inside a modified paragraph."""

    op: str  # "equal" | "insert" | "delete"
    text: str


@dataclass
class Change:
    """One paragraph's worth of difference between two filings."""

    kind: Kind
    old: str | None = None
    new: str | None = None
    similarity: float = 0.0
    spans: list[Span] = field(default_factory=list)
    section: str = ""
    #: Risk topics this paragraph is about, filled in by the pipeline. Useful
    #: on a change rather than only on a section: the reader wants to know that
    #: the one rewritten paragraph is the tariffs one.
    topics: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Whichever side exists, preferring the new one."""
        return self.new or self.old or ""

    @property
    def words_added(self) -> int:
        return sum(len(tokens(s.text)) for s in self.spans if s.op == "insert")

    @property
    def words_removed(self) -> int:
        return sum(len(tokens(s.text)) for s in self.spans if s.op == "delete")

    @property
    def churn(self) -> int:
        """Words touched, in either direction."""
        if self.kind is Kind.ADDED:
            return len(tokens(self.new or ""))
        if self.kind is Kind.REMOVED:
            return len(tokens(self.old or ""))
        return self.words_added + self.words_removed


def word_diff(old: str, new: str) -> list[Span]:
    """Word-level spans turning ``old`` into ``new``.

    The diff runs over tokens, but every span is cut straight out of the source
    string using those tokens' offsets rather than rejoined from the tokens
    themselves. Rejoining is the obvious approach and it is wrong: no set of
    spacing rules survives contact with a 10-K, which is full of "U.S.",
    "$1.2 billion", '("EU")' and "Section 232(b)". Slicing the original cannot
    get the spacing wrong, because it never takes the spacing apart.

    Runs of unchanged text are merged into single spans, so the report renders
    context as prose rather than one element per word.
    """
    a, b = token_spans(old), token_spans(new)
    matcher = SequenceMatcher(a=[t[0] for t in a], b=[t[0] for t in b], autojunk=False)
    spans: list[Span] = []

    def cut(source: str, marks: list[tuple[str, int, int]], start: int, stop: int) -> str:
        if start >= stop:
            return ""
        return source[marks[start][1] : marks[stop - 1][2]]

    def push(op: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if spans and spans[-1].op == op:
            spans[-1] = Span(op, f"{spans[-1].text} {text}")
        else:
            spans.append(Span(op, text))

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            push("equal", cut(old, a, i1, i2))
        elif tag == "delete":
            push("delete", cut(old, a, i1, i2))
        elif tag == "insert":
            push("insert", cut(new, b, j1, j2))
        else:  # replace
            push("delete", cut(old, a, i1, i2))
            push("insert", cut(new, b, j1, j2))
    return spans


def similarity(old: str, new: str) -> float:
    """Cheap 0-1 similarity, used to confirm a candidate pairing."""
    return SequenceMatcher(a=normalise(old), b=normalise(new), autojunk=False).ratio()
