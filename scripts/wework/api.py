from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .constants import CANCEL_BOOKING_URL, MEMBER_BASE, UPCOMING_BOOKINGS_URL
from .http_client import HTTP
from .utils import (
    format_day_for_mail,
    iso_date_only,
    local_offset,
    parse_hhmm,
    strip_trailing_z,
)


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
