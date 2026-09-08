"""Turning a ticker into a list of filings you can actually download.

EDGAR's data model is three hops deep and none of the hops are guessable, so
this module exists to make ``AAPL`` -> ``[the last five 10-Ks, as URLs]`` a
single call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .client import EdgarClient, EdgarError

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

#: Annual and quarterly reports carry the narrative sections we diff. Everything
#: else on EDGAR is either numbers-only or a one-page event notice.
NARRATIVE_FORMS = ("10-K", "10-Q", "20-F")


@dataclass(frozen=True)
class Filing:
    """One filing, with enough on it to fetch and to sort."""

    cik: int
    ticker: str
    company: str
    form: str
    accession: str
    filed: date
    period: date | None
    document: str

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(
            cik=self.cik,
            accession=self.accession.replace("-", ""),
            document=self.document,
        )

    @property
    def slug(self) -> str:
        return f"{self.ticker}-{self.form.replace('/', '')}-{self.filed:%Y%m%d}"

    def __str__(self) -> str:
        return f"{self.ticker} {self.form} filed {self.filed:%Y-%m-%d}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class CompanyIndex:
    """The ticker -> CIK map, fetched once and kept."""

    def __init__(self, client: EdgarClient) -> None:
        self._client = client
        self._by_ticker: dict[str, tuple[int, str]] | None = None

    def _load(self) -> dict[str, tuple[int, str]]:
        if self._by_ticker is None:
            raw = self._client.json(TICKERS_URL)
            # The payload is a dict keyed by stringified row number, not a list.
            self._by_ticker = {
                row["ticker"].upper(): (int(row["cik_str"]), row["title"])
                for row in raw.values()
            }
        return self._by_ticker

    def resolve(self, ticker: str) -> tuple[int, str]:
        """``"aapl"`` -> ``(320193, "Apple Inc.")``.

        A bare number or ``CIK0000320193`` is taken as a CIK and passed straight
        through. That escape hatch earns its keep more often than you would
        think: EDGAR maps a ticker to whichever entity currently claims it, and
        after a reorganisation that can be a brand-new holding company with no
        filing history, while thirty years of 10-Ks sit under the old CIK.
        """
        key = ticker.strip().upper()
        digits = key.removeprefix("CIK").lstrip("0")
        if digits.isdigit():
            cik = int(digits)
            return cik, self._name_for(cik) or f"CIK {cik}"
        try:
            return self._load()[key]
        except KeyError:
            raise EdgarError(
                f"no company on EDGAR with ticker {key!r}. Note that EDGAR indexes the "
                "filer, so share classes and ADRs sometimes sit under a different symbol. "
                "You can pass a CIK instead, e.g. CIK0000320193."
            ) from None

    def _name_for(self, cik: int) -> str | None:
        for c, name in self._load().values():
            if c == cik:
                return name
        return None


def _flatten(block: dict) -> list[dict]:
    """EDGAR stores filings column-wise; turn that back into rows."""
    if not block:
        return []
    keys = list(block)
    length = len(block[keys[0]])
    return [{k: block[k][i] for k in keys} for i in range(length)]


def _rows(client: EdgarClient, payload: dict, *, deep: bool) -> list[dict]:
    """All filing rows for a company, following EDGAR's history shards if asked.

    ``filings.recent`` holds only the most recent thousand filings. For a company
    that issues a press release a week that is barely two years, so anyone
    asking for a decade of 10-Ks needs the older shards listed in
    ``filings.files``. They are a separate fetch each, hence the opt-in.
    """
    filings = payload.get("filings", {})
    rows = _flatten(filings.get("recent", {}))
    if deep:
        for shard in filings.get("files", []):
            name = shard.get("name")
            if name:
                rows += _flatten(client.json(f"https://data.sec.gov/submissions/{name}"))
    return rows


def list_filings(
    client: EdgarClient,
    ticker: str,
    *,
    forms: tuple[str, ...] = ("10-K",),
    limit: int = 5,
    index: CompanyIndex | None = None,
    before: date | None = None,
    include_amendments: bool = False,
    deep: bool = False,
) -> list[Filing]:
    """The most recent ``limit`` filings of ``forms`` for ``ticker``, newest first.

    Amendments are excluded by default. This is deliberate: a 10-K/A is usually
    a cover page plus the executive compensation tables, so diffing one against
    a full 10-K reports that the company deleted its entire risk factors
    section. Pass ``include_amendments=True`` if you want them anyway.
    """
    index = index or CompanyIndex(client)
    cik, company = index.resolve(ticker)
    payload = client.json(SUBMISSIONS_URL.format(cik=cik))

    wanted = tuple(f.upper() for f in forms)
    out: list[Filing] = []
    for row in _rows(client, payload, deep=deep):
        form = (row.get("form") or "").upper()
        # "10-KSB" must not match a request for "10-K"; "10-K/A" only does when
        # amendments were asked for.
        if form in wanted or include_amendments and any(form.startswith(f"{w}/") for w in wanted):
            pass
        else:
            continue
        document = row.get("primaryDocument") or ""
        if not document.lower().endswith((".htm", ".html")):
            # Pre-2001 filings are raw SGML text blobs. The section extractor is
            # built on markup, so skip them rather than silently mangling them.
            continue
        filed = _parse_date(row.get("filingDate"))
        if filed is None or (before and filed >= before):
            continue
        out.append(
            Filing(
                cik=cik,
                ticker=ticker.upper(),
                company=company,
                form=form,
                accession=row.get("accessionNumber", ""),
                filed=filed,
                period=_parse_date(row.get("reportDate")),
                document=document,
            )
        )

    out.sort(key=lambda f: f.filed, reverse=True)

    if not out:
        raise EdgarError(
            f"{company} (CIK {cik}) has no {'/'.join(wanted)} filings on EDGAR. "
            "If the company reorganised recently the history may sit under the "
            "predecessor's CIK, which you can pass directly."
        )
    return out[:limit]


def consecutive_pairs(filings: list[Filing]) -> list[tuple[Filing, Filing]]:
    """``[c, b, a]`` -> ``[(b, c), (a, b)]``: each filing paired with its predecessor.

    Ordered ``(older, newer)`` because every diff in this project reads forwards.
    """
    ordered = sorted(filings, key=lambda f: f.filed)
    return list(zip(ordered, ordered[1:], strict=False))


_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def is_accession(value: str) -> bool:
    return bool(_ACCESSION.match(value.strip()))
