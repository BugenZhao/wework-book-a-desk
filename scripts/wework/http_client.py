from __future__ import annotations

import http.cookiejar as cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request

from .constants import MEMBER_BASE
from .utils import clip, user_uuid_from_jwt


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self) -> None:
        self.jar = cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            NoRedirect,
        )
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Request-Source": "com.wework.ondemand/WorkplaceOne/Prod/iOS/2.71.0(26.1)",
            "WeWorkMemberType": "2",
            "Origin": MEMBER_BASE,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
            "fe-pg": "/workplaceone/content2/dashboard",
            "Referer": f"{MEMBER_BASE}/workplaceone/content2/dashboard",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def request(self, method: str, url: str, payload=None, extra_headers=None, ok_redirect=False):
        data = None
        headers = dict(self.headers)
        if extra_headers:
            headers.update(extra_headers)
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=30)
            body = resp.read()
            status = resp.status
            final_url = resp.url
            response_headers = resp.headers
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status = exc.code
            final_url = exc.url
            response_headers = exc.headers
            if not (ok_redirect and 300 <= status < 400):
                raise RuntimeError(f"HTTP {status}: {clip(body)}") from None

        if not (200 <= status < 300 or (ok_redirect and 300 <= status < 400)):
            raise RuntimeError(f"HTTP {status}: {clip(body)}")
        return status, response_headers, body, final_url

    def json(self, method: str, url: str, payload=None, extra_headers=None):
        _, _, body, _ = self.request(method, url, payload, extra_headers)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def submit_form(self, action_url: str, values: dict[str, str], referer: str):
        data = urllib.parse.urlencode(values).encode("utf-8")
        headers = dict(self.headers)
        headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": referer,
            }
        )
        parsed = urllib.parse.urlparse(action_url)
        if parsed.scheme and parsed.netloc:
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"

        req = urllib.request.Request(action_url, data=data, headers=headers, method="POST")
        try:
            resp = self.opener.open(req, timeout=30)
            body = resp.read()
            return resp.status, resp.headers, body, resp.url
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if 300 <= exc.code < 400:
                return exc.code, exc.headers, body, exc.url
            raise RuntimeError(f"HTTP {exc.code}: {clip(body)}") from None

    def set_bearer(self, token: str) -> None:
        self.headers["Authorization"] = f"Bearer {token}"
        self.headers["WeWorkAuth"] = f"Bearer {token}"
        uuid = user_uuid_from_jwt(token)
        if uuid:
            self.headers["WeWorkUUID"] = uuid


def set_cookie(http: HTTP, name: str, value: str, domain: str) -> None:
    cookie = cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=False,
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )
    http.jar.set_cookie(cookie)
