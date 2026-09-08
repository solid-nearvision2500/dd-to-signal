"""A polite, cached HTTP client for SEC EDGAR.

EDGAR is free and needs no key, but it does have rules: every request must carry
a User-Agent that identifies you with a contact address, and you must stay under
ten requests a second. Break either and you get a 403 that looks like a bug in
your code for about twenty minutes before you read the fair-access notice.

So this module is the only place in the project that touches the network, and it
does three things: identifies itself, throttles itself, and caches everything to
disk. The cache is what makes the rest of the project pleasant to work on: a
second run over the same filings does no network I/O at all.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

#: The environment variable holding the caller's contact address.
#:
#: There is deliberately no default. SEC fair access asks every caller to
#: identify itself so the SEC can reach whoever is running the script. Baking one
#: address into the package would put every user of this tool behind a single
#: identity, which defeats the purpose and is unfair to whoever owns it.
#:
#: The accepted shape is narrower than the SEC documents. www.sec.gov rejects any
#: User-Agent with nothing email-shaped in it, and also rejects URLs, parentheses
#: and @users.noreply.github.com addresses. data.sec.gov is much more relaxed.
#: The form that satisfies both hosts is a plain "Name you@example.com".
USER_AGENT_ENV = "DDSIGNAL_USER_AGENT"

NO_USER_AGENT = """SEC EDGAR needs a contact address before it will serve you.

  PowerShell:  $env:DDSIGNAL_USER_AGENT = 'Your Name you@example.com'
  bash:        export DDSIGNAL_USER_AGENT='Your Name you@example.com'

Or pass --user-agent on the command line. This is the SEC's fair access rule
rather than ours, and it wants an address that can actually reach you.

No contact address is needed for 'dd-to-signal demo', which runs offline."""

#: How long a cached *index* stays usable. Filing documents are immutable and
#: cached forever; the submissions index and the ticker map are not, and an hour
#: is short enough that a filing published this morning is picked up this
#: afternoon without re-fetching the index on every single command.
INDEX_MAX_AGE = 3600.0

#: The SEC's published ceiling is ten requests a second. We sit at six, because
#: the ceiling is shared with every other tool running on your IP and being the
#: one that tips it over is not a good trade for 40% more throughput.
DEFAULT_RATE = 6.0


def default_cache_dir() -> Path:
    """Where downloaded filings live between runs.

    Honours ``DDSIGNAL_CACHE`` so CI and the test suite can point somewhere
    disposable without every call site having to thread a path through.
    """
    env = os.environ.get("DDSIGNAL_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ddsignal"
    return Path.home() / ".cache" / "ddsignal"


class RateLimiter:
    """Spaces calls at least ``1/rate`` seconds apart, across threads."""

    def __init__(self, rate: float = DEFAULT_RATE) -> None:
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if not self._interval:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_at - now
            self._next_at = max(now, self._next_at) + self._interval
        if sleep_for > 0:
            time.sleep(sleep_for)


class EdgarError(RuntimeError):
    """A request to EDGAR failed in a way retrying will not fix."""


@dataclass
class Fetch:
    """The result of a fetch, plus whether it came off disk."""

    body: bytes
    from_cache: bool

    @property
    def text(self) -> str:
        # EDGAR is inconsistent about encodings and about declaring them. Older
        # filings are latin-1 with the odd smart quote; newer ones are UTF-8.
        # Replacing undecodable bytes beats crashing on a 2009 10-K.
        return self.body.decode("utf-8", errors="replace")


class EdgarClient:
    """Fetches EDGAR URLs, once.

    >>> client = EdgarClient()                             # doctest: +SKIP
    >>> client.json("https://data.sec.gov/submissions/CIK0000320193.json")  # doctest: +SKIP
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        cache_dir: Path | None = None,
        rate: float = DEFAULT_RATE,
        offline: bool = False,
        session: requests.Session | None = None,
    ) -> None:
        self.user_agent = user_agent or os.environ.get(USER_AGENT_ENV) or ""
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.offline = offline
        self._limiter = RateLimiter(rate)
        self._session = session or requests.Session()
        self._session.headers.update({"Accept-Encoding": "gzip, deflate"})
        if self.user_agent:
            self._session.headers["User-Agent"] = self.user_agent

    # -- cache ---------------------------------------------------------------

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        # Two levels of fan-out; a full S&P 500 pull is ~20k files and one flat
        # directory of that size is miserable on every filesystem involved.
        return self.cache_dir / digest[:2] / digest[2:4] / f"{digest}.bin"

    def cached(self, url: str, *, max_age: float | None = None) -> bytes | None:
        """The cached body, if there is one and it is young enough."""
        path = self._cache_path(url)
        if not path.exists():
            return None
        if max_age is not None and time.time() - path.stat().st_mtime > max_age:
            return None
        return path.read_bytes()

    # -- fetching ------------------------------------------------------------

    def get(self, url: str, *, retries: int = 3, max_age: float | None = None) -> Fetch:
        """Return the body of ``url``, from disk if we have a usable copy.

        ``max_age`` is the point of this signature. A filing document never
        changes once filed, so it is cached forever. The indexes that say which
        filings exist change constantly, and caching those forever means the
        second run of this tool is reading a snapshot of the day you installed
        it: a company could file a new 10-K and the tool would never see it.
        Callers that read an index pass a max_age; callers that read a document
        do not. See INDEX_MAX_AGE.
        """
        hit = self.cached(url, max_age=max_age)
        if hit is not None:
            return Fetch(hit, from_cache=True)

        if self.offline:
            stale = self.cached(url)
            if stale is not None:
                log.debug("offline: using an expired copy of %s", url)
                return Fetch(stale, from_cache=True)
            raise EdgarError(
                f"offline, and {url} is not cached. Run without --offline to fetch it, "
                "or point --cache at a directory that has it."
            )

        try:
            body = self._get_uncached(url, retries=retries)
        except EdgarError:
            # An expired index still beats no answer when EDGAR is down. It is
            # only the freshness guarantee that is lost, so say so and go on.
            stale = self.cached(url)
            if stale is None:
                raise
            log.warning("EDGAR unreachable; falling back to an expired copy of %s", url)
            return Fetch(stale, from_cache=True)
        path = self._cache_path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename, so an interrupted run cannot leave a truncated file
        # that looks like a valid cache hit forever after.
        tmp = path.with_suffix(".part")
        tmp.write_bytes(body)
        tmp.replace(path)
        return Fetch(body, from_cache=False)

    def _get_uncached(self, url: str, *, retries: int) -> bytes:
        # Checked at the point of going out to the network rather than in
        # __init__, so the offline demo, the cache commands and the whole test
        # suite keep working without anyone setting a contact address.
        if not self.user_agent:
            raise EdgarError(NO_USER_AGENT)

        last: Exception | None = None
        for attempt in range(retries):
            self._limiter.wait()
            try:
                response = self._session.get(url, timeout=30)
            except requests.RequestException as exc:
                last = exc
                log.debug("edgar: %s failed (%s), retrying", url, exc)
            else:
                if response.status_code == 200:
                    return response.content
                if response.status_code == 404:
                    raise EdgarError(f"404 from EDGAR for {url}")
                if response.status_code == 403:
                    raise EdgarError(
                        f"403 from EDGAR for {url}. Your User-Agent is "
                        f"{self.user_agent!r}. www.sec.gov rejects any that has no "
                        "email-shaped contact in it, and also rejects URLs, parentheses "
                        "and @users.noreply.github.com addresses. Use the plain form: "
                        "Your Name you@example.com"
                    )
                # 429 and the 5xx family are worth another go.
                last = EdgarError(f"HTTP {response.status_code} from {url}")
                log.debug("edgar: %s returned %s, retrying", url, response.status_code)

            # Exponential, starting at a second. EDGAR's rate limiter forgives
            # quickly; its outages do not, and three tries is enough to tell them
            # apart without hanging a batch run for minutes.
            time.sleep(2**attempt)

        raise EdgarError(f"giving up on {url} after {retries} attempts") from last

    def json(self, url: str, *, max_age: float | None = None) -> dict:
        import json

        return json.loads(self.get(url, max_age=max_age).text)
