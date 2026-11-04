#!/usr/bin/env python3
"""
Verification Test 4: Rspamd Dedicated Dashboard Authentication
Tests password protection on port 11334:
1. Confirms unauthenticated access to /auth is rejected (401 / Unauthorized).
2. Authenticates using RSPAMD_PASSWORD from .env and confirms auth: ok.
3. Queries /stat endpoint passing valid authentication.
"""
import urllib.request
import urllib.error
import json
import os
import sys

from env_helper import get_rspamd_port, load_env_secret

def check_rspamd_auth():
    rspamd_port = get_rspamd_port()
    print(f"==> [4/5] Checking dedicated Rspamd dashboard authentication (port {rspamd_port})...")
    rspamd_pw = load_env_secret("RSPAMD_PASSWORD", "rspamd_secret")
    auth_url = f"http://localhost:{rspamd_port}/auth"

    # Step 1: Unauthenticated request should be rejected
    req_bad = urllib.request.Request(auth_url, data=b"", headers={"Password": "wrong_password"})
    try:
        with urllib.request.urlopen(req_bad) as resp:
            data = json.loads(resp.read().decode())
            if data.get("error") == "Unauthorized":
                print("    [+] Unauthenticated access rejected with Unauthorized response.")
            else:
                print("    [-] Unauthenticated request unexpectedly succeeded.", file=sys.stderr)
                return False
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            print(f"    [+] Unauthenticated access rejected with HTTP {err.code}.")
        else:
            print(f"    [-] Unexpected HTTP error: {err.code}", file=sys.stderr)
            return False

    # Step 2: Valid authentication should succeed
    req_good = urllib.request.Request(auth_url, data=b"", headers={"Password": rspamd_pw})
    try:
        with urllib.request.urlopen(req_good) as resp:
            data = json.loads(resp.read().decode())
            if data.get("auth") == "ok":
                print(f"    [+] Authenticated to Rspamd dashboard successfully (v{data.get('version')}).")
            else:
                print(f"    [-] Rspamd auth response did not report auth=ok: {data}", file=sys.stderr)
                return False
    except Exception as ex:
        print(f"    [-] Valid Rspamd authentication failed: {ex}", file=sys.stderr)
        return False

    # Step 3: Query /stat endpoint with authentication
    stat_url = f"http://localhost:{rspamd_port}/stat"
    req_stat = urllib.request.Request(stat_url, headers={"Password": rspamd_pw})
    try:
        with urllib.request.urlopen(req_stat) as resp:
            data = json.loads(resp.read().decode())
            scanned = data.get("scanned", 0)
            print(f"    [+] Rspamd stats retrieved. Total messages scanned so far: {scanned}")
    except Exception as ex:
        print(f"    [-] Failed to retrieve Rspamd statistics: {ex}", file=sys.stderr)
        return False

    return True

if __name__ == "__main__":
    if check_rspamd_auth():
        print("[+] SUCCESS: Rspamd dashboard authentication fully functional.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Rspamd dashboard authentication check failed.\n", file=sys.stderr)
        sys.exit(1)
