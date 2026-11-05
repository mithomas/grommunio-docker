#!/usr/bin/env python3
"""
Verification Test: Admin API Authentication
1. Invalid credentials are rejected with HTTP 401.
2. Valid credentials issue a grommunioAuthJwt token.
3. The token grants access to a protected endpoint.
"""
import sys

from env_helper import admin_session, api


def check_admin_auth():
    print("==> Checking Admin API authentication...")

    status, _ = api("POST", "/login", form={"user": "admin", "pass": "invalid_wrong_pw"})
    if status != 401:
        print(f"    [-] Invalid password returned HTTP {status}, expected 401", file=sys.stderr)
        return False
    print("    [+] Invalid password correctly rejected with HTTP 401.")

    try:
        session = admin_session()
    except RuntimeError as ex:
        print(f"    [-] {ex}", file=sys.stderr)
        return False
    print(f"    [+] Login succeeded. JWT token issued ({session.token[:16]}...).")

    status, domains = api("GET", "/domains", session=session)
    if status != 200 or domains is None:
        print(f"    [-] Protected endpoint returned HTTP {status} with token", file=sys.stderr)
        return False
    print("    [+] Protected endpoint accessible with token.")
    return True

if __name__ == "__main__":
    if check_admin_auth():
        print("[+] SUCCESS: Admin API authentication fully functional.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Admin API authentication check failed.\n", file=sys.stderr)
        sys.exit(1)
