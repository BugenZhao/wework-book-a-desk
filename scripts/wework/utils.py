from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timezone


def iso_date_only(value) -> str:
    return value[:10] if isinstance(value, str) and len(value) >= 10 else ""


def strip_trailing_z(value: str) -> str:
    return value[:-1] if isinstance(value, str) and value.endswith("Z") else value


def format_day_for_mail(start: str, fallback_date: str) -> str:
    raw = start or fallback_date
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(fallback_date, "%Y-%m-%d")
    return dt.strftime("%A, %B %-d") if sys.platform != "win32" else dt.strftime("%A, %B %#d")


def parse_hhmm(value: str) -> time:
    value = value[:5]
    return datetime.strptime(value, "%H:%M").time()


def code_from_location(location: str, expected_state: str) -> str | None:
    parsed = urllib.parse.urlparse(location)
    query = urllib.parse.parse_qs(parsed.query)
    code = (query.get("code") or [""])[0]
    if not code:
        return None
    state = (query.get("state") or [""])[0]
    if expected_state and state != expected_state:
        raise RuntimeError("state mismatch")
    return code


def user_uuid_from_jwt(token: str) -> str:
    return jwt_claims(token).get("https://wework.com/user_uuid", "")


def jwt_claims(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        return json.loads(b64url_decode(parts[1]))
    except Exception:
        return {}


def format_expiry(exp) -> str:
    if not isinstance(exp, (int, float)):
        return ""
    return datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()


def token_expired(token: str, skew_seconds: int = 300) -> bool:
    exp = jwt_claims(token).get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return datetime.now(tz=timezone.utc).timestamp() >= exp - skew_seconds


def print_saved_token(account: str, token: str, source: str) -> None:
    claims = jwt_claims(token)
    uuid = claims.get("https://wework.com/user_uuid", "")
    exp = format_expiry(claims.get("exp"))
    suffix = f" user_uuid={uuid}" if uuid else ""
    if exp:
        suffix += f" expires={exp}"
    print(f"saved WeWork token from {source} to Keychain account '{account}'{suffix}")


def local_offset() -> str:
    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return "+00:00"
    total = int(offset.total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 60:02d}:{total % 60:02d}"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def clip(body: bytes, limit: int = 500) -> str:
    text = body.decode("utf-8", errors="replace")
    return text[:limit] + ("..." if len(text) > limit else "")


def fatal(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)
