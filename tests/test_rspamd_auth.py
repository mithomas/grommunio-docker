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

def load_env_rspamd_password():
    password = os.environ.get("RSPAMD_PASSWORD")
    if password:
        return password
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("RSPAMD_PASSWORD="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "rspamd_secret"

def check_rspamd_auth():
    print("==> [4/5] Checking dedicated Rspamd dashboard authentication (port 11334)...")
    rspamd_pw = load_env_rspamd_password()
    auth_url = "http://localhost:11334/auth"

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
    stat_url = "http://localhost:11334/stat"
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
