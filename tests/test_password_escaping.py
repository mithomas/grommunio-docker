#!/usr/bin/env python3
r"""
Verification Test 13: Password Special Characters & Loopback Direct Connectivity
Verifies that passwords containing special characters ($, #, ', ", \, ;) are handled
safely across all system boundaries without syntax errors, variable interpolation, or truncation:
1. Docker Compose .env quoting & escaping rules via env_helper.parse_env_value.
2. YAML configuration generation safety (PyYAML roundtrip with single/double quotes, $, #, \).
3. Shell safety with printf piping (immune to -n, -e flags and backslashes).
4. MariaDB CLI execution uses MYSQL_PWD avoiding command-line argument conflicts.
5. Antispam configuration connects directly to Rspamd controller (127.0.0.1:11335),
   and the obsolete internal-bridge.conf (:11336) is eliminated from Nginx configuration.
"""
import os
import subprocess
import sys

from env_helper import parse_env_value


def test_env_parsing():
    print("    [>] 1. Validating Docker Compose dotenv parsing rules...")
    test_cases = [
        # Single-quoted: treated literally, preserves $, #, ", and handles \'
        ("'" + 'p@$$w0rd#123"' + r"\'" + "'", 'p@$$w0rd#123"\''),
        ("'secret$MB$4#hash'", "secret$MB$4#hash"),
        ("'" + r"don\'t touch" + "'", "don't touch"),
        # Double-quoted: strips outer quotes, converts \" to " and \\ to \
        ('"' + r'foo\"bar' + '"', 'foo"bar'),
        ('"' + r'line\\path' + '"', 'line\\path'),
        # Unquoted: preserves characters, strips inline comments with preceding space
        ("simple_pass # comment", "simple_pass"),
        ("pass_with#hash", "pass_with#hash"),
    ]
    for raw, expected in test_cases:
        actual = parse_env_value(raw)
        if actual != expected:
            print(f"[-] Env parse failed for {raw!r}: expected {expected!r}, got {actual!r}", file=sys.stderr)
            return False
    print("    [+] Dotenv parser correctly handles single quotes, double quotes, and comments.")
    return True


def test_yaml_generation():
    print("    [>] 2. Validating PyYAML generation with special characters...")
    tricky_passwords = [
        r"p@$$w0rd'#\";\4",
        "don't'break'yaml",
        'double"quotes"everywhere',
        "#leading_hash",
        "$variable$substitution$attempt",
        "-leading-dash",
        "trailing_quote'",
        r"mixed'\"quotes\"'and\backslashes",
    ]

    yaml_test_code = r'''
import sys
import yaml

passwords = %r
for pw in passwords:
    config = {
        "DB": {
            "host": "localhost",
            "port": 3306,
            "user": "grommunio",
            "pass": pw,
            "database": "grommunio",
        }
    }
    dumped = yaml.dump(config, default_flow_style=False)
    try:
        loaded = yaml.safe_load(dumped)
    except Exception as ex:
        print(f"FAIL: parse error for {pw!r}: {ex}", file=sys.stderr)
        sys.exit(1)
    if loaded["DB"]["pass"] != pw:
        print(f"FAIL: mismatch: expected {pw!r}, got {loaded['DB']['pass']!r}", file=sys.stderr)
        sys.exit(1)
print("OK_YAML_ROUNDTRIP")
''' % (tricky_passwords,)

    try:
        import yaml  # noqa: F401
        res = subprocess.run([sys.executable, "-c", yaml_test_code], capture_output=True, text=True)
    except ImportError:
        # Fallback to in-container python where python3-yaml is installed
        res = subprocess.run(
            ["docker", "exec", "-i", "grommunio", "python3", "-c", yaml_test_code],
            capture_output=True,
            text=True,
        )

    if res.returncode != 0 or "OK_YAML_ROUNDTRIP" not in res.stdout:
        print(f"[-] PyYAML validation failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        return False

    print("    [+] PyYAML safely serializes and deserializes all password special characters.")
    return True


def test_printf_shell_safety():
    print("    [>] 3. Validating printf shell safety vs echo flag interception...")
    flag_passwords = [
        "-n123456",
        "-e_tab\there",
        "-E_escaped",
        "\\backslash\\path",
    ]
    for pw in flag_passwords:
        # printf '%s\n' must produce exact bytes
        cmd = ["bash", "-c", 'printf \'%s\\n\' "$1"', "_", pw]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = proc.stdout.rstrip("\r\n")
        if out != pw:
            print(f"[-] printf pipe corrupted password {pw!r}: got {out!r}", file=sys.stderr)
            return False

    print("    [+] printf piping preserves exact bytes without flag interception.")
    return True


def test_container_configuration():
    print("    [>] 4. Validating container antispam & Nginx bridge removal...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entrypoint_path = os.path.join(root_dir, "rootfs", "entrypoint.sh")
    antispam_path = os.path.join(root_dir, "rootfs", "etc", "grommunio-admin-api.dist", "antispam.yaml")
    nginx_rspamd_path = os.path.join(root_dir, "rootfs", "etc", "nginx", "conf.d", "rspamd.conf")

    with open(entrypoint_path, "r", encoding="utf-8") as f:
        entrypoint_content = f.read()

    with open(antispam_path, "r", encoding="utf-8") as f:
        antispam_content = f.read()

    with open(nginx_rspamd_path, "r", encoding="utf-8") as f:
        nginx_rspamd_content = f.read()

    # 1. Verify internal-bridge.conf is gone from entrypoint.sh
    if "internal-bridge.conf" in entrypoint_content:
        print("[-] FAIL: rootfs/entrypoint.sh still references internal-bridge.conf", file=sys.stderr)
        return False
    if "11336" in entrypoint_content:
        print("[-] FAIL: rootfs/entrypoint.sh still references port 11336", file=sys.stderr)
        return False

    # 2. Verify antispam.yaml points to 11335 directly
    if "http://127.0.0.1:11335" not in antispam_content:
        print("[-] FAIL: antispam.yaml does not point to http://127.0.0.1:11335", file=sys.stderr)
        return False

    # 3. Verify rspamd.conf does not include internal-bridge.conf
    if "internal-bridge.conf" in nginx_rspamd_content:
        print("[-] FAIL: rootfs/etc/nginx/conf.d/rspamd.conf still includes internal-bridge.conf", file=sys.stderr)
        return False

    # 4. Verify MYSQL_PWD is used in entrypoint.sh
    if 'MYSQL_PWD="${DB_PASSWORD}"' not in entrypoint_content:
        print("[-] FAIL: rootfs/entrypoint.sh does not use MYSQL_PWD for MariaDB checks", file=sys.stderr)
        return False

    # 5. Verify printf is used for admin password in entrypoint.sh
    if 'printf \'%s\\n\' "${ADMIN_PASSWORD}"' not in entrypoint_content:
        print("[-] FAIL: rootfs/entrypoint.sh does not use printf for admin password", file=sys.stderr)
        return False

    print("    [+] In-repo configs confirmed: direct 11335 connection, bridge removed, MYSQL_PWD & printf active.")
    return True


def check_password_escaping():
    print("==> Checking Password Special Characters & Direct Controller Connection...")
    if not test_env_parsing():
        return False
    if not test_yaml_generation():
        return False
    if not test_printf_shell_safety():
        return False
    if not test_container_configuration():
        return False
    return True


if __name__ == "__main__":
    if check_password_escaping():
        print("[+] SUCCESS: Password special character handling and bridge removal verified.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Password escaping verification failed.\n", file=sys.stderr)
        sys.exit(1)
