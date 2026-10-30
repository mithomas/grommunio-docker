# Dockerized Grommunio on Debian 13 (Trixie)

A production-ready, single-container Grommunio deployment based on Debian 13 (Trixie) using official Grommunio community deb packages (`https://download.grommunio.com/community/Debian_13/`), inspired by upstream [grommunio/gromox-container](https://github.com/grommunio/gromox-container).

Designed for seamless integration into existing infrastructure:
- **MTA:** Host Exim delivers inbound mail via **SMTP** on port `2525` (no spam filtering needed on host).
- **In-Container Filtering:** Container Exim filters incoming mail through Rspamd (`:11333` spamd protocol) before handing off to Gromox via internal LMTP (`:24`), automatically tagging spam and populating the Admin UI **Spam History** tab.
- **Relay:** Outbound mail relays through container Exim back to host Exim on port `25`.
- **Directory:** OpenLDAP / generic LDAP integration for user identities and authentication.
- **Database:** Dedicated external MariaDB instance (or companion container for testing).
- **Security:** Hardened with an immutable root filesystem (`read_only: true`), `tmpfs` mounts for transient state, and named volumes for persistent data.
- **Lifecycle & Observability:** Built-in `HEALTHCHECK` monitoring Supervisor daemon health and HTTP responsiveness, with `STOPSIGNAL SIGTERM` and 30-second graceful stop grace period.
- **TLS Termination:** Managed on the host by your own reverse proxy.
- **Tuned Footprint:** Pre-configured and sized for **5 users with 10 ActiveSync devices**.

---

## Service Setup & Data Flow Diagram

```
+---------------------------------------------------------------------------------------------------------+
|                                              HOST ENVIRONMENT                                           |
|                                                                                                         |
|  +----------------------------------------------------+    +-----------------------------------------+  |
|  |             HOST REVERSE PROXY (TLS)               |    |             HOST MTA (EXIM)             |  |
|  |                                                    |    |                                         |  |
|  |  mail.example.com     admin.example.com antispam...|    |  Inbound MX Delivery:    Outbound Relay:|  |
|  +----------+--------------------+-------------+------+    +----------+-----------------------^------+  |
|             |                    |             |                      |                       |         |
|        HTTP | 8080          HTTP | 8081   HTTP | 11334           SMTP | 2525             SMTP | 25      |
|             |                    |             |                      |                       |         |
+-------------|--------------------|-------------|----------------------|-----------------------|---------+
              |                    |             |                      |                       |
+-------------v--------------------v-------------v----------------------v-----------------------+---------+
|                                    CONTAINER: grommunio (read_only: true)                               |
|                                                                                                         |
|  +-- [ NGINX INGRESS & BRIDGES ] --------------------------------------------------------------------+  |
|  |  Port 8080:  /                             -> fastcgi://unix:/run/php/php-fpm.sock (Webmail)      |  |
|  |              /Microsoft-Server-ActiveSync  -> fastcgi://unix:/run/php/php-fpm.sock (EAS long-poll) |  |
|  |              /Autodiscover/                -> http://127.0.0.1:10080 (gromox-http)                |  |
|  |  Port 8081:  /                             -> static admin-web HTML5 UI                            |  |
|  |              /api/                         -> uwsgi://unix:/run/grommunio-admin-api/uwsgi.sock     |  |
|  |  Port 11334: /                             -> http://127.0.0.1:11335/ (Dedicated Rspamd UI/API)   |  |
|  |  Port 11336: (Internal loopback bridge for Admin API -> Rspamd with password injection)           |  |
|  +---------------------------------------------------------------------------------------------------+  |
|                                                                                                         |
|  +-- [ IN-CONTAINER EXIM MTA & ANTISPAM PIPELINE ] --------------------------------------------------+  |
|  |                                                                                                   |  |
|  |  [ Container Exim (:2525) ] <---------------------------------- (Inbound mail from host Exim)     |  |
|  |        |                                                                                          |  |
|  |        +---> [ Rspamd (:11333 spamd) ] (Inspects mail, tags spam, logs to Redis buffer)           |  |
|  |        |                                                                                          |  |
|  |        v                                                                                          |  |
|  |  [ Gromox Delivery Queue (:24 LMTP) ]                                                             |  |
|  |        |                                                                                          |  |
|  |        v                                                                                          |  |
|  |  [ Gromox Delivery (LDA) ] (Moves X-Spam=Yes to Junk, clean to Inbox)                             |  |
|  |        |                                                                                          |  |
|  |        +---> (Outbound mail relays: Gromox -> Container Exim -> Host Exim:25)                     |  |
|  +---------------------------------------------------------------------------------------------------+  |
|                                                                                                         |
|  +-- [ APPLICATION WORKERS & DAEMONS (Managed by Supervisord) ] -------------------------------------+  |
|  |                                                                                                   |  |
|  |  * CONTAINER EXIM (MTA listener :2525, Rspamd spamd filter, internal LMTP transport)             |  |
|  |  * PHP-FPM POOL (Dynamic: 25 max workers sized for 10 EAS long-polling pings + 5 webmail users)   |  |
|  |  * GROMMUNIO ADMIN API (uWSGI: 2 workers, 2 threads, connects to :11336 for Spam History)         |  |
|  |  * GROMOX CORE DAEMONS (gromox-http :10080, gromox-midb, gromox-zcore, gromox-event, timer)      |  |
|  |  * REDIS (Local cache & session store, maxmemory 128MB, volatile-lru)                            |  |
|  |  * RSPAMD (Antispam daemon, controller listening on 127.0.0.1:11335)                              |  |
|  |  * GROMMUNIO-BAYES-SYNC (Bidirectional learning: auto-trains Junk spam & trains ham on move)     |  |
|  +---------------------------------------------------------------------------------------------------+  |
|                                                                                                         |
|  +-- [ TRANSIENT MEMORY (tmpfs) ] ------------------+  +-- [ PERSISTENT DATA (Named Volumes) ] ------+  |
|  |  /run:       Sockets & PIDs (64MB)               |  |  grommunio_gromox_data   -> /var/lib/gromox |  |
|  |  /tmp, /var/tmp: Scratch space (128MB / 64MB)    |  |  grommunio_antispam_data -> /var/lib/rspamd |  |
|  |  /var/lib/nginx, /var/lib/php/sessions (64M / 16M)|  |  grommunio_redis_data    -> /var/lib/redis  |  |
|  |  /etc/exim4, /etc/gromox, /etc/admin-api (16M)   |  |  grommunio_exim_spool    -> /var/spool/exim4|  |
|  |                                                  |  |  grommunio_log_data      -> /var/log        |  |
|  +--------------------------------------------------+  +---------------------------------------------+  |
+------------------------------------------+-------------------------------------+------------------------+
                                           |                                     |
                                           | TCP: 3306                           | LDAP: 389 / 636
                                           v                                     v
                        +------------------------------------+ +-----------------------------------+
                        |    EXTERNAL MARIADB DATABASE       | |       EXTERNAL OPENLDAP SERVER    |
                        | (Testing container profile: test)  | |    (Authentication & Directory)   |
                        +------------------------------------+ +-----------------------------------+
```

---

## Pool Sizing & Resource Allocation (5 Users / 10 Devices)

ActiveSync and groupware daemons have specific concurrency characteristics that differ from standard web applications. This configuration is tuned for **5 active users** with an aggregate of **10 ActiveSync devices** (phones, tablets, desktop clients):

### 1. PHP-FPM Worker Pool
* **ActiveSync Long-Polling (`PING` command):** Exchange ActiveSync devices hold HTTP connections open (heartbeats typically lasting between 15 and 30 minutes) waiting for server-side push notifications. Each connected device occupies an active PHP-FPM worker thread for the duration of its ping cycle.
* **Sizing Formula:**
  $$\text{max\_children} = (\text{EAS Devices} \times 1.5) + (\text{Webmail Concurrent Users} \times 2)$$
  For 10 devices and 5 users: $15 + 10 = \mathbf{25\text{ workers}}$.
* **Configured Pool Directives (`www.conf`):**
  - `pm = dynamic`
  - `pm.max_children = 25`: Ensures 10 devices can maintain continuous push pings simultaneously without queueing or blocking Webmail requests.
  - `pm.start_servers = 4`: Initial warm workers.
  - `pm.min_spare_servers = 2`: Minimum idle headroom.
  - `pm.max_spare_servers = 8`: Maximum idle headroom during low-activity periods.
  - `pm.max_requests = 1000`: Periodically recycles workers to prevent memory fragmentation.
* **Memory Footprint:** Each PHP worker consumes ~25MB to 30MB of RAM. Baseline idle usage is ~100MB; worst-case peak with all 25 workers active is ~650MB to 750MB.

### 2. Redis Cache & State Store
* **Role:** Stores transient ActiveSync push threads, device sync state locks, and Rspamd Bayes cache tokens.
* **Tuning Parameters:**
  - `--maxmemory 128mb`: Caps memory usage to prevent unbounded cache growth.
  - `--maxmemory-policy volatile-lru`: Evicts expired or least-recently-used cache keys when memory limit is reached.
* **Memory Footprint:** ~30MB to 60MB under typical load.

### 3. Gromox Core Services
* **Local Delivery Agent (`delivery.cfg`):**
  - `work_threads_min = 1`
  - `work_threads_max = 5`: Up to 5 parallel delivery threads can ingest incoming mail concurrently.
* **LMTP Delivery Queue (`smtp.cfg`):**
  - `lda_thread_init_num = 1`
  - `lda_thread_charge_num = 20`: Maximum connections processed per worker thread.
  - `context_num = 50`: Client context buffer pool.
* **Memory Footprint:** The combined Gromox daemons (`http`, `midb`, `zcore`, `event`, `timer`, `delivery`, `delivery-queue`) consume ~150MB to 250MB.

### 4. Admin API (uWSGI)
* **Configuration:** 2 worker processes with 2 threads (`processes = 2`, `threads = 2`).
* **Memory Footprint:** ~80MB.

### Total Container Footprint Summary
| Component | Idle / Baseline RAM | Peak / Active RAM |
| :--- | :--- | :--- |
| **PHP-FPM (25 workers max)** | ~100 MB | ~700 MB |
| **Gromox Core Daemons** | ~180 MB | ~250 MB |
| **Rspamd + Bayes Sync** | ~70 MB | ~110 MB |
| **Redis** | ~30 MB | ~60 MB |
| **Nginx + Supervisord** | ~30 MB | ~50 MB |
| **Admin API (uWSGI)** | ~80 MB | ~90 MB |
| **TOTAL** | **~490 MB** | **~1.25 GB** |

*Recommended host allocation:* **1.5 GB to 2.0 GB RAM** and **1 to 2 vCPUs**.

---

## Quick Start

### 1. Configure Environment
Copy the example environment file and customize your domain, database credentials, and LDAP settings:

```bash
cp .env.example .env
```

Key variables to inspect:
- `GROMMUNIO_HOST` & `GROMMUNIO_DOMAIN`: Your mail domain (e.g. `mail.example.com`).
- `HTTP_PORT` (default `8080`): Port for Webmail and ActiveSync.
- `ADMIN_PORT` (default `8081`): Port for Admin Web UI.
- `RSPAMD_PORT` (default `11334`): Dedicated port for Rspamd Web Dashboard.
- `LMTP_PORT` (default `2524`): Port for Exim LMTP inbound hand-off.
- `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`: MariaDB connection details.
- `ADMIN_PASSWORD` (default `admin_secret`): Initial password for Grommunio Admin UI user `admin`.
- `RSPAMD_PASSWORD` (default `rspamd_secret`): Dedicated password for Rspamd Web Dashboard.
- `LDAP_HOST`, `LDAP_BIND_USER`, `LDAP_BIND_PASS`, `LDAP_SEARCH_BASE`: OpenLDAP connection.

### 2. Pull or Build the Docker Image

**Option A: Use the pre-built image from Docker Hub ([`mithomas/grommunio-docker`](https://hub.docker.com/r/mithomas/grommunio-docker)):**
```bash
docker pull mithomas/grommunio-docker:latest
echo "IMAGE_NAME=mithomas/grommunio-docker:latest" >> .env
```

**Option B: Build the image locally:**
```bash
docker compose build
```

### 3. Start the Stack

**Testing with companion MariaDB and OpenLDAP containers:**
```bash
docker compose --profile test up -d
```
*(This launches the `grommunio` container along with `mariadb` and `openldap` seeded with `testuser@example.com` and group `grommunio-users` via `ldap-bootstrap/01-init.ldif`.)*

**Production with external MariaDB & LDAP:**
Ensure your external database is created (`CREATE DATABASE grommunio;`) and grants are applied for your database user, then start:
```bash
docker compose up -d
```

On first startup, the container will:
1. Wait for database connectivity.
2. Initialize database schemas (`gromox-dbop -CC`) strictly inside the target database.
3. Apply schema upgrades (`gromox-dbop -U`).
4. Set the initial admin password (`grommunio-admin passwd`).
5. Launch all services via `supervisord`.

---

## Host Integration

### 1. Host Exim Integration

#### A. Inbound Delivery via SMTP (No Spam Filtering Needed on Host)
Host Exim simply forwards all email destined for Grommunio domains to container port `2525`. Postfix in the container automatically runs it through Rspamd Milter, logs history, and delivers to Gromox:

```exim
# /etc/exim4/conf.d/transport/30_grommunio_smtp
grommunio_smtp:
  driver = smtp
  port = 2525
  hosts = 127.0.0.1
  allow_localhost
```

Add an Exim router before your local user router:

```exim
# /etc/exim4/conf.d/router/850_grommunio_router
grommunio_router:
  driver = accept
  domains = +grommunio_domains
  transport = grommunio_smtp
  no_more
```

#### B. Outbound Relay
Allow the Docker container to relay outbound mail through host Exim:
In your Exim configuration (`relay_from_hosts`):
```exim
hostlist relay_from_hosts = 127.0.0.1 : 172.16.0.0/12
```

---

### 2. Host Reverse Proxy (TLS Termination)

#### Example Nginx Configuration

```nginx
# Webmail & ActiveSync (mail.example.com)
server {
    listen 443 ssl http2;
    server_name mail.example.com;

    ssl_certificate /etc/letsencrypt/live/mail.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mail.example.com/privkey.pem;

    client_max_body_size 128M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}

# Admin Web & API (admin.example.com)
server {
    listen 443 ssl http2;
    server_name admin.example.com;

    ssl_certificate /etc/letsencrypt/live/admin.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/admin.example.com/privkey.pem;

    client_max_body_size 64M;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
    }
}

# Dedicated Rspamd Dashboard (antispam.example.com)
server {
    listen 443 ssl http2;
    server_name antispam.example.com;

    ssl_certificate /etc/letsencrypt/live/antispam.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/antispam.example.com/privkey.pem;

    client_max_body_size 32M;

    location / {
        proxy_pass http://127.0.0.1:11334;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
    }
}
```

---

## Administration & CLI

### Container Health & Lifecycle Status
Check container health state reported by Docker:
```bash
docker inspect --format='{{json .State.Health.Status}}' grommunio
```
Directly run the internal health check probe:
```bash
docker compose exec grommunio /usr/local/bin/container-healthcheck.sh
```

### Check Running Services
```bash
docker compose exec grommunio supervisorctl status
```

### Inspect Bayes Learning Statistics
```bash
docker compose exec grommunio rspamc stat
```

Trigger an immediate Bayes sync:
```bash
docker compose exec grommunio /usr/local/bin/grommunio-bayes-sync.sh
```

### Synchronize Users from OpenLDAP

> **Mailbox Auto-Creation Behavior:**
> Mailboxes are **not** automatically created Just-In-Time (JIT) when an LDAP user logs in for the first time. Gromox requires user account records, routing metadata, and store directory paths to already exist in MariaDB before a user session can be authenticated. Gromox's LDAP adaptor functions strictly as an authentication backend (LDAP BIND) for accounts already registered in MariaDB.
>
> To provision mailboxes for LDAP users, ensure the domain is created and execute LDAP synchronization (`downsync`):

1. Verify domain registration:
```bash
docker compose exec grommunio grommunio-admin domain create example.com
```

2. Test OpenLDAP connection:
```bash
docker compose exec grommunio grommunio-admin ldap info
```

3. Search LDAP directory (e.g. for bootstrap user):
```bash
docker compose exec grommunio grommunio-admin ldap search testuser
```

4. Perform user synchronization and mailbox provisioning:
```bash
docker compose exec grommunio grommunio-admin ldap downsync -c
```
*(This creates the MariaDB user record, initializes the SQLite/exmdb store under `/var/lib/gromox/user/example.com/testuser`, and assigns mailbox permissions. After this command, `testuser@example.com` can log in immediately with password `testuser_secret`.)*

#### Initial Seed Accounts & Groups

When launching the companion OpenLDAP service (`docker compose --profile test up -d`), the directory is pre-seeded via `ldap-bootstrap/01-init.ldif` with the following credentials:

| Attribute | Value |
| :--- | :--- |
| **Username (UID)** | `testuser` |
| **User DN** | `uid=testuser,ou=users,dc=example,dc=com` |
| **Email Address** | `testuser@example.com` |
| **Email Alias** | `alias.testuser@example.com` |
| **Password** | `testuser_secret` |
| **Display Name** | `Test User` |
| **Group Membership** | `cn=grommunio-users,ou=groups,dc=example,dc=com` (`grommunio-users@example.com`) |
| **Directory Admin DN** | `cn=admin,dc=example,dc=com` (Password: `ldap_secret`) |

### Modify User Permissions (e.g. enable ActiveSync)
```bash
docker compose exec grommunio grommunio-admin user modify user@example.com --privEas 1
```

### Reset Admin Password
```bash
docker compose exec grommunio grommunio-admin passwd <new_password>
```

---

## Volume Management

All persistent data is stored in named Docker volumes:
- `grommunio_gromox_data`: Mailboxes, SQLite indices, and store metadata (`/var/lib/gromox`).
- `grommunio_antispam_data`: Rspamd Bayes tracking state, custom maps, and stats (`/var/lib/rspamd`).
- `grommunio_redis_data`: Redis snapshots, active sync tokens, and Bayes cache (`/var/lib/redis`).
- `grommunio_exim_spool`: Exim mail queues, retry database, and pending relays (`/var/spool/exim4`).
- `grommunio_log_data`: Persistent daemon and MTA log files for auditing and Monitoring UI (`/var/log`).
- `grommunio_admin_data`: Admin API security state and configurations (`/var/lib/grommunio-admin-common`).
- `grommunio_admin_api_data`: Admin API state database and session stores (`/var/lib/grommunio-admin-api`).
- `grommunio_ldap_data`: OpenLDAP database directory (`/var/lib/ldap`) when running the test profile.
- `grommunio_ldap_config`: OpenLDAP configuration directory (`/etc/ldap/slapd.d`) when running the test profile.

---

## Automated Test Suite

A standalone test suite validating all container services, endpoints, credentials, and the mail pipeline is available under `tests/`:

| Test Script | Scope |
| :--- | :--- |
| **`tests/test_services.py`** | Asserts all 14 supervisor background processes report `RUNNING`. |
| **`tests/test_endpoints.py`** | Validates HTTP status codes and content-types across ports `8080`, `8081`, and `11334`. |
| **`tests/test_webmail_security.py`** | Validates Webmail root delivery (`:8080`), secure cookies, static assets, and absence of PHP errors. |
| **`tests/test_admin_auth.py`** | Verifies Admin API authentication, JWT token generation, and protected endpoints. |
| **`tests/test_rspamd_auth.py`** | Verifies password protection on dedicated Rspamd dashboard (`:11334`). |
| **`tests/test_mail_pipeline.py`** | End-to-end SMTP injection (`:2525`) -> Rspamd scan (`:11333`) -> Gromox LMTP (`:24`) -> Admin Spam History. |
| **`tests/test_live_status.py`** | Validates Admin UI Live Status endpoint (`:8081`), VTS telemetry, and hop-by-hop header filtering. |
| **`tests/test_monitoring_logs.py`** | Validates Admin UI Monitoring logs (`:8081`), file-based readers, and Exim log replacement for Postfix. |
| **`tests/test_defaults.py`** | Validates Admin UI mailbox creation defaults (`:8081`), language, protocols, services, integrations, and quotas. |
| **`tests/test_ldap_config.py`** | Validates default group mapping population in `/system/mconf/ldap` and disk persistence without permission errors. |
| **`tests/test_ldap.py`** | Validates companion OpenLDAP (`:389`), directory search, and mailbox provisioning via `downsync`. |

### Run All Tests
```bash
python3 tests/run_all.py
```

---

## Automated Builds & Docker Hub CI/CD

The repository includes a production-grade GitHub Actions workflow (`.github/workflows/build-and-publish.yml`) that automates container builds, integration testing, and publishing to Docker Hub at [`mithomas/grommunio-docker`](https://hub.docker.com/r/mithomas/grommunio-docker).

### Smart Upstream Update Detection
Rather than performing blind rebuilds, the workflow uses a two-stage detection pipeline:
1. **Periodic Scan:** Runs every 6 hours (`0 */6 * * *`) via GitHub Actions cron.
2. **Digest Check:** Queries the Docker Hub Registry API for `debian:trixie-slim` digest and the official Grommunio Debian 13 repository (`https://download.grommunio.com/community/Debian_13/dists/Debian_13/Release`) for the `SHA256` checksum of `main/binary-amd64/Packages`.
3. **Cache Evaluation:** If neither upstream source has changed and no local code commits occurred, the workflow terminates within seconds to conserve GitHub Actions runner minutes.
4. **Automatic Rebuild:** If an upstream Debian base update or new Grommunio deb packages are published, the workflow automatically builds, tests, and publishes the updated container image.

### Quality Gate (Integration Testing)
Before any image is pushed to Docker Hub:
- Docker Buildx compiles the image for `linux/amd64`.
- The full companion test stack (`docker compose --profile test up -d`) launches MariaDB, OpenLDAP, and Grommunio.
- Container health is verified via `/usr/local/bin/container-healthcheck.sh`.
- The entire 11-step verification test suite (`python3 tests/run_all.py`) is executed inside the CI runner.
- The workflow only pushes to Docker Hub if 100% of the tests pass. Defective builds or broken upstream packages are blocked automatically.

### Image Tagging Convention
Each published image is tagged with:
- `:latest`: Always points to the most recent verified stable release.
- `:<major>`: Floating major release tag (e.g. `3`).
- `:<minor>`: Floating minor release tag (e.g. `3.12`).
- `:<patch>`: Floating patch release tag (e.g. `3.12.268`).
- `:<patch>-<timestamp>-<commit>`: Immutable build fingerprint combining Gromox patch version, UTC timestamp (`YYYYMMDD-HHMM`), and short Git commit SHA (e.g. `3.12.268-20261001-1339-a1b2c3d`).
- `:<git-tag>`: Preserved if triggered by a Git tag push (e.g. `v1.0.0`).

### OCI Image Metadata Labels
Every published image is embedded with standard OCI labels (`org.opencontainers.image.*`) detailing the exact component versions bundled inside the build:

| OCI Label Key | Content / Description |
| :--- | :--- |
| `org.opencontainers.image.created` | Build timestamp in RFC 3339 format |
| `org.opencontainers.image.revision` | Full Git commit SHA |
| `org.opencontainers.image.version.gromox` | Gromox core server version |
| `org.opencontainers.image.version.webmail` | Grommunio Webmail version |
| `org.opencontainers.image.version.zpush` | Grommunio Sync (ActiveSync / Z-Push) version |
| `org.opencontainers.image.version.admin-api` | Grommunio Admin REST API version |
| `org.opencontainers.image.version.admin-web` | Grommunio Admin Web UI version |
| `org.opencontainers.image.version.rspamd` | Rspamd spam filter version |
| `org.opencontainers.image.version.exim` | Exim4 MTA version |
| `org.opencontainers.image.version.nginx` | Nginx web server version |
| `org.opencontainers.image.version.redis` | Redis server version |
| `org.opencontainers.image.version.php` | PHP runtime version |
| `org.opencontainers.image.version.debian` | Debian base point release |

Inspect labels on any local or pulled image:
```bash
docker inspect --format '{{json .Config.Labels}}' mithomas/grommunio-docker:latest | jq .
```

### Required GitHub Secrets
To enable Docker Hub publishing, configure the following secrets in your GitHub repository (**Settings -> Secrets and variables -> Actions**):

| Secret | Description | Required |
| :--- | :--- | :--- |
| **`DOCKERHUB_USERNAME`** | Docker Hub username or organization name. | Yes |
| **`DOCKERHUB_TOKEN`** | Docker Hub Personal Access Token (PAT) with `Read & Write` permissions. | Yes |
| **`DOCKERHUB_REPOSITORY`** | Custom repository name (e.g. `mithomas/grommunio-docker`). Defaults to `${DOCKERHUB_USERNAME}/grommunio-docker` if omitted. | Optional |

*(Note: Pull requests and forks automatically run the build and test suite, but skip Docker Hub publication.)*

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See the [`LICENSE`](LICENSE) file for the full text, aligned with the upstream Grommunio ecosystem.

---

For developer guidelines, operational standards, and architecture details, refer to [`AGENTS.md`](AGENTS.md).
