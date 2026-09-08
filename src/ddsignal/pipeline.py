"""Comparing two filings, and scoring how much the difference matters.

The output of this module is what everything else renders: a ``FilingDiff`` that
knows, per section, which paragraphs changed, how the language moved, and which
risk topics are new this year.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .diff.align import align
from .diff.changes import Change, Kind
from .edgar.client import EdgarClient
from .edgar.filings import Filing, consecutive_pairs, list_filings
from .extract.html import Block, to_blocks
from .extract.sections import Section, find_sections
from .nlp.scores import PER, Scores, score, topic_mentions, topics

log = logging.getLogger(__name__)

#: Churn below this is the annual tidy-up that every filing gets, and scores
#: zero. Churn at or above the ceiling is a rewrite. See ``FilingDiff.signal``.
FLOOR = 0.10
CEILING = 0.75


def _rank(topics_: dict[str, int]) -> dict[str, int]:
    return dict(sorted(topics_.items(), key=lambda kv: kv[1], reverse=True))


@dataclass(frozen=True)
class TopicShift:
    """A topic the company is talking about far more than it used to."""

    name: str
    old_count: int
    new_count: int
    old_rate: float
    new_rate: float

    @property
    def factor(self) -> float:
        return self.new_rate / self.old_rate if self.old_rate else float("inf")

    def __str__(self) -> str:
        return f"{self.name}: {self.old_count} -> {self.new_count} mentions"


@dataclass
class SectionDiff:
    """One section, compared across two filings."""

    key: str
    title: str
    weight: float
    changes: list[Change] = field(default_factory=list)
    old: Scores = field(default_factory=lambda: score(""))
    new: Scores = field(default_factory=lambda: score(""))
    new_topics: dict[str, int] = field(default_factory=dict)
    dropped_topics: dict[str, int] = field(default_factory=dict)
    #: Every topic present in each version, kept so that the filing-level view
    #: can be computed across sections rather than summed from per-section
    #: verdicts. Summing them is wrong: NVIDIA moving its AI discussion out of
    #: Business and into Risk Factors would otherwise report as one topic
    #: dropped and one gained, when nothing was dropped or gained at all.
    old_topic_set: dict[str, int] = field(default_factory=dict)
    new_topic_set: dict[str, int] = field(default_factory=dict)
    old_mentions: dict[str, int] = field(default_factory=dict)
    new_mentions: dict[str, int] = field(default_factory=dict)

    @property
    def edited(self) -> list[Change]:
        """The changes worth showing, biggest first."""
        out = [c for c in self.changes if c.kind is not Kind.UNCHANGED]
        return sorted(out, key=lambda c: c.churn, reverse=True)

    @property
    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(("added", "removed", "modified", "unchanged"), 0)
        for change in self.changes:
            out[change.kind.value] += 1
        return out

    @property
    def churn(self) -> int:
        """Words added or deleted anywhere in the section."""
        return sum(c.churn for c in self.changes)

    @property
    def churn_rate(self) -> float:
        """Churn as a share of the section, 0-1.

        The denominator is the larger of the two versions, so that gutting a
        section and doubling it both read as large, rather than one of them
        somehow exceeding 100%.
        """
        base = max(self.old.words, self.new.words)
        return min(self.churn / base, 1.0) if base else 0.0


@dataclass
class FilingDiff:
    """Everything we know about how one company's filing changed."""

    ticker: str
    company: str
    old: Filing
    new: Filing
    sections: dict[str, SectionDiff] = field(default_factory=dict)
    note: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.sections)

    @property
    def churn_rate(self) -> float:
        """Weighted average churn across the sections we could read."""
        total = sum(s.weight * max(s.old.words, s.new.words) for s in self.sections.values())
        if not total:
            return 0.0
        weighted = sum(
            s.weight * max(s.old.words, s.new.words) * s.churn_rate
            for s in self.sections.values()
        )
        return weighted / total

    @property
    def new_topics(self) -> dict[str, int]:
        """Topics named in the new filing that the old one never named.

        Scoped to risk factors where we have them, because that is the claim
        worth making. A company has always *mentioned* tariffs somewhere -- in
        the business description, in a cost discussion. The event is the year it
        moves the word into the section headed "things that could go wrong",
        and a union across the whole filing hides exactly that transition.
        """
        old, new = self._topic_sets()
        return _rank({k: v for k, v in new.items() if k not in old})

    @property
    def dropped_topics(self) -> dict[str, int]:
        old, new = self._topic_sets()
        return _rank({k: v for k, v in old.items() if k not in new})

    @property
    def topic_scope(self) -> str:
        """Which section the topic verdicts are about, for labelling the UI."""
        return "risk factors" if "risk_factors" in self.sections else "the filing"

    def _topic_sets(self) -> tuple[dict[str, int], dict[str, int]]:
        risk = self.sections.get("risk_factors")
        scope = [risk] if risk is not None else list(self.sections.values())
        old: dict[str, int] = {}
        new: dict[str, int] = {}
        for section in scope:
            for name, hits in section.old_topic_set.items():
                old[name] = old.get(name, 0) + hits
            for name, hits in section.new_topic_set.items():
                new[name] = new.get(name, 0) + hits
        return old, new

    @property
    def intensified_topics(self) -> list[TopicShift]:
        """Topics the filing leans on much harder than last year.

        Rates, not counts, so a section that grew 30% longer does not appear to
        have discovered every topic in it. The thresholds exist to keep the list
        short and defensible: a topic has to be mentioned at least five times in
        the new filing, and at least 75% more often per thousand words, before
        it is worth a reader's attention.
        """
        risk = self.sections.get("risk_factors")
        scope = [risk] if risk is not None else list(self.sections.values())

        old_hits: dict[str, int] = {}
        new_hits: dict[str, int] = {}
        old_words = new_words = 0
        for section in scope:
            old_words += section.old.words
            new_words += section.new.words
            for name, hits in section.old_mentions.items():
                old_hits[name] = old_hits.get(name, 0) + hits
            for name, hits in section.new_mentions.items():
                new_hits[name] = new_hits.get(name, 0) + hits

        shifts: list[TopicShift] = []
        for name, hits in new_hits.items():
            if hits < 5 or not new_words:
                continue
            before = old_hits.get(name, 0)
            old_rate = before * PER / old_words if old_words else 0.0
            new_rate = hits * PER / new_words
            # A topic absent last year is already reported as new; this list is
            # about promotion, not arrival.
            if not before or new_rate < old_rate * 1.75:
                continue
            shifts.append(TopicShift(name, before, hits, old_rate, new_rate))

        return sorted(shifts, key=lambda s: s.factor, reverse=True)

    @property
    def hedging_delta(self) -> float:
        """Change in hedging rate, weighted by section size."""
        return self._delta("hedging")

    @property
    def tone_delta(self) -> float:
        return self._delta("tone")

    def _delta(self, attr: str) -> float:
        total = sum(s.new.words for s in self.sections.values())
        if not total:
            return 0.0
        return (
            sum(
                (getattr(s.new, attr) - getattr(s.old, attr)) * s.new.words
                for s in self.sections.values()
            )
            / total
        )

    @property
    def primary_churn(self) -> float:
        """Risk-factor churn where we have it, weighted churn otherwise.

        Risk factors are the section every claim in this tool is really about,
        and they move independently of the rest: a company can rewrite its whole
        MD&A because revenue moved and leave the risks untouched.
        """
        risk = self.sections.get("risk_factors")
        return risk.churn_rate if risk is not None else self.churn_rate

    @property
    def signal(self) -> float:
        """A single 0-100 ranking number for the dashboard.

        Deliberately simple, and deliberately explainable. Three things push it
        up, in descending order of how much they should:

        - **Churn**, weighted towards risk factors. Most of the score, because a
          rewritten risk factor is the thing you actually want to read.
        - **New topics**, capped. A company naming a risk it has never named
          before is a real event; naming four is not four times the event.
        - **Hedging**, as a nudge. Language getting vaguer is suggestive, never
          conclusive, and it should never outvote the text itself.

        There is no model here and no fitted weights, because there is no label
        to fit them against. This is a sort order, not a prediction, and
        dressing it up as the latter would be the dishonest part.

        The churn bounds are calibrated, though, against the year-on-year risk
        factor churn of 21 large caps: median 41%, quartiles at 28% and 48%.
        That distribution is the reason for the floor. Every company rewrites
        something every year, so scoring raw churn puts the whole market at the
        top of the scale and sorts nothing.
        """
        churn = min(max(self.primary_churn - FLOOR, 0.0) / (CEILING - FLOOR), 1.0) * 65
        novelty = min(len(self.new_topics) / 3, 1.0) * 25
        hedging = max(min(self.hedging_delta / 6.0, 1.0), 0.0) * 10
        return round(churn + novelty + hedging, 1)

    @property
    def headline(self) -> str:
        """One sentence a human can read in a list."""
        if not self.sections:
            return self.note or "nothing readable to compare"

        risk = self.sections.get("risk_factors")
        if risk is None:
            # No risk factors to talk about, so lead with whichever section
            # moved most rather than claiming nothing happened.
            risk = max(self.sections.values(), key=lambda s: s.churn_rate)
        if not risk.edited:
            return "No material change to the narrative sections"

        counts = risk.counts
        bits = []
        if counts["added"]:
            bits.append(f"{counts['added']} new")
        if counts["modified"]:
            bits.append(f"{counts['modified']} rewritten")
        if counts["removed"]:
            bits.append(f"{counts['removed']} dropped")
        lead = ", ".join(bits) if bits else "no paragraph changes"
        topic = next(iter(self.new_topics), None)
        if topic:
            tail = f"; first mention of {topic}"
        elif self.intensified_topics:
            shift = self.intensified_topics[0]
            tail = f"; {shift.name} up {shift.old_count} to {shift.new_count} mentions"
        else:
            tail = ""
        label = "Risk factors" if risk.key == "risk_factors" else risk.title
        return f"{label}: {lead}{tail}"


def _section_diff(key: str, old: Section, new: Section) -> SectionDiff:
    changes = align(old.blocks, new.blocks, section=key)

    old_topics = topics(old.text)
    new_topics_all = topics(new.text)

    # A topic only counts as new if it is genuinely absent before, not merely
    # mentioned less. "The first time this company has ever said tariffs" is a
    # story; "said tariffs nine times instead of eleven" is not.
    added_topics = {k: v for k, v in new_topics_all.items() if k not in old_topics}
    dropped = {k: v for k, v in old_topics.items() if k not in new_topics_all}

    for change in changes:
        if change.kind is not Kind.UNCHANGED:
            change.topics = list(topics(change.text))

    return SectionDiff(
        key=key,
        title=new.title,
        weight=new.weight,
        changes=changes,
        old=score(old.text),
        new=score(new.text),
        new_topics=added_topics,
        dropped_topics=dropped,
        old_topic_set=old_topics,
        new_topic_set=new_topics_all,
        old_mentions=topic_mentions(old.text),
        new_mentions=topic_mentions(new.text),
    )


def compare(client: EdgarClient, old: Filing, new: Filing) -> FilingDiff:
    """Diff two filings of the same company, fetching them if needed."""
    log.info("comparing %s -> %s", old, new)
    return compare_blocks(
        old,
        to_blocks(client.get(old.url).body),
        new,
        to_blocks(client.get(new.url).body),
    )


def compare_blocks(
    old: Filing,
    old_blocks: list[Block],
    new: Filing,
    new_blocks: list[Block],
) -> FilingDiff:
    """Diff two filings that have already been fetched and parsed.

    Split out from ``compare`` so the bundled samples exercise exactly the same
    code path as a live run, rather than a convenient imitation of it.
    """
    old_sections = find_sections(old_blocks, old.form)
    new_sections = find_sections(new_blocks, new.form)

    diff = FilingDiff(ticker=new.ticker, company=new.company, old=old, new=new)

    for key, new_section in new_sections.items():
        old_section = old_sections.get(key)
        if not new_section or old_section is None or not old_section:
            continue
        diff.sections[key] = _section_diff(key, old_section, new_section)

    if not diff.sections:
        statuses = {v.status for v in new_sections.values()}
        if "by_reference" in statuses:
            diff.note = (
                "sections are incorporated by reference into an exhibit, so the "
                "primary document has nothing to diff"
            )
        else:
            diff.note = "no comparable narrative sections found in both filings"

    return diff


def compare_ticker(
    client: EdgarClient,
    ticker: str,
    *,
    form: str = "10-K",
    back: int = 1,
    **kwargs,
) -> list[FilingDiff]:
    """Diff the last ``back + 1`` filings of ``ticker``, pairwise."""
    filings = list_filings(client, ticker, forms=(form,), limit=back + 1, **kwargs)
    if len(filings) < 2:
        raise ValueError(f"{ticker} has only {len(filings)} {form} on EDGAR; a diff needs two.")
    return [compare(client, old, new) for old, new in consecutive_pairs(filings)]
