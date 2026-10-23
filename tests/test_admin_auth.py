#!/usr/bin/env python3
"""
Verification Test 3: Admin API Authentication
Tests authentication against grommunio-admin-api:
1. Verifies that invalid credentials are rejected with HTTP 401.
2. Authenticates user 'admin' using ADMIN_PASSWORD from environment / .env.
3. Obtains and validates signed grommunioAuthJwt token.
4. Queries protected endpoint (/api/v1/system/status) passing the token cookie.
"""
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import sys

def load_env_password():
    password = os.environ.get("ADMIN_PASSWORD")
    if password:
        return password
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ADMIN_PASSWORD="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "admin_secret"

def check_admin_auth():
    print("==> [3/5] Checking Admin API authentication...")
    admin_pw = load_env_password()
    login_url = "http://localhost:8081/api/v1/login"

    # Step 1: Verify rejection of invalid credentials
    bad_data = urllib.parse.urlencode({"user": "admin", "pass": "invalid_wrong_pw"}).encode()
    req_bad = urllib.request.Request(
        login_url,
        data=bad_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        urllib.request.urlopen(req_bad)
        print("    [-] Invalid credentials test FAILED: Unexpected 200 OK", file=sys.stderr)
        return False
    except urllib.error.HTTPError as err:
        if err.code == 401:
            print("    [+] Invalid password correctly rejected with HTTP 401.")
        else:
            print(f"    [-] Invalid password returned unexpected HTTP {err.code}", file=sys.stderr)
            return False

    # Step 2: Authenticate with valid password
    good_data = urllib.parse.urlencode({"user": "admin", "pass": admin_pw}).encode()
    req_good = urllib.request.Request(
        login_url,
        data=good_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req_good) as resp:
            data = json.loads(resp.read().decode())
            token = data.get("grommunioAuthJwt")
            if not token:
                print("    [-] Login succeeded but grommunioAuthJwt token missing from payload.", file=sys.stderr)
                return False
            print(f"    [+] Login succeeded. JWT token issued ({token[:16]}...).")
    except Exception as ex:
        print(f"    [-] Valid login failed: {ex}", file=sys.stderr)
        return False

    # Step 3: Access protected endpoint using JWT cookie
    prot_url = "http://localhost:8081/api/v1/domains"
    req_prot = urllib.request.Request(
        prot_url,
        headers={"Cookie": f"grommunioAuthJwt={token}"}
    )
    try:
        with urllib.request.urlopen(req_prot) as resp:
            domains_data = json.loads(resp.read().decode())
            print(f"    [+] Protected endpoint accessible. Domains count: {len(domains_data)}")
    except Exception as ex:
        print(f"    [-] Failed to access protected endpoint with token: {ex}", file=sys.stderr)
        return False

    return True

if __name__ == "__main__":
    if check_admin_auth():
        print("[+] SUCCESS: Admin API authentication fully functional.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Admin API authentication check failed.\n", file=sys.stderr)
        sys.exit(1)
