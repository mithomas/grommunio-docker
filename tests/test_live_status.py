#!/usr/bin/env python3
"""
Verification Test 9: Admin UI Live Status & Nginx VTS Telemetry
Validates the virtual host live status telemetry used by the Admin UI Live Status dashboard:
1. Authenticates to grommunio-admin-api with ADMIN_PASSWORD to obtain JWT cookie.
2. Queries /api/v1/system/vhostStatus and verifies configured vhosts include 'local'.
3. Queries /api/v1/system/vhostStatus/local and verifies HTTP 200 OK (no 502 Bad Gateway).
4. Asserts that the response payload contains valid Nginx VTS JSON data structure
   (connections, serverZones).
5. Verifies internal loopback /statusmonitor endpoint availability and access restriction.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def load_env_password():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("ADMIN_PASSWORD="):
                    return line.strip().split("=", 1)[1].strip("'\"")
    return "admin_secret"


def check_live_status():
    print("==> Checking Admin UI Live Status & Nginx VTS telemetry...")
    admin_pw = load_env_password()
    login_url = "http://localhost:8081/api/v1/login"

    # Step 1: Authenticate to get grommunioAuthJwt
    login_data = urllib.parse.urlencode({"user": "admin", "pass": admin_pw}).encode()
    login_req = urllib.request.Request(
        login_url,
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    try:
        with urllib.request.urlopen(login_req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            token = body.get("grommunioAuthJwt")
            if not token:
                print("    [-] Login succeeded but grommunioAuthJwt missing from response.", file=sys.stderr)
                return False
            print(f"    [+] Logged in to Admin API (token: {token[:16]}...).")
    except Exception as ex:
        print(f"    [-] Failed to login to Admin API: {ex}", file=sys.stderr)
        return False

    auth_headers = {
        "Cookie": f"grommunioAuthJwt={token}",
        "User-Agent": "GrommunioLiveStatusTest/1.0"
    }

    # Step 2: Query vhost list endpoint
    list_url = "http://localhost:8081/api/v1/system/vhostStatus"
    list_req = urllib.request.Request(list_url, headers=auth_headers)
    try:
        with urllib.request.urlopen(list_req, timeout=10) as resp:
            if resp.status != 200:
                print(f"    [-] vhostStatus list returned HTTP {resp.status}", file=sys.stderr)
                return False
            data = json.loads(resp.read().decode("utf-8"))
            vhosts = data.get("data", [])
            if "local" not in vhosts:
                print(f"    [-] 'local' vhost not found in vhost list: {vhosts}", file=sys.stderr)
                return False
            print(f"    [+] vhostStatus list endpoint verified (vhosts: {vhosts}).")
    except Exception as ex:
        print(f"    [-] Error requesting vhostStatus list: {ex}", file=sys.stderr)
        return False

    # Step 3: Query vhostStatus/local endpoint (Live Status page data)
    status_url = "http://localhost:8081/api/v1/system/vhostStatus/local"
    status_req = urllib.request.Request(status_url, headers=auth_headers)
    try:
        with urllib.request.urlopen(status_req, timeout=10) as resp:
            if resp.status != 200:
                print(f"    [-] vhostStatus/local returned HTTP {resp.status} (expected 200)", file=sys.stderr)
                return False
            raw_body = resp.read().decode("utf-8")
            status_data = json.loads(raw_body)
            print("    [+] vhostStatus/local returned HTTP 200 OK (no 502 Bad Gateway).")
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="replace")
        print(f"    [-] vhostStatus/local failed with HTTP {err.code}: {err_body[:200]}", file=sys.stderr)
        return False
    except Exception as ex:
        print(f"    [-] Error querying vhostStatus/local: {ex}", file=sys.stderr)
        return False

    # Step 4: Validate Nginx VTS telemetry payload structure
    connections = status_data.get("connections")
    if not isinstance(connections, dict):
        print(f"    [-] Missing 'connections' object in telemetry: {list(status_data.keys())}", file=sys.stderr)
        return False

    active_conn = connections.get("active")
    total_reqs = connections.get("requests")
    if active_conn is None or total_reqs is None:
        print(f"    [-] Telemetry connections object missing 'active' or 'requests': {connections}", file=sys.stderr)
        return False

    server_zones = status_data.get("serverZones")
    if not isinstance(server_zones, dict):
        print(f"    [-] Missing 'serverZones' object in telemetry: {list(status_data.keys())}", file=sys.stderr)
        return False

    print(f"    [+] Nginx VTS telemetry verified: Active={active_conn}, Total Requests={total_reqs}, Server Zones={len(server_zones)}.")

    # Step 5: Verify access control on direct /statusmonitor
    # External direct requests from outside the container must be rejected with HTTP 403 Forbidden
    sm_url = "http://localhost:8080/statusmonitor"
    sm_req = urllib.request.Request(sm_url, headers={"User-Agent": "GrommunioLiveStatusTest/1.0"})
    try:
        with urllib.request.urlopen(sm_req, timeout=10) as resp:
            print(f"    [-] Direct external /statusmonitor was unexpectedly accessible (HTTP {resp.status})", file=sys.stderr)
            return False
    except urllib.error.HTTPError as err:
        if err.code == 403:
            print("    [+] Direct external /statusmonitor access properly forbidden (HTTP 403).")
        else:
            print(f"    [-] Unexpected HTTP error on direct /statusmonitor: {err.code}", file=sys.stderr)
            return False
    except Exception as ex:
        print(f"    [-] Direct /statusmonitor connection error: {ex}", file=sys.stderr)
        return False

    return True


if __name__ == "__main__":
    if check_live_status():
        print("[+] SUCCESS: Admin UI Live Status & Nginx VTS telemetry fully functional.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Live Status check failed.\n", file=sys.stderr)
        sys.exit(1)
