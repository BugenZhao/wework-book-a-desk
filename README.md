# WeWork Codex Skill

A Codex skill plus a dependency-free Python CLI for WeWork desk booking automation.

The script uses the public `members.wework.com` member portal APIs. It can:

- authenticate with WeWork/Auth0 and store credentials in macOS Keychain
- list WeWork locations in a city
- check desk availability by city, location name, or location UUID
- dry-run a booking quote
- create a desk booking
- list upcoming bookings
- preview or confirm booking cancellation

## Requirements

- macOS for Keychain-backed persistence
- Python 3.10+
- No third-party Python packages

## Files

```text
SKILL.md
agents/openai.yaml
scripts/wework_min.py
```

## Auth

Save username/password to macOS Keychain and refresh a token:

```bash
python3 scripts/wework_min.py auth password --username you@example.com
```

The password is prompted securely if `--password` / `WEWORK_PASSWORD` is not supplied.

Save an existing token instead:

```bash
python3 scripts/wework_min.py auth token --token "$WEWORK_TOKEN"
```

Check saved auth state:

```bash
python3 scripts/wework_min.py auth status
```

## Locations

List locations in a city:

```bash
python3 scripts/wework_min.py locations --city Singapore
```

Raw JSON:

```bash
python3 scripts/wework_min.py locations --city Singapore --json
```

## Availability

Check a location by city/name:

```bash
python3 scripts/wework_min.py availability \
  --date 2026-05-26 \
  --city Singapore \
  --name "21 Collyer"
```

Check all locations in a city:

```bash
python3 scripts/wework_min.py availability \
  --date 2026-05-26 \
  --city Singapore
```

Check by UUID:

```bash
python3 scripts/wework_min.py availability \
  --date 2026-05-26 \
  --location-uuid b6fb35eb-924b-40ca-b983-b32646dbe7ee
```

## Booking

Dry-run first:

```bash
python3 scripts/wework_min.py book \
  --date 2026-05-26 \
  --city Singapore \
  --name "21 Collyer" \
  --dry-run
```

Create the booking:

```bash
python3 scripts/wework_min.py book \
  --date 2026-05-26 \
  --city Singapore \
  --name "21 Collyer"
```

## Bookings And Cancellation

List upcoming bookings:

```bash
python3 scripts/wework_min.py bookings
```

Preview cancellation:

```bash
python3 scripts/wework_min.py cancel --booking-id 123456
```

Actually cancel:

```bash
python3 scripts/wework_min.py cancel --booking-id 123456 --confirm
```

## Notes

- Always dry-run before booking a new location.
- `auth password` stores username/password and token as macOS Keychain generic passwords under service `codex-wework-skill`.
- If a saved token expires, `book`, `locations`, `availability`, `bookings`, and `cancel` can refresh it from saved username/password.
- If your account requires MFA, SSO, CAPTCHA, or a changed Auth0 flow, the minimal CLI may need updates.
