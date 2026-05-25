from __future__ import annotations

import shutil
import subprocess
import sys

from .constants import KEYCHAIN_SERVICE, PASSWORD_SUFFIX, TOKEN_SUFFIX, USERNAME_SUFFIX
from .utils import format_expiry, jwt_claims, token_expired


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
