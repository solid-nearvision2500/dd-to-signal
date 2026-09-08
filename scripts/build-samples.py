#!/usr/bin/env python
"""Rebuild the bundled sample filings in data/sample.

Fetches each company's last two 10-Ks from EDGAR, parses them to blocks and
writes them out compressed. Blocks rather than raw HTML: the HTML is 1.5MB a
filing and 95% of it is inline XBRL that the parser throws away, so storing the
parser's output keeps the repo small while still exercising section location,
alignment and scoring on a real document.

    python scripts/build-samples.py AAPL SBUX GM DAL MU TGT
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ddsignal.edgar.client import EdgarClient  # noqa: E402
from ddsignal.edgar.filings import list_filings  # noqa: E402
from ddsignal.extract.html import to_blocks  # noqa: E402
from ddsignal.extract.sections import find_sections  # noqa: E402

DEFAULT = ("AAPL", "SBUX", "GM", "DAL", "MU", "TGT")
OUT = Path(__file__).resolve().parents[1] / "data" / "sample"


def main(tickers: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    client = EdgarClient()
    total = 0

    for ticker in tickers:
        filings = list_filings(client, ticker, forms=("10-K",), limit=2)
        if len(filings) < 2:
            print(f"  {ticker}: needs two 10-Ks, found {len(filings)}")
            continue

        for filing in filings:
            blocks = to_blocks(client.get(filing.url).body)
            sections = find_sections(blocks, filing.form)
            readable = [k for k, v in sections.items() if v]
            if "risk_factors" not in readable:
                print(f"  {ticker} {filing.filed}: risk factors unreadable, skipping")
                break

            payload = {
                "cik": filing.cik,
                "ticker": filing.ticker,
                "company": filing.company,
                "form": filing.form,
                "accession": filing.accession,
                "filed": filing.filed.isoformat(),
                "period": filing.period.isoformat() if filing.period else None,
                "document": filing.document,
                "source": filing.url,
                "blocks": [{"text": b.text, "kind": b.kind} for b in blocks],
            }

            path = OUT / f"{filing.ticker}-{filing.filed:%Y%m%d}.json.gz"
            # mtime=0 so rebuilding identical samples does not churn the repo.
            with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as handle:
                handle.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

            size = path.stat().st_size / 1024
            total += path.stat().st_size
            print(f"  wrote {path.name}  {size:6.0f} KB  {len(blocks)} blocks  {readable}")

    print(f"\n  {total / 1_048_576:.1f} MB total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or list(DEFAULT)))
