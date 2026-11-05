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

    # 6. Verify single rspamadm call and fail-fast check
    if "rspamadm pw -q --password" in entrypoint_content:
        print("[-] FAIL: rootfs/entrypoint.sh still contains redundant rspamadm fallback", file=sys.stderr)
        return False
    if 'RSPAMD_HASH=$(rspamadm pw -q -p "${RSPAMD_PASSWORD}" 2>/dev/null || true)' not in entrypoint_content:
        print("[-] FAIL: rootfs/entrypoint.sh missing single rspamadm pw call", file=sys.stderr)
        return False
    if 'ERROR: Failed to generate Rspamd controller password hash' not in entrypoint_content:
        print("[-] FAIL: rootfs/entrypoint.sh missing fail-fast check for RSPAMD_HASH", file=sys.stderr)
        return False

    print("    [+] In-repo configs confirmed: direct 11335 connection, bridge removed, MYSQL_PWD & printf active.")
    return True


def test_rspamd_hash_failure_handling():
    print("    [>] 5. Validating Rspamd password hash fail-fast error handling...")
    fail_snippet = '''
set -e
rspamadm() {
    return 1
}
RSPAMD_PASSWORD="test_secret"
RSPAMD_HASH=$(rspamadm pw -q -p "${RSPAMD_PASSWORD}" 2>/dev/null || true)
if [ -z "${RSPAMD_HASH}" ]; then
    echo "ERROR: Failed to generate Rspamd controller password hash with 'rspamadm pw'." >&2
    exit 1
fi
echo "SHOULD_NOT_REACH"
'''
    res = subprocess.run(["bash", "-c", fail_snippet], capture_output=True, text=True)
    if res.returncode != 1:
        print(f"[-] FAIL: Expected exit code 1 on rspamadm failure, got {res.returncode}", file=sys.stderr)
        return False
    if "Failed to generate Rspamd controller password hash" not in res.stderr:
        print(f"[-] FAIL: Expected error message on stderr, got: {res.stderr}", file=sys.stderr)
        return False
    if "SHOULD_NOT_REACH" in res.stdout:
        print("[-] FAIL: Execution continued past hash failure!", file=sys.stderr)
        return False

    special_pw = r"p@$$w0rd'#\";\4"
    real_cmd = ["docker", "compose", "exec", "-T", "grommunio", "rspamadm", "pw", "-q", "-p", special_pw]
    real_res = subprocess.run(real_cmd, capture_output=True, text=True)
    if real_res.returncode != 0 or not real_res.stdout.strip().startswith("$2$"):
        print(f"[-] FAIL: Real rspamadm failed with special character password: {real_res.stderr or real_res.stdout}", file=sys.stderr)
        return False

    print("    [+] Fail-fast error handling and real rspamadm special character hashing verified.")
    return True


def test_default_credential_warnings_and_hardening():
    print("    [>] 6. Validating default password detection and ENFORCE_SECURE_PASSWORDS...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entrypoint_path = os.path.join(root_dir, "rootfs", "entrypoint.sh")
    with open(entrypoint_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "INSECURE_WARNINGS" in content, "FAIL: entrypoint.sh missing INSECURE_WARNINGS check"
    assert "ENFORCE_SECURE_PASSWORDS" in content, "FAIL: entrypoint.sh missing ENFORCE_SECURE_PASSWORDS check"
    assert "[ -f /usr/local/bin/grommunio-init-defaults.py ]" in content, "FAIL: entrypoint.sh missing simplified init-defaults test"
    assert "[ -x /usr/local/bin/grommunio-init-defaults.py ]" not in content, "FAIL: entrypoint.sh still contains redundant -x check"

    # Test A: Unset ENFORCE_SECURE_PASSWORDS defaults to true and fails on default credentials
    snippet_default_enforce = '''
ADMIN_PASSWORD="admin"
DB_PASSWORD="grommunio_secret"
RSPAMD_PASSWORD="rspamd_secret"
LDAP_BIND_PASS="ldap_secret"
unset ENFORCE_SECURE_PASSWORDS

INSECURE_WARNINGS=""
[ "${ADMIN_PASSWORD}" = "admin" ] && INSECURE_WARNINGS="${INSECURE_WARNINGS} ADMIN_PASSWORD (admin)"
[ "${DB_PASSWORD}" = "grommunio_secret" ] && INSECURE_WARNINGS="${INSECURE_WARNINGS} DB_PASSWORD (grommunio_secret)"
[ "${RSPAMD_PASSWORD}" = "rspamd_secret" ] && INSECURE_WARNINGS="${INSECURE_WARNINGS} RSPAMD_PASSWORD (rspamd_secret)"
[ "${LDAP_BIND_PASS}" = "ldap_secret" ] && INSECURE_WARNINGS="${INSECURE_WARNINGS} LDAP_BIND_PASS (ldap_secret)"

if [ -n "${INSECURE_WARNINGS}" ]; then
    echo "WARNING: Default credentials detected in environment:${INSECURE_WARNINGS}." >&2
    if [ "${ENFORCE_SECURE_PASSWORDS:-true}" = "true" ]; then
        exit 1
    fi
fi
exit 0
'''
    res_def_enf = subprocess.run(["bash", "-c", snippet_default_enforce], capture_output=True, text=True)
    assert res_def_enf.returncode == 1, f"Expected 1 when defaulting to true, got {res_def_enf.returncode}"
    assert "WARNING: Default credentials detected" in res_def_enf.stderr

    # Test B: Explicit ENFORCE_SECURE_PASSWORDS=false permits default credentials with warning
    snippet_opt_out = snippet_default_enforce.replace('unset ENFORCE_SECURE_PASSWORDS', 'ENFORCE_SECURE_PASSWORDS="false"')
    res_opt_out = subprocess.run(["bash", "-c", snippet_opt_out], capture_output=True, text=True)
    assert res_opt_out.returncode == 0, f"Expected 0 when opted out, got {res_opt_out.returncode}"
    assert "WARNING: Default credentials detected" in res_opt_out.stderr
    assert "ADMIN_PASSWORD (admin)" in res_opt_out.stderr

    # Test C: Custom passwords pass cleanly under default ENFORCE_SECURE_PASSWORDS=true
    snippet_custom = snippet_default_enforce.replace('ADMIN_PASSWORD="admin"', 'ADMIN_PASSWORD="custom_admin"') \
                                            .replace('DB_PASSWORD="grommunio_secret"', 'DB_PASSWORD="custom_db"') \
                                            .replace('RSPAMD_PASSWORD="rspamd_secret"', 'RSPAMD_PASSWORD="custom_rspamd"') \
                                            .replace('LDAP_BIND_PASS="ldap_secret"', 'LDAP_BIND_PASS="custom_ldap"')
    res_cust = subprocess.run(["bash", "-c", snippet_custom], capture_output=True, text=True)
    assert res_cust.returncode == 0, f"Expected 0, got {res_cust.returncode}"
    assert res_cust.stderr == "", f"Expected empty stderr, got: {res_cust.stderr}"

    print("    [+] Default password detection, stderr warnings, and ENFORCE_SECURE_PASSWORDS verified.")
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
    if not test_rspamd_hash_failure_handling():
        return False
    if not test_default_credential_warnings_and_hardening():
        return False
    return True


if __name__ == "__main__":
    if check_password_escaping():
        print("[+] SUCCESS: Password special character handling and bridge removal verified.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Password escaping verification failed.\n", file=sys.stderr)
        sys.exit(1)
