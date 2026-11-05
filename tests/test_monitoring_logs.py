#!/usr/bin/env python3
"""
Verification Test: Admin UI Monitoring Logs (Exim & Service Log Readers)
1. /system/logs lists Exim (not Postfix) and the core daemons.
2. A message injected into Exim shows up in /system/logs/Exim with schema-conformant entries.
3. The 'after' filter returns only newer entries, strictly fewer than the unfiltered tail.
4. Every core daemon log target answers without error.
"""
import re
import sys
import urllib.parse

from env_helper import admin_session, api, ensure_testuser, send_mail, wait_until

PRECTIME_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{1,6}$")
CORE_TARGETS = ["Antispam", "Gromox http", "Gromox delivery", "Admin API", "Nginx"]


def entries(session, target, query=""):
    status, body = api("GET", f"/system/logs/{urllib.parse.quote(target)}{query}", session=session)
    return status, (body or {}).get("data", [])


def check_monitoring_logs():
    print("==> Checking Admin UI Monitoring Logs (Exim & Service Log Monitoring)...")
    rcpt = ensure_testuser()
    try:
        session = admin_session()
    except RuntimeError as exc:
        print(f"    [-] {exc}", file=sys.stderr)
        return False

    status, body = api("GET", "/system/logs", session=session)
    targets = (body or {}).get("data", [])
    if status != 200 or "Exim" not in targets or "Postfix" in targets:
        print(f"    [-] Unexpected log targets (HTTP {status}): {targets}", file=sys.stderr)
        return False
    missing = [t for t in CORE_TARGETS if t not in targets]
    if missing:
        print(f"    [-] Core log targets missing: {missing}", file=sys.stderr)
        return False
    print(f"    [+] Log targets include Exim and core daemons (no Postfix): {targets}")

    subject = send_mail(rcpt, "LogCheck")
    if not wait_until(lambda: any(subject in e.get("message", "") for e in entries(session, "Exim", "?n=100")[1]), 20):
        print(f"    [-] Injected message '{subject}' never appeared in the Exim log.", file=sys.stderr)
        return False
    print("    [+] Injected message recorded in the Exim log.")

    status, exim = entries(session, "Exim", "?n=50")
    if status != 200 or not exim:
        print(f"    [-] Exim log tail returned HTTP {status} with {len(exim)} entries.", file=sys.stderr)
        return False
    for entry in exim:
        if not isinstance(entry.get("level"), int) or not isinstance(entry.get("message"), str) \
                or not PRECTIME_REGEX.match(entry.get("time", "")):
            print(f"    [-] Entry does not match the log schema: {entry}", file=sys.stderr)
            return False
    print(f"    [+] {len(exim)} Exim entries conform to the log schema.")

    pivot = exim[-3]["time"]
    status, newer = entries(session, "Exim", f"?n=50&after={urllib.parse.quote(pivot)}")
    if status != 200 or any(e["time"] <= pivot for e in newer) or len(newer) >= len(exim):
        print(f"    [-] 'after' filter returned {len(newer)} of {len(exim)} entries, "
              f"some not newer than {pivot} (HTTP {status}).", file=sys.stderr)
        return False
    print(f"    [+] 'after' filter returned {len(newer)} entries, all newer than the pivot.")

    for target in CORE_TARGETS:
        status, items = entries(session, target, "?n=5")
        if status != 200:
            print(f"    [-] Log target '{target}' returned HTTP {status}.", file=sys.stderr)
            return False
    print("    [+] All core log targets answer with HTTP 200.")
    return True


if __name__ == "__main__":
    if check_monitoring_logs():
        print("[+] SUCCESS: Admin UI monitoring logs fully verified.\n")
        sys.exit(0)
    print("[-] FAILED: Monitoring logs verification failed.\n", file=sys.stderr)
    sys.exit(1)
