"""Tests for the EDGAR client: caching, freshness, throttling and failure.

No network. A stub session stands in for requests, which is what lets these
assert the thing that actually matters -- how many times we go out to the SEC,
and when we refuse to trust what we already have.
"""

from __future__ import annotations

import os
import time

import pytest

from ddsignal.edgar.client import INDEX_MAX_AGE, EdgarClient, EdgarError, RateLimiter


class Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.content = body
        self.status_code = status


class StubSession:
    """Counts requests and serves whatever it is told to."""

    def __init__(self, body: bytes = b"payload", status: int = 200) -> None:
        self.body = body
        self.status = status
        self.calls: list[str] = []
        self.headers: dict[str, str] = {}
        self.fail = False

    def get(self, url: str, timeout: int = 0) -> Response:
        self.calls.append(url)
        if self.fail:
            import requests

            raise requests.RequestException("network down")
        return Response(self.body, self.status)


@pytest.fixture
def client(tmp_path):
    def build(session: StubSession, **kwargs) -> EdgarClient:
        return EdgarClient(cache_dir=tmp_path, rate=0, session=session, **kwargs)

    return build


URL = "https://data.sec.gov/submissions/CIK0000320193.json"


class TestCaching:
    def test_a_document_is_fetched_once(self, client):
        session = StubSession()
        edgar = client(session)
        edgar.get(URL)
        edgar.get(URL)
        assert len(session.calls) == 1

    def test_the_second_read_is_marked_as_cached(self, client):
        session = StubSession()
        edgar = client(session)
        assert edgar.get(URL).from_cache is False
        assert edgar.get(URL).from_cache is True

    def test_body_survives_the_round_trip(self, client):
        session = StubSession(b"the filing text")
        edgar = client(session)
        assert edgar.get(URL).text == "the filing text"
        assert edgar.get(URL).body == b"the filing text"


class TestFreshness:
    def test_an_index_is_refetched_once_it_expires(self, client, tmp_path):
        # The bug this is here for: caching the submissions index forever means
        # the tool never notices a filing published after your first run.
        session = StubSession()
        edgar = client(session)
        edgar.get(URL, max_age=INDEX_MAX_AGE)

        cached_file = next(tmp_path.rglob("*.bin"))
        old = time.time() - INDEX_MAX_AGE - 60
        os.utime(cached_file, (old, old))

        edgar.get(URL, max_age=INDEX_MAX_AGE)
        assert len(session.calls) == 2

    def test_a_fresh_index_is_not_refetched(self, client):
        session = StubSession()
        edgar = client(session)
        edgar.get(URL, max_age=INDEX_MAX_AGE)
        edgar.get(URL, max_age=INDEX_MAX_AGE)
        assert len(session.calls) == 1

    def test_documents_without_a_max_age_never_expire(self, client, tmp_path):
        # A filing does not change once it is filed, so re-fetching one is pure
        # waste and pure load on the SEC.
        session = StubSession()
        edgar = client(session)
        edgar.get(URL)

        cached_file = next(tmp_path.rglob("*.bin"))
        ancient = time.time() - 86400 * 3650
        os.utime(cached_file, (ancient, ancient))

        edgar.get(URL)
        assert len(session.calls) == 1


class TestFailure:
    def test_an_expired_copy_is_used_when_edgar_is_down(self, client, tmp_path, caplog):
        session = StubSession()
        edgar = client(session)
        edgar.get(URL, max_age=INDEX_MAX_AGE)

        cached_file = next(tmp_path.rglob("*.bin"))
        old = time.time() - INDEX_MAX_AGE - 60
        os.utime(cached_file, (old, old))

        session.fail = True
        result = edgar.get(URL, max_age=INDEX_MAX_AGE, retries=1)
        assert result.text == "payload"
        assert result.from_cache is True

    def test_failure_without_any_copy_raises(self, client):
        session = StubSession()
        session.fail = True
        with pytest.raises(EdgarError, match="giving up"):
            client(session).get(URL, retries=1)

    def test_a_403_explains_the_user_agent_rule(self, client):
        # The single most common way to be stuck with this API.
        session = StubSession(b"", status=403)
        with pytest.raises(EdgarError, match="User-Agent"):
            client(session).get(URL, retries=1)

    def test_a_404_is_not_retried(self, client):
        session = StubSession(b"", status=404)
        with pytest.raises(EdgarError, match="404"):
            client(session).get(URL)
        assert len(session.calls) == 1


class TestOffline:
    def test_offline_reads_the_cache(self, client):
        session = StubSession()
        client(session).get(URL)

        offline = client(StubSession(), offline=True)
        assert offline.get(URL).text == "payload"

    def test_offline_accepts_an_expired_index(self, client, tmp_path):
        session = StubSession()
        client(session).get(URL)

        cached_file = next(tmp_path.rglob("*.bin"))
        old = time.time() - INDEX_MAX_AGE * 100
        os.utime(cached_file, (old, old))

        offline = client(StubSession(), offline=True)
        assert offline.get(URL, max_age=INDEX_MAX_AGE).text == "payload"

    def test_offline_without_a_cache_says_so(self, client):
        with pytest.raises(EdgarError, match="offline"):
            client(StubSession(), offline=True).get("https://data.sec.gov/nothing.json")


class TestUserAgent:
    def test_the_default_carries_a_contact_and_no_url(self, client):
        # www.sec.gov returns 403 for any User-Agent containing a URL, while
        # data.sec.gov accepts one. The default has to satisfy both.
        session = StubSession()
        edgar = client(session)
        agent = session.headers["User-Agent"]
        assert "@" in agent
        assert "http" not in agent
        assert edgar.user_agent == agent

    def test_it_can_be_overridden(self, client):
        session = StubSession()
        client(session, user_agent="Someone someone@example.com")
        assert session.headers["User-Agent"] == "Someone someone@example.com"


class TestRateLimiter:
    def test_it_spaces_calls_out(self):
        limiter = RateLimiter(rate=50)
        start = time.monotonic()
        for _ in range(3):
            limiter.wait()
        assert time.monotonic() - start >= 0.03

    def test_a_rate_of_zero_does_not_block(self):
        limiter = RateLimiter(rate=0)
        start = time.monotonic()
        limiter.wait()
        assert time.monotonic() - start < 0.05
