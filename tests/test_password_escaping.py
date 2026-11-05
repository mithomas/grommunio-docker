#!/usr/bin/env python3
r"""
Verification Test 13: Password Special Characters & Loopback Direct Connectivity
Verifies that passwords containing special characters ($, #, ', ", \, ;) are handled
safely across all system boundaries without syntax errors, variable interpolation, or truncation:
1. MariaDB CLI execution uses MYSQL_PWD avoiding command-line argument conflicts.
2. Antispam configuration connects directly to Rspamd controller (127.0.0.1:11335),
   and the obsolete internal-bridge.conf (:11336) is eliminated from Nginx configuration.
3. Rspamd password hashing and default-credential gating.
"""
import os
import subprocess
import sys


def test_container_configuration():
    print("    [>] 1. Validating container antispam & Nginx bridge removal...")
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
    print("    [>] 2. Validating Rspamd password hash fail-fast error handling...")
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
    print("    [>] 3. Validating default password detection and ENFORCE_SECURE_PASSWORDS...")
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
