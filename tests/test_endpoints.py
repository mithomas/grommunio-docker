#!/usr/bin/env python3
"""
Verification Test 2: Ingress Endpoints & Content-Types
Validates HTTP responses, status codes, and Content-Types across all container ports:
- Port 8080: Webmail (/) and ActiveSync (/Microsoft-Server-ActiveSync)
- Port 8081: Admin Web UI (/), /config.json, and Admin API status (/api/v1/status)
- Port 11334: Dedicated Rspamd Dashboard (/)
"""
import urllib.request
import urllib.error
import json
import time
import sys

ENDPOINTS = [
    {
        "name": "Webmail Interface",
        "url": "http://localhost:8080/",
        "expected_status": 200,
        "content_type_contains": "text/html",
    },
    {
        "name": "ActiveSync (EAS Challenge)",
        "url": "http://localhost:8080/Microsoft-Server-ActiveSync",
        "expected_status": 401,
        "content_type_contains": "text/html",
        "expected_header": ("WWW-Authenticate", "Basic realm=\"GSync\""),
    },
    {
        "name": "Admin Web UI HTML",
        "url": "http://localhost:8081/",
        "expected_status": 200,
        "content_type_contains": "text/html",
    },
    {
        "name": "Admin config.json",
        "url": "http://localhost:8081/config.json",
        "expected_status": 200,
        "content_type_contains": "application/json",
        "validate_json": True,
    },
    {
        "name": "Admin API Operational Status",
        "url": "http://localhost:8081/api/v1/status",
        "expected_status": 200,
        "content_type_contains": "application/json",
        "validate_json": True,
    },
    {
        "name": "Dedicated Rspamd Web Interface",
        "url": "http://localhost:11334/",
        "expected_status": 200,
        "content_type_contains": "text/html",
    },
]

def check_endpoints():
    print("==> [2/5] Checking HTTP ingress endpoints...")
    all_ok = True

    for ep in ENDPOINTS:
        name = ep["name"]
        url = ep["url"]
        expected_code = ep["expected_status"]
        ct_expected = ep["content_type_contains"]
        req = urllib.request.Request(url, headers={"User-Agent": "GrommunioTest/1.0"})

        status = None
        headers = {}
        body = b""
        last_error = None

        for attempt in range(5):
            try:
                with urllib.request.urlopen(req) as resp:
                    status = resp.status
                    headers = resp.headers
                    body = resp.read()
                    break
            except urllib.error.HTTPError as err:
                status = err.code
                headers = err.headers
                body = err.read()
                if status in (502, 503) and attempt < 4:
                    time.sleep(1)
                    continue
                break
            except Exception as ex:
                last_error = ex
                if attempt < 4:
                    time.sleep(1)
                    continue
                break

        if status is None:
            print(f"    [-] {name:<30}: Connection error -> {last_error}", file=sys.stderr)
            all_ok = False
            continue

        ct_actual = headers.get("Content-Type", "")
        code_match = (status == expected_code)
        ct_match = (ct_expected in ct_actual)
        json_ok = True

        if ep.get("validate_json"):
            try:
                json.loads(body.decode("utf-8"))
            except Exception:
                json_ok = False

        header_match = True
        if ep.get("expected_header"):
            h_key, h_val = ep["expected_header"]
            header_match = (headers.get(h_key) == h_val)

        if code_match and ct_match and json_ok and header_match:
            print(f"    [+] {name:<30}: HTTP {status} OK ({ct_actual.split(';')[0]})")
        else:
            print(f"    [-] {name:<30}: FAILED (Got HTTP {status}, CT: {ct_actual})", file=sys.stderr)
            all_ok = False

    return all_ok

if __name__ == "__main__":
    if check_endpoints():
        print("[+] SUCCESS: All ingress endpoints verified.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: One or more endpoint checks failed.\n", file=sys.stderr)
        sys.exit(1)
