#!/usr/bin/env python3
"""
Verification Test 14: Dedicated Host Port Mapping Decoupling
Verifies that:
1. env_helper provides typed port helpers with correct defaults and .env resolution.
2. In-container healthcheck probes fixed internal container listeners (8080, 8081, 11334)
   and is decoupled from host-mapped environment variables (HTTP_PORT, ADMIN_PORT, RSPAMD_PORT).
3. In-container grommunio-init-defaults.py targets internal Admin API port 8081 directly.
4. Container entrypoint disambiguates companion MariaDB container traffic (port 3306)
   from host-mapped DB_PORT bindings.
5. All host verification tests under tests/ resolve ingress endpoints dynamically via env_helper.
"""
import os
import sys

from env_helper import (
    get_admin_port,
    get_bind_ip,
    get_http_port,
    get_ldap_port,
    get_rspamd_port,
    get_smtp_port,
    parse_env_value,
)


def test_env_helper_port_defaults():
    print("    [>] 1. Validating env_helper port extraction defaults...")
    # Clean temporary env overrides if present
    env_keys = ["HTTP_PORT", "ADMIN_PORT", "RSPAMD_PORT", "SMTP_PORT", "LDAP_PORT", "BIND_IP"]
    saved = {k: os.environ.pop(k, None) for k in env_keys}

    try:
        http_p = get_http_port()
        admin_p = get_admin_port()
        rspamd_p = get_rspamd_port()
        smtp_p = get_smtp_port()
        ldap_p = get_ldap_port()
        bind_ip = get_bind_ip()

        # If .env exists in workspace, helpers might return .env values or standard defaults
        # Validate that returned ports are integers > 0 and bind_ip is non-empty
        assert isinstance(http_p, int) and http_p > 0, f"Invalid http_port: {http_p}"
        assert isinstance(admin_p, int) and admin_p > 0, f"Invalid admin_port: {admin_p}"
        assert isinstance(rspamd_p, int) and rspamd_p > 0, f"Invalid rspamd_port: {rspamd_p}"
        assert isinstance(smtp_p, int) and smtp_p > 0, f"Invalid smtp_port: {smtp_p}"
        assert isinstance(ldap_p, int) and ldap_p > 0, f"Invalid ldap_port: {ldap_p}"
        assert isinstance(bind_ip, str) and len(bind_ip) > 0, f"Invalid bind_ip: {bind_ip}"

        # Test custom environment variable overrides
        os.environ["HTTP_PORT"] = "18080"
        os.environ["ADMIN_PORT"] = "18081"
        os.environ["RSPAMD_PORT"] = "11338"
        os.environ["SMTP_PORT"] = "12525"
        os.environ["LDAP_PORT"] = "10389"
        os.environ["BIND_IP"] = "127.0.0.5"

        assert get_http_port() == 18080, "get_http_port did not respect override"
        assert get_admin_port() == 18081, "get_admin_port did not respect override"
        assert get_rspamd_port() == 11338, "get_rspamd_port did not respect override"
        assert get_smtp_port() == 12525, "get_smtp_port did not respect override"
        assert get_ldap_port() == 10389, "get_ldap_port did not respect override"
        assert get_bind_ip() == "127.0.0.5", "get_bind_ip did not respect override"

        print("    [+] env_helper correctly parses and prioritizes port environment overrides.")
        return True
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


def test_container_healthcheck_script():
    print("    [>] 2. Validating container healthcheck script decoupling...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hc_path = os.path.join(root_dir, "rootfs", "usr", "local", "bin", "container-healthcheck.sh")

    with open(hc_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Must probe internal ports 8080, 8081, 11334
    if "INTERNAL_HTTP_PORT=8080" not in content or "http://127.0.0.1:${INTERNAL_HTTP_PORT}/" not in content:
        print("[-] FAIL: container-healthcheck.sh does not use INTERNAL_HTTP_PORT=8080", file=sys.stderr)
        return False
    if "INTERNAL_ADMIN_PORT=8081" not in content or "http://127.0.0.1:${INTERNAL_ADMIN_PORT}/api/v1/status" not in content:
        print("[-] FAIL: container-healthcheck.sh does not use INTERNAL_ADMIN_PORT=8081", file=sys.stderr)
        return False
    if "INTERNAL_RSPAMD_PORT=11334" not in content or "http://127.0.0.1:${INTERNAL_RSPAMD_PORT}/" not in content:
        print("[-] FAIL: container-healthcheck.sh does not use INTERNAL_RSPAMD_PORT=11334", file=sys.stderr)
        return False

    # Must NOT probe host-mapped environment variables directly
    if 'HTTP_PORT="${HTTP_PORT:-8080}"' in content or 'curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${HTTP_PORT}/"' in content:
        print("[-] FAIL: container-healthcheck.sh still probes host HTTP_PORT variable", file=sys.stderr)
        return False
    if 'ADMIN_PORT="${ADMIN_PORT:-8081}"' in content:
        print("[-] FAIL: container-healthcheck.sh still probes host ADMIN_PORT variable", file=sys.stderr)
        return False
    if 'RSPAMD_PORT="${RSPAMD_PORT:-11334}"' in content:
        print("[-] FAIL: container-healthcheck.sh still probes host RSPAMD_PORT variable", file=sys.stderr)
        return False

    print("    [+] container-healthcheck.sh verified: probes fixed internal listeners (8080, 8081, 11334).")
    return True


def test_init_defaults_script():
    print("    [>] 3. Validating grommunio-init-defaults.py internal listener...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    defaults_path = os.path.join(root_dir, "rootfs", "usr", "local", "bin", "grommunio-init-defaults.py")

    with open(defaults_path, "r", encoding="utf-8") as f:
        content = f.read()

    if 'api_base = "http://127.0.0.1:8081/api/v1"' not in content:
        print("[-] FAIL: grommunio-init-defaults.py does not target http://127.0.0.1:8081/api/v1", file=sys.stderr)
        return False

    if 'admin_port = os.environ.get("ADMIN_PORT"' in content:
        print("[-] FAIL: grommunio-init-defaults.py still uses os.environ.get('ADMIN_PORT')", file=sys.stderr)
        return False

    print("    [+] grommunio-init-defaults.py verified: targets fixed internal port 8081.")
    return True


def test_entrypoint_mariadb_port():
    print("    [>] 4. Validating companion MariaDB port handling in entrypoint.sh...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entrypoint_path = os.path.join(root_dir, "rootfs", "entrypoint.sh")

    with open(entrypoint_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Must preserve internal port 3306 when DB_HOST=mariadb
    if 'elif [ "${DB_HOST}" = "mariadb" ] && [ "${DB_PORT}" != "3306" ]; then' not in content:
        print("[-] FAIL: entrypoint.sh does not contain companion MariaDB port disambiguation", file=sys.stderr)
        return False

    print("    [+] entrypoint.sh verified: safely routes companion MariaDB traffic to container port 3306.")
    return True


def test_test_scripts_dynamic_ports():
    print("    [>] 5. Validating test scripts use dynamic port resolution...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tests_dir = os.path.join(root_dir, "tests")

    scripts_to_check = [
        ("test_endpoints.py", ["get_http_port", "get_admin_port", "get_rspamd_port"]),
        ("test_webmail_security.py", ["get_http_port"]),
        ("test_rspamd_auth.py", ["get_rspamd_port"]),
        ("test_mail_pipeline.py", ["get_smtp_port", "get_admin_port"]),
        ("test_live_status.py", ["get_admin_port", "get_http_port"]),
        ("test_monitoring_logs.py", ["get_admin_port", "get_smtp_port"]),
        ("test_defaults.py", ["get_admin_port"]),
        ("test_ldap_config.py", ["get_admin_port"]),
        ("test_ldap.py", ["get_ldap_port"]),
    ]

    for script_name, expected_helpers in scripts_to_check:
        script_path = os.path.join(tests_dir, script_name)
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()

        for helper in expected_helpers:
            if helper not in content:
                print(f"[-] FAIL: {script_name} does not import or use {helper}", file=sys.stderr)
                return False

    print("    [+] All host verification test scripts dynamically resolve ingress ports via env_helper.")
    return True


def check_port_configuration():
    print("==> Checking Dedicated Host Port Mapping Decoupling & Test Resolution...")
    if not test_env_helper_port_defaults():
        return False
    if not test_container_healthcheck_script():
        return False
    if not test_init_defaults_script():
        return False
    if not test_entrypoint_mariadb_port():
        return False
    if not test_test_scripts_dynamic_ports():
        return False
    return True


if __name__ == "__main__":
    if check_port_configuration():
        print("[+] SUCCESS: Port configuration decoupling verified.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Port configuration decoupling verification failed.\n", file=sys.stderr)
        sys.exit(1)
