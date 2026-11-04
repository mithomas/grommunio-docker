# Backup & Disaster Recovery Playbook

This document defines the operational backup strategy, point-in-time consistency requirements, off-site storage considerations, and step-by-step restoration procedures for **Grommunio Docker**.

---

## 1. Storage & Volume Inventory

A Grommunio deployment divides state across persistent volumes, configuration files, and regenerable runtime caches:

| Component | Storage Path / Volume | Nature | Backup Priority | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Mailbox Stores** | `gromox_data` (`/var/lib/gromox`) | Persistent | **Critical** | User mailboxes, SQLite databases (`exchange.sqlite3`), message index files, and attachment blobs. |
| **Relational Database** | `mariadb_data` (`/var/lib/mysql`) | Persistent | **Critical** | Users, domains, aliases, orgs, permissions, and system defaults. |
| **Directory Service** | `ldap_data` (`/var/lib/ldap`) | Persistent | **Critical** | OpenLDAP directory tree (when using companion LDAP). |
| **Environment & Secrets** | `.env`, `.env.secrets` | Host Files | **Critical** | Passwords, tokens, domain bindings, and configuration overrides. |
| **Antispam State** | `antispam_data` (`/var/lib/rspamd`) | Persistent | **Important** | Rspamd Bayes learned database (`bayes_learned.sqlite3`), DKIM keys, and statistics. |
| **Admin API State** | `admin_data`, `admin_api_data` | Persistent | **Important** | Session tokens, audit logs, and API preferences. |
| **Mail Queue** | `exim_spool` (`/var/spool/exim4`) | Persistent | **Low** | In-flight transient mail spool. Only needed if messages were queued during outage. |
| **Redis Cache** | `redis_data` (`/var/lib/redis`) | Persistent | **Regenerable** | Ephemeral cache and Rspamd transient state. Automatically rebuilt if lost. |
| **Service Logs** | `log_data` (`/var/log`) | Persistent | **Optional** | Rotated application logs. Not required for service recovery. |
| **Runtime Buffers** | `/run`, `/tmp`, `/var/tmp` | `tmpfs` | **Ignore** | Ephemeral sockets, PIDs, and session caches. Regenerated on container start. |

---

## 2. Consistency & Quiescence Requirements

1. **Relational Database & Mailbox Store Synchronization:**
   - MariaDB holds user account records and domain mappings, while `/var/lib/gromox` holds the actual mailbox SQLite stores (`user/<uid>/0/exchange.sqlite3`).
   - If MariaDB is restored to point-in-time $T_1$ while `/var/lib/gromox` is at $T_2$, newly created users or moved messages may desynchronize.
   - **Recommended Approach:** Coordinate backups so that MariaDB dump and volume snapshots occur simultaneously, or stop the `grommunio` container briefly during nightly backups.

2. **Live Database Dumps (`mariadb-dump`):**
   - Use `--single-transaction --quick` to produce an ACID-consistent snapshot without locking tables or interrupting mail delivery.

3. **Mailbox Store Snapshotting:**
   - Because Gromox stores are SQLite databases, taking file-level backups while the container is actively writing can capture uncommitted WAL pages.
   - For 100% clean snapshots:
     - **Option A (Scheduled Maintenance Window):** Run `docker compose stop grommunio`, archive `/var/lib/gromox`, and run `docker compose up -d grommunio`.
     - **Option B (Online LVM/ZFS/Btrfs Snapshot):** Take an atomic storage snapshot at the host volume layer.

---

## 3. Automated Backup Procedure

Run the following commands on the host (or configure them in a cron script):

### Step 1: Dump MariaDB Database
```bash
BACKUP_DIR="/var/backups/grommunio/$(date +%Y%m%d_%H%M%S)"
mkdir -p "${BACKUP_DIR}"

# Dump all MariaDB databases with transactional consistency
docker compose exec -T mariadb mariadb-dump \
  -u root -p"${DB_ROOT_PASSWORD:-root_secret}" \
  --single-transaction --quick --all-databases \
  > "${BACKUP_DIR}/mariadb_all.sql"
```

### Step 2: Dump OpenLDAP Directory (if using companion LDAP)
```bash
# Export LDAP directory tree via slapcat
docker compose exec -T openldap slapcat -b "${LDAP_SEARCH_BASE:-dc=example,dc=com}" \
  > "${BACKUP_DIR}/ldap_backup.ldif"
```

### Step 3: Archive Persistent Volumes
```bash
# Optional: quiesce container for clean SQLite checkpoint
docker compose stop grommunio

# Archive mailbox stores, Rspamd state, and Admin API data
tar -czf "${BACKUP_DIR}/gromox_data.tar.gz" -C /var/lib/docker/volumes/grommunio_gromox_data/_data .
tar -czf "${BACKUP_DIR}/antispam_data.tar.gz" -C /var/lib/docker/volumes/grommunio_antispam_data/_data .
tar -czf "${BACKUP_DIR}/admin_data.tar.gz" -C /var/lib/docker/volumes/grommunio_admin_data/_data .

# Restart container immediately after archive
docker compose up -d grommunio
```

### Step 4: Archive Configuration & Environment
```bash
cp .env .env.secrets "${BACKUP_DIR}/"
tar -czf "${BACKUP_DIR}/config_files.tar.gz" -C . docker-compose.yml ldap-bootstrap/
```

### Step 5: Encrypt and Replicate Off-Site
```bash
# Encrypt backup archive with GPG or Age before sending off-host
tar -cf - "${BACKUP_DIR}" | gpg --symmetric --cipher-algo AES256 -o "${BACKUP_DIR}.tar.gpg"

# Replicate to off-site storage (Rsync / Rclone / S3)
rclone copy "${BACKUP_DIR}.tar.gpg" remote:secure-backups/grommunio/
```

---

## 4. Disaster Recovery & Clean-Host Restoration

Follow this procedure to restore Grommunio on a clean host:

### Prerequisites on Clean Host
- Linux with Docker Engine 24+ and Docker Compose v2 installed.
- Access to the encrypted backup archive.

### Restoration Steps

#### 1. Clone Repository & Restore Configuration
```bash
git clone https://github.com/mithomas/grommunio-docker.git /opt/grommunio-docker
cd /opt/grommunio-docker

# Decrypt and unpack backup archive
gpg -d /path/to/backup.tar.gpg | tar -xf -

# Restore environment files
cp <unpacked_backup>/.env <unpacked_backup>/.env.secrets .
```

#### 2. Start Database and Directory Services
```bash
docker compose up -d mariadb openldap
```
Wait ~10 seconds for MariaDB and OpenLDAP to initialize listeners.

#### 3. Restore Database & LDAP Data
```bash
# Restore MariaDB databases
docker compose exec -T mariadb mariadb \
  -u root -p"${DB_ROOT_PASSWORD:-root_secret}" \
  < <unpacked_backup>/mariadb_all.sql

# Restore OpenLDAP directory tree
docker compose exec -T openldap slapadd \
  -b "${LDAP_SEARCH_BASE:-dc=example,dc=com}" \
  < <unpacked_backup>/ldap_backup.ldif
```

#### 4. Restore Mailbox & Antispam Volume Data
```bash
# Create persistent volumes if not already present
docker compose up --no-start

# Extract mailbox files into gromox_data volume
GROMOX_VOL_PATH=$(docker volume inspect grommunio_gromox_data --format '{{.Mountpoint}}')
tar -xzf <unpacked_backup>/gromox_data.tar.gz -C "${GROMOX_VOL_PATH}"

# Extract antispam data into antispam_data volume
RSPAMD_VOL_PATH=$(docker volume inspect grommunio_antispam_data --format '{{.Mountpoint}}')
tar -xzf <unpacked_backup>/antispam_data.tar.gz -C "${RSPAMD_VOL_PATH}"
```

#### 5. Repair Ownership & Permissions
```bash
# Start container with repair flag to restore exact ownership
GROMOX_FIX_PERMISSIONS=true docker compose up -d grommunio

# Or run the repair utility directly inside the container:
docker compose exec grommunio /usr/local/bin/grommunio-fix-permissions.sh
```

#### 6. Launch Full Stack
```bash
docker compose up -d
```

---

## 5. Post-Restore Verification Checklist

Execute the automated test suite to confirm 100% operational integrity:

```bash
# Run full automated verification suite
python3 tests/run_all.py
```

### Manual Verification Steps
1. **Container Health:** Confirm `docker compose ps` shows `healthy` status.
2. **Supervisor Daemons:** Run `docker compose exec grommunio supervisorctl status` to verify all 14 services report `RUNNING`.
3. **SQLite Integrity:** Run an integrity check on restored mailbox databases:
   ```bash
   docker compose exec grommunio find /var/lib/gromox/user -name "exchange.sqlite3" \
     -exec sqlite3 {} "PRAGMA integrity_check;" \;
   ```
4. **Webmail Access:** Log in to `http://<host>:8080/` with your credentials and verify existing folders and messages are visible.
5. **Mail Routing:** Send a test email to confirm inbound and outbound delivery.
