#!/usr/bin/env python3
"""
Verification Test: Mailbox Creation Defaults & Privileges
1. grommunio-init-defaults.py rejects an invalid quota with exit code 1 and a clear message.
2. The Admin API creation defaults (/defaults/createParams) equal the configured DEFAULT_USER_* values.
3. Non-zero quotas written through the API are returned by the next GET; the original defaults are restored.
Quota parsing is tested against the shipped parse_quota().
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys

from env_helper import ROOT_DIR, admin_session, api, load_env_secret

SCRIPT_PATH = os.path.join(ROOT_DIR, "rootfs", "usr", "local", "bin", "grommunio-init-defaults.py")
DEFAULTS_PATH = "/defaults/createParams"


def load_init_defaults():
    loader = importlib.machinery.SourceFileLoader("grommunio_init_defaults", SCRIPT_PATH)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


init_defaults = load_init_defaults()
parse_quota = init_defaults.parse_quota


def test_quota_parser_contracts():
    print("    [>] Validating parse_quota unit conversions and error handling...")
    for unlimited in ("0", 0, "unlimited", "none", "off", "false", None):
        assert parse_quota(unlimited) == 0, f"{unlimited!r} should mean unlimited"
    assert parse_quota("1048576") == 1048576
    assert parse_quota("1048576kib") == 1048576
    assert parse_quota("500MB") == 500 * 1024
    assert parse_quota("10GiB") == 10 * 1024 * 1024
    assert parse_quota("1T") == 1024 * 1024 * 1024
    assert parse_quota("1.5G") == int(1.5 * 1024 * 1024)

    for case in ["10X", "500FOO", "invalid", "-10G", "-5", "10KBX", "10.5.5M"]:
        try:
            parse_quota(case)
        except ValueError:
            continue
        raise AssertionError(f"Expected ValueError for '{case}'")

    print("    [+] Quota parser converts valid units and rejects invalid input.")
    return True


def test_invalid_quota_aborts_script():
    print("    [>] Validating grommunio-init-defaults.py exits on an invalid quota...")
    env = os.environ.copy()
    env["DEFAULT_USER_STORAGE_QUOTA"] = "10X"
    res = subprocess.run([sys.executable, SCRIPT_PATH], env=env, capture_output=True, text=True)
    assert res.returncode == 1, f"Expected exit code 1 for invalid quota, got {res.returncode}"
    assert "Unknown quota unit 'x'" in res.stderr, f"Expected error message on stderr, got: {res.stderr}"
    print("    [+] Invalid quota aborts with exit code 1 and an explanatory message.")
    return True


def configured_defaults():
    """Expected creation defaults, derived from the same env variables and parsers the init script uses."""
    return {
        key: parser(load_env_secret(env_var, default))
        for key, env_var, default, parser in init_defaults.DEFAULTS_SPEC
    }


def check_defaults():
    print("==> Checking Mailbox Creation Defaults & Privileges...")
    try:
        session = admin_session()
    except RuntimeError as ex:
        print(f"    [-] {ex}", file=sys.stderr)
        return False

    status, body = api("GET", DEFAULTS_PATH, session=session)
    if status != 200 or not body:
        print(f"    [-] GET {DEFAULTS_PATH} returned HTTP {status}", file=sys.stderr)
        return False
    initial = body["data"]
    user = initial.get("user", {})

    mismatches = []
    for key, expected in configured_defaults().items():
        actual = user.get(key)
        if actual is None and expected == 0:
            actual = 0  # an unset quota means unlimited
        if actual != expected:
            mismatches.append(f"{key}: expected {expected!r}, got {actual!r}")
    if mismatches:
        for line in mismatches:
            print(f"    [-] Mismatch for {line}", file=sys.stderr)
        return False
    print("    [+] Creation defaults match the configured DEFAULT_USER_* values.")

    quotas = {"storagequotalimit": 10485760, "prohibitsendquota": 8388608, "prohibitreceivequota": 9437184}
    ok = False
    try:
        status, _ = api("PUT", DEFAULTS_PATH, body={"user": {**user, **quotas}, "domain": initial.get("domain", {})}, session=session)
        if status != 200:
            print(f"    [-] PUT {DEFAULTS_PATH} returned HTTP {status}", file=sys.stderr)
            return False
        _, body = api("GET", DEFAULTS_PATH, session=session)
        written = body["data"]["user"]
        wrong = {k: written.get(k) for k, v in quotas.items() if written.get(k) != v}
        if wrong:
            print(f"    [-] Quotas not returned by the next GET: {wrong}", file=sys.stderr)
            return False
        print("    [+] Non-zero quotas written via the API are returned by the next GET.")
        ok = True
    finally:
        status, _ = api("PUT", DEFAULTS_PATH, body=initial, session=session)
        _, body = api("GET", DEFAULTS_PATH, session=session)
        if status == 200 and body and body["data"]["user"] == user:
            print("    [+] Initial defaults restored.")
        else:
            print("    [-] Failed to restore the initial defaults.", file=sys.stderr)
            ok = False
    return ok


def main():
    test_quota_parser_contracts()
    test_invalid_quota_aborts_script()
    if check_defaults():
        print("[+] SUCCESS: Mailbox creation defaults verified.\n")
        return 0
    print("[-] FAILED: Mailbox creation defaults verification failed.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
