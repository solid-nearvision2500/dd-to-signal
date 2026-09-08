"""Tests for the word diff and the paragraph aligner."""

from __future__ import annotations

from ddsignal.diff.align import align
from ddsignal.diff.changes import Kind, normalise, similarity, tokens, word_diff
from ddsignal.extract.html import Block


def blocks(*texts: str) -> list[Block]:
    return [Block(t) for t in texts]


def kinds(changes) -> list[str]:
    return [c.kind.value for c in changes]


class TestNormalise:
    def test_case_and_punctuation_are_ignored(self):
        # Punctuation becomes whitespace rather than vanishing, which is what
        # makes it symmetrical: both sides of a comparison are folded the same
        # way, so re-punctuating a sentence is not a change.
        assert normalise("The Company's risks.") == normalise("the company's risks")
        assert normalise("risks, and costs") == normalise("risks and costs")

    def test_whitespace_is_collapsed(self):
        assert normalise("a   b\n\nc") == "a b c"


class TestTokens:
    def test_punctuation_is_its_own_token(self):
        assert tokens("risks, and more.") == ["risks", ",", "and", "more", "."]

    def test_decimals_stay_whole(self):
        assert "1.2" in tokens("revenue of 1.2 billion")

    def test_thousands_separators_stay_whole(self):
        assert "1,234.5" in tokens("costs of 1,234.5 million")


class TestWordDiff:
    def test_spacing_of_the_original_is_preserved(self):
        # Rejoining tokens would produce "U. S." here, which is the single most
        # visible way a filing diff can look amateurish.
        spans = word_diff(
            "sales outside the U.S. were strong",
            "sales outside the U.S. were weak",
        )
        assert spans[0].text == "sales outside the U.S. were"

    def test_quotes_and_brackets_survive(self):
        spans = word_diff('the ("EU") tariffs rose', 'the ("EU") tariffs fell')
        assert '("EU")' in spans[0].text

    def test_insertion_is_marked(self):
        spans = word_diff("we face risks", "we face significant risks")
        assert [s.op for s in spans] == ["equal", "insert", "equal"]
        assert spans[1].text == "significant"

    def test_deletion_is_marked(self):
        spans = word_diff("we face significant risks", "we face risks")
        assert [s.op for s in spans] == ["equal", "delete", "equal"]

    def test_identical_text_is_all_equal(self):
        assert [s.op for s in word_diff("same words", "same words")] == ["equal"]

    def test_empty_sides(self):
        assert [s.op for s in word_diff("", "brand new text")] == ["insert"]
        assert [s.op for s in word_diff("old text", "")] == ["delete"]


class TestSimilarity:
    def test_identical_is_one(self):
        assert similarity("a risk", "a risk") == 1.0

    def test_unrelated_is_low(self):
        assert similarity("supply chain disruption", "executive compensation plan") < 0.5


class TestAlign:
    def test_unchanged_paragraphs_are_matched(self):
        old = blocks("First risk paragraph.", "Second risk paragraph.")
        assert kinds(align(old, list(old))) == ["unchanged", "unchanged"]

    def test_reordering_is_not_a_rewrite(self):
        # The point of matching rather than line-diffing: a company that moves a
        # risk factor from fourth to fortieth has not changed it.
        old = blocks("Alpha risk text.", "Beta risk text.", "Gamma risk text.")
        new = blocks("Gamma risk text.", "Alpha risk text.", "Beta risk text.")
        assert set(kinds(align(old, new))) == {"unchanged"}

    def test_edited_paragraph_is_modified_not_add_plus_remove(self):
        old = blocks(
            "We depend on a limited number of suppliers for critical components, "
            "and any disruption could materially affect our results."
        )
        new = blocks(
            "We depend on a single supplier for critical components, "
            "and any disruption could materially affect our results and margins."
        )
        changes = align(old, new)
        assert kinds(changes) == ["modified"]
        assert changes[0].words_added > 0
        assert changes[0].words_removed > 0

    def test_unrelated_paragraphs_are_add_and_remove(self):
        old = blocks("Our supply chain depends on semiconductors from Taiwan.")
        new = blocks("Executive compensation is determined by the board committee.")
        assert sorted(kinds(align(old, new))) == ["added", "removed"]

    def test_added_paragraph(self):
        old = blocks("Kept paragraph about the business and its many risks.")
        new = blocks(
            "Kept paragraph about the business and its many risks.",
            "Entirely new paragraph about artificial intelligence models.",
        )
        assert kinds(align(old, new)) == ["unchanged", "added"]

    def test_removed_paragraph(self):
        old = blocks(
            "Kept paragraph about the business and its many risks.",
            "Doomed paragraph about the pandemic and its effects.",
        )
        new = blocks("Kept paragraph about the business and its many risks.")
        assert sorted(kinds(align(old, new))) == ["removed", "unchanged"]

    def test_repeated_boilerplate_is_matched_one_for_one(self):
        # Three copies in, two copies out is one deletion -- not three matches
        # and a phantom, and not three deletions and two additions.
        line = "This paragraph is boilerplate that appears more than once here."
        changes = align(blocks(line, line, line), blocks(line, line))
        assert sorted(kinds(changes)) == ["removed", "unchanged", "unchanged"]

    def test_empty_old_makes_everything_added(self):
        assert kinds(align([], blocks("Something new entirely."))) == ["added"]

    def test_empty_new_makes_everything_removed(self):
        assert kinds(align(blocks("Something old entirely."), [])) == ["removed"]

    def test_churn_counts_words_touched(self):
        changes = align(blocks("we face risks"), blocks("we face significant risks"))
        assert changes[0].churn == 1

    def test_unchanged_paragraphs_have_no_churn(self):
        changes = align(blocks("identical text here"), blocks("identical text here"))
        assert changes[0].churn == 0
        assert changes[0].kind is Kind.UNCHANGED
