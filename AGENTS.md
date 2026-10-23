# Agent Guidelines & Operational Reference

This workspace manages a production-ready, single-container Grommunio deployment based on **Debian 13 (Trixie)** using community deb packages from `download.grommunio.com`.

---

## 1. Documentation & Architecture Pointer

For full architectural diagrams, port tables, host Exim integration snippets, host reverse proxy TLS examples, and pool sizing rationale, refer to:
- **`README.md`**: The primary operational documentation and integration guide for this project.

---

## 2. Automated Test Suite (`tests/`)

All functional verification checks performed during development are codified as standalone, focused test scripts under the `tests/` directory:

| Script | Test Scope | Verification Target |
| :--- | :--- | :--- |
| **`tests/test_services.py`** | Supervisor Process Monitoring | Verifies that all 14 background daemons inside the container report `RUNNING`. |
| **`tests/test_endpoints.py`** | HTTP Ingress & Content-Types | Validates status codes, headers, and MIME types across Webmail (`:8080`), ActiveSync (`:8080`), Admin Web UI (`:8081`), `config.json` (`:8081`), Admin API status (`:8081`), and Rspamd Dashboard (`:11334`). |
| **`tests/test_webmail_security.py`** | Webmail Root Ingress & Cookie Security | Validates Webmail delivery at root (`:8080`), absence of PHP configuration errors, `secure`/`HttpOnly` cookies, static assets, and runtime `session.cookie_secure = 1`. |
| **`tests/test_admin_auth.py`** | Admin API & JWT Authentication | Tests login credential validation, JWT token issuance, cookie handling, and access to protected administrative endpoints. |
| **`tests/test_rspamd_auth.py`** | Rspamd Dedicated Authentication | Tests that unauthenticated requests to `:11334` are rejected and that valid authentication (`RSPAMD_PASSWORD`) grants access to Rspamd controller endpoints (`/auth`, `/stat`). |
| **`tests/test_mail_pipeline.py`** | End-to-End Mail & Spam Pipeline | Injects an SMTP test email into container Exim (`:2525`), confirms Rspamd content scanning, verifies delivery to Gromox LMTP (`:24`), and asserts that the scan record is populated in the Admin UI **Spam History** API. |
| **`tests/test_live_status.py`** | Admin UI Live Status & VTS Telemetry | Validates `/system/vhostStatus/local` endpoint, verifies prevention of 502 Bad Gateway via header sanitization, and confirms Nginx VTS telemetry payload structure. |
| **`tests/test_monitoring_logs.py`** | Admin UI Monitoring Logs | Validates file-based log readers in Admin UI Monitoring tab (`/system/logs`), verifies Exim log replaces Postfix, and confirms entry formatting and time filters. |
| **`tests/test_defaults.py`** | Mailbox Creation Defaults & Privileges | Validates that Admin API creation defaults (`/defaults/createParams`) reflect configured defaults (language, protocols, services, integrations, quotas). |
| **`tests/test_ldap_config.py`** | Admin API LDAP Config & Persistence | Validates default group mapping population in `/system/mconf/ldap` and disk persistence without permission errors. |
| **`tests/test_ldap.py`** | OpenLDAP & User Provisioning | Tests companion OpenLDAP (`:389`), Admin CLI connection (`ldap info`), user search, and user mailbox provisioning via `ldap downsync -c`. |

### Running the Test Suite

Execute all verification tests in sequence:
```bash
python3 tests/run_all.py
```

Run an individual test directly:
```bash
python3 tests/test_services.py
python3 tests/test_endpoints.py
python3 tests/test_webmail_security.py
python3 tests/test_admin_auth.py
python3 tests/test_rspamd_auth.py
python3 tests/test_mail_pipeline.py
python3 tests/test_live_status.py
python3 tests/test_monitoring_logs.py
python3 tests/test_defaults.py
python3 tests/test_ldap_config.py
python3 tests/test_ldap.py
```

### Test Authoring Guidelines

- **Standalone Scripts**: All new tests MUST be added as standalone executable scripts directly inside the `tests/` folder (named `tests/test_<scope>.py`). Each script must be independently runnable (`python3 tests/test_<scope>.py`) and exit with status code 0 on success or non-zero on failure.
- **Suite Registration**: Always register new test scripts in the `TESTS` array in `tests/run_all.py` so they run automatically during full suite execution.
- **Documentation**: Update the test tables in `AGENTS.md` and `README.md` whenever a new test script is introduced.

---

## 3. Architecture & Operational Standards

1. **Read-Only Root Filesystem (`read_only: true`):**
   - The container runs with an immutable root filesystem.
   - Transient runtime data (sockets, PIDs, buffers, caches) MUST be allocated on `tmpfs` (`/run`, `/tmp`, `/var/tmp`, `/var/lib/nginx`, `/var/lib/grommunio-web`, `/var/lib/php/sessions`).
   - Persistent operational state (`/var/spool/exim4`, `/var/log`, `/var/lib/gromox`, `/var/lib/redis`, `/var/lib/rspamd`) is mounted via persistent named volumes.
   - Ephemeral configuration directories (`/etc/gromox`, `/etc/grommunio-admin-api`, `/etc/grommunio-admin-common`, `/etc/exim4`) are mounted via `tmpfs`.

2. **Configuration Templating (`.dist/` Mechanism):**
   - Stock package files and template configurations are stored under `/etc/*.dist/` within the image layers.
   - At container startup, `entrypoint.sh` populates the `tmpfs` mounts from their corresponding `.dist/` directories, performs environment variable substitution via `envsubst`, and applies file ownership and permissions before launching `supervisord`.

3. **In-Container Mail Pipeline:**
   - **Inbound:** Host Exim routes domain traffic to container Exim on port `2525`. Container Exim scans via Rspamd (`spamd_address = 127.0.0.1 11333 variant=rspamd`) and delivers via internal LMTP to Gromox on port `24`.
   - **Outbound:** Webmail and ActiveSync submit outbound messages to container Exim (`smtp://127.0.0.1:2525/`), which relays them to the host Exim smarthost (`smtp://${RELAY_HOST}:${RELAY_PORT}`).

4. **Spam History Authenticated Loopback Bridge:**
   - Nginx runs an internal listener on `127.0.0.1:11336` that injects `Password: ${RSPAMD_PASSWORD}`.
   - `grommunio-admin-api` routes Rspamd requests to `http://127.0.0.1:11336`, allowing the Admin UI's **Spam History** tab to load authenticated scan logs without exposing the controller unauthenticated to external networks.

5. **File-Based Service Log Monitoring:**
   - In containerized Supervisord environments where `systemd-journald` is absent, `grommunio-admin-api` is enhanced with a dedicated `FileReader` registered with `@LogReader.register("file")` and automatic fallback in `JournaldReader`.
   - Replaced stock Postfix configuration with Exim (`/var/log/exim4/mainlog`), and routed Gromox daemons to discrete log files under `/var/log/gromox/`.
   - Permissions allow `grommunio` (member of `Debian-exim`, `adm`, `gromox`) to read all service logs while maintaining read-only rootfs isolation.

6. **Container Lifecycle & Health Monitoring:**
   - **Healthcheck:** The container defines a `HEALTHCHECK` running `/usr/local/bin/container-healthcheck.sh` every 30s. It verifies that `supervisorctl` responds, all 14 background services report `RUNNING`, and the local webmail endpoint responds.
   - **Graceful Termination:** Configured with `STOPSIGNAL SIGTERM` and `stop_grace_period: 30s` in Docker Compose. This ensures Supervisor executes its ordered shutdown cascade (reverse priority from Nginx down to Redis), preventing MAPI/SQLite transaction interruption or Redis snapshot corruption.

7. **Automated Builds & Docker Hub CI/CD Pipeline:**
   - The workflow `.github/workflows/build-and-publish.yml` executes on scheduled cron (`0 */6 * * *`), git push to `main`, tags (`v*`), pull requests, and manual `workflow_dispatch`.
   - **Upstream Change Detection:** Compares remote `debian:trixie-slim` digest and Grommunio Debian 13 repository packages SHA256 checksum against GitHub Actions cache, bypassing redundant builds when upstream dependencies have not changed.
   - **Pre-Publication Gate:** Image builds for `linux/amd64` and starts the full test stack (`docker compose --profile test up -d`). Tests run via `python3 tests/run_all.py`. Publication to Docker Hub only proceeds if all 11 verification tests pass.
   - **Image Tagging & OCI Labels:** Pushes `:latest`, `:<major>`, `:<minor>`, `:<patch>`, and `:<patch>-<timestamp>-<short-sha>` (e.g. `3.12.268-20261001-1339-<commit>`). Embeds OCI metadata labels (`org.opencontainers.image.*`) covering all core component versions (Gromox, Webmail, Z-Push, Admin API, Admin Web, Rspamd, Exim, Nginx, Redis, PHP, and Debian point release).
   - **Required Secrets:** `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and optional `DOCKERHUB_REPOSITORY` (defaults to `${DOCKERHUB_USERNAME}/grommunio-docker`).
