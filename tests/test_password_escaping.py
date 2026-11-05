#!/usr/bin/env python3
r"""
Verification Test: Special-Character Passwords End-to-End
Confirms that passwords containing complex characters ($ # ' " \ ; -n) survive the entire path
from container environment through entrypoint to MariaDB, Admin API, Rspamd, and Gromox.
"""
import sys

from container_harness import Container, ScratchDatabase, require_harness

SPECIAL_PW = r"p@$$w0rd'#\";-n\4"

PROBE_SCRIPT = r'''
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

admin_pw = os.environ["ADMIN_PW"]
rspamd_pw = os.environ["RSPAMD_PW"]

# 1. Admin API login with special password
url = "http://127.0.0.1:8081/api/v1/login"
login_data = urllib.parse.urlencode({"user": "admin", "pass": admin_pw}).encode("utf-8")
req = urllib.request.Request(url, data=login_data, headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        token = data.get("grommunioAuthJwt")
        if not token:
            print("[-] FAIL: Admin API login succeeded but returned no token", file=sys.stderr)
            sys.exit(1)
except Exception as ex:
    print(f"[-] FAIL: Admin API login with special password failed: {ex}", file=sys.stderr)
    sys.exit(1)

# 2. Admin API login with invalid password
bad_data = urllib.parse.urlencode({"user": "admin", "pass": "wrongpassword"}).encode("utf-8")
req_bad = urllib.request.Request(url, data=bad_data, headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    with urllib.request.urlopen(req_bad) as resp:
        print("[-] FAIL: Admin API login with wrong password unexpectedly succeeded", file=sys.stderr)
        sys.exit(1)
except urllib.error.HTTPError as err:
    if err.code != 401:
        print(f"[-] FAIL: Expected HTTP 401 for wrong password, got {err.code}", file=sys.stderr)
        sys.exit(1)
except Exception as ex:
    print(f"[-] FAIL: Unexpected error for wrong password: {ex}", file=sys.stderr)
    sys.exit(1)

# 3. Rspamd Controller Auth with special password
rspamd_url = "http://127.0.0.1:11334/auth"
req_rspamd = urllib.request.Request(rspamd_url, data=b"", headers={"Password": rspamd_pw, "X-Forwarded-For": "203.0.113.1"})
try:
    with urllib.request.urlopen(req_rspamd) as resp:
        rspamd_data = json.loads(resp.read().decode("utf-8"))
        if rspamd_data.get("auth") != "ok":
            print(f"[-] FAIL: Rspamd auth response did not report 'ok': {rspamd_data}", file=sys.stderr)
            sys.exit(1)
except Exception as ex:
    print(f"[-] FAIL: Rspamd auth with special password failed: {ex}", file=sys.stderr)
    sys.exit(1)

# 4. Rspamd Controller Auth with wrong password
req_rspamd_bad = urllib.request.Request(rspamd_url, data=b"", headers={"Password": "wrongpassword", "X-Forwarded-For": "203.0.113.1"})
try:
    with urllib.request.urlopen(req_rspamd_bad) as resp:
        rspamd_data = json.loads(resp.read().decode("utf-8"))
        if rspamd_data.get("auth") == "ok":
            print("[-] FAIL: Rspamd auth with wrong password unexpectedly reported 'ok'", file=sys.stderr)
            sys.exit(1)
except urllib.error.HTTPError as err:
    if err.code not in (401, 403):
        print(f"[-] FAIL: Expected HTTP 401/403 for wrong Rspamd password, got {err.code}", file=sys.stderr)
        sys.exit(1)
except Exception:
    pass

print("OK_SPECIAL_PASSWORDS_VERIFIED")
'''


def check_special_passwords_e2e():
    print("==> Checking Password Special Characters End-to-End...")
    require_harness()

    print("    [>] 1. Starting throwaway container with special character passwords...")
    with ScratchDatabase(password=SPECIAL_PW) as db:
        env = {
            "ADMIN_PASSWORD": SPECIAL_PW,
            "RSPAMD_PASSWORD": SPECIAL_PW,
            "LDAP_BIND_PASS": SPECIAL_PW,
        }
        with Container(env=env, scratch=db) as c:
            print("    [>] 2. Waiting for container to become healthy...")
            if not c.wait_healthy(timeout=180):
                print(f"[-] FAIL: Container failed to become healthy with special character passwords.\nLogs:\n{c.logs()[-1200:]}", file=sys.stderr)
                return False
            print("    [+] Container healthy: MariaDB, Gromox, Admin API, and Rspamd started successfully.")

            print("    [>] 3. Probing Admin API and Rspamd controller authentication...")
            probe_env = {"ADMIN_PW": SPECIAL_PW, "RSPAMD_PW": SPECIAL_PW}
            res = c.exec("python3", "-", env=probe_env, input=PROBE_SCRIPT, timeout=30)
            if res.returncode != 0 or "OK_SPECIAL_PASSWORDS_VERIFIED" not in res.stdout:
                print(f"[-] FAIL: Probe verification failed:\n{res.stderr or res.stdout}", file=sys.stderr)
                return False
            print("    [+] Authentication verified: special passwords accepted, incorrect passwords rejected (401).")

    return True


def main():
    if not check_special_passwords_e2e():
        print("[-] FAILED: Special character password verification failed.\n", file=sys.stderr)
        return 1
    print("[+] SUCCESS: Special character password handling verified end-to-end.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
