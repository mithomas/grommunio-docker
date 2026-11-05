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
| **`tests/test_endpoints.py`** | HTTP Ingress & Content-Types | Validates status codes, headers, and MIME types across Webmail (`:8080`), ActiveSync (`:8080`), Admin Web UI (`:8081`), `config.json` (`:8081`), Admin API status (`:8081`), and Rspamd Dashboard (`:11334`). |
| **`tests/test_webmail_security.py`** | Webmail Root Ingress & Cookie Security | Validates Webmail delivery at root (`:8080`), absence of PHP configuration errors, `secure`/`HttpOnly` cookies, static assets, and logon/Origin handling. |
| **`tests/test_admin_auth.py`** | Admin API & JWT Authentication | Tests login credential validation, JWT token issuance, access to protected endpoints, and rejection of missing or forged tokens (401). |
| **`tests/test_rspamd_auth.py`** | Rspamd Dedicated Authentication | Tests that unauthenticated requests to `:11334` are rejected and that valid authentication (`RSPAMD_PASSWORD`) grants access to Rspamd controller endpoints (`/auth`, `/stat`). |
| **`tests/test_mail_pipeline.py`** | End-to-End Mail & Spam Pipeline | Injects an SMTP test email into container Exim (`:2525`), confirms delivery into the mailbox via Rspamd and Gromox LMTP (`:24`), asserts the message's **Spam History** record, and checks that a GTUBE message is rejected (550) and not delivered. |
| **`tests/test_live_status.py`** | Admin UI Live Status & VTS Telemetry | Validates `/system/vhostStatus/local` endpoint, verifies prevention of 502 Bad Gateway via header sanitization, and confirms Nginx VTS telemetry payload structure. |
| **`tests/test_monitoring_logs.py`** | Admin UI Monitoring Logs | Validates file-based log readers in Admin UI Monitoring tab (`/system/logs`), verifies Exim log replaces Postfix, and confirms entry formatting and time filters. |
| **`tests/test_defaults.py`** | Mailbox Creation Defaults & Privileges | Validates that Admin API creation defaults (`/defaults/createParams`) reflect configured defaults (language, protocols, services, integrations, quotas). |
| **`tests/test_ldap_config.py`** | Admin API LDAP Config & Persistence | Validates default group mappings in `/system/mconf/ldap` and that a saved change is returned by the API and written to `/etc/gromox/ldap_adaptor.cfg` (then restored). |
| **`tests/test_ldap.py`** | OpenLDAP & User Provisioning | Tests companion OpenLDAP (`:389`), Admin CLI connection (`ldap info`), user search, and user mailbox provisioning via `ldap downsync -c`. |
| **`tests/test_db_socket.py`** | MariaDB Unix Socket | Runs the real image against a socket-only MariaDB with a custom `DB_SOCKET` path: the entrypoint connects, links the socket where libmariadb looks for it, and Gromox and the Admin API both work through it. |
| **`tests/test_multidomain_delivery.py`** | Multi-Domain Routing | Starts the real image with `DOMAINS` set and verifies mail for the primary domain, a primary alias and both secondary domains reaches the mailbox via Exim -> LMTP. |
| **`tests/test_password_escaping.py`** | Password Special Characters & Direct Controller Auth | Validates safe handling of passwords with special characters ($, #, ', ", \, ;) in Rspamd hashing, default-credential gating, and direct Rspamd controller connectivity. |
| **`tests/test_db_init_safety.py`** | Database Schema & Migration Idempotence | Validates the live database is populated and `gromox-dbop -U` re-runs cleanly. |
| **`tests/test_startup_safety.py`** | Container Startup Safety | Runs the real image in throwaway containers against a scratch database: default credentials are refused, an unreachable database exits with a diagnostic, SIGTERM during the schema-lock wait exits 143, and a restart reuses the existing schema. |
| **`tests/test_log_retention.py`** | Log Rotation & Admin UI Tail Continuity | Validates the Admin UI `FileReader` (format parsing, continuation-line timestamps, `after` filter, `.1` rotated-log fallback) and the in-container logrotate dry run. |
| **`tests/test_mailbox_ownership.py`** | Mailbox Volume Permissions & Repair | Validates gromox:gromox ownership of the mailbox volume and that `grommunio-fix-permissions.sh` repairs a root-owned tree (shallow touches only the top level, recursive fixes everything). |
| **`tests/test_bayes_sync.py`** | Bayes Synchronization Engine & Scan Cost Reduction | Validates Python batched sync engine with SQLite ATTACH DATABASE, set-based differential queries, spam/ham/trash/expunge lifecycle transitions, retry resilience, and zero redundant process forks. |
| **`tests/test_healthcheck.py`** | Container Healthcheck Diagnostics & Thresholds | Validates a healthy container passes, a stopped service fails with an explicit diagnostic, and a 100% full volume is flagged. |

### Running the Test Suite

Execute all verification tests in sequence:
```bash
python3 tests/run_all.py
```

Run an individual test directly:
```bash
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
python3 tests/test_db_socket.py
python3 tests/test_multidomain_delivery.py
python3 tests/test_password_escaping.py
python3 tests/test_db_init_safety.py
python3 tests/test_startup_safety.py
python3 tests/test_log_retention.py
python3 tests/test_mailbox_ownership.py
python3 tests/test_bayes_sync.py
python3 tests/test_healthcheck.py
```

### Test Authoring Guidelines

- **Standalone Scripts**: All new tests MUST be added as standalone executable scripts directly inside the `tests/` folder (named `tests/test_<scope>.py`). Each script must be independently runnable (`python3 tests/test_<scope>.py`) and exit with status code 0 on success or non-zero on failure.
- **Suite Registration**: Always register new test scripts in the `TESTS` array in `tests/run_all.py` so they run automatically during full suite execution.
- **Documentation**: Update the test tables in `AGENTS.md` and `README.md` whenever a new test script is introduced.
- **Shared Helpers**: Use `tests/env_helper.py` (`admin_session()`, `api()`, `container_exec()`, `wait_until()`, `send_mail()`, `mailbox_contains()`, `ensure_testuser()`) instead of re-implementing login, container access, sleeps or fixtures. Poll with `wait_until`, never fixed `sleep`.
- **Skips**: A test that cannot run exits with `SKIP_EXIT` (77). `run_all.py` reports it as skipped locally and fails it when `CI` is set unless the script is listed in `ALLOW_SKIP`.
- **Startup Behavior**: Test entrypoint/startup behavior with `tests/container_harness.py` (`run_startup()`, `Container`, `ScratchDatabase`), which runs the real image against a disposable database and refuses to touch the live one. Requires the compose `test` profile.

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

4. **Spam History Direct Controller Integration:**
   - `grommunio-admin-api` routes Rspamd requests directly to `http://127.0.0.1:11335`, authenticated automatically via Rspamd's `secure_ip = ["127.0.0.1", "::1"]`.
   - External access via port `:11334` strictly requires `RSPAMD_PASSWORD`, while internal API communication bypasses password injection, avoiding escaping failures for arbitrary passwords.

5. **File-Based Service Log Monitoring:**
   - In containerized Supervisord environments where `systemd-journald` is absent, `grommunio-admin-api` is enhanced with a dedicated `FileReader` registered with `@LogReader.register("file")` and automatic fallback in `JournaldReader`.
   - Replaced stock Postfix configuration with Exim (`/var/log/exim4/mainlog`), and routed Gromox daemons to discrete log files under `/var/log/gromox/`.
   - Permissions allow `grommunio` (member of `Debian-exim`, `adm`, `gromox`) to read all service logs while maintaining read-only rootfs isolation.

6. **Container Lifecycle & Health Monitoring:**
   - **Healthcheck:** The container defines a `HEALTHCHECK` running `/usr/local/bin/container-healthcheck.sh` every 30s. It verifies that `supervisorctl` responds, all 14 background services report `RUNNING`, the local webmail endpoint responds, and persistent volume disk space is adequate.
   - **Graceful Termination:** Configured with `STOPSIGNAL SIGTERM` and `stop_grace_period: 30s` in Docker Compose. This ensures Supervisor executes its ordered shutdown cascade (reverse priority from Nginx down to Redis), preventing MAPI/SQLite transaction interruption or Redis snapshot corruption.

7. **Automated Builds & Docker Hub CI/CD Pipeline:**
   - The workflow `.github/workflows/build-and-publish.yml` executes on scheduled cron (`0 */6 * * *`), git push to `main`, tags (`v*`), pull requests, and manual `workflow_dispatch`.
   - **Docker Hub Overview Sync:** The workflow `.github/workflows/sync-dockerhub-readme.yml` automatically syncs `README.md` (along with the short description and completed GitHub URLs) to the Docker Hub repository overview whenever `README.md` is updated on `main` or triggered via `workflow_dispatch` (as well as during image publication in `build-and-publish.yml`). `README.md` must stay under Docker Hub's 25,000-byte limit.
   - **Upstream Change Detection:** Compares remote `debian:trixie-slim` digest and Grommunio Debian 13 repository packages SHA256 checksum against GitHub Actions cache, bypassing redundant builds when upstream dependencies have not changed.
   - **Pre-Publication Gate:** Image builds for `linux/amd64` and starts the full test stack (`docker compose --profile test up -d`). Tests run via `python3 tests/run_all.py`. Publication to Docker Hub only proceeds if all 19 verification tests pass.
   - **Image Tagging & OCI Labels:** Pushes `:latest`, `:<major>`, `:<minor>`, `:<patch>`, and `:<patch>-<timestamp>-<short-sha>` (e.g. `3.12.268-20261001-1339-<commit>`). Embeds OCI metadata labels (`org.opencontainers.image.*`) covering all core component versions (Gromox, Webmail, Z-Push, Admin API, Admin Web, Rspamd, Exim, Nginx, Redis, PHP, and Debian point release).
   - **Required Secrets:** `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and optional `DOCKERHUB_REPOSITORY` (defaults to `${DOCKERHUB_USERNAME}/grommunio-docker`).

8. **Dedicated Host Port Mapping Architecture:**
   - In-container background daemons (Nginx, Exim, Gromox, PHP-FPM, uWSGI) strictly listen on fixed standard ports (`8080`, `8081`, `11334`, `2525`, `24`, `10080`) within the isolated container network namespace.
   - Environment variables (`HTTP_PORT`, `ADMIN_PORT`, `RSPAMD_PORT`, `SMTP_PORT`, `DB_PORT`, `LDAP_PORT`, `BIND_IP`) configure host-exposed ingress bindings mapped via `docker-compose.yml`.
   - In-container healthchecks and init-defaults scripts query internal loopback listeners, decoupling container health status from external host-port customizations.
   - Host verification test scripts dynamically resolve host-mapped ingress ports via `tests/env_helper.py`.

9. **Fail-Safe Database Initialization & Multi-Replica Advisory Locking:**
   - Database operations in `entrypoint.sh` acquire a MariaDB advisory lock (`GET_LOCK('grommunio_schema_lock', 120)`), serializing concurrent container startups and avoiding schema creation / migration race conditions across multiple replicas or restarts.
   - Startup traps separate `EXIT` (`cleanup_db_lock`) and `INT`/`TERM` (`'exit 143'`) so a stop signal aborts startup immediately with status 143 and releases the advisory lock exactly once, before disarming via `trap - EXIT INT TERM` prior to `supervisord` execution.
   - Table count queries strictly check command exit codes and integer formats, ensuring connection errors or query failures halt startup rather than falsely signaling an empty database.
   - Schema creation (`gromox-dbop -CC` / `-C`), migrations (`gromox-dbop -U`), and initial admin password provisioning run fail-fast without `|| true` fallbacks, logging actionable diagnostics upon failure.

10. **Persistent Log Growth Bounding & 1-Year Retention:**
    - Standard `logrotate` runs periodically via `grommunio-logrotate` under Supervisor, tracking rotation state on `/var/log/logrotate.status`.
    - Retains 53 weekly archives (covering 371 days > 1 full year) with `maxage 365` and `maxsize 25M` per file, using `copytruncate` so active daemons continue writing to original file handles.
    - Uses gzip compression with `delaycompress` so the most recent rotated file (`.1`) remains uncompressed, allowing Admin UI log tailing to seamlessly bridge rotation events without gaps.
    - Container stdout/stderr is bounded to 30MB via Docker Compose `json-file` logging (`max-size: 10m`, `max-file: 3`), and `container-healthcheck.sh` monitors persistent disk utilization to detect low disk space before it disrupts mail delivery.

11. **Constant-Time Mailbox Volume Ownership & On-Demand Repair:**
    - Startup logic applies constant-time ($O(1)$) shallow ownership to `/var/lib/gromox` and immediate subdirectories without recursively traversing user mailbox stores, keeping container start time under 5ms regardless of mailbox count or size.
    - A dedicated repair script (`/usr/local/bin/grommunio-fix-permissions.sh`) and opt-in environment flag (`GROMOX_FIX_PERMISSIONS=true`) provide on-demand recursive ownership restoration when migrating existing volumes or restoring external backups.

12. **Batched Bayes Synchronization & Scan Cost Reduction:**
    - Background Bayes training executes via `/usr/local/bin/grommunio-bayes-sync.py`, attaching each mailbox database (`exchange.sqlite3`) to `/var/lib/rspamd/bayes_learned.sqlite3` in a single transaction.
    - Executes set-based differential queries, completely eliminating per-message subprocess forks and reducing steady-state scan cost to under 2ms per mailbox.
    - Correctly handles spam learning (Junk `23`), ham learning on moves (Inbox `19`), trash pruning (Deleted Items `27`), expunge cleanup, and transient Rspamd connection failures.

13. **Backup, Disaster Recovery & Storage Consistency:**
    - Operational backup procedures, storage inventory, and clean-host disaster recovery procedures are codified in `BACKUP_AND_RESTORE.md`.
    - Transactional MariaDB dumps (`mariadb-dump --single-transaction --quick`) preserve ACID consistency while services run.
    - Mailbox store recovery integrity is validated via `sqlite3 PRAGMA integrity_check;`, and post-restore ownership is restored via `/usr/local/bin/grommunio-fix-permissions.sh`.
