"""Turning a section of prose into a handful of numbers.

Every score here is a rate per thousand words rather than a raw count, because
the interesting comparison is always between two filings of different lengths.
A risk factors section that grows 20% longer will contain 20% more of every word
in the language, and a metric that cannot tell that apart from a change in tone
is worse than no metric.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lexicon import count_terms, load_terms, load_topics, word_count

PER = 1000.0


@dataclass(frozen=True)
class Scores:
    """The measurements taken of one section of one filing."""

    words: int
    hedging: float
    negative: float
    positive: float
    tone: float

    def delta(self, other: Scores) -> dict[str, float]:
        """``new.delta(old)`` -> how each measure moved."""
        return {
            "words": self.words - other.words,
            "hedging": self.hedging - other.hedging,
            "negative": self.negative - other.negative,
            "positive": self.positive - other.positive,
            "tone": self.tone - other.tone,
        }


def score(text: str) -> Scores:
    words = word_count(text)
    if not words:
        return Scores(0, 0.0, 0.0, 0.0, 0.0)

    hedges = sum(count_terms(text, load_terms("hedging")).values())
    negative = sum(count_terms(text, load_terms("negative")).values())
    positive = sum(count_terms(text, load_terms("positive")).values())

    # Polarity normalised to [-1, 1]. The denominator is the sentiment words
    # only, so "this filing barely uses loaded language at all" shows up as a
    # tone near zero rather than being diluted by section length.
    total = negative + positive
    tone = (positive - negative) / total if total else 0.0

    return Scores(
        words=words,
        hedging=hedges * PER / words,
        negative=negative * PER / words,
        positive=positive * PER / words,
        tone=tone,
    )


def topic_mentions(text: str) -> dict[str, int]:
    """Total term hits per qualifying topic.

    ``topics()`` answers "does this filing talk about tariffs at all", which is
    binary and therefore blind to the most common real move: a company that
    mentioned tariffs twice last year mentioning them ten times this year. That
    is not a new risk, it is a promoted one, and it needs a count rather than a
    flag.
    """
    found: dict[str, int] = {}
    for topic in load_topics():
        counts = count_terms(text, topic.terms)
        distinct = set(counts)
        if not distinct:
            continue
        if len(distinct) >= 2 or distinct & topic.strong:
            found[topic.name] = sum(counts.values())
    return found


def topics(text: str) -> dict[str, int]:
    """Which risk topics this text covers, and how strongly.

    A topic needs two distinct terms, or one term flagged as sufficient alone.
    The value returned is the number of distinct terms matched, which is a
    better proxy for "how much of this paragraph is about the topic" than a raw
    frequency that one repeated word can dominate.
    """
    found: dict[str, int] = {}
    for topic in load_topics():
        counts = count_terms(text, topic.terms)
        distinct = set(counts)
        if not distinct:
            continue
        if len(distinct) >= 2 or distinct & topic.strong:
            found[topic.name] = len(distinct)
    return found
