#!/usr/bin/env python3
"""
Verification Test: MariaDB Unix Socket (DB_SOCKET)
Runs the shipped image against a MariaDB that is reachable only through a Unix socket in a shared volume,
with DB_SOCKET pointing at a non-default path, and checks that:
1. The entrypoint connects through the socket and links it where libmariadb looks for it.
2. Gromox (libmariadb) and the Admin API (SQLAlchemy) both work through that socket.
"""
import sys

from container_harness import Container, SocketDatabase, require_harness

SOCKET_PATH = "/sock/mysqld.sock"
DEFAULT_SOCKET = "/run/mysqld/mysqld.sock"


def check_db_socket():
    print("==> Checking MariaDB Unix Socket (DB_SOCKET) with a custom socket path...")
    with SocketDatabase() as sdb:
        env = {"DB_SOCKET": SOCKET_PATH, "DB_NAME": sdb.db, "DB_USER": sdb.user, "DB_PASSWORD": sdb.password}
        with Container(env=env, volumes=[sdb.mount("/sock")]) as c:
            if not c.wait_healthy(240):
                print(f"    [-] Container did not become healthy:\n{c.logs()[-1200:]}", file=sys.stderr)
                return False
            logs = c.logs()
            if f"via Unix socket {SOCKET_PATH}" not in logs or "Database reachable" not in logs:
                print(f"    [-] Entrypoint did not connect through the socket:\n{logs[-800:]}", file=sys.stderr)
                return False
            print("    [+] Entrypoint connected through the Unix socket and finished startup.")

            link = c.exec("readlink", DEFAULT_SOCKET).stdout.strip()
            if link != SOCKET_PATH:
                print(f"    [-] {DEFAULT_SOCKET} points to '{link}', expected {SOCKET_PATH}", file=sys.stderr)
                return False
            print(f"    [+] {DEFAULT_SOCKET} links to the configured socket.")

            dbop = c.exec("gromox-dbop", "-U")
            if dbop.returncode != 0:
                print(f"    [-] Gromox could not use the socket: {dbop.stderr or dbop.stdout}", file=sys.stderr)
                return False
            users = c.exec("grommunio-admin", "user", "query")
            if users.returncode != 0 or "admin" not in users.stdout:
                print(f"    [-] Admin API could not use the socket: {users.stderr or users.stdout}", file=sys.stderr)
                return False
            print("    [+] Gromox (libmariadb) and the Admin API (SQLAlchemy) work through the socket.")
    return True


def main():
    require_harness()
    if check_db_socket():
        print("[+] SUCCESS: DB_SOCKET connectivity verified.\n")
        return 0
    print("[-] FAILED: DB_SOCKET verification failed.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
