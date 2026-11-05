#!/usr/bin/env python3
"""
Verification Test: Container Healthcheck Behavior & Diagnostics
Verifies that rootfs/usr/local/bin/container-healthcheck.sh correctly reports service
and storage health:
1. Static contract verification:
   - supervisorctl status assignment uses '|| true' to avoid premature abort under set -e.
   - Persistent storage check performs numeric comparison (pct + 0 >= 95) for awk string safety.
2. Healthy container pass:
   - Probes live container healthcheck and confirms exit code 0.
3. Stopped service diagnostic reporting:
   - Simulates a stopped supervisor daemon, verifying exit code 1 and explicit diagnostic output
     naming the non-running service.
4. Storage threshold reporting:
   - Simulates 100% disk usage with ample free space via a temporary df stub, verifying exit code 1
     and diagnostic output.
"""
import os
import subprocess
import sys


def test_static_contracts():
    print("    [>] 1. Validating static contracts in container-healthcheck.sh...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    healthcheck_path = os.path.join(root_dir, "rootfs", "usr", "local", "bin", "container-healthcheck.sh")

    with open(healthcheck_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "supervisorctl status 2>/dev/null || true" in content, (
        "FAIL: container-healthcheck.sh missing '|| true' on supervisorctl status assignment"
    )
    assert "pct + 0 >= 95" in content, (
        "FAIL: container-healthcheck.sh missing numeric comparison 'pct + 0 >= 95'"
    )
    assert "curl -fs -o /dev/null" in content, (
        "FAIL: container-healthcheck.sh missing 'curl -fs -o /dev/null' HTTP probe pattern"
    )
    print("    [+] Static contracts verified.")


def test_healthy_container():
    print("    [>] 2. Verifying healthy container passes healthcheck...")
    cmd = ["docker", "compose", "exec", "-T", "grommunio", "/usr/local/bin/container-healthcheck.sh"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[-] FAIL: Healthcheck failed unexpectedly on healthy container:\n{proc.stderr}", file=sys.stderr)
        sys.exit(1)
    print("    [+] Healthy container check passed (exit 0).")


def test_stopped_service():
    print("    [>] 3. Verifying stopped service fails healthcheck with diagnostic output...")
    # Stop a service temporarily, test healthcheck, then restart service
    test_svc = "gromox-midb"
    shell_cmd = (
        f"supervisorctl stop {test_svc} >/dev/null 2>&1 && "
        "/usr/local/bin/container-healthcheck.sh; RC=$?; "
        f"supervisorctl start {test_svc} >/dev/null 2>&1; "
        "exit $RC"
    )
    cmd = ["docker", "compose", "exec", "-T", "grommunio", "sh", "-c", shell_cmd]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode == 0:
        print("[-] FAIL: Healthcheck passed despite stopped service!", file=sys.stderr)
        sys.exit(1)

    combined_output = proc.stdout + proc.stderr
    if "HEALTHCHECK FAILED: services not in RUNNING state:" not in combined_output:
        print(f"[-] FAIL: Missing diagnostic header in output:\n{combined_output}", file=sys.stderr)
        sys.exit(1)

    if test_svc not in combined_output:
        print(f"[-] FAIL: Missing stopped service '{test_svc}' in output:\n{combined_output}", file=sys.stderr)
        sys.exit(1)

    print("    [+] Stopped service correctly failed healthcheck with service diagnostic.")


def test_simulated_full_disk():
    print("    [>] 4. Verifying simulated 100% full volume fails healthcheck...")
    stub_dir = "/var/log/healthcheck-test-bin"
    shell_cmd = (
        f"mkdir -p {stub_dir} && "
        f"printf '#!/bin/sh\\ncat << \"DFEOF\"\\nFilesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/sda1 1000000000 1000000000 200000000 100%% /var/log\\n/dev/sda2 1000000000 10000000 990000000 10%% /var/lib/gromox\\nDFEOF\\n' > {stub_dir}/df && "
        f"chmod +x {stub_dir}/df && "
        f"PATH={stub_dir}:$PATH /usr/local/bin/container-healthcheck.sh; RC=$?; "
        f"rm -rf {stub_dir}; exit $RC"
    )
    cmd = ["docker", "compose", "exec", "-T", "grommunio", "sh", "-c", shell_cmd]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode == 0:
        print("[-] FAIL: Healthcheck passed despite simulated 100% full volume!", file=sys.stderr)
        sys.exit(1)

    combined_output = proc.stdout + proc.stderr
    if "critically low disk space" not in combined_output or "100%" not in combined_output:
        print(f"[-] FAIL: Missing low disk space diagnostic:\n{combined_output}", file=sys.stderr)
        sys.exit(1)

    print("    [+] 100% full volume correctly flagged by healthcheck.")


if __name__ == "__main__":
    print("==> Checking Container Healthcheck Behavior & Diagnostics...")
    test_static_contracts()
    test_healthy_container()
    test_stopped_service()
    test_simulated_full_disk()
    print("[+] SUCCESS: Container healthcheck behavior fully verified.\n")
    sys.exit(0)
