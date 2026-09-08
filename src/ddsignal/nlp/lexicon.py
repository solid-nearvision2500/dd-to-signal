"""Loading the word lists, and matching them against text.

The lists are plain text so that a user can edit them without touching Python,
which matters: the whole project is a set of opinions about which words are
worth counting, and yours will not be identical to mine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

#: The lists live in the repo during development and inside the wheel once
#: installed. Checking both keeps ``pip install -e .`` and a real install honest.
_SEARCH_PATHS = (
    Path(__file__).resolve().parents[3] / "data" / "lexicons",
    Path(__file__).resolve().parent.parent / "_data" / "lexicons",
)


def lexicon_dir() -> Path:
    for path in _SEARCH_PATHS:
        if path.is_dir():
            return path
    raise FileNotFoundError(
        "cannot find the lexicons. Looked in: " + ", ".join(str(p) for p in _SEARCH_PATHS)
    )


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


@cache
def load_terms(name: str) -> tuple[str, ...]:
    """The terms in ``data/lexicons/<name>.txt``."""
    path = lexicon_dir() / f"{name}.txt"
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        term = _strip_comment(line)
        if term:
            # Acronyms keep their case; everything else is folded, so the
            # lists can be written naturally.
            terms.append(term if _is_acronym(term) else term.lower())
    return tuple(terms)


@dataclass(frozen=True)
class Topic:
    name: str
    terms: tuple[str, ...]
    #: Terms specific enough to tag a paragraph on their own.
    strong: frozenset[str]


@cache
def load_topics(name: str = "topics") -> tuple[Topic, ...]:
    """Parse the ``[section]``-delimited topic file."""
    path = lexicon_dir() / f"{name}.txt"
    topics: list[Topic] = []
    current: str | None = None
    terms: list[str] = []
    strong: set[str] = set()

    def flush() -> None:
        if current and terms:
            topics.append(Topic(current, tuple(terms), frozenset(strong)))

    for line in path.read_text(encoding="utf-8").splitlines():
        text = _strip_comment(line)
        if not text:
            continue
        if text.startswith("[") and text.endswith("]"):
            flush()
            current, terms, strong = text[1:-1].strip(), [], set()
            continue
        term = text if _is_acronym(text.lstrip("!").strip()) else text.lower()
        if term.startswith("!"):
            term = term[1:].strip()
            strong.add(term)
        terms.append(term)
    flush()
    return tuple(topics)


def _is_acronym(term: str) -> bool:
    r"""``AI`` and ``LLM`` are acronyms; ``may`` and ``supply chain`` are not.

    Acronyms have to be matched case-sensitively or they are useless. A
    case-insensitive ``\bai\b`` fires on the Italian and Japanese fragments that
    turn up in exhibit lists and on OCR noise in older filings, which is how you
    end up reporting that a shipping company discovered machine learning.
    """
    return len(term) >= 2 and term.isupper() and term.isalpha()


def _alternation(terms: list[str]) -> str:
    # Longest-first, so "no assurance" wins over "no" when both are present.
    ordered = sorted(set(terms), key=len, reverse=True)
    return "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in ordered)


@cache
def compile_terms(
    terms: tuple[str, ...],
) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """Compile a list into a (case-insensitive, case-sensitive) pair of patterns.

    ``\b`` on both ends of each, so "may" does not match "maybe".
    """
    loose = [t for t in terms if not _is_acronym(t)]
    exact = [t for t in terms if _is_acronym(t)]
    return (
        re.compile(rf"\b(?:{_alternation(loose)})\b", re.IGNORECASE) if loose else None,
        re.compile(rf"\b(?:{_alternation(exact)})\b") if exact else None,
    )


def count_terms(text: str, terms: tuple[str, ...]) -> dict[str, int]:
    """How many times each term appears, keyed by the term as written."""
    counts: dict[str, int] = {}
    loose, exact = compile_terms(terms)
    for pattern, fold in ((loose, True), (exact, False)):
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            key = re.sub(r"\s+", " ", match.group(0))
            key = key.lower() if fold else key
            counts[key] = counts.get(key, 0) + 1
    return counts


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))
