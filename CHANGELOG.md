# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The filing index is no longer cached forever. Filing documents are immutable
  and still cached permanently, but the submissions index and the ticker map now
  expire after an hour. Without this, the second run of the tool read the list of
  filings captured on the day it was installed, so a newly filed 10-K would never
  be picked up.
- An expired index is used as a fallback when EDGAR is unreachable, with a
  warning, rather than failing the run outright.

## [0.1.0] - 2026-09-08

First release.

### Added

- `diff`, `scan`, `sections`, `demo` and `cache` commands.
- Section location for 10-K and 10-Q narrative items, with contents-page
  detection, a title-only heading fallback for filers that omit item numbers,
  and honest `by_reference` / `too_short` / `not_found` reporting for filings
  that cannot be read.
- Paragraph alignment that survives reordering, splitting and rewriting, so a
  risk factor moved down the document does not read as a deletion.
- Word-level redlines cut from the source text by offset, preserving the
  spacing of `U.S.`, `("EU")` and `Section 232(b)`.
- Hedging, sentiment and risk-topic scoring from editable plain-text lexicons,
  including case-sensitive matching for acronyms such as `AI`.
- Self-contained HTML reports and a ranked dashboard.
- Six real companies' 10-K pairs bundled, so the demo and the test suite run
  with no network.
