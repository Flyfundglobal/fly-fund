"""Local social inbox + draft outbox. No posting, trading, or model decisions."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
NUMBER = re.compile(r"[0-9]{2,25}\Z")
MAX_RESPONSE = 4_000_000


class InputError(ValueError):
    pass


class ProviderError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    if not value:
        return None
    try:
        if isinstance(value, (float, int)):
            result = datetime.fromtimestamp(value, timezone.utc)
        else:
            try:
                result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        raise InputError("Invalid timestamp; include a timezone") from None


def post_url(value):
    if not isinstance(value, str):
        raise InputError("A public post URL is required")
    u = urllib.parse.urlsplit(value)
    if u.scheme != "https" or u.username or u.password or u.port:
        raise InputError("Use an HTTPS X or Binance Square post URL")
    if u.hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        match = re.fullmatch(r"/(?:[A-Za-z0-9_]{1,15}|i/web)/status/([0-9]{2,25})/?", u.path)
        if match:
            return "x", match[1]
    if u.hostname in {"binance.com", "www.binance.com"}:
        match = re.fullmatch(r"/(?:[a-z]{2}(?:-[A-Za-z]{2,4})?/)?square/post/([0-9]{2,25})/?", u.path)
        if match:
            return "square", match[1]
    raise InputError("Unsupported post URL")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an API key to a redirect target.
        return None


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "FlyFundSocial/0.1", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
            if response.status == 204:
                return {"code": 200, "results": [], "cursor": {}}
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE or response.status != 200:
                raise ProviderError("unexpected_response", "Provider returned an unexpected response")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError()
            return result
    except urllib.error.HTTPError as error:
        raise ProviderError(f"http_{error.code}", f"Provider HTTP {error.code}; no automatic retry") from None
    except (urllib.error.URLError, TimeoutError):
        raise ProviderError("network_error", "Provider connection failed; no automatic retry") from None
    except (ValueError, UnicodeError):
        raise ProviderError("invalid_json", "Provider did not return valid JSON") from None


def normalize_x(row, provider):
    if not isinstance(row, dict):
        raise ProviderError("schema_changed", "Expected a post object")
    post_id = str(row.get("id", ""))
    author = row.get("author") or {}
    if not isinstance(author, dict):
        raise ProviderError("schema_changed", "Invalid post author")
    handle = author.get("screen_name") or author.get("userName")
    text = row.get("text")
    if not NUMBER.fullmatch(post_id) or not isinstance(handle, str) or not HANDLE.fullmatch(handle) or not isinstance(text, str):
        raise ProviderError("schema_changed", "Post ID, author, or text missing")
    if len(text) > 100_000:
        raise ProviderError("oversized_post", "Post body exceeds the inbox limit")
    return {
        "id": f"x:{post_id}", "platform": "x", "post_id": post_id,
        "url": f"https://x.com/{handle}/status/{post_id}",
        "author": handle, "text": text,
        "created_at": timestamp(row.get("created_timestamp") or row.get("createdAt") or row.get("created_at")),
        "provider": provider, "provenance": "provider_fetched",
        "content_scope": "provider_returned_text",
        "content_trust": "untrusted_external_text", "input_kind": "observation",
        "paid_feed_weight": 0, "claim_verified": False,
    }


class XReader:
    def __init__(self, provider="fxtwitter", key=None, fetch=get_json):
        if provider not in {"fxtwitter", "twitterapi_io"}:
            raise InputError("X provider must be fxtwitter or twitterapi_io")
        self.provider, self.key, self.fetch = provider, key, fetch

    def request(self, fx_path, paid_path, params):
        if self.provider == "twitterapi_io":
            if not self.key:
                raise ProviderError("not_configured", "TWITTERAPI_IO_KEY is not configured")
            data = self.fetch("https://api.twitterapi.io" + paid_path + "?" + urllib.parse.urlencode(params[1]), {"X-API-Key": self.key})
            if data.get("status") not in {None, "success"} or "error" in data:
                raise ProviderError("upstream_error", "TwitterAPI.io reported an error")
        else:
            data = self.fetch("https://api.fxtwitter.com" + fx_path + "?" + urllib.parse.urlencode(params[0]))
            if data.get("code") != 200:
                raise ProviderError("upstream_error", "FxTwitter reported an error")
        return data

    def post(self, url):
        platform, post_id = post_url(url)
        if platform != "x":
            raise ProviderError("square_read_unavailable", "No verified Square reading API configured. Use manual import with the original URL and text.")
        data = self.request(f"/2/status/{post_id}", "/twitter/tweets", ({}, {"tweet_ids": post_id}))
        rows = [data.get("status")] if self.provider == "fxtwitter" else data.get("tweets", [])
        if not isinstance(rows, list) or len(rows) != 1:
            raise ProviderError("not_found", "Expected one matching post")
        post = normalize_x(rows[0], self.provider)
        if post["post_id"] != post_id:
            raise ProviderError("id_mismatch", "Provider returned a different post")
        return post

    def timeline(self, handle, cursor="", since=None):
        if not isinstance(handle, str) or not HANDLE.fullmatch(handle):
            raise InputError("Invalid X handle")
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise InputError("Invalid cursor")
        since = timestamp(since)
        data = self.request(f"/2/profile/{handle}/statuses", "/twitter/user/last_tweets", (
            {"count": 20, **({"cursor": cursor} if cursor else {})},
            {"userName": handle, "cursor": cursor, "includeReplies": "false"},
        ))
        rows = data.get("results") if self.provider == "fxtwitter" else data.get("tweets")
        if not isinstance(rows, list):
            raise ProviderError("schema_changed", "Timeline post list missing")
        posts, skipped = [], 0
        for row in rows:
            if isinstance(row, dict) and row.get("type") in {"tombstone", "thread"}:
                skipped += 1
                continue
            post = normalize_x(row, self.provider)
            # Profiles include reposts: retain the real author, never attribute all rows to the queried account.
            post["observed_on_profile"] = handle
            if since and (not post["created_at"] or post["created_at"] < since):
                skipped += 1
                continue
            posts.append(post)
        next_cursor = (data.get("cursor") or {}).get("bottom") if self.provider == "fxtwitter" else data.get("next_cursor")
        return {"posts": posts, "next_cursor": next_cursor or None, "skipped": skipped,
                "coverage": "one_page_only", "complete_daily_history": False,
                "note": "Explicitly request next_cursor for more. This is not a full daily crawl."}


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY, payload TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_post(self, post):
        stamp = now()
        post = {**post, "content_sha256": hashlib.sha256(post["text"].encode()).hexdigest()}
        with self.connect() as db:
            old = db.execute("SELECT payload FROM posts WHERE id=?", (post["id"],)).fetchone()
            if old and json.loads(old[0])["provenance"] == "provider_fetched" and post["provenance"] != "provider_fetched":
                raise InputError("Manual import cannot overwrite a provider-fetched post")
            db.execute("INSERT INTO posts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen", (post["id"], json.dumps(post, ensure_ascii=False), stamp, stamp))
        return {**post, "new": old is None}

    def posts(self):
        with self.connect() as db:
            rows = db.execute("SELECT payload,first_seen,last_seen FROM posts ORDER BY first_seen DESC LIMIT 500").fetchall()
        return [{**json.loads(p), "first_seen": first, "last_seen": last} for p, first, last in rows]

    def import_post(self, args):
        platform, post_id = post_url(args.get("url"))
        text = args.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 100_000:
            raise InputError("Provide 1–100000 characters of copied post text")
        author = args.get("author", "unverified")
        if not isinstance(author, str) or len(author) > 150:
            raise InputError("Invalid author")
        scope = args.get("content_scope", "unknown")
        if scope not in {"unknown", "full_text", "excerpt", "paraphrase"}:
            raise InputError("Invalid content_scope")
        return self.save_post({"id": f"{platform}:{post_id}", "platform": platform, "post_id": post_id,
                              "url": args["url"].split("?")[0].split("#")[0], "text": text.strip(), "author": author,
                              "created_at": timestamp(args.get("created_at")), "provider": "manual_import",
                              "provenance": "manual_unverified", "content_trust": "untrusted_external_text",
                              "content_scope": scope,
                              "input_kind": "observation", "paid_feed_weight": 0, "claim_verified": False})

    def create_draft(self, args):
        platform, text, sources = args.get("platform"), args.get("text"), args.get("source_ids", [])
        if platform not in {"x", "square"} or not isinstance(text, str) or not 1 <= len(text.strip()) <= 10000:
            raise InputError("A platform (x/square) and 1–10000 characters of draft text are required")
        if not isinstance(sources, list) or len(sources) > 50 or not all(isinstance(s, str) for s in sources):
            raise InputError("Invalid source_ids")
        with self.connect() as db:
            source_snapshots = []
            for source_id in dict.fromkeys(sources):
                row = db.execute("SELECT payload FROM posts WHERE id=?", (source_id,)).fetchone()
                if not row:
                    raise InputError("Draft references an unknown source")
                post = json.loads(row[0])
                source_snapshots.append({k: post.get(k) for k in ["id", "url", "author", "text", "content_sha256", "provenance", "content_scope"]})
            draft = {"id": str(uuid.uuid4()), "platform": platform, "text": text.strip(), "state": "draft",
                     "published": False, "created_at": now(), "source_snapshots": source_snapshots,
                     "length_check": "not_platform_validated", "generation": "supplied_text"}
            # This is an exportable body only. No outbound POST capability exists in this service.
            draft["proposed_payload"] = {"bodyTextOnly": draft["text"]} if platform == "square" else {"text": draft["text"]}
            db.execute("INSERT INTO drafts VALUES (?,?,?)", (draft["id"], json.dumps(draft, ensure_ascii=False), draft["created_at"]))
        return draft

    def drafts(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM drafts ORDER BY created_at DESC LIMIT 100").fetchall()]


def handler(store, reader):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, value, code=200):
            raw = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def local_request(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return host in allowed and (not self.headers.get("Origin") or self.headers["Origin"] == "http://" + host)

        def do_GET(self):
            if not self.local_request():
                return self.respond({"error": "local_only"}, 403)
            if self.path == "/api/social/status":
                return self.respond({"x_provider": reader.provider, "x_configured": reader.provider == "fxtwitter" or bool(reader.key),
                                     "square_read": "manual_import_only", "square_post_api": "documented_not_connected",
                                     "publishing_enabled": False, "automatic_collection_enabled": False,
                                     "storage": "sqlite", "drafts": ["x", "square"]})
            if self.path == "/api/social/posts":
                return self.respond({"posts": store.posts(), "limit": 500})
            if self.path == "/api/social/drafts":
                return self.respond({"drafts": store.drafts(), "limit": 100})
            return self.respond({"error": "not_found"}, 404)

        def do_POST(self):
            if not self.local_request():
                return self.respond({"error": "local_only"}, 403)
            if self.headers.get_content_type() != "application/json":
                return self.respond({"error": "application/json required"}, 415)
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 450000:
                    raise InputError("Invalid request size")
                args = json.loads(self.rfile.read(size))
                if not isinstance(args, dict):
                    raise InputError("Expected a JSON object")
                if self.path == "/api/social/read":
                    return self.respond(store.save_post(reader.post(args.get("url"))))
                if self.path == "/api/social/collect":
                    result = reader.timeline(args.get("handle"), args.get("cursor", ""), args.get("since"))
                    result["posts"] = [store.save_post(p) for p in result["posts"]]
                    return self.respond(result)
                if self.path == "/api/social/import":
                    return self.respond(store.import_post(args))
                if self.path == "/api/social/drafts":
                    return self.respond(store.create_draft(args), 201)
                return self.respond({"error": "not_found"}, 404)
            except ProviderError as error:
                return self.respond({"error": error.code, "message": str(error)}, 503 if error.code.endswith("unavailable") or error.code == "not_configured" else 502)
            except (ValueError, TypeError, KeyError):
                return self.respond({"error": "invalid_input"}, 400)
            except sqlite3.Error:
                return self.respond({"error": "storage_unavailable"}, 503)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5188)
    parser.add_argument("--db", type=Path, default=ROOT / ".sites-runtime/social/inbox.sqlite3")
    parser.add_argument("--x-provider", default=os.environ.get("FLY_X_PROVIDER", "fxtwitter"), choices=["fxtwitter", "twitterapi_io"])
    args = parser.parse_args()
    reader = XReader(args.x_provider, os.environ.get("TWITTERAPI_IO_KEY"))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(Store(args.db), reader))
    print(f"FLY FUND social inbox: http://127.0.0.1:{args.port}/api/social/status (drafts only)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
