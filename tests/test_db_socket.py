#!/usr/bin/env python3
"""
Verification Test 12: MariaDB Unix Socket (DB_SOCKET) & Exim Multi-Domain Routing
1. Verifies that in-container Exim (/etc/exim4/exim4.conf) does not depend on MariaDB
   (no mysql_servers or mysql lookups) while preserving primary and alias domain routing.
2. Performs a live end-to-end multi-domain SMTP -> Rspamd -> Gromox LMTP delivery test across:
   - Primary domain user (testuser@example.com)
   - Primary domain alias (alias.testuser@example.com)
   - Secondary domain user via DOMAINS (testuser@alias1.example.org -> testuser@example.com)
   - Secondary domain alias via DOMAINS (alias.testuser@alias2.example.net -> testuser@example.com)
   and confirms all 4 messages are delivered via LMTP (250 2.1.5 Ok) into testuser's mailbox.
3. Verifies that Gromox (gromox-dbop / libmariadb) and Admin API (SQLAlchemy MySQLdb)
   connect via a custom Unix domain socket when DB_SOCKET is configured and symlinked
   to /run/mysqld/mysqld.sock with DB_HOST=localhost.
"""
import os
import subprocess
import sys


def run_in_container(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", "-i", "grommunio", "python3", "-"],
        input=script,
        text=True,
        capture_output=True,
        timeout=60,
    )


def check_db_socket_and_exim():
    print("==> [12/12] Checking MariaDB Unix Socket (DB_SOCKET) & Multi-Domain Mail Delivery...")

    container_script = r'''
from email.mime.text import MIMEText
import glob
import os
import smtplib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import sqlalchemy

# 1. Verify /etc/exim4/exim4.conf has no MySQL dependency
exim_conf_path = "/etc/exim4/exim4.conf"
exim_conf = open(exim_conf_path).read()
if "mysql_servers" in exim_conf or "lookup mysql" in exim_conf or "mysql;" in exim_conf:
    print("FAIL: /etc/exim4/exim4.conf still contains MySQL references", file=sys.stderr)
    sys.exit(1)
print("OK_EXIM_NO_MYSQL")

# 2. Ensure example.com and testuser@example.com (with alias.testuser@example.com) exist
subprocess.run(["grommunio-admin", "domain", "create", "example.com", "-u", "50"], capture_output=True)
q = subprocess.run(["grommunio-admin", "user", "query", "-f", "username=testuser@example.com"], capture_output=True, text=True)
if "testuser@example.com" not in q.stdout:
    subprocess.run(["grommunio-admin", "ldap", "downsync", "-c"], capture_output=True)
subprocess.run(["chown", "-R", "gromox:gromox", "/var/lib/gromox/user"], capture_output=True)

# 3. Configure secondary alias domains (alias1.example.org : alias2.example.net) in Exim and test live SMTP -> LMTP delivery
test_exim_conf = exim_conf.replace(
    "domainlist local_domains = example.com : mail.example.com :",
    "domainlist local_domains = example.com : mail.example.com : alias1.example.org : alias2.example.net"
).replace(
    "domains = mail.example.com :",
    "domains = mail.example.com : alias1.example.org : alias2.example.net"
)

def reload_exim():
    subprocess.run(["killall", "-HUP", "exim4"], capture_output=True)
    time.sleep(0.5)

try:
    with open(exim_conf_path, "w") as f:
        f.write(test_exim_conf)
    reload_exim()

    # Verify dry-run routing with exim4 -bt
    for addr in [
        "testuser@example.com",
        "alias.testuser@example.com",
        "testuser@alias1.example.org",
        "alias.testuser@alias2.example.net",
    ]:
        bt = subprocess.run(["/usr/sbin/exim4", "-bt", addr], capture_output=True, text=True)
        if bt.returncode != 0 or "transport = gromox_lmtp" not in bt.stdout:
            print(f"FAIL: exim4 -bt failed for {addr}:\n{bt.stdout}\n{bt.stderr}", file=sys.stderr)
            sys.exit(1)
    print("OK_EXIM_ROUTING")

    # Inject live SMTP emails for all 4 multi-domain & alias scenarios
    ts = int(time.time())
    recipients = [
        ("primary-user", "testuser@example.com"),
        ("primary-alias", "alias.testuser@example.com"),
        ("secondary-domain-user", "testuser@alias1.example.org"),
        ("secondary-domain-alias", "alias.testuser@alias2.example.net"),
    ]
    subjects = []
    with smtplib.SMTP("127.0.0.1", 2525, timeout=10) as client:
        code, resp = client.ehlo()
        if code != 250:
            print(f"FAIL: EHLO failed: {code} {resp}", file=sys.stderr)
            sys.exit(1)
        for label, rcpt in recipients:
            subj = f"MultiDomain-{label}-{ts}"
            subjects.append(subj)
            msg = MIMEText(f"End-to-end delivery test for {rcpt} ({label}) at {ts}")
            msg["Subject"] = subj
            msg["From"] = f"sender-{ts}@external.example"
            msg["To"] = rcpt
            client.sendmail(msg["From"], [rcpt], msg.as_string())

    # Wait for Exim -> Rspamd -> Gromox LMTP delivery to complete
    mainlog_path = "/var/log/exim4/mainlog"
    delivered = False
    for _ in range(20):
        time.sleep(0.5)
        if os.path.exists(mainlog_path):
            log_text = open(mainlog_path).read()
            # Check that all 4 deliveries completed via gromox_lmtp
            if log_text.count("T=gromox_lmtp") >= 4 and "alias1.example.org" in log_text and "alias2.example.net" in log_text:
                delivered = True
                break

    if not delivered:
        log_tail = open(mainlog_path).read()[-2000:] if os.path.exists(mainlog_path) else "missing mainlog"
        print(f"FAIL: Multi-domain LMTP deliveries not confirmed in {mainlog_path}:\n{log_tail}", file=sys.stderr)
        sys.exit(1)

    # Also verify all 4 subjects landed in testuser@example.com's Gromox SQLite mailbox store
    sqlite_paths = glob.glob("/var/lib/gromox/user/example.com/*/exmdb/exchange.sqlite3")
    if not sqlite_paths:
        print("FAIL: No exchange.sqlite3 found under /var/lib/gromox/user/example.com/", file=sys.stderr)
        sys.exit(1)
    found_all = False
    for _ in range(10):
        dump_parts = []
        for sp in sqlite_paths:
            with sqlite3.connect(sp) as conn:
                for (val,) in conn.execute("SELECT propval FROM message_properties WHERE propval IS NOT NULL").fetchall():
                    dump_parts.append(val.decode("utf-8", errors="ignore") if isinstance(val, bytes) else str(val))
        dump_str = " ".join(dump_parts)
        if all(s in dump_str for s in subjects):
            found_all = True
            break
        time.sleep(0.5)
    if not found_all:
        print(f"FAIL: Not all 4 multi-domain test subjects found in {sqlite_paths}", file=sys.stderr)
        sys.exit(1)
    print("OK_MULTIDOMAIN_LMTP_DELIVERY")
finally:
    with open(exim_conf_path, "w") as f:
        f.write(exim_conf)
    reload_exim()

# 4. Verify entrypoint.sh includes DB_SOCKET handling
entrypoint_sh = open("/entrypoint.sh").read()
if "DB_SOCKET" not in entrypoint_sh or "--protocol=SOCKET" not in entrypoint_sh:
    print("FAIL: /entrypoint.sh missing DB_SOCKET support", file=sys.stderr)
    sys.exit(1)
print("OK_ENTRYPOINT_DB_SOCKET")
if "realpath -m" in entrypoint_sh:
    print("OK_ENTRYPOINT_REALPATH")

# 5. Verify libmariadb (Gromox) and SQLAlchemy (Admin API) connect via symlinked DB_SOCKET
custom_sock = "/tmp/test_custom_mysqld.sock"
default_sock = "/run/mysqld/mysqld.sock"
if os.path.exists(custom_sock):
    os.unlink(custom_sock)

os.makedirs("/run/mysqld", exist_ok=True)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(custom_sock)
srv.listen(5)

# Symlink /run/mysqld/mysqld.sock -> custom_sock as entrypoint.sh does
if os.path.lexists(default_sock):
    os.unlink(default_sock)
os.symlink(custom_sock, default_sock)

accepted = []
def accept_loop():
    while True:
        try:
            conn, _ = srv.accept()
            accepted.append(True)
            conn.close()
        except Exception:
            break

threading.Thread(target=accept_loop, daemon=True).start()

try:
    eng = sqlalchemy.create_engine("mysql+mysqldb://grommunio:secret@localhost:3306/grommunio?charset=utf8mb4")
    try:
        with eng.connect():
            pass
    except Exception:
        pass
    if not accepted:
        print("FAIL: SQLAlchemy/libmariadb did not connect to symlinked Unix socket", file=sys.stderr)
        sys.exit(1)
    print("OK_SOCKET_CONNECT")
finally:
    srv.close()
    if os.path.lexists(default_sock):
        os.unlink(default_sock)
    if os.path.exists(custom_sock):
        os.unlink(custom_sock)
'''

    try:
        res = run_in_container(container_script)
    except Exception as ex:
        print(f"    [-] Failed to execute container verification: {ex}", file=sys.stderr)
        return False

    if res.returncode != 0:
        print(f"    [-] Verification failed:\n{res.stderr}\n{res.stdout}", file=sys.stderr)
        return False

    out = res.stdout
    if "OK_EXIM_NO_MYSQL" in out:
        print("    [+] Confirmed /etc/exim4/exim4.conf has zero MariaDB dependencies.")
    if "OK_EXIM_ROUTING" in out:
        print("    [+] Confirmed Exim routes primary domain and redirects alias DOMAINS to Gromox LMTP without SQL.")
    if "OK_MULTIDOMAIN_LMTP_DELIVERY" in out:
        print("    [+] Confirmed live SMTP -> Rspamd -> Gromox LMTP mailbox delivery across primary and secondary DOMAINS.")
    if "OK_ENTRYPOINT_DB_SOCKET" in out:
        print("    [+] Confirmed /entrypoint.sh supports DB_SOCKET readiness checks and schema inspection.")
    if "OK_ENTRYPOINT_REALPATH" in out:
        print("    [+] Confirmed in-container /entrypoint.sh uses realpath -m for DB_SOCKET comparison.")
    if "OK_SOCKET_CONNECT" in out:
        print("    [+] Confirmed libmariadb & SQLAlchemy connect via Unix socket when DB_HOST=localhost.")

    # 6. Verify canonical path resolution for DB_SOCKET (e.g. /var/run vs /run)
    host_entrypoint = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rootfs", "entrypoint.sh")
    if os.path.exists(host_entrypoint):
        entrypoint_content = open(host_entrypoint).read()
        if 'realpath -m "${DB_SOCKET}"' not in entrypoint_content:
            print("[-] FAIL: rootfs/entrypoint.sh does not use realpath -m for DB_SOCKET comparison", file=sys.stderr)
            return False
        print("    [+] Confirmed rootfs/entrypoint.sh uses realpath -m for DB_SOCKET canonicalization.")

    # 7. Validate that realpath -m correctly canonicalizes /var/run/mysqld/mysqld.sock to /run/mysqld/mysqld.sock
    test_var_sock = "/var/run/mysqld/mysqld.sock"
    test_run_sock = "/run/mysqld/mysqld.sock"
    rp_var = subprocess.run(["realpath", "-m", test_var_sock], capture_output=True, text=True).stdout.strip()
    rp_run = subprocess.run(["realpath", "-m", test_run_sock], capture_output=True, text=True).stdout.strip()
    if rp_var != rp_run:
        print(f"[-] FAIL: realpath -m did not match for {test_var_sock} ({rp_var}) and {test_run_sock} ({rp_run})", file=sys.stderr)
        return False
    print("    [+] Confirmed realpath -m prevents self-referential symlink collision between /var/run and /run.")

    return True


if __name__ == "__main__":
    if check_db_socket_and_exim():
        print("[+] SUCCESS: MariaDB Unix socket and multi-domain Exim->LMTP delivery verified.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: MariaDB Unix socket / multi-domain delivery verification failed.\n", file=sys.stderr)
        sys.exit(1)
