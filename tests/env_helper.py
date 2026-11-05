#!/usr/bin/env python3
"""
Environment Helper for Grommunio Verification Tests
Parses environment variables and secrets from os.environ, .env.secrets, and .env
strictly complying with Docker Compose quoting and escaping rules:
- Single quotes ('...'): treated literally, preserves $, #, and ", converts \' to '
- Double quotes ("..."): strips outer quotes, converts \" to " and \\ to \
- Unquoted: strips inline comments starting with '#'
"""
import gzip
import json
import os
import re
import smtplib
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import namedtuple
from email.mime.text import MIMEText

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Exit code a test uses to report "not runnable here"; run_all.py fails these under CI.
SKIP_EXIT = 77

def parse_env_value(raw_val: str) -> str:
    """Parses a raw dotenv value string according to Docker Compose semantics."""
    val = raw_val.strip()
    if not val:
        return ""

    # Single-quoted value: literal, no variable interpolation
    if val.startswith("'") and val.endswith("'") and len(val) >= 2:
        inner = val[1:-1]
        return inner.replace(r"\'", "'")

    # Double-quoted value
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        inner = val[1:-1]
        inner = inner.replace(r'\"', '"').replace(r"\\", "\\")
        return inner

    # Unquoted value: strip trailing inline comments separated by whitespace
    val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
    return val

def load_env_secret(var_name: str, default: str = "") -> str:
    """Loads a configuration secret from environment, .env.secrets, or .env."""
    env_val = os.environ.get(var_name)
    if env_val is not None and env_val != "":
        return env_val

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fname in (".env.secrets", ".env"):
        fpath = os.path.join(root_dir, fname)
        if os.path.isfile(fpath):
            try:
                found = None
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or not line or "=" not in line:
                            continue
                        key, raw_val = line.split("=", 1)
                        if key.strip() == var_name:
                            found = parse_env_value(raw_val)
                if found is not None:
                    return found
            except OSError:
                pass

    return default


def get_http_port() -> int:
    """Returns the host-mapped HTTP port for Webmail and ActiveSync (default: 8080)."""
    return int(load_env_secret("HTTP_PORT", "8080"))


def get_admin_port() -> int:
    """Returns the host-mapped Admin UI and API port (default: 8081)."""
    return int(load_env_secret("ADMIN_PORT", "8081"))


def get_rspamd_port() -> int:
    """Returns the host-mapped dedicated Rspamd dashboard port (default: 11334)."""
    return int(load_env_secret("RSPAMD_PORT", "11334"))


def get_smtp_port() -> int:
    """Returns the host-mapped SMTP port for Exim inbound delivery (default: 2525)."""
    return int(load_env_secret("SMTP_PORT", "2525"))


def get_ldap_port() -> int:
    """Returns the host-mapped LDAP port for companion OpenLDAP (default: 389)."""
    return int(load_env_secret("LDAP_PORT", "389"))


def get_bind_ip() -> str:
    """Returns the interface IP the ports are bound to (default: 127.0.0.1)."""
    return load_env_secret("BIND_IP", "127.0.0.1")


def ldap_available(timeout: float = 2.0) -> bool:
    """True when the companion OpenLDAP port accepts connections."""
    try:
        with socket.create_connection((get_bind_ip(), get_ldap_port()), timeout=timeout):
            return True
    except OSError:
        return False


def wait_until(predicate, timeout: float = 15.0, interval: float = 0.5):
    """Polls predicate() until it returns a truthy value (returned) or the deadline passes (falsy returned)."""
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result or time.monotonic() >= deadline:
            return result
        time.sleep(interval)


AdminSession = namedtuple("AdminSession", "token csrf")


def api(method, path, body=None, session=None, form=None, timeout=10):
    """Calls the Admin API and returns (status, parsed JSON or None); HTTP errors are returned, not raised."""
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if session is not None:
        headers["Cookie"] = f"grommunioAuthJwt={session.token}"
        if method != "GET":
            headers["X-Csrf-Token"] = session.csrf

    url = f"http://localhost:{get_admin_port()}/api/v1{path}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        return status, None


def admin_session(password=None) -> AdminSession:
    """Logs in as 'admin' (ADMIN_PASSWORD by default); raises RuntimeError on failure."""
    pw = password or load_env_secret("ADMIN_PASSWORD", "admin_secret")
    status, data = api("POST", "/login", form={"user": "admin", "pass": pw})
    if status != 200 or not data or not data.get("grommunioAuthJwt"):
        raise RuntimeError(f"Admin login failed (HTTP {status})")
    return AdminSession(data["grommunioAuthJwt"], data.get("csrf", ""))


def container_exec(*args, input=None, timeout=60) -> subprocess.CompletedProcess:
    """Runs a command inside the compose 'grommunio' service."""
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "grommunio", *args],
        input=input, capture_output=True, text=True, timeout=timeout, cwd=ROOT_DIR,
    )


def send_mail(rcpt: str, label: str = "Verification", body: str = "Automated verification message.",
              subject: str = None) -> str:
    """Injects a message via host-mapped SMTP and returns its unique subject (SMTP rejections propagate)."""
    subject = subject or f"{label}-{uuid.uuid4().hex[:12]}"
    sender = f"verifier-{subject}@external.example"
    msg = MIMEText(body)
    msg["Subject"], msg["From"], msg["To"] = subject, sender, rcpt
    with smtplib.SMTP(get_bind_ip(), get_smtp_port(), timeout=10) as client:
        client.sendmail(sender, [rcpt], msg.as_string())
    return subject


def mailbox_dir(address: str, runner=None):
    """Returns the store directory of a Grommunio user, or None if the user does not exist."""
    res = (runner or container_exec)("grommunio-admin", "user", "show", address)
    if res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        if line.strip().startswith("maildir:"):
            return line.split(":", 1)[1].strip()
    return None


# 236781599 == PR_NORMALIZED_SUBJECT (0x0E1D001F), which Gromox stores for every delivered message.
_MAILBOX_QUERY = r'''
import sqlite3, sys
db, token = sys.argv[1:3]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
print(c.execute(
    "SELECT COUNT(*) FROM message_properties p JOIN messages m USING (message_id) "
    "WHERE p.proptag = 236781599 AND m.is_deleted = 0 AND p.propval LIKE ?",
    ("%" + token + "%",),
).fetchone()[0])
'''


def mailbox_contains(address: str, subject_token: str, runner=None) -> bool:
    """True when a non-deleted message whose subject contains the token is stored for the user.

    runner executes commands in the container under test (defaults to the compose service).
    """
    runner = runner or container_exec
    maildir = mailbox_dir(address, runner)
    if not maildir:
        return False
    res = runner("python3", "-c", _MAILBOX_QUERY, f"{maildir}/exmdb/exchange.sqlite3", subject_token)
    return res.returncode == 0 and res.stdout.strip().isdigit() and int(res.stdout.strip()) > 0


def ensure_testuser(address: str = "testuser@example.com", password: str = "testuser_secret") -> str:
    """Idempotently provisions the test mailbox (via LDAP downsync when available) and returns its address."""
    domain, local = address.split("@")[1], address.split("@")[0]

    def exists():
        res = container_exec("grommunio-admin", "user", "query", "-f", f"username={address}")
        return address in res.stdout

    if exists():
        return address
    container_exec("grommunio-admin", "domain", "create", domain, "-u", "50")
    if ldap_available():
        container_exec("grommunio-admin", "ldap", "downsync", "-c")
    if not exists():
        # --no-defaults: the CLI rejects the quota keys of the configured creation defaults.
        container_exec("grommunio-admin", "user", "create", "--no-defaults", "--privWeb", "true", "--privEas", "true",
                       "--altname", local, address)
        container_exec("grommunio-admin", "passwd", address, "--password", password)
    if not exists():
        raise RuntimeError(f"Could not provision {address}")
    container_exec("chown", "-R", "gromox:gromox", f"/var/lib/gromox/user/{domain}/{local}")
    return address

