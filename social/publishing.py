"""Text publishers. One explicit draft submission; never automatically retry."""
from datetime import datetime, timezone
import json
import re
import urllib.error
import urllib.parse
import urllib.request

X_ENDPOINT = "https://api.twitterapi.io/twitter/create_tweet_v2"
SQUARE_ENDPOINT = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
REQUIRED = {
    "x": ("TWITTERAPI_IO_KEY", "FLY_X_LOGIN_COOKIES", "FLY_X_PROXY", "FLY_X_ACCOUNT"),
    "square": ("BINANCE_SQUARE_OPENAPI_KEY", "FLY_SQUARE_PROFILE_URL"),
}


class PublishError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def send_json(url, headers, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "User-Agent": "FlyFundSocial/0.2", **headers}, method="POST")
    try:
        response = urllib.request.build_opener(NoRedirect).open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError):
        # The request may already have reached the publisher. Do not retry it.
        return 0, None
    with response:
        raw = response.read(1_000_001)
        try:
            data = json.loads(raw) if len(raw) <= 1_000_000 else None
        except (ValueError, UnicodeError):
            data = None
        return response.code, data


class Publishers:
    def __init__(self, credentials=None, send=send_json):
        self.credentials = credentials or {}
        self.send = send

    def status(self):
        return {platform: {"configured": not self.missing(platform), "missing": self.missing(platform),
                           "provider": "twitterapi_io" if platform == "x" else "binance_square_official",
                           "live_verified": False}
                for platform in REQUIRED}

    def missing(self, platform):
        return [name for name in REQUIRED[platform] if not self.credentials.get(name)]

    def prepare(self, draft):
        platform = draft["platform"]
        if platform not in REQUIRED:
            raise PublishError("invalid_platform", "Unsupported publishing platform")
        missing = self.missing(platform)
        if missing:
            raise PublishError("not_configured", "Missing project configuration: " + ", ".join(missing))
        text = draft["text"]
        c = self.credentials
        if platform == "x":
            if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", c["FLY_X_ACCOUNT"]):
                raise PublishError("invalid_config", "FLY_X_ACCOUNT must be a handle without @")
            proxy = urllib.parse.urlsplit(c["FLY_X_PROXY"])
            if proxy.scheme not in {"http", "https", "socks5"} or not proxy.hostname:
                raise PublishError("invalid_config", "X provider requires a proxy URL")
            # Conservative short-post limit; no Premium/long-post assumption.
            if sum(1 if ord(char) < 128 else 2 for char in text) > 280:
                raise PublishError("text_too_long", "X basic publisher accepts at most 280 conservative weighted characters")
            return X_ENDPOINT, {"X-API-Key": c["TWITTERAPI_IO_KEY"]}, {
                "login_cookies": c["FLY_X_LOGIN_COOKIES"], "tweet_text": text, "proxy": c["FLY_X_PROXY"]}
        profile = urllib.parse.urlsplit(c["FLY_SQUARE_PROFILE_URL"])
        if profile.scheme != "https" or profile.netloc != "www.binance.com" or not re.fullmatch(r"/(?:[a-z]{2}(?:-[A-Za-z]{2,4})?/)?square/profile/[A-Za-z0-9_-]+/?", profile.path):
            raise PublishError("invalid_config", "Use the project Binance Square profile URL")
        return SQUARE_ENDPOINT, {"X-Square-OpenAPI-Key": c["BINANCE_SQUARE_OPENAPI_KEY"], "clienttype": "binanceSkill"}, {"bodyTextOnly": text}

    def submit(self, draft, prepared):
        http, data = self.send(*prepared)
        if not isinstance(data, dict):
            return {"state": "unknown", "published": None, "result_code": f"http_{http}_unconfirmed"}
        if draft["platform"] == "x":
            post_id = str(data.get("tweet_id", ""))
            success = data.get("status") == "success"
            rejected = data.get("status") == "error"
            link = f"https://x.com/i/web/status/{post_id}"
        else:
            details = data.get("data") if isinstance(data.get("data"), dict) else {}
            post_id = str(details.get("id", ""))
            success = data.get("code") == "000000"
            rejected = bool(re.fullmatch(r"[0-9]+", str(data.get("code", "")))) and not success
            link = f"https://www.binance.com/en/square/post/{post_id}"
        if http == 200 and success and re.fullmatch(r"[0-9]{2,25}", post_id):
            return {"state": "published", "published": True, "post_id": post_id, "post_url": link,
                    "published_at": datetime.now(timezone.utc).isoformat(), "verification": "provider_returned_post_id"}
        if http in {200, 400, 401, 403, 422, 429} and rejected:
            return {"state": "rejected", "published": False, "result_code": "provider_rejected"}
        # Includes Binance's HTTP 504 and success-without-ID: no invented receipt.
        return {"state": "unknown", "published": None, "result_code": f"http_{http}_unconfirmed"}

    def publish(self, store, draft_id):
        draft = store.draft(draft_id)
        if draft["state"] != "draft":
            return draft  # Published, rejected, in-flight and unknown drafts are never sent again.
        prepared = self.prepare(draft)
        account = self.credentials["FLY_X_ACCOUNT" if draft["platform"] == "x" else "FLY_SQUARE_PROFILE_URL"]
        draft, claimed = store.claim_publication(draft_id, account)
        if not claimed:
            return draft
        try:
            result = self.submit(draft, prepared)
        except Exception:
            # Suppress raw SDK/transport exceptions which may contain credentials.
            result = {"state": "unknown", "published": None, "result_code": "submission_unconfirmed"}
        return store.finish_publication(draft_id, result)
