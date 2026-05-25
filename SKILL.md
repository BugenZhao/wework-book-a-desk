---
name: wework
description: Use this skill for WeWork member portal automation, especially authenticating against members.wework.com and booking or dry-running WeWork desk reservations from the command line. It includes a dependency-free Python CLI for auth, location lookup, desk availability, quote, booking, upcoming booking listing, and cancellation.
---

# WeWork Book A Desk

## Use This For

- Booking a WeWork desk from `members.wework.com` with a WeWork username/password.
- Dry-running a desk booking by calling the quote endpoint without creating a reservation.
- Looking up a WeWork location UUID by city and location name before booking.
- Listing WeWork locations in a city, including UUIDs for direct booking.
- Checking desk availability for a location or all locations in a city on a given date.
- Listing upcoming bookings and cancelling a booking with explicit confirmation.

## Script

Use `scripts/wework_min.py`. It uses only the Python standard library.

### Auth

Persist username/password to macOS Keychain and refresh a token immediately:

```bash
export WEWORK_USERNAME="you@example.com"
python3 scripts/wework_min.py auth password
```

The script prompts for the password if `WEWORK_PASSWORD` / `--password` is not provided. Passwords are stored in macOS Keychain only, not in local files.

Persist an already-known token instead:

```bash
python3 scripts/wework_min.py auth token \
  --token "$WEWORK_TOKEN"
```

Use a separate Keychain account label if needed:

```bash
python3 scripts/wework_min.py auth password \
  --account work \
  --username "you@example.com"
```

Check saved auth state:

```bash
python3 scripts/wework_min.py auth status
```

List locations in a city:

```bash
python3 scripts/wework_min.py locations \
  --city "Singapore"
```

Emit raw location JSON:

```bash
python3 scripts/wework_min.py locations \
  --city "Singapore" \
  --json
```

Check availability for one location/date:

```bash
python3 scripts/wework_min.py availability \
  --date 2026-05-26 \
  --city "Singapore" \
  --name "21 Collyer"
```

Check all locations in a city:

```bash
python3 scripts/wework_min.py availability \
  --date 2026-05-26 \
  --city "Singapore"
```

Check by location UUID:

```bash
python3 scripts/wework_min.py availability \
  --date 2026-05-26 \
  --location-uuid "LOCATION_UUID"
```

List upcoming bookings:

```bash
python3 scripts/wework_min.py bookings
```

Preview a cancellation by booking ID:

```bash
python3 scripts/wework_min.py cancel \
  --booking-id "BOOKING_ID"
```

Actually cancel:

```bash
python3 scripts/wework_min.py cancel \
  --booking-id "BOOKING_ID" \
  --confirm
```

Cancel can also match a single booking by date and optional location filter:

```bash
python3 scripts/wework_min.py cancel \
  --date 2026-05-26 \
  --city "Singapore" \
  --name "21 Collyer" \
  --confirm
```

Dry-run a quote:

```bash
python3 scripts/wework_min.py book \
  --date 2026-05-26 \
  --city "Singapore" \
  --name "WeWork" \
  --dry-run
```

Book by location UUID:

```bash
python3 scripts/wework_min.py book \
  --date 2026-05-26 \
  --location-uuid "LOCATION_UUID"
```

Book by city and fuzzy location name:

```bash
python3 scripts/wework_min.py book \
  --date 2026-05-26 \
  --city "Singapore" \
  --name "21 Collyer Quay"
```

## Constraints

- The script intentionally implements only the common Auth0 password/login-ticket path. If login requires MFA, CAPTCHA, SSO, or a changed hosted form, use a fresh browser session or update the auth flow.
- `auth password` stores username/password and the resolved WeWork token as macOS Keychain generic passwords under service `wework-book-a-desk`; `book` uses the token if still valid and refreshes it from saved username/password if expired.
- `auth token` stores only an existing token. If it expires and no username/password is saved, run `auth token` or `auth password` again.
- Do not print passwords or tokens. Prefer `auth password --username ...` with the hidden password prompt, or `auth token --token ...` when the token came from an already-authenticated browser session.
- Always dry-run first for a new location or after any WeWork member portal change.
- The script books the first workspace returned by WeWork for the location/date. If the endpoint returns multiple workspaces, inspect the output and adjust selection logic before booking.
