from __future__ import annotations

import base64
import hashlib
import html.parser
import json
import secrets
import urllib.parse
from dataclasses import dataclass

from .constants import AUTH0_CLIENT, MEMBER_BASE
from .http_client import HTTP, set_cookie
from .utils import b64url, clip, code_from_location


@dataclass
class AuthConfig:
    client_id: str
    domain: str
    redirect_uri: str
    audience: str


class FormParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._current: dict | None = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag.lower() == "form":
            self._current = {"action": attr.get("action", ""), "inputs": {}}
            self.forms.append(self._current)
        elif tag.lower() == "input" and self._current is not None:
            name = attr.get("name")
            if name:
                self._current["inputs"][name] = attr.get("value", "")

    def handle_endtag(self, tag):
        if tag.lower() == "form":
            self._current = None


def authenticate(http: HTTP, username: str, password: str) -> str:
    cfg = auth0_config(http)
    verifier = b64url(secrets.token_bytes(32))
    code_challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = b64url(secrets.token_bytes(32))
    nonce = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    ticket = cross_origin_authenticate(http, cfg, username, password)
    code = authorize_with_ticket(http, cfg, ticket, state, nonce, verifier, code_challenge, username, password)
    tokens = exchange_code(http, cfg, code, verifier)
    return login_to_wework(http, cfg, tokens)


def auth0_config(http: HTTP) -> AuthConfig:
    query = urllib.parse.urlencode(
        {"companyId": "00000000-0000-0000-0000-000000000000", "domain": "members.wework.com"}
    )
    data = http.json("GET", f"{MEMBER_BASE}/workplaceone/api/auth0/config?{query}")
    set_cookie(http, "auth0.zE51Ep7FttlmtQV6ZEGyJKsY2jD1EtAu.is.authenticated", "true", "members.wework.com")
    set_cookie(http, "_legacy_auth0.zE51Ep7FttlmtQV6ZEGyJKsY2jD1EtAu.is.authenticated", "true", "members.wework.com")
    return AuthConfig(
        client_id=data["client_id"],
        domain=data["domain"],
        redirect_uri=data["redirect_uri"],
        audience=data["audience"],
    )


def cross_origin_authenticate(http: HTTP, cfg: AuthConfig, username: str, password: str) -> str:
    payload = {
        "client_id": cfg.client_id,
        "username": username,
        "password": password,
        "realm": "id-wework",
        "credential_type": "http://auth0.com/oauth/grant-type/password-realm",
    }
    status, _, body, _ = http.request(
        "POST",
        f"https://{cfg.domain}/co/authenticate",
        payload,
        {
            "Accept": "application/json",
            "Origin": MEMBER_BASE,
            "Referer": f"{MEMBER_BASE}/workplaceone/content2/login",
            "Auth0-Client": AUTH0_CLIENT,
        },
    )
    data = json.loads(body.decode("utf-8"))
    if status != 200 or not data.get("login_ticket"):
        raise RuntimeError(f"credential auth failed: {data}")
    return data["login_ticket"]


def authorize_with_ticket(
    http: HTTP,
    cfg: AuthConfig,
    ticket: str,
    state: str,
    nonce: str,
    verifier: str,
    code_challenge: str,
    username: str,
    password: str,
) -> str:
    cookie_payload = {
        "nonce": nonce,
        "code_verifier": verifier,
        "scope": "openid profile email offline_access",
        "audience": cfg.audience,
        "redirect_uri": cfg.redirect_uri,
        "state": state,
    }
    encoded_cookie = urllib.parse.quote(json.dumps(cookie_payload, separators=(",", ":")))
    set_cookie(http, f"_legacy_a0.spajs.txs.{cfg.client_id}", encoded_cookie, cfg.domain)
    set_cookie(http, f"a0.spajs.txs.{cfg.client_id}", encoded_cookie, cfg.domain)

    params = {
        "redirect_uri": cfg.redirect_uri,
        "client_id": cfg.client_id,
        "audience": cfg.audience,
        "scope": "openid profile email offline_access",
        "response_type": "code",
        "response_mode": "query",
        "nonce": nonce,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "auth0Client": AUTH0_CLIENT,
        "login_ticket": ticket,
    }
    next_url = f"https://{cfg.domain}/authorize?{urllib.parse.urlencode(params)}"
    status = 0
    headers = {}
    body = b""
    final_url = ""
    for _ in range(20):
        status, headers, body, final_url = http.request("GET", next_url, ok_redirect=True)
        while True:
            location = headers.get("Location")
            if location:
                code = code_from_location(location, state)
                if code:
                    return code
                next_url = location if location.startswith(("http://", "https://")) else f"https://{cfg.domain}{location}"
                break

            if status == 200:
                handled = handle_intermediate_page(http, body, final_url, username, password)
                if handled is None:
                    raise RuntimeError(
                        f"authorization stopped without Location header: HTTP {status} {final_url} {clip(body)}"
                    )
                status, headers, body, final_url = handled
                continue

            raise RuntimeError(f"authorization stopped: HTTP {status} {final_url} {clip(body)}")
    raise RuntimeError(f"authorization redirect limit exceeded: HTTP {status} {final_url} {clip(body)}")


def handle_intermediate_page(
    http: HTTP,
    body: bytes,
    page_url: str,
    username: str,
    password: str,
):
    parser = FormParser()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not parser.forms:
        return None

    selected = None
    selected_kind = ""
    for form in parser.forms:
        inputs = form["inputs"]
        if "password" in inputs:
            selected = form
            selected_kind = "password"
            break
        if "js-available" in inputs and selected is None:
            selected = form
            selected_kind = "detection"
        if "username" in inputs and selected is None:
            selected = form
            selected_kind = "identifier"

    if selected is None:
        return None

    values = dict(selected["inputs"])
    if selected_kind == "identifier":
        values["username"] = username
    elif selected_kind == "password":
        values["password"] = password
    elif selected_kind == "detection":
        if "js-available" in values:
            values["js-available"] = "true"
        if "webauthn-available" in values:
            values["webauthn-available"] = "false"
        if "webauthn-platform-available" in values:
            values["webauthn-platform-available"] = "false"
        if "is-brave" in values:
            values["is-brave"] = "false"
        if not values.get("action"):
            values["action"] = "default"

    action = selected.get("action") or page_url
    action_url = urllib.parse.urljoin(page_url, action)
    return http.submit_form(action_url, values, page_url)


def exchange_code(http: HTTP, cfg: AuthConfig, code: str, verifier: str) -> dict:
    return http.json(
        "POST",
        f"https://{cfg.domain}/oauth/token",
        {
            "client_id": cfg.client_id,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cfg.redirect_uri,
        },
    )


def login_to_wework(http: HTTP, cfg: AuthConfig, tokens: dict) -> str:
    payload = {
        "id_token": tokens.get("id_token"),
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": tokens.get("expires_in"),
        "scope": tokens.get("scope"),
        "token_type": tokens.get("token_type"),
        "client_id": cfg.client_id,
        "audience": cfg.audience,
    }
    data = http.json("POST", f"{MEMBER_BASE}/workplaceone/api/auth0/login-by-auth0-token", payload)
    token = data.get("a0token")
    if not token:
        raise RuntimeError("login response missing a0token")
    return token
