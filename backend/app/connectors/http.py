"""Polite HTTP for live retailer fetches.

Explicit non-goals, stated here because they are architectural decisions and not
oversights: this client does **not** rotate user agents, does not solve or route
around CAPTCHAs, does not spoof browser fingerprints, and does not retry a refusal
in the hope of slipping through. A 403 is a considered answer from the retailer and
is treated as terminal - the connector degrades and falls back to fixtures.

What it does do is behave well: identify itself honestly, respect a rate limit and
`Retry-After`, back off exponentially with jitter on genuinely transient failures,
and give up quickly on anything that is not transient.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


class LiveFetchError(RuntimeError):
    """A live fetch failed. The caller falls back to fixtures."""


class LiveUnsupported(LiveFetchError):
    """No permitted live endpoint is configured for this retailer.

    Distinct from a failure: we are not blocked, we simply have no lawful,
    documented way in. Guessing at private endpoints is not attempted.
    """


class BlockedError(LiveFetchError):
    """The retailer refused us - WAF, bot protection, CAPTCHA, or rate limit.

    Terminal by design. Retrying a block is precisely the behaviour we refuse.
    """


class TransientError(LiveFetchError):
    """A timeout or 5xx that is worth retrying a bounded number of times."""


#: Response markers that mean "you are being challenged", not "here is the page".
_CHALLENGE_MARKERS = re.compile(
    r"(captcha|are you a human|access denied|request unsuccessful|"
    r"bot detection|cf-browser-verification|px-captcha|incapsula)",
    re.IGNORECASE,
)

BLOCKING_STATUSES = frozenset({401, 403, 405, 429, 451})
TRANSIENT_STATUSES = frozenset({408, 500, 502, 503, 504})


@dataclass
class RateLimiter:
    """Simple per-host minimum interval between requests."""

    requests_per_second: float = 0.5
    _last_call: float = field(default=0.0, repr=False)

    @property
    def min_interval(self) -> float:
        return 1.0 / self.requests_per_second if self.requests_per_second > 0 else 0.0

    def wait(self, sleep=time.sleep) -> float:
        elapsed = time.monotonic() - self._last_call
        delay = max(0.0, self.min_interval - elapsed)
        if delay:
            sleep(delay)
        self._last_call = time.monotonic()
        return delay


@dataclass
class HttpConfig:
    timeout_seconds: float = 15.0
    max_retries: int = 3
    requests_per_second: float = 0.5
    user_agent: str = "RetailScout/0.1 (+contact: you@example.com)"
    backoff_base: float = 0.5
    backoff_cap: float = 8.0


class PoliteClient:
    """A thin, rate-limited httpx wrapper with terminal-vs-transient classification."""

    def __init__(
        self,
        config: HttpConfig | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self.config = config or HttpConfig()
        self.limiter = RateLimiter(self.config.requests_per_second)
        self._sleep = sleep
        self._client = httpx.Client(
            timeout=self.config.timeout_seconds,
            transport=transport,
            headers={
                # Identifies us honestly. Never disguised as a consumer browser.
                "User-Agent": self.config.user_agent,
                "Accept": "application/json, text/html;q=0.9",
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            # The retailer told us how long to wait. Honour it exactly.
            return retry_after
        delay = min(self.config.backoff_cap, self.config.backoff_base * 2**attempt)
        return delay * (0.5 + random.random() / 2)  # jitter, to avoid lockstep retries

    def get(self, url: str, **kwargs) -> httpx.Response:
        last: LiveFetchError | None = None

        for attempt in range(self.config.max_retries):
            self.limiter.wait(self._sleep)
            try:
                response = self._client.get(url, **kwargs)
            except httpx.TimeoutException as exc:
                last = TransientError(f"timeout after {self.config.timeout_seconds}s")
                logger.warning("timeout fetching %s (attempt %d)", url, attempt + 1)
            except httpx.HTTPError as exc:
                last = TransientError(f"{type(exc).__name__}: {exc}")
            else:
                status = response.status_code

                if status in BLOCKING_STATUSES:
                    # Terminal. We do not retry, disguise, or work around this.
                    raise BlockedError(
                        f"{url} refused the request (HTTP {status}). "
                        "Treating as blocked; not retrying."
                    )

                if status < 400 and _CHALLENGE_MARKERS.search(response.text[:4000]):
                    # A 200 that is really a challenge page. Also terminal.
                    raise BlockedError(
                        f"{url} returned a bot challenge page (HTTP {status})."
                    )

                if status in TRANSIENT_STATUSES:
                    last = TransientError(f"HTTP {status} from {url}")
                elif status >= 400:
                    raise LiveFetchError(f"HTTP {status} from {url}")
                else:
                    return response

                retry_after = _parse_retry_after(response)
                if attempt + 1 < self.config.max_retries:
                    self._sleep(self._backoff(attempt, retry_after))
                continue

            if attempt + 1 < self.config.max_retries:
                self._sleep(self._backoff(attempt, None))

        raise last or TransientError(f"giving up on {url}")


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
