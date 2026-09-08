"""Tests for the lexicons and the scores built on them."""

from __future__ import annotations

import pytest

from ddsignal.nlp.lexicon import (
    _is_acronym,
    count_terms,
    load_terms,
    load_topics,
    word_count,
)
from ddsignal.nlp.scores import score, topic_mentions, topics


class TestLexiconLoading:
    def test_lists_load_and_are_not_empty(self):
        for name in ("hedging", "negative", "positive"):
            assert len(load_terms(name)) > 20

    def test_comments_are_stripped(self):
        assert not any(term.startswith("#") for term in load_terms("hedging"))

    def test_topics_parse_into_sections(self):
        names = {topic.name for topic in load_topics()}
        assert "artificial intelligence" in names
        assert "supply chain" in names

    def test_strong_terms_are_recorded(self):
        ai = next(t for t in load_topics() if t.name == "artificial intelligence")
        assert "artificial intelligence" in ai.strong
        assert "algorithms" not in ai.strong


class TestAcronyms:
    @pytest.mark.parametrize("term", ["AI", "LLM", "GDPR"])
    def test_all_caps_words_are_acronyms(self, term):
        assert _is_acronym(term)

    @pytest.mark.parametrize("term", ["may", "supply chain", "A", "10-K"])
    def test_ordinary_terms_are_not(self, term):
        assert not _is_acronym(term)

    def test_acronyms_match_case_sensitively(self):
        # "AI" as a word must count; "ai" inside an Italian exhibit title, or as
        # OCR noise in a scanned filing, must not.
        assert count_terms("We invest in AI research", ("AI",)) == {"AI": 1}
        assert count_terms("Societa ai sensi dell articolo", ("AI",)) == {}

    def test_ordinary_terms_still_match_case_insensitively(self):
        assert count_terms("Tariffs and TARIFFS", ("tariffs",)) == {"tariffs": 2}


class TestCountTerms:
    def test_word_boundaries_are_respected(self):
        assert count_terms("maybe", ("may",)) == {}
        assert count_terms("we may act", ("may",)) == {"may": 1}

    def test_multi_word_terms_match_across_whitespace(self):
        assert count_terms("a supply   chain issue", ("supply chain",)) == {"supply chain": 1}

    def test_longest_term_wins(self):
        counts = count_terms("there is no assurance of profit", ("no assurance", "no"))
        assert counts == {"no assurance": 1}


class TestScores:
    def test_empty_text_scores_zero(self):
        result = score("")
        assert result.words == 0
        assert result.hedging == 0.0

    def test_hedging_is_a_rate_not_a_count(self):
        # The same prose twice over is twice as long and equally hedged.
        once = score("We may possibly be unable to predict the outcome of this matter.")
        twice = score("We may possibly be unable to predict the outcome of this matter. " * 2)
        assert once.hedging == pytest.approx(twice.hedging, rel=0.01)
        assert twice.words > once.words

    def test_negative_language_moves_tone_down(self):
        bad = score("Adverse disruption caused losses, impairment and a material weakness.")
        good = score("Strong growth improved profitability and expanded our leadership.")
        assert bad.tone < 0 < good.tone

    def test_tone_is_bounded(self):
        assert -1.0 <= score("losses losses losses").tone <= 1.0

    def test_delta_reports_movement(self):
        delta = score("adverse losses everywhere").delta(score("strong growth everywhere"))
        assert delta["tone"] < 0


class TestTopics:
    def test_two_terms_are_needed_for_a_weak_topic(self):
        # One passing mention of "employees" is every filing ever written.
        assert "labour and talent" not in topics("Our employees are important to us.")
        assert "labour and talent" in topics(
            "Our employees and workforce retention depend on hiring and wage inflation."
        )

    def test_one_strong_term_is_enough(self):
        assert "cybersecurity" in topics("A ransomware event would be costly.")

    def test_mentions_count_intensity(self):
        text = "Tariffs on imports. More tariffs. Further tariffs and trade policy changes."
        assert topic_mentions(text)["tariffs and trade"] >= 3

    def test_absent_topics_are_absent(self):
        assert "climate and environment" not in topics("We sell shoes in retail stores.")


def test_word_count_counts_words():
    assert word_count("one two three") == 3
    assert word_count("") == 0
