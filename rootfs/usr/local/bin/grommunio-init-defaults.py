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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def str2bool(val, default=False):
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def str2int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def main():
    lang = os.environ.get("DEFAULT_USER_LANG", "de_DE")
    priv_web = str2bool(os.environ.get("DEFAULT_USER_PRIV_WEB", "true"), True)
    priv_eas = str2bool(os.environ.get("DEFAULT_USER_PRIV_EAS", "true"), True)
    priv_dav = str2bool(os.environ.get("DEFAULT_USER_PRIV_DAV", "false"), False)
    priv_smtp = str2bool(os.environ.get("DEFAULT_USER_PRIV_SMTP", "false"), False)
    priv_pop3_imap = str2bool(os.environ.get("DEFAULT_USER_PRIV_POP3_IMAP", "false"), False)
    priv_chg_passwd = str2bool(os.environ.get("DEFAULT_USER_PRIV_CHANGE_PASSWORD", "false"), False)
    priv_chat = str2bool(os.environ.get("DEFAULT_USER_PRIV_CHAT", "false"), False)
    priv_meet = str2bool(os.environ.get("DEFAULT_USER_PRIV_MEET", "false"), False)
    priv_files = str2bool(os.environ.get("DEFAULT_USER_PRIV_FILES", "false"), False)
    priv_archive = str2bool(os.environ.get("DEFAULT_USER_PRIV_ARCHIVE", "false"), False)
    chat_user = str2bool(os.environ.get("DEFAULT_USER_CREATE_CHAT_USER", "false"), False)

    storage_quota = str2int(os.environ.get("DEFAULT_USER_STORAGE_QUOTA", "0"), 0)
    send_quota = str2int(os.environ.get("DEFAULT_USER_SEND_QUOTA", "0"), 0)
    receive_quota = str2int(os.environ.get("DEFAULT_USER_RECEIVE_QUOTA", "0"), 0)

    admin_pw = os.environ.get("ADMIN_PASSWORD", "admin_secret")
    admin_port = os.environ.get("ADMIN_PORT", "8081")
    api_base = f"http://127.0.0.1:{admin_port}/api/v1"

    # Step 1: Wait for Admin API to become reachable
    max_wait = 90
    start_time = time.time()
    token = None
    csrf = None

    print(f"[init-defaults] Connecting to Grommunio Admin API at {api_base}...")
    while time.time() - start_time < max_wait:
        try:
            login_data = urllib.parse.urlencode({"user": "admin", "pass": admin_pw}).encode()
            req = urllib.request.Request(
                f"{api_base}/login",
                data=login_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                auth_data = json.loads(resp.read().decode())
                token = auth_data.get("grommunioAuthJwt")
                csrf = auth_data.get("csrf")
                if token and csrf:
                    break
        except Exception:
            time.sleep(2)

    if not token or not csrf:
        print("[init-defaults] Could not authenticate to Admin API within timeout. Exiting.", file=sys.stderr)
        return 1

    print("[init-defaults] Successfully authenticated to Admin API.")

    # Step 2: Fetch current defaults
    current_data = {}
    try:
        req_get = urllib.request.Request(
            f"{api_base}/defaults/createParams",
            headers={"Cookie": f"grommunioAuthJwt={token}"}
        )
        with urllib.request.urlopen(req_get, timeout=5) as resp:
            existing_resp = json.loads(resp.read().decode())
            current_data = existing_resp.get("data", {})
    except Exception as ex:
        print(f"[init-defaults] Notice: Could not read existing defaults ({ex}), initializing fresh.", file=sys.stderr)

    user_defaults = current_data.get("user", {})

    # Check if target values match existing settings
    desired = {
        "lang": lang,
        "privWeb": priv_web,
        "privEas": priv_eas,
        "privDav": priv_dav,
        "smtp": priv_smtp,
        "pop3_imap": priv_pop3_imap,
        "changePassword": priv_chg_passwd,
        "privChat": priv_chat,
        "privVideo": priv_meet,
        "privFiles": priv_files,
        "privArchive": priv_archive,
        "chat": chat_user,
    }

    already_matches = all(user_defaults.get(k) == v for k, v in desired.items())
    if already_matches:
        print("[init-defaults] Defaults are already configured with desired parameters. No changes needed.")
        return 0

    # Step 3: Update defaults
    user_defaults.update(desired)
    payload = {
        "user": user_defaults,
        "domain": current_data.get("domain", {})
    }

    req_put = urllib.request.Request(
        f"{api_base}/defaults/createParams",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cookie": f"grommunioAuthJwt={token}",
            "X-Csrf-Token": csrf
        },
        method="PUT"
    )

    try:
        with urllib.request.urlopen(req_put, timeout=5) as resp:
            print(f"[init-defaults] Successfully configured defaults:")
            print(f"    - Language: {lang}")
            print(f"    - Web: {priv_web}, EAS: {priv_eas}, DAV: {priv_dav}")
            print(f"    - SMTP: {priv_smtp}, POP3/IMAP: {priv_pop3_imap}, ChangePassword: {priv_chg_passwd}")
            print(f"    - Chat: {priv_chat}, Meet: {priv_meet}, Files: {priv_files}, Archive: {priv_archive}")
            print(f"    - Chat user: {chat_user}")
            print(f"    - Quotas: storage={storage_quota}, send={send_quota}, receive={receive_quota}")
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
