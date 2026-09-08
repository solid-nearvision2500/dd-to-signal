"""Finding Item 1A, Item 7 and friends inside a filing.

This is the part of SEC parsing that everybody underestimates. The naive version
(search for "Item 1A", slice to "Item 1B") fails on essentially every real
filing, for three reasons:

1. The table of contents lists every item, so the first "Item 1A" in the
   document is usually a link, not the section.
2. Filings cross-reference each other mid-sentence: "as described in Item 1A.
   Risk Factors above" is not the start of anything.
3. Companies skip items. A 10-K with nothing to report under Item 1B goes
   straight from 1A to Item 2, and a slicer that insists on 1B returns nothing.

The approach here: find every anchor, throw out the table of contents by
detecting the cluster of anchors that defines it, throw out anchors that are
mid-sentence, then take the first surviving start and the first surviving stop
after it. Any of several stop items will do, so a skipped item is survivable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .html import Block


@dataclass(frozen=True)
class SectionSpec:
    """How to find one named section."""

    key: str
    title: str
    start: str  # item number, e.g. "1A"
    stops: tuple[str, ...]  # any of these item numbers ends it
    part: str | None = None  # 10-Q items are numbered per part
    weight: float = 1.0  # how much a change here matters, see FilingDiff.signal
    #: Name of the heading to look for when the item number is not in the body.
    #: Amazon and Walmart both head the section "RISK FACTORS" with no "Item 1A"
    #: anywhere near it, and they are not unusual: the item numbers are required
    #: in the table of contents, not above the text.
    heading: str = ""


#: The narrative sections worth diffing. Everything else in a 10-K is either
#: financial statements (better handled as XBRL numbers) or boilerplate that
#: never changes.
TENK_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        "risk_factors", "Item 1A. Risk Factors", "1A", ("1B", "1C", "2"),
        weight=1.0, heading=r"risk factors",
    ),
    SectionSpec(
        "business", "Item 1. Business", "1", ("1A", "1B", "2"),
        weight=0.6, heading=r"business",
    ),
    SectionSpec(
        "legal", "Item 3. Legal Proceedings", "3", ("4", "5"),
        weight=0.9, heading=r"legal proceedings",
    ),
    SectionSpec(
        "mdna", "Item 7. Management's Discussion", "7", ("7A", "8"),
        weight=0.8, heading=r"management'?s discussion and analysis.*",
    ),
    SectionSpec(
        "market_risk", "Item 7A. Market Risk", "7A", ("8",),
        weight=0.7, heading=r"quantitative and qualitative disclosures.*",
    ),
)

#: Every heading that can legitimately end a section, as a title rather than an
#: item number. Used only by the title fallback, and kept in filing order so the
#: end of one section is simply the next of these that appears.
BOUNDARY_HEADINGS: tuple[str, ...] = (
    r"business",
    r"risk factors",
    r"unresolved staff comments",
    r"cybersecurity",
    r"properties",
    r"legal proceedings",
    r"mine safety disclosures",
    r"market for (?:the )?registrant.*",
    r"selected financial data",
    r"management'?s discussion and analysis.*",
    r"quantitative and qualitative disclosures.*",
    r"financial statements and supplementary data",
    r"changes in and disagreements with accountants.*",
    r"controls and procedures",
    r"other information",
    r"directors, executive officers.*",
    r"executive compensation",
    r"security ownership.*",
    r"certain relationships and related.*",
    r"principal account(?:ant|ing) fees.*",
    r"exhibit(?:s)?(?:,| and) financial statement schedules",
    r"exhibits",
    r"signatures",
)

TENQ_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("mdna", "Item 2. Management's Discussion", "2", ("3", "4"), part="I", weight=0.8),
    SectionSpec("risk_factors", "Item 1A. Risk Factors", "1A", ("2", "3"), part="II", weight=1.0),
    SectionSpec("legal", "Item 1. Legal Proceedings", "1", ("1A", "2"), part="II", weight=0.9),
)


def sections_for(form: str) -> tuple[SectionSpec, ...]:
    form = form.upper()
    if form.startswith("10-Q"):
        return TENQ_SECTIONS
    return TENK_SECTIONS


#: Why a section is not available to diff. Reporting this honestly matters more
#: than it sounds: "JPMorgan rewrote nothing this year" and "we could not read
#: JPMorgan's risk factors" look identical in a dashboard that only counts words.
STATUSES = ("found", "by_reference", "too_short", "not_found")

_BY_REFERENCE = re.compile(
    r"refer to|incorporated (?:herein )?by reference|included in (?:the )?[^.]*annual report",
    re.I,
)


@dataclass
class Section:
    """A located section and its blocks."""

    key: str
    title: str
    blocks: list[Block] = field(default_factory=list)
    weight: float = 1.0
    status: str = "found"

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)

    @property
    def words(self) -> int:
        return sum(len(b.text.split()) for b in self.blocks)

    def __bool__(self) -> bool:
        return bool(self.blocks)


def _anchor_re(item: str) -> re.Pattern[str]:
    """Match "Item 1A", "ITEM 1A.", "Item 1A -" at the start of a block."""
    number = re.escape(item)
    # The optional letter is separated because filings write "Item 1 A" and
    # "Item 1A" interchangeably, and a few insert a non-breaking space.
    if item[-1].isalpha():
        number = rf"{re.escape(item[:-1])}\s*{re.escape(item[-1])}"
    return re.compile(rf"^item\s*{number}\s*[.:\-\u2014)]?\s*(?![\dA-Za-z])", re.I)


_PART = re.compile(r"^part\s+([ivx]+)\b", re.I)
#: A block that starts with "Item 1A" but runs on for a full sentence is a
#: cross-reference inside body text rather than a heading. The cut-off is
#: generous rather than tight because some filers put the section title and its
#: first paragraph in a single block.
_MAX_HEADING_CHARS = 240


def _anchors(blocks: list[Block], item: str) -> list[int]:
    pattern = _anchor_re(item)
    hits = []
    for i, block in enumerate(blocks):
        if not pattern.match(block.text):
            continue
        if block.kind != "heading" and len(block.text) > _MAX_HEADING_CHARS:
            continue
        hits.append(i)
    return hits


#: A contents row is a line, not a paragraph. Runs of blocks under this length
#: are where a table of contents can live; a real section always breaks the run.
_SHORT_BLOCK = 200


def _toc_range(blocks: list[Block]) -> tuple[int, int] | None:
    """Locate the table of contents, as a block index range.

    Two properties identify one, and both are needed.

    The first is *shape*. A contents page is an unbroken run of short lines --
    every row is "Item 1A. Risk Factors ... 9". Body sections cannot look like
    that, because a body section contains prose, and one long paragraph ends the
    run. Grouping anchors by runs of short blocks is what separates the contents
    page from Apple's 10-K, which stacks five empty items ("Item 1B. None.")
    within twenty blocks and would fool any rule based on proximity alone.

    The second is *redundancy*: a contents entry promises the item appears again
    below. Most filings satisfy this, but not all. Pfizer numbers its items in
    the contents and then heads the body sections by name only. So a dense run
    of six or more distinct items near the top of the document is taken as a
    contents page even when nothing repeats, since nothing else in a filing has
    that shape.
    """
    items = ("1", "1A", "1B", "1C", "2", "3", "4", "5", "7", "7A", "8", "9", "9A")
    positions: dict[int, str] = {}
    for item in items:
        for i in _anchors(blocks, item):
            positions.setdefault(i, item)

    if len(positions) < 4:
        return None

    by_item: dict[str, list[int]] = {}
    for i, item in sorted(positions.items()):
        by_item.setdefault(item, []).append(i)

    # Split the document into runs of consecutive short blocks.
    runs: list[list[int]] = []
    current: list[int] = []
    for i, block in enumerate(blocks):
        if len(block.text) <= _SHORT_BLOCK:
            current.append(i)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)

    depth_limit = max(int(len(blocks) * 0.15), 12)

    best: tuple[int, int] | None = None
    best_count = 0
    for run in runs:
        inside = [i for i in run if i in positions]
        distinct = {positions[i] for i in inside}
        if len(distinct) < 4:
            continue

        # The run ends at the first long paragraph, so its final anchor is
        # usually the body's first section heading rather than a contents row:
        # "Item 1A. Risk Factors" immediately followed by the risk factors
        # themselves. Give it back, or the section we most want is swallowed by
        # the contents page we just found.
        if len(inside) > 1 and inside[-1] == run[-1]:
            inside = inside[:-1]
            distinct = {positions[i] for i in inside}
            if len(distinct) < 4:
                continue

        first, last = inside[0], inside[-1]
        repeated = sum(any(j > last for j in by_item[item]) for item in distinct)
        # Two thirds rather than all: filings routinely list an item in the
        # contents and then omit the heading in the body because the section is
        # a single "None." line.
        redundant = repeated >= len(distinct) * 2 / 3
        dense_and_early = len(distinct) >= 6 and first <= depth_limit

        if (redundant or dense_and_early) and len(distinct) > best_count:
            best_count = len(distinct)
            best = (first, last)

    return best


def _heading_re(pattern: str) -> re.Pattern[str]:
    # Anchored at both ends: "RISK FACTORS" is the section, while "Summary of
    # Risk Factors" and "the risk factors described below" are not.
    return re.compile(rf"^(?:item\s*\d+[a-c]?\s*[.:\-]?\s*)?{pattern}\s*$", re.I)


def _title_anchors(blocks: list[Block], pattern: str) -> list[int]:
    matcher = _heading_re(pattern)
    return [
        i
        for i, block in enumerate(blocks)
        if len(block.text) <= 120 and matcher.match(block.text.strip())
    ]


def _by_heading(blocks: list[Block], spec: SectionSpec, skip: range | None) -> list[Block] | None:
    """Locate a section by its title, for filings that omit the item numbers."""
    if not spec.heading:
        return None

    starts = [i for i in _title_anchors(blocks, spec.heading) if not (skip and i in skip)]
    if not starts:
        return None
    start = starts[0]

    ends = [
        i
        for pattern in BOUNDARY_HEADINGS
        if pattern != spec.heading
        for i in _title_anchors(blocks, pattern)
        if i > start and not (skip and i in skip)
    ]
    end = min(ends) if ends else len(blocks)

    # If no boundary heading followed, the "section" is everything to the end of
    # the filing, which is not a section. It is a failed match that happens to
    # contain the right words. Intel's 10-K produced a 68,000-word Item 7A this
    # way. Better to report nothing found than to report the whole document.
    if (end - start) > len(blocks) * 0.45:
        return None

    return blocks[start + 1 : end]


def _followed_by_prose(blocks: list[Block], i: int) -> bool:
    """Whether the block after ``i`` is a sentence rather than a contents row.

    The one shape the contents-page detector cannot resolve on its own is a body
    section whose entire content is a single short line, such as "Refer to
    pages 9 through 31 of the 2025 Annual Report." That is a real section,
    reported as incorporated by reference, but by shape it is indistinguishable
    from a row in the table of contents. A sentence is the tell: contents rows
    are titles and page numbers, and they do not end in full stops.
    """
    if i + 1 >= len(blocks):
        return False
    text = blocks[i + 1].text.strip()
    return text.endswith(".") and len(text.split()) >= 8


def find_sections(blocks: list[Block], form: str = "10-K") -> dict[str, Section]:
    """Split a filing's blocks into the named narrative sections we care about."""
    toc = _toc_range(blocks)

    def usable(hits: list[int]) -> list[int]:
        if toc is None:
            return hits
        lo, hi = toc
        return [i for i in hits if not (lo <= i <= hi) or _followed_by_prose(blocks, i)]

    part_starts: dict[str, int] = {}
    for i, block in enumerate(blocks):
        match = _PART.match(block.text)
        if match and len(block.text) < _MAX_HEADING_CHARS:
            part_starts.setdefault(match.group(1).upper(), i)

    out: dict[str, Section] = {}
    for spec in sections_for(form):
        floor = part_starts.get(spec.part or "", 0) if spec.part else 0
        starts = [i for i in usable(_anchors(blocks, spec.start)) if i >= floor]
        if not starts:
            fallback = _by_heading(blocks, spec, range(*toc) if toc else None)
            section = Section(spec.key, spec.title, [], spec.weight, "not_found")
            if fallback:
                section.blocks = [b for b in fallback if b.kind != "heading" or len(b.text) > 60]
                if section.words >= 120:
                    section.status = "found"
                else:
                    section.blocks = []
            out[spec.key] = section
            continue
        start = starts[0]

        stops = [i for stop in spec.stops for i in usable(_anchors(blocks, stop)) if i > start]
        end = min(stops) if stops else len(blocks)

        body = [b for b in blocks[start + 1 : end] if b.kind != "heading" or len(b.text) > 60]
        section = Section(spec.key, spec.title, body, spec.weight)

        # A section of two paragraphs is a filer who incorporated the real thing
        # into an exhibit, or a mis-parse. Either way there is nothing to diff,
        # and reporting "risk factors rewritten" off forty words would be a lie.
        # Big banks do this constantly: JPMorgan's 10-K Item 1A is one sentence
        # pointing at pages 9-31 of a document that is not this document.
        if section.words < 120:
            fallback = _by_heading(blocks, spec, range(*toc) if toc else None)
            if fallback is not None:
                section.blocks = [b for b in fallback if b.kind != "heading" or len(b.text) > 60]

        if section.words < 120:
            head = section.text[:600]
            section.blocks = []
            section.status = "by_reference" if _BY_REFERENCE.search(head) else "too_short"

        out[spec.key] = section

    return out


def readable(sections: dict[str, Section]) -> dict[str, Section]:
    """Just the sections with text in them."""
    return {k: v for k, v in sections.items() if v}
