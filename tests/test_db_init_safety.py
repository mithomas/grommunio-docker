#!/usr/bin/env python3
"""
Verification Test: Database Schema & Migration Idempotence
Confirms the live database is populated and that `gromox-dbop -U` succeeds when re-run.
"""
import re
import subprocess
import sys


def test_schema_migration_idempotence():
    print("    [>] 1. Validating database table presence and schema migration idempotence...")
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


def main():
    print("==> Checking Database Schema & Migration Idempotence...")
    if not test_schema_migration_idempotence():
        return 1
    print("    [+] ALL DATABASE VERIFICATIONS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
