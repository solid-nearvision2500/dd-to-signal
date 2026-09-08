"""Rendering diffs as self-contained HTML.

Self-contained matters more than it sounds. The people who want this output
email it to each other, drop it in a shared folder and open it eighteen months
later. So: one file, no CDN, no build step, and the CSS inlined at render time.
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..diff.changes import Kind, Span
from ..pipeline import FilingDiff

TEMPLATES = Path(__file__).parent / "templates"
ASSETS = Path(__file__).parent / "assets"

#: How many changes to render per section by default. A heavily rewritten 10-K
#: can produce 200. A browser will render all of them, but nobody reads past
#: the biggest few dozen and the file stops being small enough to email.
DEFAULT_LIMIT = 40


def _redline(spans: list[Span]) -> str:
    """Word-diff spans -> ``<ins>``/``<del>`` markup."""
    out = []
    for span in spans:
        text = html.escape(span.text)
        if span.op == "insert":
            out.append(f"<ins>{text}</ins>")
        elif span.op == "delete":
            out.append(f"<del>{text}</del>")
        else:
            out.append(text)
    return " ".join(out)


def _thousands(value: int) -> str:
    return f"{value:,}"


def signal_class(diff: FilingDiff) -> str:
    """Colour band for a signal score."""
    if diff.signal >= 60:
        return "sig-high"
    if diff.signal >= 30:
        return "sig-mid"
    return "sig-low"


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["redline"] = _redline
    env.filters["thousands"] = _thousands
    env.filters["signal_class"] = signal_class
    return env


def _css() -> str:
    return (ASSETS / "report.css").read_text(encoding="utf-8")


def render_report(diff: FilingDiff, *, limit: int = DEFAULT_LIMIT) -> str:
    """One company, one year-on-year redline."""
    total_changed = sum(len(s.edited) for s in diff.sections.values())
    total_paragraphs = sum(len(s.changes) for s in diff.sections.values())

    return _environment().get_template("report.html.j2").render(
        diff=diff,
        css=_css(),
        limit=limit,
        signal_class=signal_class(diff),
        total_changed=total_changed,
        total_paragraphs=total_paragraphs,
    )


def render_dashboard(
    diffs: list[FilingDiff],
    *,
    form: str = "10-K",
    skipped: list[tuple[str, str]] | None = None,
    reports: dict[str, str] | None = None,
) -> str:
    """Many companies, ranked by how much their filing moved."""
    ordered = sorted(diffs, key=lambda d: d.signal, reverse=True)
    return _environment().get_template("dashboard.html.j2").render(
        diffs=ordered,
        css=_css(),
        form=form,
        skipped=skipped or [],
        reports=reports or {},
        generated=dt.date.today().isoformat(),
        top_class=signal_class(ordered[0]) if ordered else "sig-low",
    )


def write(path: Path, markup: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markup, encoding="utf-8")
    return path


__all__ = ["Kind", "render_dashboard", "render_report", "signal_class", "write"]
