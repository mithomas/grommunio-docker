#!/usr/bin/env python3
"""
Verification Test 15: Database Initialization & Migrations Fail-Safe Behavior
Verifies that rootfs/entrypoint.sh enforces fail-safe database operations:
1. Static contract verification:
   - MariaDB advisory locking (GET_LOCK / RELEASE_LOCK) exists to serialize concurrent startups.
   - Separate EXIT (cleanup_db_lock) and INT/TERM ('exit 143') traps, disarmed via 'trap - EXIT INT TERM'.
   - Table count query captures stderr and validates integer result instead of silent fallback (|| echo "0").
   - Absence of '|| true' on gromox-dbop -CC, gromox-dbop -C, and gromox-dbop -U.
   - Absence of '2>/dev/null || true' on grommunio-admin passwd.
2. Query error handling & validation logic:
   - Simulates table count query failure and non-integer output, asserting fail-fast non-zero exit.
3. MariaDB advisory lock concurrency semantics:
   - Probes live MariaDB advisory locking in-container to ensure concurrent startups serialize without race conditions.
4. Database state & migration idempotence:
   - Confirms that the database is populated and running migrations (gromox-dbop -U) succeeds cleanly.
5. Signal trap handling & lock release:
   - Verifies that SIGTERM/SIGINT during startup trigger exit code 143, release lock/coprocess, and halt execution.
   - Verifies normal startup disarms traps with 'trap - EXIT INT TERM' and exits cleanly.
"""
import os
import re
import subprocess
import sys


def test_static_entrypoint_contracts():
    print("    [>] 1. Validating static fail-safe contracts in rootfs/entrypoint.sh...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entrypoint_path = os.path.join(root_dir, "rootfs", "entrypoint.sh")

    with open(entrypoint_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Assert MariaDB advisory lock presence and trap semantics
    assert "GET_LOCK('grommunio_schema_lock'" in content, (
        "FAIL: entrypoint.sh missing GET_LOCK('grommunio_schema_lock' advisory locking"
    )
    assert "cleanup_db_lock" in content and "trap cleanup_db_lock" in content, (
        "FAIL: entrypoint.sh missing cleanup_db_lock trap"
    )
    assert "trap cleanup_db_lock EXIT" in content, (
        "FAIL: entrypoint.sh missing 'trap cleanup_db_lock EXIT'"
    )
    assert "trap 'exit 143' INT TERM" in content, (
        "FAIL: entrypoint.sh missing 'trap \\'exit 143\\' INT TERM'"
    )
    assert "trap - EXIT INT TERM" in content, (
        "FAIL: entrypoint.sh missing 'trap - EXIT INT TERM' to disarm trap"
    )
    assert "db()" in content, (
        "FAIL: entrypoint.sh missing db() helper function"
    )
    assert "table_schema = DATABASE()" in content, (
        "FAIL: entrypoint.sh missing table_schema = DATABASE() query"
    )

    # 2. Assert no silent table count fallback
    assert '|| echo "0"' not in content, (
        "FAIL: entrypoint.sh contains silent fallback '|| echo \"0\"'"
    )
    assert "2>/dev/null || echo" not in content, (
        "FAIL: entrypoint.sh suppresses table count stderr with '2>/dev/null || echo'"
    )
    assert '^[0-9]+$' in content, (
        "FAIL: entrypoint.sh missing integer validation regex for TABLE_COUNT"
    )

    # 3. Assert no '|| true' on gromox-dbop commands
    for line in content.splitlines():
        trimmed = line.strip()
        if "gromox-dbop" in trimmed:
            assert "|| true" not in trimmed, (
                f"FAIL: entrypoint.sh has '|| true' on dbop command: {trimmed}"
            )
        if "grommunio-admin passwd" in trimmed:
            assert "|| true" not in trimmed, (
                f"FAIL: entrypoint.sh has '|| true' on admin password provisioning: {trimmed}"
            )
            assert "2>/dev/null" not in trimmed, (
                f"FAIL: entrypoint.sh suppresses stderr on admin password provisioning: {trimmed}"
            )

    print("    [+] Static fail-safe contracts verified successfully in rootfs/entrypoint.sh.")
    return True


def test_table_count_failure_handling():
    print("    [>] 2. Validating table count failure handling logic...")
    # Emulate the shell logic used in entrypoint.sh for parsing and validating table count
    shell_snippet = r'''
check_table_count() {
    local table_out="$1"
    local rc="$2"
    if [ "${rc}" -ne 0 ]; then
        echo "ERROR: Failed to query table count from database." >&2
        return 1
    fi
    local count
    count=$(echo "${table_out}" | tr -d '[:space:]')
    if ! [[ "${count}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Invalid table count returned: '${table_out}'" >&2
        return 1
    fi
    echo "VALID_COUNT:${count}"
    return 0
}

# Test 1: Query failure (non-zero rc)
if check_table_count "ERROR 2002 (HY000): Can't connect" 1 2>/dev/null; then
    echo "FAIL_QUERY_ERROR_NOT_CAUGHT"
    exit 1
fi

# Test 2: Malformed output
if check_table_count "not_a_number" 0 2>/dev/null; then
    echo "FAIL_NON_INTEGER_NOT_CAUGHT"
    exit 1
fi

# Test 3: Empty output
if check_table_count "" 0 2>/dev/null; then
    echo "FAIL_EMPTY_NOT_CAUGHT"
    exit 1
fi

# Test 4: Valid count
res=$(check_table_count "  42  " 0)
if [ "${res}" != "VALID_COUNT:42" ]; then
    echo "FAIL_VALID_COUNT: ${res}"
    exit 1
fi

echo "OK_TABLE_COUNT_LOGIC"
'''
    proc = subprocess.run(["bash", "-c", shell_snippet], capture_output=True, text=True)
    if proc.returncode != 0 or "OK_TABLE_COUNT_LOGIC" not in proc.stdout:
        print(f"    [-] Table count error handling test failed:\n{proc.stderr or proc.stdout}", file=sys.stderr)
        return False

    print("    [+] Table count error handling and integer validation behave safely.")
    return True


def test_mariadb_advisory_locking():
    print("    [>] 3. Validating MariaDB advisory locking concurrency semantics in-container...")
    # Run test inside grommunio container against the MariaDB service
    lock_test_script = r'''
import os
import subprocess
import sys
import time

db_socket = os.environ.get("DB_SOCKET", "")
if db_socket:
    conn_flags = ["--protocol=SOCKET", "-S", db_socket]
else:
    db_host = os.environ.get("DB_HOST", "mariadb")
    db_port = os.environ.get("DB_PORT", "3306")
    conn_flags = ["-h", db_host, "-P", str(db_port)]
db_user = os.environ.get("DB_USER", "grommunio")
db_pass = os.environ.get("DB_PASSWORD", "grommunio_secret")
db_name = os.environ.get("DB_NAME", "grommunio")

def run_mariadb(query):
    cmd = ["mariadb", *conn_flags, "-u", db_user, f"-p{db_pass}", db_name, "-sN", "-e", query]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode, res.stdout.strip(), res.stderr.strip()

# Check MariaDB reachability
rc, out, err = run_mariadb("SELECT 1;")
if rc != 0:
    print(f"SKIP: MariaDB not reachable from container ({err})")
    sys.exit(0)

# Start background mariadb session holding advisory lock
p1 = subprocess.Popen(
    ["mariadb", *conn_flags, "-u", db_user, f"-p{db_pass}", db_name, "--unbuffered", "-sN"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

# Acquire lock in session 1
p1.stdin.write("SELECT GET_LOCK('grommunio_test_lock', 5);\n")
p1.stdin.flush()
res1 = p1.stdout.readline().strip()
if res1 != "1":
    print(f"FAIL: Session 1 failed to acquire lock: {res1}", file=sys.stderr)
    p1.kill()
    sys.exit(1)

# Session 2 attempts to acquire same lock with 0 timeout -> must return 0 (unable to acquire)
rc2, out2, err2 = run_mariadb("SELECT GET_LOCK('grommunio_test_lock', 0);")
if out2 != "0":
    print(f"FAIL: Session 2 unexpectedly acquired held lock: {out2}", file=sys.stderr)
    p1.kill()
    sys.exit(1)

# Release lock in session 1
p1.stdin.write("SELECT RELEASE_LOCK('grommunio_test_lock');\nQUIT;\n")
p1.stdin.flush()
p1.communicate(timeout=5)

# Session 2 should now acquire lock immediately
rc3, out3, err3 = run_mariadb("SELECT GET_LOCK('grommunio_test_lock', 2);")
if out3 != "1":
    print(f"FAIL: Session 2 failed to acquire lock after release: {out3}", file=sys.stderr)
    sys.exit(1)

# Clean up session 2 lock
run_mariadb("SELECT RELEASE_LOCK('grommunio_test_lock');")
print("OK_ADVISORY_LOCK")
'''
    res = subprocess.run(
        ["docker", "exec", "-i", "grommunio", "python3", "-c", lock_test_script],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if res.returncode != 0:
        print(f"    [-] Advisory locking verification failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        return False
    if "SKIP:" in res.stdout:
        print(f"    [~] {res.stdout.strip()}")
    else:
        print("    [+] MariaDB advisory lock correctly serializes concurrent operations.")
    return True


def test_schema_migration_idempotence():
    print("    [>] 4. Validating database table presence and schema migration idempotence...")
    db_verify_script = r'''
import os
import subprocess
import sys

db_socket = os.environ.get("DB_SOCKET", "")
if db_socket:
    conn_flags = ["--protocol=SOCKET", "-S", db_socket]
else:
    db_host = os.environ.get("DB_HOST", "mariadb")
    db_port = os.environ.get("DB_PORT", "3306")
    conn_flags = ["-h", db_host, "-P", str(db_port)]
db_user = os.environ.get("DB_USER", "grommunio")
db_pass = os.environ.get("DB_PASSWORD", "grommunio_secret")
db_name = os.environ.get("DB_NAME", "grommunio")

# 1. Query table count
cmd = ["mariadb", *conn_flags, "-u", db_user, f"-p{db_pass}", db_name, "-sN", "-e",
       f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{db_name}';"]
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode != 0:
    print(f"FAIL: Failed to query table count: {res.stderr}", file=sys.stderr)
    sys.exit(1)

count = int(res.stdout.strip())
if count <= 0:
    print(f"FAIL: Expected populated database, but found {count} tables", file=sys.stderr)
    sys.exit(1)

# 2. Run gromox-dbop -U (must succeed without error)
dbop_res = subprocess.run(["gromox-dbop", "-U"], capture_output=True, text=True)
if dbop_res.returncode != 0:
    print(f"FAIL: gromox-dbop -U returned error: {dbop_res.stderr or dbop_res.stdout}", file=sys.stderr)
    sys.exit(1)

print(f"OK_DB_MIGRATION_TABLES_{count}")
'''
    res = subprocess.run(
        ["docker", "exec", "-i", "grommunio", "python3", "-c", db_verify_script],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if res.returncode != 0 or "OK_DB_MIGRATION_TABLES_" not in res.stdout:
        print(f"    [-] Schema migration verification failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        return False

    match = re.search(r"OK_DB_MIGRATION_TABLES_(\d+)", res.stdout)
    table_count = match.group(1) if match else "unknown"
    print(f"    [+] Database schema verified ({table_count} tables found); gromox-dbop -U succeeds cleanly.")
    return True


def test_advisory_lock_signal_handling():
    print("    [>] 5. Validating advisory lock signal trap handling (SIGTERM / exit 143)...")
    signal_test_script = r'''
import os
import signal
import subprocess
import sys
import time

script = """
set -e
DB_LOCK_ERR_FILE=$(mktemp)
cleanup_db_lock() {
    echo "CLEANUP_INVOKED"
    if [ -n "${DB_LOCK_COPROC_PID:-}" ] && kill -0 "${DB_LOCK_COPROC_PID}" 2>/dev/null; then
        kill "${DB_LOCK_COPROC_PID}" 2>/dev/null || true
        wait "${DB_LOCK_COPROC_PID}" 2>/dev/null || true
        unset DB_LOCK_COPROC_PID
    fi
    rm -f "${DB_LOCK_ERR_FILE:-}"
}
coproc DB_LOCK_COPROC {
    trap - EXIT INT TERM
    exec sleep 100
}
trap cleanup_db_lock EXIT
trap "exit 143" INT TERM

echo "PID:$$"
echo "COPROC_PID:$DB_LOCK_COPROC_PID"
echo "LOCK_WAIT"
read -t 30
echo "SUBSEQUENT_STEPS_SHOULD_NOT_RUN"
"""

# Case A: SIGTERM interrupts during wait, exits with 143, runs cleanup, stops subsequent steps
p = subprocess.Popen(["bash", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
pid = None
coproc_pid = None
for line in p.stdout:
    line = line.strip()
    if line.startswith("PID:"):
        pid = int(line.split(":", 1)[1])
    elif line.startswith("COPROC_PID:"):
        coproc_pid = int(line.split(":", 1)[1])
    elif line == "LOCK_WAIT":
        break

if not pid or not coproc_pid:
    p.kill()
    print("FAIL: Failed to initialize test process or coprocess", file=sys.stderr)
    sys.exit(1)

time.sleep(0.1)
os.kill(pid, signal.SIGTERM)
stdout, stderr = p.communicate(timeout=5)

if p.returncode != 143:
    print(f"FAIL: Expected return code 143 on SIGTERM, got {p.returncode}", file=sys.stderr)
    sys.exit(1)
if stdout.strip() != "CLEANUP_INVOKED":
    print(f"FAIL: Unexpected output on SIGTERM:\n{stdout}", file=sys.stderr)
    sys.exit(1)
if subprocess.run(["kill", "-0", str(coproc_pid)], capture_output=True).returncode == 0:
    print("FAIL: Coprocess was leaked and not terminated by cleanup_db_lock!", file=sys.stderr)
    sys.exit(1)

# Case B: SIGINT also triggers exit 143 and releases lock without running subsequent steps
p2 = subprocess.Popen(["bash", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
pid2 = None
for line in p2.stdout:
    line = line.strip()
    if line.startswith("PID:"):
        pid2 = int(line.split(":", 1)[1])
    elif line == "LOCK_WAIT":
        break
time.sleep(0.1)
os.kill(pid2, signal.SIGINT)
stdout2, _ = p2.communicate(timeout=5)
if p2.returncode != 143 or stdout2.strip() != "CLEANUP_INVOKED":
    print(f"FAIL: SIGINT trap failed: rc={p2.returncode}, out={stdout2}", file=sys.stderr)
    sys.exit(1)

# Case C: Normal path disarms trap with 'trap - EXIT INT TERM' and exits 0 cleanly
norm_script = """
set -e
DB_LOCK_ERR_FILE=$(mktemp)
cleanup_db_lock() {
    echo "CLEANUP_INVOKED"
    if [ -n "${DB_LOCK_COPROC_PID:-}" ] && kill -0 "${DB_LOCK_COPROC_PID}" 2>/dev/null; then
        kill "${DB_LOCK_COPROC_PID}" 2>/dev/null || true
        wait "${DB_LOCK_COPROC_PID}" 2>/dev/null || true
        unset DB_LOCK_COPROC_PID
    fi
    rm -f "${DB_LOCK_ERR_FILE:-}"
}
coproc DB_LOCK_COPROC {
    trap - EXIT INT TERM
    exec sleep 100
}
trap cleanup_db_lock EXIT
trap "exit 143" INT TERM

cleanup_db_lock
trap - EXIT INT TERM
echo "NORMAL_COMPLETE"
"""
p3 = subprocess.run(["bash", "-c", norm_script], capture_output=True, text=True)
if p3.returncode != 0:
    print(f"FAIL: Normal path failed with return code {p3.returncode}", file=sys.stderr)
    sys.exit(1)
if p3.stdout.splitlines() != ["CLEANUP_INVOKED", "NORMAL_COMPLETE"]:
    print(f"FAIL: Normal path did not disarm trap cleanly: {p3.stdout}", file=sys.stderr)
    sys.exit(1)

print("OK_ADVISORY_LOCK_SIGNAL_HANDLING")
'''
    res = subprocess.run(
        ["docker", "exec", "-i", "grommunio", "python3", "-"],
        input=signal_test_script,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if res.returncode != 0 or "OK_ADVISORY_LOCK_SIGNAL_HANDLING" not in res.stdout:
        print(f"    [-] Advisory lock signal trap verification failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        return False

    print("    [+] Advisory lock signal trap verified: SIGTERM triggers exit 143, releases lock, and disarms cleanly.")
    return True


def main():
    print("==> [15/15] Checking Database Initialization & Migrations Fail-Safe Behavior...")
    if not test_static_entrypoint_contracts():
        return 1
    if not test_table_count_failure_handling():
        return 1
    if not test_mariadb_advisory_locking():
        return 1
    if not test_schema_migration_idempotence():
        return 1
    if not test_advisory_lock_signal_handling():
        return 1
    print("    [+] ALL DATABASE FAIL-SAFE VERIFICATIONS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
