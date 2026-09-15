from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest

from publishing import Publishers, PublishError, X_ENDPOINT, SQUARE_ENDPOINT
from service import Store


CONFIG = {"TWITTERAPI_IO_KEY": "fake-key", "FLY_X_LOGIN_COOKIES": "fake-cookie", "FLY_X_PROXY": "http://test-proxy.invalid:8080",
          "FLY_X_ACCOUNT": "flyfund_test", "BINANCE_SQUARE_OPENAPI_KEY": "fake-square-key",
          "FLY_SQUARE_PROFILE_URL": "https://www.binance.com/en/square/profile/flyfund_test"}


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "social.sqlite3")

    def draft(self, platform="x", text="A test draft, never sent to any external service."):
        return self.store.create_draft({"platform": platform, "text": text})

    def test_both_real_endpoint_request_shapes_and_repeat_protection(self):
        for platform in ["x", "square"]:
            calls = []
            def send(url, headers, body):
                calls.append(url)
                if platform == "x":
                    self.assertEqual(url, X_ENDPOINT)
                    self.assertEqual(headers["X-API-Key"], CONFIG["TWITTERAPI_IO_KEY"])
                    self.assertEqual(body, {"tweet_text": draft["text"], "login_cookies": "fake-cookie", "proxy": CONFIG["FLY_X_PROXY"]})
                    return 200, {"status": "success", "tweet_id": "123456"}
                self.assertEqual(url, SQUARE_ENDPOINT)
                self.assertEqual(headers["clienttype"], "binanceSkill")
                self.assertEqual(body, {"bodyTextOnly": draft["text"]})
                return 200, {"code": "000000", "data": {"id": "654321"}}
            draft = self.draft(platform)
            publisher = Publishers(CONFIG, send)
            first = publisher.publish(self.store, draft["id"])
            self.assertTrue(first["published"])
            self.assertIn("post_url", first)
            second = publisher.publish(Store(self.store.path), draft["id"])
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)
            for secret in ["fake-key", "fake-cookie", "fake-square-key", "test-proxy.invalid"]:
                self.assertNotIn(secret, json.dumps(first))

    def test_timeouts_504_and_success_without_id_stay_unknown(self):
        for response in [(0, None), (504, None), (200, {"code": "000000", "data": {}}), (200, {"status": "success"})]:
            draft = self.draft("square")
            calls = []
            def send(*_):
                calls.append(1)
                return response
            publisher = Publishers(CONFIG, send)
            first = publisher.publish(self.store, draft["id"])
            self.assertEqual(first["state"], "unknown")
            self.assertIsNone(first["published"])
            publisher.publish(Store(self.store.path), draft["id"])
            self.assertEqual(len(calls), 1)

    def test_rejection_is_not_published_and_never_automatically_retried(self):
        draft = self.draft("square")
        result = Publishers(CONFIG, lambda *_: (200, {"code": "220003", "message": "API key missing"})).publish(self.store, draft["id"])
        self.assertEqual(result["state"], "rejected")
        self.assertFalse(result["published"])

    def test_unconfigured_and_overlong_text_do_not_claim_or_send(self):
        def forbidden(*_):
            self.fail("Must not send")
        for config, text, error in [({}, "hello", "not_configured"), (CONFIG, "中" * 141, "text_too_long")]:
            draft = self.draft(text=text)
            with self.assertRaises(PublishError) as context:
                Publishers(config, forbidden).publish(self.store, draft["id"])
            self.assertEqual(context.exception.code, error)
            self.assertEqual(self.store.draft(draft["id"])["state"], "draft")

    def test_concurrent_requests_only_submit_once(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def send(*_):
            calls.append(1)
            entered.set()
            release.wait(3)
            return 200, {"status": "success", "tweet_id": "123456"}
        draft = self.draft()
        publisher = Publishers(CONFIG, send)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(publisher.publish, self.store, draft["id"])
            self.assertTrue(entered.wait(2))
            second = executor.submit(publisher.publish, self.store, draft["id"])
            self.assertEqual(second.result(timeout=2)["state"], "publishing")
            release.set()
            self.assertTrue(first.result(timeout=2)["published"])
        self.assertEqual(len(calls), 1)

    def test_crash_after_claim_does_not_resubmit_on_restart(self):
        draft = self.draft()
        self.store.claim_publication(draft["id"], "flyfund_test")
        def forbidden(*_):
            self.fail("A request may already be in flight")
        result = Publishers(CONFIG, forbidden).publish(Store(self.store.path), draft["id"])
        self.assertEqual(result["state"], "publishing")


if __name__ == "__main__":
    unittest.main()
