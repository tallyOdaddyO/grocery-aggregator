"""The polite HTTP client.

The most important assertions here are the ones about what the client refuses to
do: it must not retry a block, and it must not treat a challenge page as content.
"""
from __future__ import annotations

import httpx
import pytest

from app.connectors.http import (
    BlockedError, HttpConfig, LiveFetchError, PoliteClient, RateLimiter,
    TransientError,
)


def client(handler, **config) -> PoliteClient:
    slept: list[float] = []
    c = PoliteClient(
        config=HttpConfig(**{"requests_per_second": 0, "backoff_base": 0.0, **config}),
        transport=httpx.MockTransport(handler),
        sleep=slept.append,
    )
    c.slept = slept  # type: ignore[attr-defined]
    return c


class TestBlocking:
    @pytest.mark.parametrize("status", [401, 403, 405, 429, 451])
    def test_refusal_statuses_are_terminal(self, status):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(status)

        with client(handler) as c, pytest.raises(BlockedError):
            c.get("https://example.test/search")
        # Exactly one attempt: retrying a refusal is the behaviour we refuse.
        assert calls["n"] == 1

    def test_a_200_challenge_page_is_treated_as_a_block(self):
        def handler(request):
            return httpx.Response(200, text="<html>Please complete the CAPTCHA</html>")

        with client(handler) as c, pytest.raises(BlockedError):
            c.get("https://example.test/search")

    @pytest.mark.parametrize(
        "marker",
        ["Access Denied", "Bot detection triggered", "cf-browser-verification", "Incapsula"],
    )
    def test_common_waf_signatures_are_recognised(self, marker):
        def handler(request):
            return httpx.Response(200, text=f"<html>{marker}</html>")

        with client(handler) as c, pytest.raises(BlockedError):
            c.get("https://example.test/search")

    def test_block_message_names_the_status(self):
        with client(lambda r: httpx.Response(403)) as c:
            with pytest.raises(BlockedError, match="403"):
                c.get("https://example.test/x")


class TestTransientRetry:
    def test_retries_a_500_then_succeeds(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        with client(handler, max_retries=3) as c:
            assert c.get("https://example.test/x").json() == {"ok": True}
        assert calls["n"] == 3

    def test_gives_up_after_max_retries(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(502)

        with client(handler, max_retries=3) as c, pytest.raises(TransientError):
            c.get("https://example.test/x")
        assert calls["n"] == 3

    def test_timeouts_are_transient(self):
        def handler(request):
            raise httpx.ConnectTimeout("too slow")

        with client(handler, max_retries=2) as c, pytest.raises(TransientError):
            c.get("https://example.test/x")

    def test_honours_retry_after(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, headers={"Retry-After": "7"})
            return httpx.Response(200, json={})

        c = client(handler, max_retries=3)
        with c:
            c.get("https://example.test/x")
        assert 7.0 in c.slept  # type: ignore[attr-defined]

    def test_backoff_grows_and_is_capped(self):
        c = PoliteClient(config=HttpConfig(backoff_base=1.0, backoff_cap=4.0))
        delays = [c._backoff(attempt, None) for attempt in range(6)]
        assert all(d <= 4.0 for d in delays)
        assert delays[3] >= delays[0]

    def test_a_404_is_not_retried(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(404)

        with client(handler) as c, pytest.raises(LiveFetchError):
            c.get("https://example.test/x")
        assert calls["n"] == 1


class TestPoliteness:
    def test_identifies_itself_honestly(self):
        seen = {}

        def handler(request):
            seen.update(request.headers)
            return httpx.Response(200, json={})

        with client(handler) as c:
            c.get("https://example.test/x")
        agent = seen["user-agent"]
        assert "RetailScout" in agent
        # Never disguised as a consumer browser.
        assert "Mozilla" not in agent and "Chrome" not in agent

    def test_rate_limiter_spaces_requests(self):
        slept: list[float] = []
        limiter = RateLimiter(requests_per_second=2.0)
        limiter.wait(slept.append)
        limiter.wait(slept.append)
        assert limiter.min_interval == 0.5
        assert slept and slept[-1] > 0
