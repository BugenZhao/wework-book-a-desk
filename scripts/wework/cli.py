from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from getpass import getpass

from .api import (
    availability_for_location,
    booking_quote,
    cancel_booking,
    create_booking,
    fetch_upcoming_bookings,
    first_available_space,
    get_locations_by_city,
    normalize_booking,
    resolve_location,
    select_locations,
)
from .auth import authenticate
from .constants import DEFAULT_KEYCHAIN_ACCOUNT
from .http_client import HTTP
from .keychain import (
    get_usable_token,
    load_password_from_keychain,
    load_token_from_keychain,
    load_username_from_keychain,
    print_saved_token,
    save_token_to_keychain,
    save_username_password_to_keychain,
)
from .utils import fatal, format_expiry, jwt_claims, token_expired


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
