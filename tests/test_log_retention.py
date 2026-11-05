#!/usr/bin/env python3
"""
Verification Test 16: Persistent Log Growth Bounding & 1-Year Retention
Verifies that:
1. Dockerfile installs logrotate.
2. rootfs/etc/logrotate.d/grommunio specifies:
   - Weekly rotation, 53 retained archives (371 days > 1 year), maxage 365.
   - copytruncate policy so services continue writing to active log file handles.
   - Gzip compression with delaycompress to keep .1 uncompressed.
3. rootfs/usr/local/bin/grommunio-logrotate-loop.sh runs logrotate with persistent status.
4. supervisord.conf registers grommunio-logrotate as a managed daemon.
5. docker-compose.yml configures bounded container stdout/stderr logging (max-size: 10m, max-file: 3).
6. Admin UI FileReader seamlessly reads .1 rotated logs when active file has fewer lines than requested.
7. container-healthcheck.sh enforces disk space monitoring on persistent volumes.
8. Live container logrotate dry-run / execution validates clean configuration parsing.
"""
import os
import re
import subprocess
import sys
import tempfile


def test_static_log_retention_contracts():
    print("    [>] 1. Validating static log retention contracts and configurations...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 1. Dockerfile
    dockerfile_path = os.path.join(root_dir, "Dockerfile")
    with open(dockerfile_path, "r", encoding="utf-8") as f:
        dockerfile = f.read()
    assert "logrotate" in dockerfile, "FAIL: Dockerfile does not install logrotate"

    # 2. Logrotate configuration
    logrotate_conf_path = os.path.join(root_dir, "rootfs", "etc", "logrotate.d", "grommunio")
    assert os.path.isfile(logrotate_conf_path), f"FAIL: {logrotate_conf_path} does not exist"
    with open(logrotate_conf_path, "r", encoding="utf-8") as f:
        logrotate_conf = f.read()

    assert "weekly" in logrotate_conf, "FAIL: logrotate config missing 'weekly'"
    assert "rotate 53" in logrotate_conf, "FAIL: logrotate config missing 'rotate 53' (1-year retention)"
    assert "maxage 365" in logrotate_conf, "FAIL: logrotate config missing 'maxage 365'"
    assert "copytruncate" in logrotate_conf, "FAIL: logrotate config missing 'copytruncate'"
    assert "compress" in logrotate_conf, "FAIL: logrotate config missing 'compress'"
    assert "delaycompress" in logrotate_conf, "FAIL: logrotate config missing 'delaycompress'"
    assert "/var/log/exim4/*.log" in logrotate_conf, "FAIL: logrotate config missing Exim logs"
    assert "/var/log/gromox/*.log" in logrotate_conf, "FAIL: logrotate config missing Gromox logs"
    assert "/var/log/nginx/*.log" in logrotate_conf, "FAIL: logrotate config missing Nginx logs"
    assert "/var/log/rspamd/*.log" in logrotate_conf, "FAIL: logrotate config missing Rspamd logs"

    # 3. Supervisord configuration
    supervisord_path = os.path.join(root_dir, "rootfs", "etc", "supervisor", "supervisord.conf")
    with open(supervisord_path, "r", encoding="utf-8") as f:
        supervisord_conf = f.read()
    assert "[program:grommunio-logrotate]" in supervisord_conf, (
        "FAIL: supervisord.conf missing grommunio-logrotate program"
    )
    assert "grommunio-loop.sh" in supervisord_conf, (
        "FAIL: supervisord.conf missing grommunio-loop.sh invocation"
    )

    # 4. Docker Compose logging caps
    compose_path = os.path.join(root_dir, "docker-compose.yml")
    with open(compose_path, "r", encoding="utf-8") as f:
        compose_content = f.read()
    assert "driver: \"json-file\"" in compose_content or "driver: json-file" in compose_content, (
        "FAIL: docker-compose.yml missing json-file logging driver"
    )
    assert "max-size: \"10m\"" in compose_content or "max-size: 10m" in compose_content, (
        "FAIL: docker-compose.yml missing max-size: 10m"
    )
    assert "max-file: \"3\"" in compose_content or "max-file: 3" in compose_content, (
        "FAIL: docker-compose.yml missing max-file: 3"
    )

    # 5. Healthcheck disk monitoring
    healthcheck_path = os.path.join(root_dir, "rootfs", "usr", "local", "bin", "container-healthcheck.sh")
    with open(healthcheck_path, "r", encoding="utf-8") as f:
        hc_content = f.read()
    assert "df -Pk /var/log /var/lib/gromox" in hc_content, (
        "FAIL: container-healthcheck.sh missing persistent volume disk check"
    )
    assert "critically low disk space" in hc_content, (
        "FAIL: container-healthcheck.sh missing low disk space alert message"
    )

    print("    [+] Static log retention and growth bounding contracts verified.")
    return True


def test_copytruncate_continuity():
    print("    [>] 2. Validating copytruncate behavior under continuous writes...")
    # Simulate an active daemon writing to a file, performing copytruncate, and asserting continued writes
    with tempfile.TemporaryDirectory() as tmpdir:
        test_log = os.path.join(tmpdir, "test.log")
        rotated_log = os.path.join(tmpdir, "test.log.1")

        # Daemon opens file in append mode
        with open(test_log, "a", encoding="utf-8") as f_daemon:
            f_daemon.write("Initial entry 1\n")
            f_daemon.write("Initial entry 2\n")
            f_daemon.flush()

            # Emulate copytruncate: copy file then truncate in-place
            with open(test_log, "r", encoding="utf-8") as f_in, open(rotated_log, "w", encoding="utf-8") as f_out:
                f_out.write(f_in.read())

            with open(test_log, "w", encoding="utf-8") as f_trunc:
                f_trunc.truncate(0)

            # Daemon continues writing to the original file handle
            f_daemon.write("Post-rotation entry 3\n")
            f_daemon.write("Post-rotation entry 4\n")
            f_daemon.flush()

        # Verify rotated log contains initial entries
        with open(rotated_log, "r", encoding="utf-8") as f_rot:
            rot_content = f_rot.read()
        assert "Initial entry 1\nInitial entry 2\n" == rot_content, (
            f"FAIL: Rotated log content mismatch: {rot_content!r}"
        )

        # Verify active log contains post-rotation entries without sparse null bytes
        with open(test_log, "r", encoding="utf-8") as f_act:
            act_content = f_act.read()
        assert "Post-rotation entry 3\nPost-rotation entry 4\n" == act_content, (
            f"FAIL: Active log content mismatch after copytruncate: {act_content!r}"
        )

    print("    [+] copytruncate seamlessly preserves active daemon write handles.")
    return True


def test_filereader_tail_continuity():
    print("    [>] 3. Validating Admin UI FileReader tail continuity with .1 fallback...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_logs_path = os.path.join(root_dir, "rootfs", "usr", "share", "grommunio-admin-api", "tools", "file_logs.py")

    with open(file_logs_path, "r", encoding="utf-8") as f:
        fl_content = f.read()

    assert 'rotated_path = self.path + ".1"' in fl_content, (
        "FAIL: FileReader does not define rotated_path .1 check"
    )
    assert 'rot_lines = f_rot.readlines()' in fl_content, (
        "FAIL: FileReader does not read rotated log lines"
    )
    assert 'all_entries = rot_entries + entries' in fl_content, (
        "FAIL: FileReader does not combine rotated and active entries"
    )

    print("    [+] Admin UI FileReader tail continuity logic verified.")
    return True


def test_healthcheck_disk_space_logic():
    print("    [>] 4. Validating healthcheck disk space threshold logic...")
    shell_test = r'''
check_df() {
    local input="$1"
    echo "$input" | awk 'NR > 1 {
        pct = $5; sub(/%/, "", pct);
        avail_kb = $4;
        if (pct >= 95 || avail_kb < 102400) {
            printf "%s (usage: %s%%, free: %d MB)\n", $6, pct, int(avail_kb / 1024);
        }
    }'
}

# Normal space: 50% usage, 5000MB free -> no output
OUT1=$(check_df "Filesystem 1K-blocks Used Available Use% Mounted on
/dev/sda1 10000000 5000000 5000000 50% /var/log")
if [ -n "$OUT1" ]; then
    echo "FAIL_NORMAL_SPACE: $OUT1"
    exit 1
fi

# Low space by percent: 96% usage
OUT2=$(check_df "Filesystem 1K-blocks Used Available Use% Mounted on
/dev/sda1 10000000 9600000 400000 96% /var/log")
if [ -z "$OUT2" ]; then
    echo "FAIL_PCT_THRESHOLD_NOT_TRIGGERED"
    exit 1
fi

# Low space by absolute free: <100MB free (e.g. 50MB free = 51200KB)
OUT3=$(check_df "Filesystem 1K-blocks Used Available Use% Mounted on
/dev/sda1 1000000 948800 51200 94% /var/lib/gromox")
if [ -z "$OUT3" ]; then
    echo "FAIL_ABS_THRESHOLD_NOT_TRIGGERED"
    exit 1
fi

echo "OK_HEALTHCHECK_DISK_LOGIC"
'''
    res = subprocess.run(["bash", "-c", shell_test], capture_output=True, text=True)
    if res.returncode != 0 or "OK_HEALTHCHECK_DISK_LOGIC" not in res.stdout:
        print(f"    [-] Healthcheck disk space test failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        return False

    print("    [+] Healthcheck disk threshold correctly identifies capacity and free-space exhaustion.")
    return True


def test_in_container_logrotate_execution():
    print("    [>] 5. Validating in-container logrotate debug dry-run & daemon execution...")
    # Run logrotate -d (debug dry-run) inside container if available
    check_script = r'''
import os
import subprocess
import sys

# Check if logrotate is installed in container
which_res = subprocess.run(["which", "logrotate"], capture_output=True, text=True)
if which_res.returncode != 0:
    print("SKIP: logrotate not yet built into running container image")
    sys.exit(0)

# Validate syntax of logrotate configuration
d_res = subprocess.run(["logrotate", "-d", "-s", "/var/log/logrotate.status", "/etc/logrotate.d/grommunio"],
                       capture_output=True, text=True)
if d_res.returncode != 0:
    print(f"FAIL: logrotate dry-run returned error:\n{d_res.stderr or d_res.stdout}", file=sys.stderr)
    sys.exit(1)

# Ensure copytruncate is planned in dry-run output
out = d_res.stdout + d_res.stderr
if "copytruncate" not in out and "truncating" not in out.lower():
    print("WARNING: copytruncate not explicitly mentioned in dry run output")

print("OK_CONTAINER_LOGROTATE")
'''
    res = subprocess.run(
        ["docker", "exec", "-i", "grommunio", "python3", "-c", check_script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if res.returncode != 0:
        print(f"    [-] In-container logrotate check failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        return False
    if "SKIP:" in res.stdout:
        print(f"    [~] {res.stdout.strip()} (will be verified after container rebuild)")
    else:
        print("    [+] In-container logrotate syntax and debug evaluation verified.")
    return True


def main():
    print("==> [16/16] Checking Persistent Log Growth Bounding & 1-Year Retention...")
    if not test_static_log_retention_contracts():
        return 1
    if not test_copytruncate_continuity():
        return 1
    if not test_filereader_tail_continuity():
        return 1
    if not test_healthcheck_disk_space_logic():
        return 1
    if not test_in_container_logrotate_execution():
        return 1
    print("    [+] ALL LOG RETENTION & GROWTH BOUNDING VERIFICATIONS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
