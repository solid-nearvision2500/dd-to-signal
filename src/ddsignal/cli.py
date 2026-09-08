"""The command line.

Three verbs, because there are three things people want:

    dd-to-signal diff AAPL              what changed in Apple's last 10-K
    dd-to-signal scan AAPL NVDA MSFT    rank a list by how much moved
    dd-to-signal sections AAPL          what we can and cannot read, and why

``sections`` looks like a debugging command and is, but it ships as a first
class verb on purpose. Anyone who runs this over a universe will hit a filer
whose sections cannot be found, and the difference between "nothing changed" and
"we could not read it" has to be one command away.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .edgar.client import EdgarClient, EdgarError, default_cache_dir
from .edgar.filings import list_filings
from .extract.html import to_blocks
from .extract.sections import find_sections
from .pipeline import FilingDiff, compare_ticker
from .report import html as html_report
from .report import text as text_report

log = logging.getLogger("ddsignal")


def _client(args: argparse.Namespace) -> EdgarClient:
    return EdgarClient(
        user_agent=args.user_agent,
        cache_dir=Path(args.cache) if args.cache else None,
        offline=args.offline,
    )


def _configure_output() -> None:
    """Make Windows consoles able to print the output we generate.

    A default Windows console is cp1252, and a 10-K is full of characters it
    cannot encode. Without this the tool dies on a UnicodeEncodeError halfway
    through printing a paragraph, which looks like a parsing bug and is not.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            # Exotic terminals and redirected pipes can refuse; the output is
            # still worth attempting rather than crashing over.
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def _emit(diff: FilingDiff, args: argparse.Namespace, colour: bool) -> Path | None:
    print(text_report.render(diff, colour=colour, excerpt=not args.no_excerpt))

    if not args.out:
        return None
    path = Path(args.out)
    if path.is_dir() or args.out.endswith(("/", "\\")):
        path = path / f"{diff.ticker}-{diff.new.filed:%Y%m%d}.html"
    html_report.write(path, html_report.render_report(diff, limit=args.limit))
    print(f"  report: {path}")
    return path


def cmd_diff(args: argparse.Namespace) -> int:
    client = _client(args)
    colour = args.colour if args.colour is not None else text_report.supports_colour()

    diffs = compare_ticker(
        client,
        args.ticker,
        form=args.form,
        back=args.back,
        include_amendments=args.amendments,
        deep=args.back > 3,
    )

    written: list[Path] = []
    for diff in diffs:
        path = _emit(diff, args, colour)
        if path:
            written.append(path)

    if args.open and written:
        webbrowser.open(written[0].resolve().as_uri())
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    client = _client(args)
    colour = args.colour if args.colour is not None else text_report.supports_colour()

    diffs: list[FilingDiff] = []
    skipped: list[tuple[str, str]] = []
    reports: dict[str, str] = {}

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for ticker in args.tickers:
        try:
            found = compare_ticker(client, ticker, form=args.form, back=1)
        except (EdgarError, ValueError) as exc:
            # One unreadable filer must never take down a scan of five hundred.
            log.warning("%s: %s", ticker, exc)
            skipped.append((ticker.upper(), str(exc)))
            continue

        diff = found[0]
        if not diff.ok:
            skipped.append((diff.ticker, diff.note))
            continue

        diffs.append(diff)
        print(f"  {diff.ticker:<8} signal {diff.signal:>5.1f}  {diff.headline}")

        if out_dir:
            name = f"{diff.ticker}-{diff.new.filed:%Y%m%d}.html"
            html_report.write(out_dir / name, html_report.render_report(diff, limit=args.limit))
            reports[diff.ticker] = name

    print(text_report.render_scan(diffs, colour=colour))
    for ticker, reason in skipped:
        print(f"  {ticker}: skipped, {reason}")

    if out_dir:
        index = out_dir / "index.html"
        html_report.write(
            index,
            html_report.render_dashboard(
                diffs, form=args.form, skipped=skipped, reports=reports
            ),
        )
        print(f"\n  dashboard: {index}")
        if args.open:
            webbrowser.open(index.resolve().as_uri())

    return 0 if diffs else 1


def cmd_sections(args: argparse.Namespace) -> int:
    """Report what the extractor can see in a filing, and why it cannot see the rest."""
    client = _client(args)
    filings = list_filings(
        client,
        args.ticker,
        forms=(args.form,),
        limit=args.back + 1,
        include_amendments=args.amendments,
    )

    explain = {
        "found": "",
        "by_reference": "incorporated by reference into an exhibit",
        "too_short": "present but too short to be the real section",
        "not_found": "no heading for this item in the primary document",
    }

    for filing in filings:
        print(f"\n  {filing}")
        print(f"  {filing.url}")
        sections = find_sections(to_blocks(client.get(filing.url).body), filing.form)
        for key, section in sections.items():
            mark = "ok " if section else "-- "
            detail = f"{section.words:>6,} words" if section else explain[section.status]
            print(f"    {mark}{key:<14}{detail}")
    print()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the bundled filings. No network, no key, no arguments."""
    from . import samples

    colour = args.colour if args.colour is not None else text_report.supports_colour()

    diffs = [samples.load(args.ticker)] if args.ticker else samples.load_all()

    out_dir = Path(args.out) if args.out else None
    reports: dict[str, str] = {}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for diff in diffs:
        if len(diffs) == 1:
            print(text_report.render(diff, colour=colour, excerpt=True))
        if out_dir:
            name = f"{diff.ticker}-{diff.new.filed:%Y%m%d}.html"
            html_report.write(out_dir / name, html_report.render_report(diff, limit=args.limit))
            reports[diff.ticker] = name

    if len(diffs) > 1:
        print(text_report.render_scan(diffs, colour=colour))

    if out_dir:
        index = out_dir / "index.html"
        html_report.write(
            index, html_report.render_dashboard(diffs, reports=reports)
        )
        print(f"  dashboard: {index}")
        if args.open:
            webbrowser.open(index.resolve().as_uri())
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    path = Path(args.cache) if args.cache else default_cache_dir()
    if not path.exists():
        print(f"  cache is empty ({path})")
        return 0
    files = list(path.rglob("*.bin"))
    size = sum(f.stat().st_size for f in files)
    print(f"  {path}\n  {len(files):,} documents, {size / 1_048_576:.1f} MB")
    if args.clear:
        for file in files:
            file.unlink()
        print("  cleared")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dd-to-signal",
        description="Diff SEC filings against the previous one and show what changed.",
    )
    parser.add_argument("--version", action="version", version=f"dd-to-signal {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--form", default="10-K", help="form type to compare (default: 10-K)")
    common.add_argument("--cache", help="directory for downloaded filings")
    common.add_argument(
        "--user-agent",
        help="SEC requires 'Name your@email'. A URL in here gets you a 403 from www.sec.gov.",
    )
    common.add_argument(
        "--offline", action="store_true", help="use only what is already cached"
    )
    common.add_argument(
        "--colour",
        "--color",
        dest="colour",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="force colour on or off (default: auto)",
    )
    common.add_argument("-v", "--verbose", action="store_true", help="log what it is doing")

    render = argparse.ArgumentParser(add_help=False)
    render.add_argument("-o", "--out", help="write an HTML report here")
    render.add_argument("--open", action="store_true", help="open the report when it is written")
    render.add_argument(
        "--limit",
        type=int,
        default=html_report.DEFAULT_LIMIT,
        help="most changes to render per section (default: %(default)s)",
    )

    subs = parser.add_subparsers(dest="command", required=True)

    diff = subs.add_parser(
        "diff", parents=[common, render], help="compare one company's last two filings"
    )
    diff.add_argument("ticker", help="ticker, or a CIK like CIK0000320193")
    diff.add_argument(
        "--back", type=int, default=1, help="how many year-on-year pairs (default: 1)"
    )
    diff.add_argument(
        "--amendments", action="store_true", help="include 10-K/A amendments in the history"
    )
    diff.add_argument(
        "--no-excerpt", action="store_true", help="skip the excerpt of the biggest change"
    )
    diff.set_defaults(func=cmd_diff, no_excerpt=False)

    scan = subs.add_parser("scan", parents=[common, render], help="rank many companies")
    scan.add_argument("tickers", nargs="+", help="tickers to compare")
    scan.set_defaults(func=cmd_scan, no_excerpt=True)

    sections = subs.add_parser(
        "sections", parents=[common], help="show which sections can be read from a filing"
    )
    sections.add_argument("ticker")
    sections.add_argument("--back", type=int, default=1)
    sections.add_argument("--amendments", action="store_true")
    sections.set_defaults(func=cmd_sections)

    demo = subs.add_parser(
        "demo",
        parents=[common, render],
        help="run the bundled sample filings, with no network",
    )
    demo.add_argument("ticker", nargs="?", help="one bundled ticker (default: all of them)")
    demo.set_defaults(func=cmd_demo, no_excerpt=False)

    cache = subs.add_parser("cache", parents=[common], help="show or clear the download cache")
    cache.add_argument("--clear", action="store_true", help="delete every cached document")
    cache.set_defaults(func=cmd_cache)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="  %(levelname).1s %(message)s",
    )

    try:
        return args.func(args)
    except EdgarError as exc:
        print(f"\n  error: {exc}\n", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
