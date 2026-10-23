#!/usr/bin/env python3
"""
Verification Test 1: Supervisor Services Status
Asserts that all 14 required background processes inside the Grommunio container
are in the RUNNING state.
"""
import subprocess
import sys

EXPECTED_SERVICES = {
    "exim",
    "grommunio-admin-api",
    "grommunio-bayes-sync",
    "gromox-delivery",
    "gromox-delivery-queue",
    "gromox-event",
    "gromox-http",
    "gromox-midb",
    "gromox-timer",
    "gromox-zcore",
    "nginx",
    "php-fpm",
    "redis",
    "rspamd",
}

def check_services():
    print("==> [1/5] Checking Supervisor processes inside container...")
    cmd = ["docker", "compose", "exec", "-T", "grommunio", "supervisorctl", "status"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as err:
        print(f"[-] Error querying supervisorctl: {err.stderr}", file=sys.stderr)
        return False

    running = {}
    for line in proc.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            running[parts[0]] = parts[1]

    all_ok = True
    for svc in sorted(EXPECTED_SERVICES):
        status = running.get(svc, "MISSING")
        if status == "RUNNING":
            print(f"    [+] {svc:<24}: RUNNING")
        else:
            print(f"    [-] {svc:<24}: {status}", file=sys.stderr)
            all_ok = False

    return all_ok

if __name__ == "__main__":
    if check_services():
        print("[+] SUCCESS: All 14 services are RUNNING.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: One or more services are not running.\n", file=sys.stderr)
        sys.exit(1)
