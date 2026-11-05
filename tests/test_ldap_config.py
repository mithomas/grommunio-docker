#!/usr/bin/env python3
"""
Verification Test: Admin API LDAP Configuration & Disk Persistence
1. GET /system/mconf/ldap returns the default group mappings and LDAP enabled.
2. A PUT (as the Admin UI save does) succeeds without permission errors, is returned by the next GET,
   and is written to /etc/gromox/ldap_adaptor.cfg.
3. The original configuration is restored and the restore is verified on disk.
"""
import copy
import sys

from env_helper import admin_session, api, container_exec

CFG_PATH = "/etc/gromox/ldap_adaptor.cfg"
PUT_PATH = "/system/mconf/ldap?force=true"


def cfg_value(key):
    res = container_exec("grep", "-E", f"^{key}=", CFG_PATH)
    return res.stdout.strip().split("=", 1)[1] if res.returncode == 0 else None


def check_ldap_config():
    print("==> Checking Admin API LDAP configuration & disk persistence...")
    try:
        session = admin_session()
    except RuntimeError as ex:
        print(f"    [-] {ex}", file=sys.stderr)
        return False
    if not session.csrf:
        print("    [-] Missing CSRF token from login response.", file=sys.stderr)
        return False

    status, body = api("GET", "/system/mconf/ldap", session=session)
    if status != 200 or not body:
        print(f"    [-] GET /system/mconf/ldap returned HTTP {status}", file=sys.stderr)
        return False
    original = body["data"]

    missing = [k for k in ("groupMemberAttr", "groupaddr", "groupfilter", "groupname") if not original.get("groups", {}).get(k)]
    if missing:
        print(f"    [-] Missing default group configurations: {missing}", file=sys.stderr)
        return False
    if original.get("disabled") is True:
        print("    [-] LDAP is unexpectedly reported as disabled.", file=sys.stderr)
        return False
    print("    [+] Default LDAP groups are configured and LDAP is enabled.")

    old_value = original["users"]["displayName"]
    new_value = "cn" if old_value != "cn" else "displayName"
    modified = copy.deepcopy(original)
    modified["users"]["displayName"] = new_value

    ok = False
    try:
        status, body = api("PUT", PUT_PATH, body=modified, session=session)
        if status != 200:
            print(f"    [-] PUT /system/mconf/ldap failed with HTTP {status}: {body}", file=sys.stderr)
            return False

        _, body = api("GET", "/system/mconf/ldap", session=session)
        if body["data"]["users"]["displayName"] != new_value:
            print("    [-] Changed value was not returned by the next GET.", file=sys.stderr)
            return False
        if cfg_value("ldap_user_displayname") != new_value:
            print(f"    [-] {CFG_PATH} does not contain the saved value '{new_value}'.", file=sys.stderr)
            return False
        print(f"    [+] Change persisted via API and on disk ({old_value} -> {new_value}).")
        ok = True
    finally:
        status, _ = api("PUT", PUT_PATH, body=original, session=session)
        restored = status == 200 and cfg_value("ldap_user_displayname") == old_value
        if restored:
            print("    [+] Original configuration restored.")
        else:
            print("    [-] Failed to restore the original LDAP configuration.", file=sys.stderr)
            ok = False
    return ok


def main():
    if check_ldap_config():
        print("[+] LDAP CONFIGURATION TEST PASSED!\n")
        return 0
    print("[-] LDAP CONFIGURATION TEST FAILED.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
