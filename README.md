# dd-to-signal

[![CI](https://github.com/GeoCodeCrafter/dd-to-signal/actions/workflows/ci.yml/badge.svg)](https://github.com/GeoCodeCrafter/dd-to-signal/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Every 10-K is a rewrite of last year's 10-K. **dd-to-signal** finds the edits.

![The CLI comparing Apple's 2025 10-K against its 2024 one: a signal score, per-section change counts, and the rewritten tariffs paragraph marked up word by word](docs/demo.gif)

## Install and run

```bash
pip install git+https://github.com/GeoCodeCrafter/dd-to-signal

dd-to-signal diff AAPL
```

That is the whole setup. The second command downloads Apple's two most recent
10-K filings from SEC EDGAR, lines up the paragraphs, and prints what changed.

More of the same:

```bash
dd-to-signal diff NVDA --back 4                 # four year-on-year pairs
dd-to-signal diff MSFT --form 10-Q              # quarterlies work too
dd-to-signal diff CIK0000034088                 # or address a filer by CIK
dd-to-signal scan AAPL NVDA GM F TGT -o out     # rank a basket, write HTML
```

## Yes, the data is live

Every command above hits [SEC EDGAR](https://www.sec.gov/edgar) directly and
reads whatever is filed right now. There is no vendor, no snapshot, no dataset
to keep updated, and no API key, because EDGAR is a free public service. A 10-K
filed this morning is available to `dd-to-signal diff` this afternoon.

Two details worth knowing, since caching is where tools like this usually go
quietly stale:

* **Filing documents are cached forever.** A filing never changes once it is
  filed, so re-downloading one is wasted bandwidth for you and wasted load on
  the SEC. The second run over the same pair of filings does no network I/O.
* **The filing index expires after an hour.** The list of what a company has
  filed obviously does change. Caching that with the same permanence would mean
  the tool kept reading a snapshot of the day you installed it and never noticed
  a new annual report, which is the failure mode you would not spot until it had
  already cost you something.

`dd-to-signal cache` shows what is on disk. `--offline` works from the cache
alone, which is useful on a plane and useful for testing.

There is also a bundled demo that needs no network at all, using six real
filings shipped inside the package. It exists so the test suite has something
honest to run against, and so you can see the output before deciding whether to
install anything:

```bash
dd-to-signal demo             # six companies, ranked
dd-to-signal demo AAPL        # one of them, in detail
```

## What it produces

`-o` writes a self-contained HTML report per company plus a ranked dashboard
across all of them. One file each, no CDN and no build step, so you can email
one to somebody and still open it in two years.

![The dashboard ranking six companies by how much their filing moved, then Apple's report: metric tiles, topic chips, and the risk factors filtered down to the paragraphs that are new this year](docs/report.gif)

## What it found

A `scan` across 21 large caps, comparing each company's two most recent 10-Ks.
Every number here is counted from the filings and reproducible with one command.

| | Company | What changed |
| --- | --- | --- |
| **68.8** | Starbucks | 150 risk paragraphs dropped, 74 new. The section was rebuilt, not edited |
| **46.9** | Eli Lilly | 54 of its risk factors rewritten |
| **42.3** | Apple | tariffs 2 → 13 mentions; AI 5 → 10 |
| **38.5** | General Motors | tariffs 3 → 19 mentions |
| **31.6** | AMD | tariffs 7 → 19 mentions |
| **26.7** | Moderna | 120 risk factors rewritten; tariffs 2 → 12 |
| **18.2** | Micron | climate and environment 13 → 27 mentions |
| **15.9** | Target | tariffs 6 → 18 mentions |
| **12.4** | Delta | first ever mention of artificial intelligence in its risk factors |
| **3.2** | Costco | 22 paragraphs touched, nothing else |

Tariffs are the obvious pattern, and it repeats in eleven of the twenty-one. In
most cases the language was already somewhere in the filing. What changed is
that it got promoted into the risk factors.

## How it works

Four steps. The interesting problems are in the first two.

**1. Find the section.** The naive version, searching for "Item 1A" and slicing
to "Item 1B", fails on almost every real filing, because the first "Item 1A" in
the document is the table of contents. Detecting a contents page is fiddlier
than it looks. Proximity alone flags Apple, which has nothing to report under
items 1B, 1C, 2, 3 and 5 and so stacks five real headings inside twenty blocks.
What works is shape plus redundancy: a contents page is an unbroken run of short
lines, which body prose always breaks, and its entries reappear further down.
Filers like Amazon and Walmart head the section "RISK FACTORS" with no item
number at all, so there is a title fallback for those.

**2. Line the paragraphs up.** A year-on-year filing diff is not a line diff.
Companies reorder risk factors, split one in two, and promote sub-points. Move a
paragraph from fourth to fortieth and `difflib` calls it a deletion plus an
unrelated addition, which is a lie in a tool whose only job is describing
change. So it is a matching problem, done cheapest-first: hash the identical
paragraphs, which is most of them, then TF-IDF cosine similarity on what is
left, paired greedily above a threshold. Anything still unpaired is a genuine
addition or deletion.

**3. Diff the words.** Ordinary sequence matching over tokens, except that each
span is cut out of the source string by offset instead of being rejoined from
its tokens. No set of spacing rules survives a 10-K, which is full of `U.S.`,
`$1.2 billion`, `("EU")` and `Section 232(b)`. Slicing the original cannot get
the spacing wrong, because it never takes the spacing apart.

**4. Score it.** Text churn weighted towards risk factors, plus risk topics that
are new this year, plus a small nudge for hedging language. The churn bounds are
calibrated against measured reality: across 21 large caps, year-on-year risk
factor churn has a median of 41%, with quartiles at 28% and 48%. The floor
matters. Every company rewrites something every year, so scoring raw churn puts
the entire market at the top of the scale and sorts nothing.

The score is a sort order, not a prediction. There are no fitted weights,
because there is no label to fit them against. It tells you which filing to read
first. It does not tell you what a stock will do, and anything that claims a
0-100 number derived from word counts predicts returns is selling something.

## What it cannot read

Some filings genuinely cannot be diffed, and the tool reports that instead of a
confident zero. Of 24 large caps tested, 21 have readable risk factors. The
other three:

* **JPMorgan** files a wrapper. Its Item 1A is a single sentence pointing at
  pages 9-31 of a document that is not this document. Reported as
  `by_reference`.
* **Intel** and **United Airlines** use heading structures the extractor does
  not recognise yet. Reported as `not_found`.

```bash
dd-to-signal sections JPM     # which sections it can see, and why not the rest
```

That command exists because "nothing changed" and "we could not read it" look
identical in a dashboard that only counts words. Conflating the two is how a
screening tool starts quietly lying to you.

Other limits: pre-2001 filings are raw SGML and get skipped; amendments
(`10-K/A`) are excluded by default, since diffing one against a full 10-K
reports that the company deleted its entire risk factors section; and a ticker
resolves to whichever entity currently holds it, so after a reorganisation the
history may sit under a predecessor's CIK that you can pass directly.

## Commands

| | |
| --- | --- |
| `diff TICKER` | compare a company's last two filings |
| `scan TICKER...` | rank a basket by how much moved |
| `sections TICKER` | what can be read from a filing, and why the rest cannot |
| `demo [TICKER]` | the bundled filings, offline |
| `cache` | show or clear the download cache |

Useful flags: `--form 10-Q`, `--back N` for more history, `-o DIR` for HTML,
`--open`, `--offline`, `--limit N` to cap the changes rendered per section.

**EDGAR etiquette.** SEC fair access asks for a User-Agent containing a contact
address and caps you at ten requests a second. This runs at six. Set your own
contact with `DDSIGNAL_USER_AGENT="Your Name you@example.com"`. Note the shape
carefully: `www.sec.gov` returns 403 for any User-Agent containing a URL, while
`data.sec.gov` accepts one happily. That difference costs everybody an afternoon
the first time.

## The word lists are yours to change

Hedging, sentiment and the risk topics live in plain text under
[`data/lexicons/`](data/lexicons/) rather than in the code, because the whole
project is a set of opinions about which words are worth counting and yours will
not match mine. A topic needs two distinct terms before it fires, or one marked
`!` as sufficient alone. Every 10-K ever written says "competition" once in
passing, and tagging on that makes a topic meaningless.

## Development

```bash
git clone https://github.com/GeoCodeCrafter/dd-to-signal
cd dd-to-signal
pip install -e ".[dev]"

pytest                            # 134 tests, no network needed
ruff check .

python scripts/build-samples.py   # refresh the bundled filings
npm install && npm run gif        # re-record the README GIFs
```

The tests run against the bundled filings, which are real 10-Ks, so they
exercise section location, alignment, scoring and rendering on documents written
by filing agents rather than by me. Both GIFs are generated by script from the
output of the real command, so if the tool breaks they cannot be recorded.

## Licence

MIT. Filing text belongs to the filers. This only diffs it.
