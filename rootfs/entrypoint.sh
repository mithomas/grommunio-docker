#!/bin/bash
set -eo pipefail

echo "==> Initializing Grommunio container on Debian 13 (Trixie)..."

# 1. Environment defaults & substitution variable table
DEFAULTS_TABLE="
GROMMUNIO_HOST=mail.example.com
GROMMUNIO_DOMAIN=example.com
DOMAINS=
RELAY_HOST=host.docker.internal
RELAY_PORT=25
DB_HOST=mariadb
DB_PORT=3306
DB_SOCKET=
DB_NAME=grommunio
DB_USER=grommunio
DB_PASSWORD=grommunio_secret
ADMIN_PASSWORD=admin
LDAP_HOST=ldap://ldap.example.com
LDAP_START_TLS=false
LDAP_BIND_USER=cn=admin,dc=example,dc=com
LDAP_BIND_PASS=ldap_secret
LDAP_SEARCH_BASE=dc=example,dc=com
LDAP_USER_FILTER=(objectClass=posixAccount)
LDAP_OBJECT_ID=entryUUID
LDAP_MAIL_ATTR=mail
LDAP_USER_ALIASES=mailAlternativeAddress
LDAP_USER_DISPLAYNAME=displayName
LDAP_USER_ALTNAME=uid
LDAP_GROUP_MEMBER_ATTR=memberOf
LDAP_GROUP_ADDR=mailPrimaryAddress
LDAP_GROUP_FILTER=(objectclass=posixgroup)
LDAP_GROUP_NAME=cn
"

VAR_LIST=""
while IFS='=' read -r var default || [ -n "${var}" ]; do
    [ -z "${var}" ] && continue
    eval "export ${var}=\"\${${var}:-\${default}}\""
    VAR_LIST="${VAR_LIST} \${${var}}"
done <<< "${DEFAULTS_TABLE}"

# Non-substituted daemon/script runtime variables
export RSPAMD_PASSWORD="${RSPAMD_PASSWORD:-rspamd_secret}"
export TIMEZONE="${TIMEZONE:-UTC}"
export BAYES_SYNC_INTERVAL="${BAYES_SYNC_INTERVAL:-300}"
export LOGROTATE_INTERVAL="${LOGROTATE_INTERVAL:-3600}"
export LDAP_ENABLED="${LDAP_ENABLED:-true}"

if [ "${LDAP_ENABLED}" = "true" ]; then
    export GROMOX_AUTH_BACKEND="ldap"
    export LDAP_DISABLED="false"
else
    export GROMOX_AUTH_BACKEND="deny_all"
    export LDAP_DISABLED="true"
fi
VAR_LIST="${VAR_LIST} \${GROMOX_AUTH_BACKEND} \${LDAP_DISABLED}"

if [ -n "${DB_SOCKET}" ]; then
    export DB_HOST="localhost"
elif [ "${DB_HOST}" = "mariadb" ] && [ "${DB_PORT}" != "3306" ]; then
    echo "==> Companion MariaDB detected on internal network; using container port 3306 (DB_PORT=${DB_PORT} maps host ingress)..."
    export DB_PORT="3306"
fi

# Check for insecure default credentials
INSECURE_WARNINGS=""
[ "${ADMIN_PASSWORD}" = "admin" ] && INSECURE_WARNINGS="${INSECURE_WARNINGS} ADMIN_PASSWORD (admin)"
[ "${DB_PASSWORD}" = "grommunio_secret" ] && INSECURE_WARNINGS="${INSECURE_WARNINGS} DB_PASSWORD (grommunio_secret)"
[ "${RSPAMD_PASSWORD}" = "rspamd_secret" ] && INSECURE_WARNINGS="${INSECURE_WARNINGS} RSPAMD_PASSWORD (rspamd_secret)"
[ "${LDAP_BIND_PASS}" = "ldap_secret" ] && INSECURE_WARNINGS="${INSECURE_WARNINGS} LDAP_BIND_PASS (ldap_secret)"

if [ -n "${INSECURE_WARNINGS}" ]; then
    echo "WARNING: Default credentials detected in environment:${INSECURE_WARNINGS}." >&2
    echo "         Please configure custom passwords in production!" >&2
    if [ "${ENFORCE_SECURE_PASSWORDS:-true}" = "true" ]; then
        echo "ERROR: Refusing to start with default credentials. Set ENFORCE_SECURE_PASSWORDS=false to permit insecure defaults for development/testing." >&2
        exit 1
    fi
fi

# 2. Setup tmpfs directories
mkdir -p /run/gromox /run/php /run/redis /run/rspamd /run/supervisor /run/grommunio-admin-api /run/nginx /run/mysqld
if [ -n "${DB_SOCKET}" ] && [ "$(realpath -m "${DB_SOCKET}")" != "$(realpath -m /run/mysqld/mysqld.sock)" ]; then
    ln -sf "${DB_SOCKET}" /run/mysqld/mysqld.sock
fi
mkdir -p /var/log/gromox /var/log/supervisor /var/log/nginx /var/log/grommunio-sync /var/log/redis /var/log/rspamd /var/log/exim4
mkdir -p /var/lib/nginx/body /var/lib/nginx/fastcgi /var/lib/nginx/proxy /var/lib/nginx/scgi /var/lib/nginx/uwsgi
mkdir -p /var/lib/gromox/queue /var/lib/redis /var/lib/rspamd /var/lib/grommunio-admin-common /var/lib/grommunio-admin-api /var/lib/grommunio-web/session /var/lib/grommunio-web/tmp /var/lib/php/sessions
mkdir -p /var/spool/exim4 /etc/exim4 /etc/gromox /etc/grommunio-admin-api/conf.d /etc/grommunio-admin-common

# Create service log files dynamically for active PHP version
touch /var/log/exim4/mainlog /var/log/grommunio-admin-api.log
for php_conf in /etc/php/*/fpm/php-fpm.conf; do
    if [ -f "${php_conf}" ]; then
        php_log=$(awk -F'= ' '/^error_log/ {print $2}' "${php_conf}" | tr -d ' ')
        [ -n "${php_log}" ] && touch "${php_log}"
    fi
done

# Ensure persistent JWT RSA keypair exists for Admin API to prevent multi-worker signature mismatch
if [ ! -f /var/lib/grommunio-admin-api/auth-private.pem ] || [ ! -f /var/lib/grommunio-admin-api/auth-public.pem ]; then
    echo "==> Generating persistent JWT RSA keypair for Admin API..."
    openssl genrsa -out /var/lib/grommunio-admin-api/auth-private.pem 2048 2>/dev/null
    openssl rsa -in /var/lib/grommunio-admin-api/auth-private.pem -pubout -out /var/lib/grommunio-admin-api/auth-public.pem 2>/dev/null
    chmod 0600 /var/lib/grommunio-admin-api/auth-private.pem
    chmod 0644 /var/lib/grommunio-admin-api/auth-public.pem
    chown grommunio:grommunio /var/lib/grommunio-admin-api/auth-*.pem 2>/dev/null || true
fi

# Generate Rspamd controller password hash
RSPAMD_HASH=$(rspamadm pw -q -p "${RSPAMD_PASSWORD}" 2>/dev/null || true)
if [ -z "${RSPAMD_HASH}" ]; then
    echo "ERROR: Failed to generate Rspamd controller password hash with 'rspamadm pw'." >&2
    exit 1
fi
echo "password = \"${RSPAMD_HASH}\";" > /run/rspamd/worker-controller-password.inc
chmod 0640 /run/rspamd/worker-controller-password.inc
chown _rspamd:_rspamd /run/rspamd/worker-controller-password.inc

# Export timezone for processes
export TZ="${TIMEZONE:-UTC}"

# 3. Render configurations from templates
echo "==> Templating configuration files..."
render_dir() {
    local src="$1" dst="$2"
    mkdir -p "${dst}"
    for tmpl in "${src}"/*.template; do
        [ -f "${tmpl}" ] || continue
        local fname
        fname=$(basename "${tmpl}" .template)
        envsubst "${VAR_LIST}" < "${tmpl}" > "${dst}/${fname}"
    done
}

render_dir /etc/gromox.dist /etc/gromox
render_dir /etc/exim4.dist /etc/exim4
render_dir /etc/grommunio-admin-common.dist /etc/grommunio-admin-common

cp -a /etc/grommunio-admin-api.dist/. /etc/grommunio-admin-api/
[ -f /etc/grommunio-admin-api/uwsgi.ini ] && cp -f /etc/grommunio-admin-api/uwsgi.ini /etc/grommunio-admin-api/conf.d/
[ -f /etc/grommunio-admin-api/antispam.yaml ] && cp -f /etc/grommunio-admin-api/antispam.yaml /etc/grommunio-admin-api/conf.d/

python3 -c "
import os, yaml
config = {
    'DB': {
        'host': os.environ.get('DB_HOST', 'mariadb'),
        'port': int(os.environ.get('DB_PORT') or 3306),
        'user': os.environ.get('DB_USER', 'grommunio'),
        'pass': os.environ.get('DB_PASSWORD', 'grommunio_secret'),
        'database': os.environ.get('DB_NAME', 'grommunio')
    }
}
with open('/etc/grommunio-admin-api/conf.d/database.yaml', 'w') as f:
    yaml.dump(config, f, default_flow_style=False)
"

# 4. Wait for MariaDB database connectivity
MAX_RETRIES="${DB_WAIT_RETRIES:-45}"
COUNT=0
if [ -n "${DB_SOCKET}" ]; then
    echo "==> Checking database connection via Unix socket ${DB_SOCKET}..."
    MARIADB_CONN=(--protocol=SOCKET -S "${DB_SOCKET}")
else
    echo "==> Checking database connection to ${DB_HOST}:${DB_PORT}..."
    MARIADB_CONN=(-h "${DB_HOST}" -P "${DB_PORT}")
fi

db() {
    MYSQL_PWD="${DB_PASSWORD}" mariadb "${MARIADB_CONN[@]}" -u "${DB_USER}" "${DB_NAME}" "$@"
}

DB_ERR=""
while true; do
    if DB_ERR=$(db -e "SELECT 1;" 2>&1 >/dev/null); then
        break
    fi
    COUNT=$((COUNT + 1))
    if [ ${COUNT} -ge ${MAX_RETRIES} ]; then
        echo "ERROR: Database connection failed after ${MAX_RETRIES} retries." >&2
        echo "Last error: ${DB_ERR}" >&2
        exit 1
    fi
    echo "Waiting for MariaDB... (${COUNT}/${MAX_RETRIES}): ${DB_ERR}" >&2
    sleep 2
done
echo "==> Database reachable and responsive."

# 5. Acquire advisory lock to prevent schema initialization / migration races across replicas
echo "==> Acquiring MariaDB schema migration advisory lock..."
cleanup_db_lock() {
    if [ -n "${DB_LOCK_COPROC_PID:-}" ] && kill -0 "${DB_LOCK_COPROC_PID}" 2>/dev/null; then
        echo "==> Releasing MariaDB schema advisory lock..."
        exec {DB_LOCK_COPROC[1]}>&- 2>/dev/null || true
        exec {DB_LOCK_COPROC[0]}<&- 2>/dev/null || true
        # A GET_LOCK still waiting on another replica ignores EOF, so stop the client explicitly.
        pkill -P "${DB_LOCK_COPROC_PID}" 2>/dev/null || true
        wait "${DB_LOCK_COPROC_PID}" 2>/dev/null || true
        unset DB_LOCK_COPROC_PID
    fi
}
coproc DB_LOCK_COPROC {
    trap - EXIT INT TERM
    db --unbuffered -sN
}
trap cleanup_db_lock EXIT
trap 'exit 143' INT TERM

echo "SELECT GET_LOCK('grommunio_schema_lock', 120);" >&"${DB_LOCK_COPROC[1]}"
LOCK_RESULT=""
if ! read -r -t 125 -u "${DB_LOCK_COPROC[0]}" LOCK_RESULT; then
    echo "ERROR: Timed out waiting for MariaDB schema advisory lock response." >&2
    exit 1
fi

if [ "${LOCK_RESULT}" != "1" ]; then
    echo "ERROR: Failed to acquire MariaDB schema lock 'grommunio_schema_lock' (result: ${LOCK_RESULT:-empty})." >&2
    exit 1
fi
echo "==> Acquired schema advisory lock."

# 6. Check database table count strictly within DB_NAME
echo "==> Checking database table count for '${DB_NAME}'..."
TABLE_ERR_FILE=$(mktemp)
TABLE_COUNT_RAW=""
if ! TABLE_COUNT_RAW=$(db -sN -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE();" 2>"${TABLE_ERR_FILE}"); then
    echo "ERROR: Failed to query table count from database '${DB_NAME}'." >&2
    echo "MariaDB error: $(cat "${TABLE_ERR_FILE}")" >&2
    rm -f "${TABLE_ERR_FILE}"
    exit 1
fi
rm -f "${TABLE_ERR_FILE}"

TABLE_COUNT=$(echo "${TABLE_COUNT_RAW}" | tr -d '[:space:]')
if ! [[ "${TABLE_COUNT}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: Invalid table count returned for database '${DB_NAME}': '${TABLE_COUNT_RAW}'" >&2
    exit 1
fi

# 7. Initialize database schema if needed (strictly when TABLE_COUNT is 0)
if [ "${TABLE_COUNT}" -eq 0 ]; then
    echo "==> Database '${DB_NAME}' has no tables. Running initial schema creation (gromox-dbop -CC)..."
    if ! gromox-dbop -CC; then
        echo "WARNING: gromox-dbop -CC returned non-zero. Attempting single gromox-dbop -C..." >&2
        if ! gromox-dbop -C; then
            echo "ERROR: Database schema creation failed (both gromox-dbop -CC and -C failed)." >&2
            exit 1
        fi
    fi
    echo "==> Initial database schema created successfully."
else
    echo "==> Database '${DB_NAME}' already initialized (${TABLE_COUNT} tables found). Skipping initial creation."
fi

# 8. Run schema migrations
echo "==> Running schema migrations (gromox-dbop -U)..."
if ! gromox-dbop -U; then
    echo "ERROR: Database schema migration failed (gromox-dbop -U)." >&2
    exit 1
fi
echo "==> Schema migrations completed successfully."

# 9. Provision admin user password
if [ -n "${ADMIN_PASSWORD}" ]; then
    echo "==> Setting initial admin password..."
    if ! printf '%s\n' "${ADMIN_PASSWORD}" | grommunio-admin passwd --password-stdin; then
        echo "ERROR: Failed to set initial admin password with 'grommunio-admin passwd --password-stdin'." >&2
        exit 1
    fi
    echo "==> Initial admin password provisioned successfully."
fi

# Release advisory lock now that schema init/migration and admin provisioning are complete
cleanup_db_lock
trap - EXIT INT TERM

# 10. Set permissions across volumes and runtime paths
echo "==> Applying file ownership and permissions..."
if [ "${GROMOX_FIX_PERMISSIONS:-false}" = "true" ]; then
    /usr/local/bin/grommunio-fix-permissions.sh recursive
else
    /usr/local/bin/grommunio-fix-permissions.sh shallow
fi

# 11. Trigger background mailbox creation defaults initialization
if [ -f /usr/local/bin/grommunio-init-defaults.py ]; then
    python3 /usr/local/bin/grommunio-init-defaults.py &
fi

# 12. Start Supervisor daemon
echo "==> Configuration complete. Starting Supervisor..."
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
