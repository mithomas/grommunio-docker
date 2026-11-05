#!/usr/bin/env python3
"""
Verification Test: Container Startup Safety
Starts the shipped image in throwaway containers against a scratch database and checks:
1. Default credentials are refused (exit 1) unless ENFORCE_SECURE_PASSWORDS=false.
2. An unreachable database ends startup with an actionable error and no schema operation.
3. SIGTERM while waiting for the schema lock exits 143 and creates nothing.
4. A restart on an initialised database skips schema creation and leaves the tables untouched.
"""
import os
import subprocess
import sys

from container_harness import DB_CONTAINER, Container, ScratchDatabase, require_harness, run_startup
from env_helper import load_env_secret


def check(condition, message):
    if not condition:
        print(f"    [-] {message}", file=sys.stderr)
    return bool(condition)


def test_default_credentials():
    print("    [>] 1. Default credentials are refused unless explicitly allowed...")
    result = run_startup(env={"ADMIN_PASSWORD": "admin"}, timeout=60)
    if not (check(result.exit_code == 1, f"expected exit 1, got {result.exit_code}")
            and check("ADMIN_PASSWORD" in result.output and "Refusing to start" in result.output,
                      f"no refusal naming ADMIN_PASSWORD:\n{result.output[-600:]}")):
        return False

    with Container(env={"ADMIN_PASSWORD": "admin", "ENFORCE_SECURE_PASSWORDS": "false", "DB_WAIT_RETRIES": "1"}) as c:
        if not check(c.wait_for_log("Checking database connection", 60), "opt-out did not proceed past the credential gate"):
            return False
        if not check("Refusing to start" not in c.logs(), "opt-out still refused to start"):
            return False
    print("    [+] Default credentials exit 1 naming ADMIN_PASSWORD; ENFORCE_SECURE_PASSWORDS=false proceeds with a warning.")
    return True


def test_unreachable_database():
    print("    [>] 2. Unreachable database ends startup with a diagnostic...")
    result = run_startup(env={"DB_WAIT_RETRIES": "2"}, timeout=90)
    out = result.output
    ok = (check(result.exit_code == 1, f"expected exit 1, got {result.exit_code}")
          and check("Database connection failed after 2 retries" in out and "Last error:" in out,
                    f"missing connection diagnostic:\n{out[-600:]}")
          and check("advisory lock" not in out and "schema creation" not in out,
                    "schema operations were attempted without a database"))
    if ok:
        print("    [+] Exit 1 with the last MariaDB error, before any schema operation.")
    return ok


def test_sigterm_while_waiting_for_lock():
    print("    [>] 3. SIGTERM while waiting for the schema lock exits 143...")
    env = {**os.environ, "MYSQL_PWD": load_env_secret("DB_ROOT_PASSWORD", "root_secret")}
    with ScratchDatabase() as db:
        holder = subprocess.Popen(
            ["docker", "exec", "-i", "-e", "MYSQL_PWD", DB_CONTAINER, "mariadb", "-uroot", "--unbuffered", "-sN"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
        )
        try:
            holder.stdin.write("SELECT GET_LOCK('grommunio_schema_lock', 10);\n")
            holder.stdin.flush()
            if not check(holder.stdout.readline().strip() == "1", "could not take the schema lock from a side session"):
                return False
            with Container(scratch=db) as c:
                waiting = c.wait_for_log("Acquiring MariaDB schema migration advisory lock", 90)
                if not check(waiting, "container never reached the lock wait"):
                    return False
                code = c.stop(grace=20)
                logs = c.logs()
                ok = (check(code == 143, f"expected exit 143 on SIGTERM, got {code}")
                      and check("Releasing MariaDB schema advisory lock" in logs, "lock cleanup did not run")
                      and check("Acquired schema advisory lock" not in logs and "schema creation" not in logs,
                                "startup continued after SIGTERM")
                      and check(db.table_count() == 0, "tables were created although the lock was never acquired"))
        finally:
            holder.stdin.close()
            holder.wait(timeout=10)
    if ok:
        print("    [+] Exit 143, lock cleanup ran, and nothing was created.")
    return ok


def test_restart_is_idempotent():
    print("    [>] 4. Restart on an initialised database skips schema creation...")
    with ScratchDatabase() as db:
        with Container(scratch=db) as first:
            if not check(first.wait_for_log("Starting Supervisor", 240), f"first start failed:\n{first.logs()[-800:]}"):
                return False
            created = "Initial database schema created successfully" in first.logs()
            first.stop()
        tables = db.table_count()
        if not (check(created, "first start did not create the schema") and check(tables > 0, "no tables after first start")):
            return False

        with Container(scratch=db) as second:
            if not check(second.wait_for_log("Starting Supervisor", 240), f"second start failed:\n{second.logs()[-800:]}"):
                return False
            logs = second.logs()
            ok = (check(f"already initialized ({tables} tables found)" in logs, "second start did not report an initialised database")
                  and check("Initial database schema created" not in logs, "second start re-created the schema")
                  and check(db.table_count() == tables, "table count changed on restart"))
    if ok:
        print(f"    [+] Second start reused the {tables} existing tables.")
    return ok


def main():
    require_harness()
    print("==> Checking Container Startup Safety...")
    for scenario in (test_default_credentials, test_unreachable_database,
                     test_sigterm_while_waiting_for_lock, test_restart_is_idempotent):
        if not scenario():
            print("[-] FAILED: startup safety verification failed.\n", file=sys.stderr)
            return 1
    print("[+] SUCCESS: Container startup safety verified.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
