#!/usr/bin/env python3
"""
Minimal WeWork desk booking script.

Reduced from the public dvcrn/wework-cli flow to:
auth -> location lookup -> spaces -> quote -> booking.

Usage:
  python3 wework_min.py auth password --username you@example.com
  python3 wework_min.py auth status
  python3 wework_min.py locations --city Singapore
  python3 wework_min.py availability --date 2026-05-26 --city Singapore --name "21 Collyer"
  python3 wework_min.py book --date 2026-05-26 --city Singapore --name "21 Collyer" --dry-run
  python3 wework_min.py bookings
  python3 wework_min.py cancel --booking-id 123456 --confirm
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html.parser
import http.cookiejar as cookiejar
import json
import os
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timezone
from getpass import getpass
from zoneinfo import ZoneInfo


MEMBER_BASE = "https://members.wework.com"
AUTH0_CLIENT = (
    "eyJuYW1lIjoiQGF1dGgwL2F1dGgwLWFuZ3VsYXIiLCJ2ZXJzaW9uIjoiMS4xMS4x"
    "LmN1c3RvbSIsImVudiI6eyJhbmd1bGFyL2NvcmUiOiIxMy4xLjEifX0="
)
KEYCHAIN_SERVICE = "codex-wework-skill"
DEFAULT_KEYCHAIN_ACCOUNT = "default"
TOKEN_SUFFIX = "token"
USERNAME_SUFFIX = "username"
PASSWORD_SUFFIX = "password"
UPCOMING_BOOKINGS_URL = (
    MEMBER_BASE
    + "/workplaceone/api/common-booking/get-app-upcoming-bookings"
    + "?isPastBooking=false&platFormType=1&startDate=&endDate="
)
CANCEL_BOOKING_URL = MEMBER_BASE + "/workplaceone/api/common-booking/cancel?isOnDemand=false&platFormType=1"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal WeWork desk booking helper")
    sub = parser.add_subparsers(dest="command", required=True)

    auth_parser = sub.add_parser("auth", help="manage persisted WeWork auth in macOS Keychain")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", required=True)

    auth_password = auth_sub.add_parser("password", help="save username/password and refresh token")
    auth_password.add_argument("--account", default=DEFAULT_KEYCHAIN_ACCOUNT, help="Keychain account label")
    auth_password.add_argument("--username", default=os.environ.get("WEWORK_USERNAME"))
    auth_password.add_argument("--password", default=os.environ.get("WEWORK_PASSWORD"))
    auth_password.add_argument(
        "--no-prompt",
        action="store_true",
        help="do not prompt for a missing password",
    )

    auth_token = auth_sub.add_parser("token", help="save an existing WeWork token")
    auth_token.add_argument("--account", default=DEFAULT_KEYCHAIN_ACCOUNT, help="Keychain account label")
    auth_token.add_argument("--token", default=os.environ.get("WEWORK_TOKEN"), help="existing WeWork a0token/JWT")

    auth_status = auth_sub.add_parser("status", help="show Keychain auth status")
    auth_status.add_argument("--account", default=DEFAULT_KEYCHAIN_ACCOUNT, help="Keychain account label")

    locations_parser = sub.add_parser("locations", help="list WeWork locations in a city")
    locations_parser.add_argument("--account", default=DEFAULT_KEYCHAIN_ACCOUNT, help="Keychain account label")
    locations_parser.add_argument("--token", default=os.environ.get("WEWORK_TOKEN"), help="override Keychain token")
    locations_parser.add_argument("--city", required=True)
    locations_parser.add_argument("--json", action="store_true", help="emit raw location JSON")

    availability_parser = sub.add_parser("availability", help="show desk availability for a location/date")
    availability_parser.add_argument("--account", default=DEFAULT_KEYCHAIN_ACCOUNT, help="Keychain account label")
    availability_parser.add_argument("--token", default=os.environ.get("WEWORK_TOKEN"), help="override Keychain token")
    availability_parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    availability_parser.add_argument("--location-uuid", default="")
    availability_parser.add_argument("--city", default="")
    availability_parser.add_argument("--name", default="")
    availability_parser.add_argument("--json", action="store_true", help="emit raw availability JSON")

    bookings_parser = sub.add_parser("bookings", help="list upcoming WeWork bookings")
    bookings_parser.add_argument("--account", default=DEFAULT_KEYCHAIN_ACCOUNT, help="Keychain account label")
    bookings_parser.add_argument("--token", default=os.environ.get("WEWORK_TOKEN"), help="override Keychain token")
    bookings_parser.add_argument("--json", action="store_true", help="emit raw booking JSON")

    book_parser = sub.add_parser("book", help="dry-run or create a WeWork desk booking")
    book_parser.add_argument("--account", default=DEFAULT_KEYCHAIN_ACCOUNT, help="Keychain account label")
    book_parser.add_argument("--token", default=os.environ.get("WEWORK_TOKEN"), help="override Keychain token")
    book_parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    book_parser.add_argument("--location-uuid", default="")
    book_parser.add_argument("--city", default="")
    book_parser.add_argument("--name", default="")
    book_parser.add_argument("--dry-run", action="store_true")

    cancel_parser = sub.add_parser("cancel", help="cancel an upcoming WeWork booking")
    cancel_parser.add_argument("--account", default=DEFAULT_KEYCHAIN_ACCOUNT, help="Keychain account label")
    cancel_parser.add_argument("--token", default=os.environ.get("WEWORK_TOKEN"), help="override Keychain token")
    cancel_parser.add_argument("--booking-id", default="", help="bookingId from `bookings`")
    cancel_parser.add_argument("--date", default="", help="YYYY-MM-DD; used when booking-id is omitted")
    cancel_parser.add_argument("--location-uuid", default="")
    cancel_parser.add_argument("--city", default="")
    cancel_parser.add_argument("--name", default="")
    cancel_parser.add_argument("--confirm", action="store_true", help="actually cancel; otherwise only preview")
    cancel_parser.add_argument("--json", action="store_true", help="emit JSON result")
    args = parser.parse_args()

    if args.command == "auth":
        return run_auth(args)
    if args.command == "locations":
        return run_locations(args)
    if args.command == "availability":
        return run_availability(args)
    if args.command == "bookings":
        return run_bookings(args)
    if args.command == "book":
        return run_book(args)
    if args.command == "cancel":
        return run_cancel(args)
    parser.error("unknown command")
    return 2


def run_auth(args) -> int:
    if args.auth_command == "password":
        return run_auth_password(args)
    if args.auth_command == "token":
        return run_auth_token(args)
    if args.auth_command == "status":
        return run_auth_status(args)
    fatal("unknown auth command")
    return 2


def run_auth_password(args) -> int:
    if not args.username:
        fatal("auth password requires --username or WEWORK_USERNAME")
    password = args.password
    if not password and not args.no_prompt:
        password = getpass("WeWork password: ")
    if not password:
        fatal("missing password: pass --password, set WEWORK_PASSWORD, or omit --no-prompt")

    save_username_password_to_keychain(args.account, args.username, password)
    http = HTTP()
    token = authenticate(http, args.username, password)
    save_token_to_keychain(args.account, token)
    print_saved_token(args.account, token, f"login for {args.username}")
    return 0


def run_auth_token(args) -> int:
    if not args.token:
        fatal("auth token requires --token or WEWORK_TOKEN")
    save_token_to_keychain(args.account, args.token)
    print_saved_token(args.account, args.token, "provided token")
    return 0


def run_auth_status(args) -> int:
    token = load_token_from_keychain(args.account)
    username = load_username_from_keychain(args.account)
    password_saved = bool(load_password_from_keychain(args.account))
    print(f"account: {args.account}")
    print(f"username saved: {'yes (' + username + ')' if username else 'no'}")
    print(f"password saved: {'yes' if password_saved else 'no'}")
    if token:
        claims = jwt_claims(token)
        exp = claims.get("exp")
        print(f"token saved: yes")
        print(f"token user_uuid: {claims.get('https://wework.com/user_uuid', '') or '-'}")
        print(f"token expires: {format_expiry(exp) or '-'}")
        print(f"token expired: {'yes' if token_expired(token) else 'no'}")
    else:
        print("token saved: no")
    return 0


def run_book(args) -> int:
    if not args.location_uuid and (not args.city or not args.name):
        fatal("provide --location-uuid or both --city and --name")

    try:
        date = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError as exc:
        fatal(f"invalid --date: {exc}")

    http = HTTP()
    token = args.token or get_usable_token(args.account)
    if not token:
        fatal(
            "missing token/credentials: run `wework_min.py auth password --username ...`, "
            "`wework_min.py auth token --token ...`, or pass --token / WEWORK_TOKEN"
        )
    http.set_bearer(token)

    location_uuid = args.location_uuid
    if not location_uuid:
        location_uuid = resolve_location(http, args.city, args.name)
        print(f"resolved location UUID: {location_uuid}")

    space = first_available_space(http, date, location_uuid)
    print(
        f"selected: {space['location']['name']} ({space['location']['uuid']}), "
        f"workspace {space['uuid']}"
    )

    quote = booking_quote(http, date, space)
    grand = quote.get("grandTotal") or {}
    print(
        "quote: "
        f"{grand.get('currency', '')} {float(grand.get('amount') or 0):.2f}, "
        f"creditRatio {float(grand.get('creditRatio') or 0):.2f}, "
        f"uuid {quote.get('uuid', '')}"
    )

    if args.dry_run:
        print("dry run complete; booking was not created")
        return 0

    booking = create_booking(http, date, space, quote)
    if booking.get("BookingStatus") != "BookingSuccess":
        fatal(
            "booking rejected: "
            + str(booking.get("BookingStatus"))
            + " "
            + "; ".join(booking.get("Errors") or [])
        )
    print(f"booking successful: reservation {booking.get('ReservationID', '')}")
    return 0


def run_locations(args) -> int:
    http = HTTP()
    token = args.token or get_usable_token(args.account)
    if not token:
        fatal(
            "missing token/credentials: run `wework_min.py auth password --username ...`, "
            "`wework_min.py auth token --token ...`, or pass --token / WEWORK_TOKEN"
        )
    http.set_bearer(token)

    locations = get_locations_by_city(http, args.city)
    if args.json:
        print(json.dumps(locations, indent=2, sort_keys=True))
        return 0

    if not locations:
        print(f"no locations found for {args.city!r}")
        return 0
    for loc in locations:
        address = loc.get("address") or {}
        address_parts = [
            address.get("line1"),
            address.get("line2"),
            address.get("city"),
            address.get("zip"),
            address.get("country"),
        ]
        address_text = ", ".join(str(p).strip() for p in address_parts if p)
        print(f"{loc.get('name', '')}")
        print(f"  uuid: {loc.get('uuid', '')}")
        if address_text:
            print(f"  address: {address_text}")
        if loc.get("timeZone"):
            print(f"  timezone: {loc.get('timeZone')}")
    return 0


def run_availability(args) -> int:
    if not args.location_uuid and not args.city:
        fatal("provide --location-uuid or --city")
    if args.name and not args.city:
        fatal("--name requires --city")
    try:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError as exc:
        fatal(f"invalid --date: {exc}")

    http = HTTP()
    token = args.token or get_usable_token(args.account)
    if not token:
        fatal(
            "missing token/credentials: run `wework_min.py auth password --username ...`, "
            "`wework_min.py auth token --token ...`, or pass --token / WEWORK_TOKEN"
        )
    http.set_bearer(token)

    locations = select_locations(http, args.city, args.name, args.location_uuid)
    results = [availability_for_location(http, target_date, loc) for loc in locations]

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0

    print(f"date: {target_date.isoformat()}")
    for row in results:
        loc = row["location"]
        spaces = row["spaces"]
        total_available = sum((s.get("available") or 0) for s in spaces)
        total_capacity = sum((s.get("capacity") or 0) for s in spaces)
        print(f"{loc.get('name', '')}")
        print(f"  uuid: {loc.get('uuid', '')}")
        if row.get("error"):
            print(f"  error: {row['error']}")
            continue
        print(f"  availability: {total_available} / {total_capacity}")
        if not spaces:
            print("  spaces: none")
        for s in spaces:
            hours = ""
            if s.get("open") or s.get("close"):
                hours = f", hours={s.get('open', '')}-{s.get('close', '')}"
            print(
                f"  - available={s.get('available', 0)}, capacity={s.get('capacity', 0)}, "
                f"credits={s.get('credits', 0)}{hours}"
            )
    return 0


def run_bookings(args) -> int:
    http = authenticated_http(args)
    bookings = [normalize_booking(b) for b in fetch_upcoming_bookings(http)]
    bookings = [b for b in bookings if b and not b.get("isCancelled")]

    if args.json:
        print(json.dumps(bookings, indent=2, sort_keys=True))
        return 0

    if not bookings:
        print("no upcoming bookings")
        return 0
    for b in bookings:
        print(f"{b['dateISO']}  #{b['bookingId']}  {b['locationName']}")
        if b.get("spaceName"):
            print(f"  desk: {b['spaceName']}")
        print(f"  location_uuid: {b['locationId']}")
        print(f"  time: {b.get('startDate', '')} -> {b.get('endDate', '')}")
        if b.get("reservationId"):
            print(f"  reservation_id: {b['reservationId']}")
    return 0


def run_cancel(args) -> int:
    if not args.booking_id and not args.date:
        fatal("cancel requires --booking-id or --date")
    if args.name and not args.city:
        fatal("--name requires --city")
    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            fatal(f"invalid --date: {exc}")

    http = authenticated_http(args)
    target_location_uuid = args.location_uuid
    if not target_location_uuid and args.city and args.name:
        target_location_uuid = resolve_location(http, args.city, args.name)

    bookings = [normalize_booking(b) for b in fetch_upcoming_bookings(http)]
    bookings = [b for b in bookings if b and not b.get("isCancelled")]
    matches = []
    for b in bookings:
        if args.booking_id and b.get("bookingId") != str(args.booking_id):
            continue
        if target_date and b.get("dateISO") != target_date:
            continue
        if target_location_uuid and b.get("locationId") != target_location_uuid:
            continue
        matches.append(b)

    if not matches:
        fatal("no matching upcoming booking found")
    if len(matches) > 1 and not args.booking_id:
        summary = "; ".join(f"{b['dateISO']} #{b['bookingId']} {b['locationName']}" for b in matches)
        fatal(f"multiple matching bookings; use --booking-id: {summary}")

    booking = matches[0]
    if args.json and not args.confirm:
        print(json.dumps({"wouldCancel": booking, "confirmed": False}, indent=2, sort_keys=True))
        return 0

    if not args.confirm:
        print("preview only; pass --confirm to cancel")
        print(f"{booking['dateISO']}  #{booking['bookingId']}  {booking['locationName']}")
        if booking.get("spaceName"):
            print(f"  desk: {booking['spaceName']}")
        print(f"  location_uuid: {booking['locationId']}")
        return 0

    result = cancel_booking(http, booking)
    if args.json:
        print(json.dumps({"cancelled": True, "booking": booking, "result": result}, indent=2, sort_keys=True))
    else:
        print(f"cancelled {booking['dateISO']} #{booking['bookingId']} at {booking['locationName']}")
    return 0


def authenticated_http(args) -> HTTP:
    http = HTTP()
    token = args.token or get_usable_token(args.account)
    if not token:
        fatal(
            "missing token/credentials: run `wework_min.py auth password --username ...`, "
            "`wework_min.py auth token --token ...`, or pass --token / WEWORK_TOKEN"
        )
    http.set_bearer(token)
    return http


def availability_for_location(http: HTTP, target_date, loc: dict) -> dict:
    spaces = get_available_spaces(http, target_date, loc["uuid"])
    return {"location": loc, "spaces": [summarize_space(s) for s in spaces]}


def fetch_upcoming_bookings(http: HTTP) -> list[dict]:
    data = http.json("GET", UPCOMING_BOOKINGS_URL)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("bookings", "Bookings", "data", "WeWorkBookings"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def normalize_booking(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    loc = raw.get("location") or raw.get("Location") or {}
    address = loc.get("address") or loc.get("Address") or {}
    start = raw.get("startDate") or raw.get("bookingDate") or raw.get("start_date") or ""
    end = raw.get("endDate") or raw.get("end_date") or ""
    date_iso = iso_date_only(start) or iso_date_only(raw.get("bookingDate")) or ""
    if not date_iso:
        return {}
    return {
        "bookingId": str(raw.get("bookingId") or raw.get("BookingId") or raw.get("id") or ""),
        "dateISO": date_iso,
        "locationId": loc.get("id") or loc.get("Id") or raw.get("locationId") or "",
        "locationName": loc.get("name") or loc.get("Name") or raw.get("locationName") or "",
        "locationAddress": address.get("line1") or address.get("Line1") or "",
        "locationCity": address.get("city") or address.get("City") or "",
        "locationCountry": address.get("country") or address.get("Country") or "",
        "locationType": loc.get("type") or raw.get("locationType") or 2,
        "spaceName": raw.get("spaceName") or raw.get("SpaceName") or "",
        "spaceId": raw.get("spaceId") or raw.get("SpaceId") or raw.get("spaceExternalReference") or "",
        "reservationId": raw.get("kubeBookingExternalReference")
        or raw.get("KubeBookingExternalReference")
        or "",
        "startDate": start,
        "endDate": end,
        "creditCost": raw.get("creditCost") or raw.get("CreditCost") or 0,
        "isCancelled": bool(raw.get("isCancelled") or raw.get("IsCancelled")),
        "raw": raw,
    }


def cancel_booking(http: HTTP, booking: dict) -> dict:
    start = booking.get("startDate") or f"{booking['dateISO']}T06:00:00Z"
    end = booking.get("endDate") or f"{booking['dateISO']}T23:59:00Z"
    day_formatted = format_day_for_mail(start, booking["dateISO"])
    body = {
        "bookingId": booking["bookingId"],
        "bookingLocationType": booking.get("locationType") or 2,
        "creditsUsed": booking.get("creditCost") or 0,
        "startTime": strip_trailing_z(start),
        "endTime": strip_trailing_z(end),
        "locationId": booking.get("locationId") or "",
        "reservableId": booking.get("spaceId") or "",
        "isBookingApprovalOn": False,
        "bookingType": 4,
        "spaceId": booking.get("spaceId") or "",
        "cancellationNote": "",
        "mailParams": {
            "workspaceType": 1,
            "dayFormatted": day_formatted,
            "startTimeFormatted": strip_trailing_z(start),
            "endTimeFormatted": strip_trailing_z(end),
            "floorAddress": "",
            "locationAddress": booking.get("locationAddress") or booking.get("locationName") or "",
            "locationCountry": booking.get("locationCountry") or "",
        },
        "reservationId": booking.get("reservationId") or "",
    }
    _, _, response_body, _ = http.request("POST", CANCEL_BOOKING_URL, body)
    text = response_body.decode("utf-8", errors="replace").strip()
    if text == "true":
        return {"ok": True}
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {"ok": True, "raw": text}
    if data is True:
        return {"ok": True}
    if data is False or data.get("success") is False or data.get("ok") is False:
        raise RuntimeError(f"cancel rejected: {data}")
    return {"ok": True, "data": data}


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


def resolve_location(http: HTTP, city: str, name: str) -> str:
    locations = select_locations(http, city, name, "")
    if len(locations) != 1:
        raise RuntimeError(f"expected one location, got {len(locations)}")
    return locations[0]["uuid"]


def select_locations(http: HTTP, city: str, name: str, location_uuid: str) -> list[dict]:
    if location_uuid:
        if city:
            locations = get_locations_by_city(http, city)
            matches = [loc for loc in locations if loc.get("uuid") == location_uuid]
            if matches:
                return matches
        return [{"uuid": location_uuid, "name": location_uuid}]

    locations = get_locations_by_city(http, city)
    if not name:
        return locations

    needle = name.lower()
    matches = [
        loc for loc in locations
        if needle in (loc.get("name") or "").lower()
    ]
    if not matches:
        raise RuntimeError(f"no location matching {name!r} in {city!r}")
    if len(matches) > 1:
        names = "; ".join(f"{m.get('name')} ({m.get('uuid')})" for m in matches)
        raise RuntimeError(f"multiple matches: {names}")
    return matches


def get_locations_by_city(http: HTTP, city: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {"isAuthenticated": "true", "city": city, "isOnDemandUser": "false", "isWeb": "true"}
    )
    data = http.json(
        "GET",
        f"{MEMBER_BASE}/workplaceone/api/wework-yardi/ondemand/get-locations-by-geo?{query}",
    )
    locations = data.get("locationsByGeo", []) if isinstance(data, dict) else []
    return sorted(locations, key=lambda loc: (loc.get("name") or "").lower())


def first_available_space(http: HTTP, date, location_uuid: str) -> dict:
    spaces = get_available_spaces(http, date, location_uuid)
    if not spaces:
        raise RuntimeError("no workspace returned")
    if len(spaces) > 1:
        print(f"warning: {len(spaces)} workspaces returned; using the first", file=sys.stderr)
    return spaces[0]


def get_available_spaces(http: HTTP, date, location_uuid: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "locationUUIDs": location_uuid,
            "closestCity": "",
            "userLatitude": "",
            "userLongitude": "",
            "boundnwLat": "",
            "boundnwLng": "",
            "boundseLat": "",
            "boundseLng": "",
            "type": "0",
            "offset": "0",
            "limit": "50",
            "roomTypeFilter": "",
            "date": date.strftime("%Y-%m-%d"),
            "duration": "30",
            "locationOffset": local_offset(),
            "isWeb": "true",
            "capacity": "0",
            "endDate": "",
        }
    )
    data = http.json("GET", f"{MEMBER_BASE}/workplaceone/api/spaces/get-spaces?{query}")
    spaces = ((data.get("getSharedWorkspaces") or {}).get("workspaces")) or []
    return spaces


def summarize_space(space: dict) -> dict:
    seat = space.get("seat") or {}
    available = space.get("seatsAvailable")
    if not available:
        available = seat.get("available")
    capacity = space.get("capacity") or seat.get("total") or 0
    return {
        "uuid": space.get("uuid"),
        "inventoryUuid": space.get("inventoryUuid"),
        "available": int(available or 0),
        "capacity": int(capacity or 0),
        "credits": space.get("credits") or 0,
        "open": space.get("openTime") or "",
        "close": space.get("closeTime") or "",
    }


def booking_quote(http: HTTP, date, space: dict) -> dict:
    return http.json(
        "POST",
        f"{MEMBER_BASE}/workplaceone/api/common-booking/quote",
        booking_body(date, space),
    )


def create_booking(http: HTTP, date, space: dict, quote: dict) -> dict:
    return http.json(
        "POST",
        f"{MEMBER_BASE}/workplaceone/api/common-booking/",
        booking_body(date, space, quote),
    )


def booking_body(date, space: dict, quote: dict | None = None) -> dict:
    loc = space["location"]
    tz = ZoneInfo(loc["timeZone"])
    open_time = parse_hhmm(space.get("openTime") or "08:30")
    close_time = parse_hhmm(space.get("closeTime") or "20:00")
    start_local = datetime.combine(date, open_time, tz)
    end_local = datetime.combine(date, close_time, tz)
    start_utc = start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_text = date.strftime("%Y-%m-%d")
    open_text = open_time.strftime("%H:%M")
    close_text = close_time.strftime("%H:%M")
    space_id = quote_space_id(space)

    body = {
        "SpaceType": 4,
        "ReservationID": "",
        "TriggerCalendarEvent": True,
        "Notes": None,
        "MailData": {
            "dayFormatted": date.strftime("%A, %B %-d") if sys.platform != "win32" else date.strftime("%A, %B %#d"),
            "startTimeFormatted": open_text,
            "endTimeFormatted": close_text,
            "floorAddress": "",
            "locationAddress": (loc.get("address") or {}).get("line1", ""),
            "creditsUsed": "0",
            "Capacity": "1",
            "TimezoneUsed": "GMT " + (loc.get("timezoneOffset") or local_offset()),
            "TimezoneIana": loc.get("timeZone", ""),
            "startDateTime": f"{date_text} {open_text}",
            "endDateTime": f"{date_text} {close_text}",
            "locationName": loc.get("name", ""),
            "locationCity": (loc.get("address") or {}).get("city", ""),
            "locationCountry": (loc.get("address") or {}).get("country", ""),
            "locationState": (loc.get("address") or {}).get("state", ""),
        },
        "LocationType": loc.get("accountType"),
        "UTCOffset": loc.get("timezoneOffset") or local_offset(),
        "Currency": "com.wework.credits",
        "LocationID": loc.get("uuid"),
        "SpaceID": space_id,
        "WeWorkSpaceID": space.get("uuid"),
        "StartTime": start_utc,
        "EndTime": end_utc,
    }
    if quote is not None:
        body["ApplicationType"] = "WorkplaceOne"
        body["PlatformType"] = "iOS_APP"
        body["CreditRatio"] = (quote.get("grandTotal") or {}).get("creditRatio")
        body["SpaceID"] = booking_space_id(space)
    return body


def quote_space_id(space: dict) -> str:
    return space.get("inventoryUuid") or space.get("uuid")


def booking_space_id(space: dict) -> str:
    loc = space.get("location") or {}
    reservable = space.get("reservable") or {}
    if loc.get("accountType") == 2 and reservable.get("KubeId"):
        return reservable["KubeId"]
    return space.get("inventoryUuid") or space.get("uuid")


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


def get_usable_token(account: str) -> str:
    token = load_token_from_keychain(account)
    if token and not token_expired(token):
        return token

    username = load_username_from_keychain(account)
    password = load_password_from_keychain(account)
    if not username or not password:
        return token if token and not jwt_claims(token).get("exp") else ""

    print("stored token is missing or expired; refreshing with Keychain username/password", file=sys.stderr)
    http = HTTP()
    token = authenticate(http, username, password)
    save_token_to_keychain(account, token)
    return token


def keychain_item_account(account: str, kind: str) -> str:
    return f"{account}:{kind}"


def save_username_password_to_keychain(account: str, username: str, password: str) -> None:
    save_secret_to_keychain(keychain_item_account(account, USERNAME_SUFFIX), username)
    save_secret_to_keychain(keychain_item_account(account, PASSWORD_SUFFIX), password)


def load_username_from_keychain(account: str) -> str:
    return load_secret_from_keychain(keychain_item_account(account, USERNAME_SUFFIX))


def load_password_from_keychain(account: str) -> str:
    return load_secret_from_keychain(keychain_item_account(account, PASSWORD_SUFFIX))


def save_token_to_keychain(account: str, token: str) -> None:
    save_secret_to_keychain(keychain_item_account(account, TOKEN_SUFFIX), token)


def load_token_from_keychain(account: str) -> str:
    return load_secret_from_keychain(keychain_item_account(account, TOKEN_SUFFIX))


def save_secret_to_keychain(item_account: str, value: str) -> None:
    require_security()
    run_security(
        [
            "add-generic-password",
            "-a",
            item_account,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            value,
            "-U",
        ],
        "save secret to Keychain",
    )


def load_secret_from_keychain(item_account: str) -> str:
    require_security()
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            item_account,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def require_security() -> None:
    if shutil.which("security") is None:
        fatal("macOS `security` command not found; Keychain persistence is unavailable")


def run_security(args: list[str], action: str) -> None:
    result = subprocess.run(
        ["security", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"failed to {action}: {detail}")


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


if __name__ == "__main__":
    raise SystemExit(main())
