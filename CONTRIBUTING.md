# Contributing

## Getting set up

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

The test suite needs no network: it runs against the filings bundled in
`data/sample`, which are real 10-Ks.

## The most useful contribution

**A filing this cannot read.** Coverage of narrative sections is around 21 in 24
for large caps, and every failure is a filer doing something structurally
different rather than a bug in the abstract. If `dd-to-signal sections TICKER`
reports `not_found` for a section you can plainly see in the document, that is a
bug worth reporting — include the ticker, the filing date and what the heading
actually looks like.

Please do not "fix" these by loosening the matching until something is returned.
Returning the wrong 40,000 words is worse than returning nothing, which is why
the title fallback refuses any match spanning more than 45% of a document.

## Changing the lexicons

`data/lexicons/*.txt` are plain text and meant to be edited. A few rules keep
them honest:

- A topic needs two distinct terms to fire, or one marked `!`. Single common
  words tag every filing ever written and make the topic meaningless.
- All-caps terms are matched case-sensitively, so `AI` does not fire on Italian
  exhibit titles.
- Terms are matched on word boundaries; `may` must not match `maybe`.

## Style

- `ruff check .` passes.
- Comments explain *why*, especially where the obvious approach was tried and
  did not survive contact with a real filing. Most of the non-obvious code in
  this project exists because of a specific document, and naming that document
  in the comment is more useful than describing the code.
- New behaviour comes with a test. Prefer a test against a bundled filing over a
  synthetic fixture when the behaviour is about real-world messiness.
