import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
import urllib.error
import urllib.request

from service import InputError, ProviderError, Store, XReader, handler, normalize_x, post_url


def fixture(post_id="123456", author="original_author"):
    return {"id": post_id, "type": "status", "text": "BNB example opinion; not a verified fact.",
            "created_at": "Tue Sep 15 04:15:05 +0000 2026", "author": {"screen_name": author}}


class SocialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "social.sqlite3")

    def test_only_supported_urls_can_reach_provider(self):
        for url in ["http://127.0.0.1/", "https://x.com.evil.test/a/status/123", "https://x.com@evil.test/a/status/123", "https://x.com:443/a/status/123", "https://www.binance.com/en/square/profile/cz"]:
            with self.subTest(url=url), self.assertRaises(InputError):
                post_url(url)
        self.assertEqual(post_url("https://www.binance.com/zh-CN/square/post/123456?x=y"), ("square", "123456"))

    def test_dedup_preserves_first_seen_and_paid_weight_zero(self):
        post = normalize_x(fixture(), "fxtwitter")
        self.assertTrue(self.store.save_post(post)["new"])
        first = self.store.posts()[0]["first_seen"]
        self.assertFalse(self.store.save_post(post)["new"])
        posts = Store(self.store.path).posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["first_seen"], first)
        self.assertEqual(posts[0]["paid_feed_weight"], 0)
        with self.assertRaises(InputError):
            self.store.import_post({"url": post["url"], "text": "spoof"})

    def test_timeline_preserves_repost_author_and_does_not_claim_full_day(self):
        def fetch(url):
            self.assertIn("/2/profile/cz_binance/statuses?", url)
            return {"code": 200, "results": [fixture()], "cursor": {"bottom": "next-page"}}
        result = XReader(fetch=fetch).timeline("cz_binance")
        self.assertEqual(result["posts"][0]["author"], "original_author")
        self.assertEqual(result["posts"][0]["observed_on_profile"], "cz_binance")
        self.assertFalse(result["complete_daily_history"])
        self.assertEqual(result["next_cursor"], "next-page")

    def test_since_filters_old_posts_and_missing_dates(self):
        def fetch(url):
            return {"code": 200, "results": [fixture()], "cursor": {}}
        self.assertEqual(XReader(fetch=fetch).timeline("cz_binance", since="2026-09-16T00:00:00Z")["posts"], [])

    def test_missing_key_never_makes_a_paid_request(self):
        def forbidden(*args):
            self.fail("No network request expected")
        with self.assertRaises(ProviderError) as context:
            XReader("twitterapi_io", fetch=forbidden).post("https://x.com/cz_binance/status/123456")
        self.assertEqual(context.exception.code, "not_configured")

    def test_provider_error_and_wrong_id_are_not_imported(self):
        for response in [{"code": 401}, {"code": 200, "status": fixture("999999")}]:
            with self.subTest(response=response), self.assertRaises(ProviderError):
                XReader(fetch=lambda *_: response).post("https://x.com/cz_binance/status/123456")
        self.assertEqual(self.store.posts(), [])

    def test_paid_adapter_matches_documented_request_and_shape(self):
        def fetch(url, headers):
            self.assertEqual(url, "https://api.twitterapi.io/twitter/tweets?tweet_ids=123456")
            self.assertEqual(headers, {"X-API-Key": "test-only"})
            row = fixture()
            row["author"] = {"userName": "original_author"}
            return {"status": "success", "tweets": [row]}
        self.assertEqual(XReader("twitterapi_io", "test-only", fetch).post("https://x.com/a/status/123456")["id"], "x:123456")

    def test_square_is_explicitly_manual_and_draft_keeps_source_snapshot(self):
        post = self.store.import_post({"url": "https://www.binance.com/en/square/post/123456", "text": "Original copied text"})
        self.assertEqual(post["provenance"], "manual_unverified")
        for platform in ["square", "x"]:
            draft = self.store.create_draft({"platform": platform, "text": "Draft for review", "source_ids": [post["id"]]})
            self.assertFalse(draft["published"])
            self.assertEqual(draft["state"], "draft")
            self.assertEqual(draft["source_snapshots"][0]["text"], post["text"])
        self.store.import_post({"url": post["url"], "text": "Changed text"})
        self.assertEqual(self.store.drafts()[0]["source_snapshots"][0]["text"], "Original copied text")
        with self.assertRaises(InputError):
            self.store.create_draft({"platform": "x", "text": "Draft", "source_ids": ["square:9999"]})

    def test_local_http_read_draft_and_unconfigured_publish(self):
        reader = XReader(fetch=lambda *_: {"code": 200, "status": fixture()})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.store, reader))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        def request(path, data=None, headers=None):
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(base + path, data=body, headers={"Content-Type": "application/json", **(headers or {})})
            with urllib.request.urlopen(req) as response:
                return json.load(response)
        self.assertFalse(request("/api/social/status")["publishing_enabled"])
        post = request("/api/social/read", {"url": "https://x.com/a/status/123456"})
        self.assertEqual(post["id"], "x:123456")
        draft = request("/api/social/drafts", {"platform": "x", "text": "Draft", "source_ids": [post["id"]]})
        self.assertEqual(draft["state"], "draft")
        with self.assertRaises(urllib.error.HTTPError) as context:
            request("/api/social/publish", {"draft_id": draft["id"]})
        self.assertEqual(context.exception.code, 503)
        self.assertEqual(self.store.draft(draft["id"])["state"], "draft")
        for path, expected, headers in [("/api/social/publish", 400, {}), ("/api/social/import", 403, {"Origin": "https://evil.test"})]:
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as context:
                request(path, {}, headers)
            self.assertEqual(context.exception.code, expected)


if __name__ == "__main__":
    unittest.main()
