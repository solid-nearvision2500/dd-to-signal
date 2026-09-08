"""Terminal rendering.

The HTML report is the deliverable, but the terminal output is what people see
first and what decides whether they keep going, so it gets the same care: a
readable summary, the numbers that justify the verdict, and one real excerpt of
changed text with the words marked up.
"""

from __future__ import annotations

import os
import sys

from ..diff.changes import Kind
from ..pipeline import FilingDiff

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
GREY = "\033[90m"
RED_BG = "\033[41m\033[97m"
GREEN_BG = "\033[42m\033[30m"


def supports_colour(stream=None) -> bool:
    """Whether to emit ANSI at all.

    Honours ``NO_COLOR`` (the de facto standard) and ``FORCE_COLOR``, and calls
    ``os.system("")`` on Windows, which is the documented incantation for
    getting a legacy console to turn on virtual terminal processing.
    """
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        if sys.platform == "win32":
            os.system("")
        return True
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if sys.platform == "win32":
        os.system("")
    return True


class Painter:
    """Applies colour, or does not."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return f"{''.join(codes)}{text}{RESET}"


def signal_colour(signal: float) -> str:
    if signal >= 60:
        return RED
    if signal >= 30:
        return YELLOW
    return GREY


def _wrap(text: str, width: int, indent: str) -> list[str]:
    """Wrap already-coloured text without counting escape codes as characters."""
    lines: list[str] = []
    line = ""
    visible = 0
    for word in text.split(" "):
        bare = _strip_ansi(word)
        if visible and visible + 1 + len(bare) > width:
            lines.append(indent + line)
            line, visible = word, len(bare)
        else:
            line = f"{line} {word}" if line else word
            visible += (1 if visible else 0) + len(bare)
    if line:
        lines.append(indent + line)
    return lines


def _strip_ansi(text: str) -> str:
    out = []
    skip = False
    for char in text:
        if char == "\033":
            skip = True
        elif skip:
            if char.isalpha():
                skip = False
        else:
            out.append(char)
    return "".join(out)


def render(diff: FilingDiff, *, colour: bool = True, excerpt: bool = True, width: int = 88) -> str:
    """A whole diff, as terminal text."""
    c = Painter(colour)
    out: list[str] = []
    add = out.append

    add("")
    add(f"  {c(diff.ticker, BOLD, CYAN)}  {c(diff.company, BOLD)}")
    add(
        f"  {c(f'{diff.old.form} {diff.old.filed}', GREY)} "
        f"{c('->', GREY)} {c(f'{diff.new.form} {diff.new.filed}', GREY)}"
    )
    add("")

    if not diff.sections:
        add(f"  {c('nothing to compare:', YELLOW)} {diff.note}")
        add("")
        return "\n".join(out)

    signal = c(f"{diff.signal:>5.1f}", BOLD, signal_colour(diff.signal))
    add(
        f"  signal {signal}   "
        f"churn {c(f'{diff.churn_rate:.0%}', BOLD)}   "
        f"hedging {c(f'{diff.hedging_delta:+.1f}', RED if diff.hedging_delta > 0 else GREEN)}   "
        f"tone {c(f'{diff.tone_delta:+.2f}', GREEN if diff.tone_delta > 0 else RED)}"
    )
    add("")
    for line in _wrap(c(diff.headline, BOLD), width - 4, "  "):
        add(line)
    add("")

    label = max((len(s.title) for s in diff.sections.values()), default=0)
    for section in diff.sections.values():
        counts = section.counts
        added = c(f"{counts['added']:>3} new", GREEN)
        rewritten = c(f"{counts['modified']:>3} rewritten", BLUE)
        dropped = c(f"{counts['removed']:>3} dropped", RED)
        churn = c(f"{section.churn_rate:>4.0%} churn", GREY)
        add(f"  {section.title:<{label}}  {added}  {rewritten}  {dropped}  {churn}")

    if diff.new_topics or diff.intensified_topics or diff.dropped_topics:
        add("")
        for name in diff.new_topics:
            add(f"  {c('+', GREEN)} {c(f'new to {diff.topic_scope}:', GREY)} {c(name, BOLD)}")
        for shift in diff.intensified_topics:
            add(
                f"  {c('^', YELLOW)} {c(shift.name, BOLD)} "
                f"{c(f'{shift.old_count} -> {shift.new_count} mentions', GREY)} "
                f"{c(f'({shift.factor:.1f}x)', YELLOW)}"
            )
        for name in diff.dropped_topics:
            add(f"  {c('-', RED)} {c(f'gone from {diff.topic_scope}:', GREY)} {name}")

    if excerpt:
        out.extend(_excerpt(diff, c, width))

    add("")
    return "\n".join(out)


def _excerpt(diff: FilingDiff, c: Painter, width: int) -> list[str]:
    """Show the single biggest change, marked up word by word."""
    risk = diff.sections.get("risk_factors") or next(iter(diff.sections.values()), None)
    if risk is None or not risk.edited:
        return []

    change = risk.edited[0]
    out = ["", f"  {c('biggest change in ' + risk.title, GREY)}"]

    if change.kind is Kind.MODIFIED:
        out.append(
            f"  {c('REWRITTEN', BOLD, BLUE)} "
            f"{c(f'+{change.words_added} -{change.words_removed} words', GREY)}"
        )
        painted = " ".join(
            c(span.text, GREEN_BG)
            if span.op == "insert"
            else c(span.text, RED_BG)
            if span.op == "delete"
            else c(span.text, DIM)
            for span in change.spans
        )
    else:
        tag = "NEW" if change.kind is Kind.ADDED else "DROPPED"
        colour = GREEN if change.kind is Kind.ADDED else RED
        out.append(f"  {c(tag, BOLD, colour)} {c(f'{change.churn} words', GREY)}")
        painted = c(change.text, colour)

    out.extend(_wrap(painted, width - 4, "  "))
    return out


def render_scan(diffs: list[FilingDiff], *, colour: bool = True) -> str:
    """A ranked table of many diffs."""
    c = Painter(colour)
    ordered = sorted(diffs, key=lambda d: d.signal, reverse=True)
    width = max((len(d.ticker) for d in ordered), default=6)

    out = ["", f"  {c('SIGNAL  TICKER  WHAT CHANGED', BOLD, GREY)}", ""]
    for diff in ordered:
        signal = c(f"{diff.signal:>6.1f}", BOLD, signal_colour(diff.signal))
        out.append(f"  {signal}  {c(diff.ticker.ljust(width), CYAN)}  {diff.headline}")
    out.append("")
    return "\n".join(out)
