#!/usr/bin/env python3
# ==============================================================================
# grommunio-init-defaults.py
# Pre-configures mailbox creation defaults in Grommunio Admin API:
# - Language (DEFAULT_USER_LANG, default: de_DE)
# - Protocols: Web (DEFAULT_USER_PRIV_WEB), EAS (DEFAULT_USER_PRIV_EAS), DAV (DEFAULT_USER_PRIV_DAV)
# - Services: SMTP (DEFAULT_USER_PRIV_SMTP), POP3/IMAP (DEFAULT_USER_PRIV_POP3_IMAP)
# - Account: Change password (DEFAULT_USER_PRIV_CHANGE_PASSWORD)
# - Integrations: Chat (DEFAULT_USER_PRIV_CHAT), Meet (DEFAULT_USER_PRIV_MEET),
#                 Files (DEFAULT_USER_PRIV_FILES), Archive (DEFAULT_USER_PRIV_ARCHIVE),
#                 Chat user creation (DEFAULT_USER_CREATE_CHAT_USER)
# - Quotas: Storage (DEFAULT_USER_STORAGE_QUOTA), Send (DEFAULT_USER_SEND_QUOTA),
#           Receive (DEFAULT_USER_RECEIVE_QUOTA)
# ==============================================================================

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

QUOTA_MULTIPLIERS = {
    "": 1, "k": 1, "kb": 1, "kib": 1,
    "m": 1024, "mb": 1024, "mib": 1024,
    "g": 1024 * 1024, "gb": 1024 * 1024, "gib": 1024 * 1024,
    "t": 1024 * 1024 * 1024, "tb": 1024 * 1024 * 1024, "tib": 1024 * 1024 * 1024,
}

DEFAULTS_SPEC = [
    ("lang", "DEFAULT_USER_LANG", "de_DE", str),
    ("privWeb", "DEFAULT_USER_PRIV_WEB", "true", lambda v: str2bool(v)),
    ("privEas", "DEFAULT_USER_PRIV_EAS", "true", lambda v: str2bool(v)),
    ("privDav", "DEFAULT_USER_PRIV_DAV", "false", lambda v: str2bool(v)),
    ("smtp", "DEFAULT_USER_PRIV_SMTP", "false", lambda v: str2bool(v)),
    ("pop3_imap", "DEFAULT_USER_PRIV_POP3_IMAP", "false", lambda v: str2bool(v)),
    ("changePassword", "DEFAULT_USER_PRIV_CHANGE_PASSWORD", "false", lambda v: str2bool(v)),
    ("privChat", "DEFAULT_USER_PRIV_CHAT", "false", lambda v: str2bool(v)),
    ("privVideo", "DEFAULT_USER_PRIV_MEET", "false", lambda v: str2bool(v)),
    ("privFiles", "DEFAULT_USER_PRIV_FILES", "false", lambda v: str2bool(v)),
    ("privArchive", "DEFAULT_USER_PRIV_ARCHIVE", "false", lambda v: str2bool(v)),
    ("chat", "DEFAULT_USER_CREATE_CHAT_USER", "false", lambda v: str2bool(v)),
    ("storagequotalimit", "DEFAULT_USER_STORAGE_QUOTA", "0", lambda v: parse_quota(v)),
    ("prohibitsendquota", "DEFAULT_USER_SEND_QUOTA", "0", lambda v: parse_quota(v)),
    ("prohibitreceivequota", "DEFAULT_USER_RECEIVE_QUOTA", "0", lambda v: parse_quota(v)),
]


def str2bool(val):
    if val is None:
        return False
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def parse_quota(val):
    """
    Parses quota values into integer Kilobytes (KiB) as expected by MAPI store properties
    (storagequotalimit, prohibitsendquota, prohibitreceivequota).
    Supports flexible human units (e.g. '10G', '10GB', '500M', '500MB', '1048576KB')
    as well as plain integers.
    Returns 0 for unlimited / disabled ('0', 'unlimited', 'none', 'off', 'false').
    Raises ValueError for unknown units or unparsable values.
    """
    if val is None:
        return 0
    s = str(val).strip().lower()
    if not s or s in ("0", "unlimited", "none", "off", "false"):
        return 0

    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-z]*)$", s)
    if not m:
        raise ValueError(f"Invalid quota specification: '{val}'")

    num_str, unit = m.groups()
    try:
        num = float(num_str)
    except ValueError:
        raise ValueError(f"Invalid numeric value in quota: '{val}'")

    if num < 0:
        raise ValueError(f"Quota value cannot be negative: '{val}'")

    if unit not in QUOTA_MULTIPLIERS:
        raise ValueError(f"Unknown quota unit '{unit}' in '{val}'. Supported units: B, KiB/KB, MiB/MB, GiB/GB, TiB/TB")

    return int(num * QUOTA_MULTIPLIERS[unit])


def api_request(api_base, method, path, data=None, headers=None):
    url = f"{api_base}{path}"
    h = headers.copy() if headers else {}
    body = None
    if data is not None:
        if isinstance(data, dict) and h.get("Content-Type") == "application/x-www-form-urlencoded":
            body = urllib.parse.urlencode(data).encode()
        else:
            body = json.dumps(data).encode()
            h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        content = resp.read().decode()
        return json.loads(content) if content else {}


def main():
    desired = {}
    for key, env_var, default_val, parser in DEFAULTS_SPEC:
        try:
            desired[key] = parser(os.environ.get(env_var, default_val))
        except ValueError as exc:
            print(f"[init-defaults] ERROR: {exc}", file=sys.stderr)
            return 1

    admin_pw = os.environ.get("ADMIN_PASSWORD", "admin")
    api_base = "http://127.0.0.1:8081/api/v1"

    # Step 1: Wait for Admin API to become reachable
    max_wait = 90
    start_time = time.time()
    token = None
    csrf = None

    print(f"[init-defaults] Connecting to Grommunio Admin API at {api_base}...")
    while time.time() - start_time < max_wait:
        try:
            auth_data = api_request(
                api_base, "POST", "/login",
                data={"user": "admin", "pass": admin_pw},
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            token = auth_data.get("grommunioAuthJwt")
            csrf = auth_data.get("csrf")
            if token and csrf:
                break
        except Exception:
            pass
        time.sleep(2)

    if not token or not csrf:
        print("[init-defaults] Could not authenticate to Admin API within timeout. Exiting.", file=sys.stderr)
        return 1

    print("[init-defaults] Successfully authenticated to Admin API.")

    # Step 2: Fetch current defaults
    current_data = {}
    try:
        resp = api_request(
            api_base, "GET", "/defaults/createParams",
            headers={"Cookie": f"grommunioAuthJwt={token}"}
        )
        current_data = resp.get("data", {})
    except Exception as ex:
        print(f"[init-defaults] Notice: Could not read existing defaults ({ex}), initializing fresh.", file=sys.stderr)

    user_defaults = current_data.get("user", {})
    if all(user_defaults.get(k) == v for k, v in desired.items()):
        print("[init-defaults] Defaults are already configured with desired parameters. No changes needed.")
        return 0

    # Step 3: Update defaults
    user_defaults.update(desired)
    payload = {
        "user": user_defaults,
        "domain": current_data.get("domain", {})
    }

    try:
        api_request(
            api_base, "PUT", "/defaults/createParams",
            data=payload,
            headers={
                "Cookie": f"grommunioAuthJwt={token}",
                "X-Csrf-Token": csrf
            }
        )
        print("[init-defaults] Successfully configured defaults:")
        for k, v in desired.items():
            print(f"    - {k}: {v}")
        return 0
    except urllib.error.HTTPError as ex:
        err_msg = ex.read().decode()
        print(f"[init-defaults] Error updating defaults (HTTP {ex.code}): {err_msg}", file=sys.stderr)
        return 1
    except Exception as ex:
        print(f"[init-defaults] Failed to update defaults: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
