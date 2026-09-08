"""Tests for the comparison pipeline, the report renderers and the CLI.

These run against the bundled sample filings, which are real 10-Ks. That makes
them slower than the unit tests above and much more useful: they exercise
section location, alignment, scoring and rendering on documents that were
written by filing agents rather than by me.
"""

from __future__ import annotations

import datetime as dt

import pytest

from ddsignal import samples
from ddsignal.cli import main
from ddsignal.diff.changes import Kind
from ddsignal.edgar.filings import Filing, consecutive_pairs, is_accession
from ddsignal.extract.html import Block
from ddsignal.pipeline import CEILING, FLOOR, compare_blocks
from ddsignal.report import html as html_report
from ddsignal.report import text as text_report


@pytest.fixture(scope="module")
def apple():
    return samples.load("AAPL")


@pytest.fixture(scope="module")
def every():
    return samples.load_all()


class TestSamples:
    def test_samples_are_bundled(self):
        assert len(samples.available()) >= 5

    def test_every_sample_compares(self, every):
        for diff in every:
            assert diff.ok, f"{diff.ticker} produced nothing"
            assert "risk_factors" in diff.sections

    def test_missing_sample_is_a_clear_error(self):
        with pytest.raises(FileNotFoundError, match="no bundled pair"):
            samples.load("NOTATICKER")


class TestFilingDiff:
    def test_metadata_survives(self, apple):
        assert apple.ticker == "AAPL"
        assert "Apple" in apple.company
        assert apple.old.filed < apple.new.filed

    def test_sections_have_changes(self, apple):
        risk = apple.sections["risk_factors"]
        assert risk.counts["unchanged"] > 0
        assert risk.counts["modified"] > 0

    def test_churn_rate_is_a_fraction(self, every):
        for diff in every:
            assert 0.0 <= diff.churn_rate <= 1.0
            for section in diff.sections.values():
                assert 0.0 <= section.churn_rate <= 1.0

    def test_signal_is_bounded(self, every):
        for diff in every:
            assert 0.0 <= diff.signal <= 100.0

    def test_signal_spreads_across_the_sample(self, every):
        # The calibration exists so that a scan sorts. If every company scores
        # the same the number is decoration, which is what the first cut of it
        # was: 21 large caps all landed within a point of 65.
        signals = sorted(d.signal for d in every)
        assert signals[-1] - signals[0] > 20

    def test_edited_changes_are_biggest_first(self, apple):
        churns = [c.churn for c in apple.sections["risk_factors"].edited]
        assert churns == sorted(churns, reverse=True)

    def test_unchanged_paragraphs_are_excluded_from_edited(self, apple):
        assert all(
            c.kind is not Kind.UNCHANGED for c in apple.sections["risk_factors"].edited
        )

    def test_headline_mentions_risk_factors(self, apple):
        assert "Risk factors" in apple.headline

    def test_apple_moved_on_tariffs(self, apple):
        # A real, checkable finding: Apple's FY2025 risk factors mention tariffs
        # far more than FY2024's did. If the topic machinery breaks, this fails.
        shifts = {s.name: s for s in apple.intensified_topics}
        assert "tariffs and trade" in shifts
        assert shifts["tariffs and trade"].new_count > shifts["tariffs and trade"].old_count

    def test_topic_verdicts_are_scoped_to_risk_factors(self, apple):
        assert apple.topic_scope == "risk factors"

    def test_a_topic_is_not_both_new_and_dropped(self, every):
        for diff in every:
            assert not set(diff.new_topics) & set(diff.dropped_topics)

    def test_changes_carry_their_topics(self, apple):
        edited = apple.sections["risk_factors"].edited
        assert any(c.topics for c in edited)


class TestSignalCalibration:
    def _diff(self, churn_rate: float):
        """A synthetic diff with a known churn rate, to test the score's shape."""

        class Fake:
            primary_churn = churn_rate
            new_topics: dict[str, int] = {}
            hedging_delta = 0.0
            signal = property(lambda self: None)

        from ddsignal.pipeline import FilingDiff

        return FilingDiff.signal.fget(Fake())

    def test_routine_churn_scores_nothing(self):
        assert self._diff(FLOOR) == 0.0

    def test_a_full_rewrite_maxes_the_churn_term(self):
        assert self._diff(CEILING) == pytest.approx(65.0)
        assert self._diff(1.0) == pytest.approx(65.0)

    def test_it_is_monotonic(self):
        assert self._diff(0.2) < self._diff(0.4) < self._diff(0.6)


class TestNothingToCompare:
    def _filing(self, day: int) -> Filing:
        return Filing(
            cik=1,
            ticker="TEST",
            company="Test Corp",
            form="10-K",
            accession="0000000000-00-000000",
            filed=dt.date(2025, 1, day),
            period=None,
            document="a.htm",
        )

    def test_empty_filings_report_a_reason(self):
        diff = compare_blocks(self._filing(1), [], self._filing(2), [])
        assert not diff.ok
        assert diff.note
        assert diff.signal == 0.0
        # The headline falls back to the reason, so a dashboard row never says
        # "no change" when the truth is "we could not read it".
        assert diff.headline == diff.note
        assert "no comparable narrative sections" in diff.headline

    def test_rendering_an_empty_diff_does_not_crash(self):
        diff = compare_blocks(self._filing(1), [], self._filing(2), [])
        assert "nothing to compare" in text_report.render(diff, colour=False)

    def test_by_reference_filings_say_so(self):
        blocks = [
            Block("Item 1A. Risk Factors", "heading"),
            Block("Refer to pages 9 through 31 of the 2025 Annual Report for the risks."),
            Block("Item 1B. Unresolved Staff Comments", "heading"),
        ]
        diff = compare_blocks(self._filing(1), blocks, self._filing(2), list(blocks))
        assert not diff.ok
        assert "reference" in diff.note


class TestHtmlReport:
    def test_report_is_a_whole_document(self, apple):
        markup = html_report.render_report(apple)
        assert markup.startswith("<!doctype html>")
        assert "</html>" in markup

    def test_css_is_inlined_so_the_file_stands_alone(self, apple):
        markup = html_report.render_report(apple)
        assert "<style>" in markup
        assert "http://" not in markup.split("</head>")[0]

    def test_redline_markup_is_present(self, apple):
        markup = html_report.render_report(apple)
        assert "<ins>" in markup and "<del>" in markup

    def test_filing_text_is_escaped(self):
        from ddsignal.diff.changes import Span
        from ddsignal.report.html import _redline

        markup = _redline([Span("insert", "<script>alert(1)</script>")])
        assert "<script>" not in markup
        assert "&lt;script&gt;" in markup

    def test_limit_caps_the_rendered_changes(self, apple):
        small = html_report.render_report(apple, limit=2)
        large = html_report.render_report(apple, limit=60)
        assert len(small) < len(large)

    def test_dashboard_ranks_by_signal(self, every):
        markup = html_report.render_dashboard(every)
        order = [d.ticker for d in sorted(every, key=lambda d: d.signal, reverse=True)]
        positions = [markup.index(f">\n        {t}") if False else markup.index(t) for t in order]
        assert positions == sorted(positions)

    def test_dashboard_lists_skipped_companies(self, every):
        markup = html_report.render_dashboard(every, skipped=[("JPM", "by reference")])
        assert "JPM" in markup and "by reference" in markup

    def test_empty_dashboard_renders(self):
        assert "Nothing to compare" in html_report.render_dashboard([])


class TestTextReport:
    def test_plain_output_has_no_escape_codes(self, apple):
        assert "\033" not in text_report.render(apple, colour=False)

    def test_colour_output_has_escape_codes(self, apple):
        assert "\033" in text_report.render(apple, colour=True)

    def test_summary_mentions_the_numbers(self, apple):
        out = text_report.render(apple, colour=False)
        assert "signal" in out and "churn" in out and apple.ticker in out

    def test_excerpt_can_be_switched_off(self, apple):
        assert len(text_report.render(apple, colour=False, excerpt=False)) < len(
            text_report.render(apple, colour=False, excerpt=True)
        )

    def test_scan_table_is_ordered(self, every):
        out = text_report.render_scan(every, colour=False)
        tickers = [d.ticker for d in sorted(every, key=lambda d: d.signal, reverse=True)]
        assert [line.split()[1] for line in out.splitlines() if line.strip()][1:] == tickers

    def test_wrapping_ignores_escape_codes(self):
        # A line of coloured text must wrap on visible width, not on the length
        # of the string including its escape sequences.
        painted = "\033[32mword\033[0m " * 20
        lines = text_report._wrap(painted, 40, "  ")
        assert all(len(text_report._strip_ansi(line)) <= 44 for line in lines)


class TestCli:
    def test_demo_runs_offline(self, capsys):
        assert main(["demo", "AAPL", "--no-colour"]) == 0
        assert "AAPL" in capsys.readouterr().out

    def test_demo_writes_html(self, tmp_path, capsys):
        assert main(["demo", "--no-colour", "-o", str(tmp_path)]) == 0
        assert (tmp_path / "index.html").exists()
        assert len(list(tmp_path.glob("*.html"))) > 1

    def test_offline_without_a_cache_fails_clearly(self, tmp_path, capsys):
        code = main(["diff", "AAPL", "--offline", "--cache", str(tmp_path)])
        assert code == 2
        assert "offline" in capsys.readouterr().err

    def test_version(self, capsys):
        with pytest.raises(SystemExit):
            main(["--version"])
        assert "dd-to-signal" in capsys.readouterr().out

    def test_no_command_is_an_error(self):
        with pytest.raises(SystemExit):
            main([])


class TestFilingHelpers:
    def _filing(self, day: int) -> Filing:
        return Filing(1, "T", "T Co", "10-K", "0000000000-00-000000", dt.date(2025, 1, day),
                      None, "a.htm")

    def test_pairs_are_oldest_first(self):
        filings = [self._filing(3), self._filing(1), self._filing(2)]
        pairs = consecutive_pairs(filings)
        assert [(p[0].filed.day, p[1].filed.day) for p in pairs] == [(1, 2), (2, 3)]

    def test_one_filing_makes_no_pairs(self):
        assert consecutive_pairs([self._filing(1)]) == []

    def test_url_is_built_without_dashes_in_the_accession(self):
        filing = Filing(320193, "AAPL", "Apple", "10-K", "0000320193-25-000123",
                        dt.date(2025, 1, 1), None, "aapl.htm")
        assert "000032019325000123" in filing.url
        assert filing.url.startswith("https://www.sec.gov/Archives/")

    def test_accession_format_is_validated(self):
        assert is_accession("0000320193-25-000123")
        assert not is_accession("nonsense")
