"""Tests for turning filing HTML into blocks and sections."""

from __future__ import annotations

from ddsignal.extract.html import Block, to_blocks, to_text
from ddsignal.extract.sections import find_sections

# Long enough that any real section built from it clears the 120-word floor that
# separates a section from a cross-reference.
LONG = "The Company is exposed to a great many risks in the ordinary course of business. " * 12


def para(text: str) -> str:
    return f"<p>{text}</p>"


class TestToText:
    def test_block_tags_separate_paragraphs(self):
        text = to_text("<div><p>First one here.</p><p>Second one here.</p></div>")
        assert text.split("\n") == ["First one here.", "Second one here."]

    def test_inline_tags_do_not_separate(self):
        text = to_text("<p>A <b>bold</b> claim about <i>risk</i>.</p>")
        assert text == "A bold claim about risk."

    def test_hidden_elements_are_dropped(self):
        html = "<p>Visible text.</p><p style='display:none'>Hidden text.</p>"
        assert "Hidden" not in to_text(html)

    def test_script_and_style_are_dropped(self):
        html = "<style>p{color:red}</style><script>var x=1</script><p>Real text.</p>"
        assert to_text(html) == "Real text."

    def test_typographic_characters_are_folded(self):
        # An em dash swapped for a hyphen between two filings must not register
        # as a change, so both normalise to the same thing.
        assert to_text("<p>the “Company”—its risks</p>") == 'the "Company"--its risks'

    def test_non_breaking_space_becomes_a_space(self):
        assert to_text("<p>Item 1A</p>") == "Item 1A"

    def test_empty_input(self):
        assert to_text("") == ""
        assert to_text("<html><body></body></html>") == ""

    def test_malformed_html_still_parses(self):
        assert "Unclosed" in to_text("<div><p>Unclosed paragraph")


class TestToBlocks:
    def test_short_furniture_is_dropped(self):
        blocks = to_blocks(f"<p>17</p><p>Table of Contents</p>{para(LONG)}")
        assert [b.text for b in blocks] == [LONG.strip()]

    def test_headings_are_kept_despite_being_short(self):
        blocks = to_blocks(f"<p>ITEM 1A. RISK FACTORS</p>{para(LONG)}")
        assert blocks[0].kind == "heading"
        assert blocks[0].text == "ITEM 1A. RISK FACTORS"

    def test_numeric_rows_are_tagged_as_tables(self):
        row = "2025 1,234.5 2024 1,100.2 2023 998.7 2022 812.4 2021 640.1 640.1 812"
        blocks = to_blocks(para(row))
        assert blocks[0].kind == "table"


def build_filing(*, contents: bool = True, items: dict[str, str] | None = None) -> str:
    """A minimal but realistically shaped 10-K."""
    items = items or {}
    parts = []
    if contents:
        parts.append("<p>Table of Contents</p>")
        for item in ("1", "1A", "1B", "2", "3", "5", "7", "7A", "8"):
            parts.append(f"<p>Item {item}. Something {item}</p>")
    for item, body in items.items():
        parts.append(f"<p>Item {item}. Heading</p>")
        parts.append(para(body))
    return "".join(parts)


class TestFindSections:
    def test_finds_risk_factors_after_the_contents_page(self):
        html = build_filing(items={"1A": LONG * 3, "1B": LONG})
        sections = find_sections(to_blocks(html))
        assert sections["risk_factors"]
        assert sections["risk_factors"].status == "found"
        assert "ordinary course of business" in sections["risk_factors"].text

    def test_contents_page_is_not_mistaken_for_the_section(self):
        # Without contents-page detection the first "Item 1A" is the contents
        # entry, and the section comes back as one line long.
        html = build_filing(items={"1A": LONG * 3, "1B": LONG})
        assert find_sections(to_blocks(html))["risk_factors"].words > 100

    def test_consecutive_empty_items_are_not_a_contents_page(self):
        # Apple's 10-K stacks 1B, 1C, 2, 3 and 5 within twenty blocks because it
        # has nothing to report under any of them. That density must not be
        # mistaken for a contents page, or the real sections get skipped.
        html = build_filing(
            contents=False,
            items={"1A": LONG * 3, "1B": "None.", "1C": "None.", "2": "None.", "3": "None."},
        )
        assert find_sections(to_blocks(html))["risk_factors"].words > 100

    def test_section_ends_at_the_next_item(self):
        html = build_filing(items={"1A": LONG, "1B": "UNIQUE-MARKER-TEXT " * 20})
        assert "UNIQUE-MARKER" not in find_sections(to_blocks(html))["risk_factors"].text

    def test_skipped_stop_item_falls_through_to_the_next(self):
        # A filer with nothing under 1B or 1C goes straight to Item 2, and the
        # section still has to end somewhere.
        html = build_filing(items={"1A": LONG, "2": "PROPERTIES-MARKER " * 20})
        section = find_sections(to_blocks(html))["risk_factors"]
        assert section
        assert "PROPERTIES-MARKER" not in section.text

    def test_incorporation_by_reference_is_reported_not_guessed(self):
        html = build_filing(
            items={"1A": "Refer to pages 9 through 31 of the 2025 Annual Report.", "1B": LONG}
        )
        section = find_sections(to_blocks(html))["risk_factors"]
        assert not section
        assert section.status == "by_reference"

    def test_missing_item_is_reported_as_not_found(self):
        html = build_filing(items={"1": LONG, "2": LONG})
        assert find_sections(to_blocks(html))["risk_factors"].status == "not_found"

    def test_title_only_headings_are_found(self):
        # Amazon and Walmart head the section "RISK FACTORS" with no item number
        # anywhere near the text.
        html = (
            "<p>Table of Contents</p>"
            + "".join(f"<p>Item {i}. Something</p>" for i in ("1", "1A", "1B", "2", "3", "7", "8"))
            + "<p>RISK FACTORS</p>"
            + para(LONG * 3)
            + "<p>PROPERTIES</p>"
            + para("PROPERTIES-MARKER " * 20)
        )
        section = find_sections(to_blocks(html))["risk_factors"]
        assert section
        assert "PROPERTIES-MARKER" not in section.text

    def test_title_fallback_refuses_to_swallow_the_document(self):
        # A title match with no closing boundary would otherwise return
        # everything to the end of the filing as one section.
        html = "<p>QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK</p>" + para(
            LONG * 200
        )
        assert not find_sections(to_blocks(html))["market_risk"]

    def test_cross_reference_mid_sentence_is_not_a_heading(self):
        html = build_filing(
            items={"1A": LONG},
            ) + para(
            "Item 1A. Risk Factors above describes the risks, and this sentence runs on well "
            "past the length any heading would, so it must not be treated as one. " * 3
        )
        assert find_sections(to_blocks(html))["risk_factors"]


class TestTenQ:
    def test_ten_q_uses_part_scoped_items(self):
        html = (
            "<p>Part I</p><p>Item 2. Management's Discussion</p>"
            + para(LONG * 2)
            + "<p>Item 3. Quantitative</p>"
            + "<p>Part II</p><p>Item 1A. Risk Factors</p>"
            + para(LONG * 2)
            + "<p>Item 2. Unregistered Sales</p>"
        )
        sections = find_sections(to_blocks(html), form="10-Q")
        assert sections["mdna"]
        assert sections["risk_factors"]


def test_block_equality_is_by_value():
    assert Block("a", "text") == Block("a", "text")
