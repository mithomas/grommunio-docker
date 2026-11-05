#!/usr/bin/env python3
"""
Throwaway-container harness: starts the shipped image with chosen environment against a
disposable database on the compose MariaDB (requires `docker compose --profile test up -d`).
No ports are published and the live stack is never touched.
"""
import os
import secrets
import subprocess
import sys
from collections import namedtuple

from env_helper import SKIP_EXIT, load_env_secret, wait_until

DB_CONTAINER = "grommunio-db"

# Mirrors the tmpfs layout in docker-compose.yml; persistent volumes become tmpfs so runs leave nothing behind.
TMPFS = [
    "/run:rw,noexec,nosuid,size=64M",
    "/tmp:rw,size=128M",
    "/var/tmp:rw,size=64M",
    "/var/lib/nginx:rw,size=64M",
    "/var/lib/grommunio-web:rw,size=32M",
    "/var/lib/php/sessions:rw,size=16M",
    "/etc/exim4:rw,size=8M",
    "/etc/gromox:rw,size=16M",
    "/etc/grommunio-admin-api:rw,size=16M",
    "/etc/grommunio-admin-common:rw,size=4M",
    "/var/lib/gromox:rw,size=64M",
    "/var/lib/rspamd:rw,size=64M",
    "/var/lib/redis:rw,size=32M",
    "/var/lib/grommunio-admin-api:rw,size=16M",
    "/var/spool/exim4:rw,size=32M",
    "/var/log:rw,size=64M",
]

StartupResult = namedtuple("StartupResult", "exit_code output")


def _docker(*args, timeout=60, env=None):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, env=env)


def require_harness():
    """Exits with SKIP_EXIT when the compose MariaDB the harness depends on is not running."""
    res = _docker("inspect", "-f", "{{.State.Running}}", DB_CONTAINER)
    if res.returncode != 0 or res.stdout.strip() != "true":
        print(f"[~] Harness unavailable: container '{DB_CONTAINER}' is not running "
              "(docker compose --profile test up -d).", file=sys.stderr)
        sys.exit(SKIP_EXIT)


def _network() -> str:
    res = _docker("inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", DB_CONTAINER)
    names = res.stdout.split()
    if res.returncode != 0 or not names:
        raise RuntimeError(f"Cannot determine the network of {DB_CONTAINER}: {res.stderr}")
    return names[0]


class ScratchDatabase:
    """A disposable database plus a dedicated user, created and dropped on the compose MariaDB."""

    def __init__(self):
        suffix = secrets.token_hex(4)
        self.name = f"gtest{suffix}"
        self.user = f"gtest{suffix}"
        self.password = secrets.token_urlsafe(18)

    def sql(self, statement: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "MYSQL_PWD": load_env_secret("DB_ROOT_PASSWORD", "root_secret")}
        return _docker("exec", "-e", "MYSQL_PWD", DB_CONTAINER, "mariadb", "-uroot", "-e", statement, env=env)

    def __enter__(self):
        res = self.sql(
            f"CREATE DATABASE `{self.name}`; "
            f"CREATE USER '{self.user}'@'%' IDENTIFIED BY '{self.password}'; "
            f"GRANT ALL PRIVILEGES ON `{self.name}`.* TO '{self.user}'@'%';"
        )
        if res.returncode != 0:
            raise RuntimeError(f"Could not create scratch database: {res.stderr}")
        return self

    def __exit__(self, *exc):
        self.sql(f"DROP DATABASE IF EXISTS `{self.name}`; DROP USER IF EXISTS '{self.user}'@'%';")

    def table_count(self) -> int:
        res = self.sql(
            "SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_schema = '{self.name}';"
        )
        return int(res.stdout.split()[-1])


class Container:
    """One detached run of the image; always removed on exit."""

    def __init__(self, env=None, scratch=None):
        self.name = f"grommunio-t-{secrets.token_hex(4)}"
        base = {"LDAP_ENABLED": "false"}
        if scratch is not None:
            base.update(DB_NAME=scratch.name, DB_USER=scratch.user, DB_PASSWORD=scratch.password)
        else:
            base["DB_HOST"] = "db.invalid"
        # Non-default credentials so the secure-password gate passes unless a test overrides them.
        base.update(ADMIN_PASSWORD=secrets.token_urlsafe(12), RSPAMD_PASSWORD=secrets.token_urlsafe(12),
                    LDAP_BIND_PASS=secrets.token_urlsafe(12))
        base.update(env or {})
        self.env = base
        # A throwaway run must never initialise (or reset the admin password of) the live database.
        reaches_live = base.get("DB_HOST", "mariadb") in ("mariadb", DB_CONTAINER) and not base.get("DB_SOCKET")
        if reaches_live and base.get("DB_NAME", "grommunio") == load_env_secret("DB_NAME", "grommunio"):
            raise ValueError("Refusing to run against the live database; pass a ScratchDatabase")

    def start(self):
        cmd = ["run", "-d", "--name", self.name, "--read-only", "--network", _network(),
               "--security-opt", "no-new-privileges:true"]
        for mount in TMPFS:
            cmd += ["--tmpfs", mount]
        for key, value in self.env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd.append(load_env_secret("IMAGE_NAME", "grommunio:local"))
        res = _docker(*cmd)
        if res.returncode != 0:
            raise RuntimeError(f"docker run failed: {res.stderr}")
        return self

    def logs(self) -> str:
        res = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True, timeout=30)
        return res.stdout + res.stderr

    def exit_code(self):
        """Exit code, or None while the container is still running."""
        res = _docker("inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", self.name)
        running, code = res.stdout.split()
        return None if running == "true" else int(code)

    def wait_exit(self, timeout=60.0):
        """Exit code once the container has stopped, or None if it is still running at the deadline."""
        wait_until(lambda: self.exit_code() is not None, timeout, 0.5)
        return self.exit_code()

    def wait_for_log(self, text: str, timeout=60.0) -> bool:
        return bool(wait_until(lambda: text in self.logs(), timeout, 1.0))

    def stop(self, grace=30):
        """Sends SIGTERM via docker stop and returns the exit code."""
        _docker("stop", "-t", str(grace), self.name, timeout=grace + 30)
        return self.exit_code()

    def exec(self, *args, timeout=60) -> subprocess.CompletedProcess:
        return _docker("exec", self.name, *args, timeout=timeout)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        _docker("rm", "-fv", self.name)


def run_startup(env=None, timeout=60, scratch=None) -> StartupResult:
    """Runs the image until it exits (or the timeout, in which case exit_code is None) and returns its output."""
    with Container(env, scratch) as container:
        code = container.wait_exit(timeout)
        output = container.logs()
        return StartupResult(code, output)
