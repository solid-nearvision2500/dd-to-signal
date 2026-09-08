"""The filings bundled with the repo, so the demo runs with no network at all.

A tool that needs an API key, a rate limit and a good connection before it will
draw anything gets cloned and abandoned. These fixtures are real filings, parsed
into blocks and stored compressed, so ``dd-to-signal demo`` works offline the
moment the package is installed -- and it runs the same comparison code that a
live fetch does, so a passing demo is evidence the real thing works.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from .edgar.filings import Filing
from .extract.html import Block
from .pipeline import FilingDiff, compare_blocks

_SEARCH_PATHS = (
    Path(__file__).resolve().parents[2] / "data" / "sample",
    Path(__file__).resolve().parent / "_data" / "sample",
)


def sample_dir() -> Path:
    for path in _SEARCH_PATHS:
        if path.is_dir():
            return path
    raise FileNotFoundError(
        "no bundled samples. Looked in: " + ", ".join(str(p) for p in _SEARCH_PATHS)
    )


def available() -> list[str]:
    """Tickers with a bundled year-on-year pair, in a stable order."""
    return sorted({path.name.split("-")[0] for path in sample_dir().glob("*.json.gz")})


def _load(path: Path) -> tuple[Filing, list[Block]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)

    import datetime as dt

    filing = Filing(
        cik=payload["cik"],
        ticker=payload["ticker"],
        company=payload["company"],
        form=payload["form"],
        accession=payload["accession"],
        filed=dt.date.fromisoformat(payload["filed"]),
        period=dt.date.fromisoformat(payload["period"]) if payload.get("period") else None,
        document=payload["document"],
    )
    blocks = [Block(b["text"], b["kind"]) for b in payload["blocks"]]
    return filing, blocks


def load(ticker: str) -> FilingDiff:
    """The bundled year-on-year diff for ``ticker``."""
    paths = sorted(sample_dir().glob(f"{ticker.upper()}-*.json.gz"))
    if len(paths) < 2:
        raise FileNotFoundError(
            f"no bundled pair for {ticker.upper()}. Available: {', '.join(available())}"
        )
    (old_filing, old_blocks), (new_filing, new_blocks) = _load(paths[-2]), _load(paths[-1])
    return compare_blocks(old_filing, old_blocks, new_filing, new_blocks)


def load_all() -> list[FilingDiff]:
    return [load(ticker) for ticker in available()]
