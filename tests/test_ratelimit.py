import unittest

import requests

from execution.ratelimit import call_with_retry, request_json


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def request(self, *_args, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


class RateLimitTests(unittest.TestCase):
    def test_request_json_respects_retry_after_on_429(self):
        sleeps = []
        session = FakeSession(
            [
                FakeResponse(429, payload={"msg": "slow down"}, headers={"Retry-After": "3"}),
                FakeResponse(200, payload={"ok": True}),
            ]
        )

        payload = request_json(
            "https://example.test",
            session=session,
            sleep_func=sleeps.append,
        )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(session.calls, 2)
        self.assertEqual(sleeps, [3.0])

    def test_request_json_backs_off_on_418_without_looping(self):
        sleeps = []
        session = FakeSession(
            [
                FakeResponse(418, payload={"msg": "banned"}),
                FakeResponse(200, payload={"ok": True}),
            ]
        )

        payload = request_json(
            "https://example.test",
            session=session,
            sleep_func=sleeps.append,
        )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(session.calls, 2)
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0.0)

    def test_callable_retry_stops_after_success(self):
        sleeps = []

        class FakeRateLimitError(Exception):
            def __init__(self):
                super().__init__("429")
                self.status_code = 429
                self.response_headers = {"Retry-After": "1"}

        attempts = {"count": 0}

        def flaky():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise FakeRateLimitError()
            return "ok"

        result = call_with_retry(flaky, operation="test.flaky", sleep_func=sleeps.append)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(sleeps, [1.0])

    def test_callable_retry_on_ccxt_like_network_error(self):
        sleeps = []

        class NetworkError(Exception):
            pass

        attempts = {"count": 0}

        def flaky():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise NetworkError("binance GET https://api.binance.com/api/v3/account")
            return "ok"

        result = call_with_retry(flaky, operation="test.ccxt_network", sleep_func=sleeps.append)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 0.0)


if __name__ == "__main__":
    unittest.main()
