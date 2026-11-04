#!/bin/bash
set -eo pipefail

echo "==> Initializing Grommunio container on Debian 13 (Trixie)..."

# 1. Environment defaults
export GROMMUNIO_HOST="${GROMMUNIO_HOST:-mail.example.com}"
export GROMMUNIO_DOMAIN="${GROMMUNIO_DOMAIN:-example.com}"
export DOMAINS="${DOMAINS:-}"
export BAYES_SYNC_INTERVAL="${BAYES_SYNC_INTERVAL:-300}"
export RELAY_HOST="${RELAY_HOST:-host.docker.internal}"
export RELAY_PORT="${RELAY_PORT:-25}"
export DB_HOST="${DB_HOST:-mariadb}"
export DB_PORT="${DB_PORT:-3306}"
export DB_SOCKET="${DB_SOCKET:-}"
export DB_NAME="${DB_NAME:-grommunio}"
export DB_USER="${DB_USER:-grommunio}"
export DB_PASSWORD="${DB_PASSWORD:-grommunio_secret}"
if [ -n "${DB_SOCKET}" ]; then
    export DB_HOST="localhost"
elif [ "${DB_HOST}" = "mariadb" ] && [ "${DB_PORT}" != "3306" ]; then
    echo "==> Companion MariaDB detected on internal network; using container port 3306 (DB_PORT=${DB_PORT} maps host ingress)..."
    export DB_PORT="3306"
fi
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}"
export RSPAMD_PASSWORD="${RSPAMD_PASSWORD:-rspamd_secret}"
export LDAP_ENABLED="${LDAP_ENABLED:-true}"
export LDAP_HOST="${LDAP_HOST:-ldap://ldap.example.com}"
export LDAP_START_TLS="${LDAP_START_TLS:-false}"
export LDAP_BIND_USER="${LDAP_BIND_USER:-cn=admin,dc=example,dc=com}"
export LDAP_BIND_PASS="${LDAP_BIND_PASS:-ldap_secret}"
export LDAP_SEARCH_BASE="${LDAP_SEARCH_BASE:-dc=example,dc=com}"
export LDAP_USER_FILTER="${LDAP_USER_FILTER:-(objectClass=posixAccount)}"
export LDAP_OBJECT_ID="${LDAP_OBJECT_ID:-entryUUID}"
export LDAP_MAIL_ATTR="${LDAP_MAIL_ATTR:-mail}"
export LDAP_USER_ALIASES="${LDAP_USER_ALIASES:-mailAlternativeAddress}"
export LDAP_USER_DISPLAYNAME="${LDAP_USER_DISPLAYNAME:-displayName}"
export LDAP_USER_ALTNAME="${LDAP_USER_ALTNAME:-uid}"
export LDAP_GROUP_MEMBER_ATTR="${LDAP_GROUP_MEMBER_ATTR:-memberOf}"
export LDAP_GROUP_ADDR="${LDAP_GROUP_ADDR:-mailPrimaryAddress}"
export LDAP_GROUP_FILTER="${LDAP_GROUP_FILTER:-(objectclass=posixgroup)}"
export LDAP_GROUP_NAME="${LDAP_GROUP_NAME:-cn}"
export TIMEZONE="${TIMEZONE:-UTC}"

if [ "${LDAP_ENABLED}" = "true" ]; then
    export GROMOX_AUTH_BACKEND="ldap"
    export LDAP_DISABLED="false"
else
    export GROMOX_AUTH_BACKEND="deny_all"
    export LDAP_DISABLED="true"
fi

# 2. Setup tmpfs directories
mkdir -p /run/gromox /run/php /run/redis /run/rspamd /run/supervisor /run/grommunio-admin-api /run/nginx /run/mysqld
if [ -n "${DB_SOCKET}" ] && [ "$(realpath -m "${DB_SOCKET}")" != "$(realpath -m /run/mysqld/mysqld.sock)" ]; then
    ln -sf "${DB_SOCKET}" /run/mysqld/mysqld.sock
fi
mkdir -p /var/log/gromox /var/log/supervisor /var/log/nginx /var/log/grommunio-sync /var/log/redis /var/log/rspamd /var/log/exim4
mkdir -p /var/lib/nginx/body /var/lib/nginx/fastcgi /var/lib/nginx/proxy /var/lib/nginx/scgi /var/lib/nginx/uwsgi
mkdir -p /var/lib/gromox/queue /var/lib/redis /var/lib/rspamd /var/lib/grommunio-admin-common /var/lib/grommunio-admin-api /var/lib/grommunio-web/session /var/lib/grommunio-web/tmp /var/lib/php/sessions
mkdir -p /var/spool/exim4 /etc/exim4
mkdir -p /etc/gromox /etc/grommunio-admin-api/conf.d /etc/grommunio-admin-common
touch /var/log/exim4/mainlog /var/log/php8.4-fpm.log /var/log/grommunio-admin-api.log

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
if [ -n "${RSPAMD_PASSWORD}" ]; then
    RSPAMD_HASH=$(rspamadm pw -q --password="${RSPAMD_PASSWORD}" 2>/dev/null || rspamadm pw -p "${RSPAMD_PASSWORD}" 2>/dev/null || true)
    if [ -n "${RSPAMD_HASH}" ]; then
        echo "password = \"${RSPAMD_HASH}\";" > /run/rspamd/worker-controller-password.inc
        chmod 0640 /run/rspamd/worker-controller-password.inc 2>/dev/null || true
        chown _rspamd:_rspamd /run/rspamd/worker-controller-password.inc 2>/dev/null || true
    fi
fi

# Export timezone for processes
export TZ="${TIMEZONE:-UTC}"

# 3. Render configurations from templates
echo "==> Templating configuration files..."
if [ -d /etc/grommunio-admin-api.dist ]; then
    cp -a /etc/grommunio-admin-api.dist/. /etc/grommunio-admin-api/ 2>/dev/null || true
fi
mkdir -p /etc/grommunio-admin-api/conf.d

VAR_LIST='$GROMMUNIO_HOST $GROMMUNIO_DOMAIN $DOMAINS $RELAY_HOST $RELAY_PORT $DB_HOST $DB_PORT $DB_SOCKET $DB_NAME $DB_USER $DB_PASSWORD $ADMIN_PASSWORD $GROMOX_AUTH_BACKEND $LDAP_DISABLED $LDAP_HOST $LDAP_START_TLS $LDAP_BIND_USER $LDAP_BIND_PASS $LDAP_SEARCH_BASE $LDAP_USER_FILTER $LDAP_OBJECT_ID $LDAP_MAIL_ATTR $LDAP_USER_ALIASES $LDAP_USER_DISPLAYNAME $LDAP_USER_ALTNAME $LDAP_GROUP_MEMBER_ATTR $LDAP_GROUP_ADDR $LDAP_GROUP_FILTER $LDAP_GROUP_NAME'

envsubst "${VAR_LIST}" < /etc/gromox.dist/gromox.cfg.template > /etc/gromox/gromox.cfg
envsubst "${VAR_LIST}" < /etc/gromox.dist/smtp.cfg.template > /etc/gromox/smtp.cfg
envsubst "${VAR_LIST}" < /etc/gromox.dist/mysql_adaptor.cfg.template > /etc/gromox/mysql_adaptor.cfg
envsubst "${VAR_LIST}" < /etc/gromox.dist/ldap_adaptor.cfg.template > /etc/gromox/ldap_adaptor.cfg
envsubst "${VAR_LIST}" < /etc/gromox.dist/http.cfg.template > /etc/gromox/http.cfg
envsubst "${VAR_LIST}" < /etc/gromox.dist/delivery.cfg.template > /etc/gromox/delivery.cfg
envsubst "${VAR_LIST}" < /etc/gromox.dist/zcore.cfg.template > /etc/gromox/zcore.cfg
envsubst "${VAR_LIST}" < /etc/gromox.dist/event.cfg.template > /etc/gromox/event.cfg
envsubst "${VAR_LIST}" < /etc/gromox.dist/timer.cfg.template > /etc/gromox/timer.cfg
envsubst "${VAR_LIST}" < /etc/gromox.dist/midb.cfg.template > /etc/gromox/midb.cfg

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
cp -f /etc/grommunio-admin-api.dist/uwsgi.ini /etc/grommunio-admin-api/conf.d/uwsgi.ini 2>/dev/null || true
if [ -f /etc/grommunio-admin-api.dist/antispam.yaml ]; then
    cp -f /etc/grommunio-admin-api.dist/antispam.yaml /etc/grommunio-admin-api/conf.d/antispam.yaml 2>/dev/null || true
fi
if [ -f /etc/grommunio-admin-api.dist/conf.d/logs.yaml ]; then
    cp -f /etc/grommunio-admin-api.dist/conf.d/logs.yaml /etc/grommunio-admin-api/conf.d/logs.yaml 2>/dev/null || true
fi

# Exim configuration
mkdir -p /etc/exim4
if [ -d /etc/exim4.dist ]; then
    cp -a /etc/exim4.dist/. /etc/exim4/ 2>/dev/null || true
fi
if [ -f /etc/exim4.dist/exim4.conf.template ]; then
    envsubst "${VAR_LIST}" < /etc/exim4.dist/exim4.conf.template > /etc/exim4/exim4.conf
fi
chmod 0644 /etc/exim4/exim4.conf 2>/dev/null || true

mkdir -p /etc/grommunio-admin-common
if [ -d /etc/grommunio-admin-common.dist ]; then
    cp -a /etc/grommunio-admin-common.dist/. /etc/grommunio-admin-common/ 2>/dev/null || true
fi
if [ -f /etc/grommunio-admin-common.dist/config.json.template ]; then
    envsubst "${VAR_LIST}" < /etc/grommunio-admin-common.dist/config.json.template > /etc/grommunio-admin-common/config.json
fi
chmod 0644 /etc/grommunio-admin-common/config.json 2>/dev/null || true

# 4. Wait for MariaDB database connectivity
MAX_RETRIES=45
COUNT=0
if [ -n "${DB_SOCKET}" ]; then
    echo "==> Checking database connection via Unix socket ${DB_SOCKET}..."
    MARIADB_CONN=(--protocol=SOCKET -S "${DB_SOCKET}")
else
    echo "==> Checking database connection to ${DB_HOST}:${DB_PORT}..."
    MARIADB_CONN=(-h "${DB_HOST}" -P "${DB_PORT}")
fi

DB_ERR=""
while true; do
    if DB_ERR=$(MYSQL_PWD="${DB_PASSWORD}" mariadb "${MARIADB_CONN[@]}" -u "${DB_USER}" "${DB_NAME}" -e "SELECT 1;" 2>&1 >/dev/null); then
        break
    fi
    COUNT=$((COUNT + 1))
    if [ ${COUNT} -ge ${MAX_RETRIES} ]; then
        echo "ERROR: Database connection failed after ${MAX_RETRIES} retries."
        echo "Last error: ${DB_ERR}"
        exit 1
    fi
    echo "Waiting for MariaDB... (${COUNT}/${MAX_RETRIES}): ${DB_ERR}"
    sleep 2
done
echo "==> Database reachable and responsive."

TABLE_COUNT=$(MYSQL_PWD="${DB_PASSWORD}" mariadb "${MARIADB_CONN[@]}" -u "${DB_USER}" "${DB_NAME}" -sN -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '${DB_NAME}';" 2>/dev/null || echo "0")

# 5. Initialize database schema if needed (strictly within DB_NAME)
if [ "${TABLE_COUNT}" = "0" ] || [ -z "${TABLE_COUNT}" ]; then
    echo "==> Database '${DB_NAME}' has no tables. Running initial schema creation (gromox-dbop -CC)..."
    gromox-dbop -CC || {
        echo "WARNING: gromox-dbop -CC returned non-zero. Attempting single -C..."
        gromox-dbop -C || true
    }
fi

echo "==> Running schema migrations (gromox-dbop -U)..."
gromox-dbop -U || true

# 6. Provision admin user password
if [ -n "${ADMIN_PASSWORD}" ]; then
    echo "==> Setting initial admin password..."
    printf '%s\n' "${ADMIN_PASSWORD}" | grommunio-admin passwd --password-stdin 2>/dev/null || true
fi

# 7. Set permissions across volumes and runtime paths
echo "==> Applying file ownership and permissions..."
usermod -aG gromox www-data 2>/dev/null || true
chown -R gromox:gromox /var/lib/gromox /run/gromox /var/log/gromox 2>/dev/null || true
chmod 0755 /run/gromox 2>/dev/null || true
chown -R grommunio:gromoxcf /etc/gromox 2>/dev/null || true
chmod 0775 /etc/gromox 2>/dev/null || true
chmod 0660 /etc/gromox/* 2>/dev/null || true
chmod 0770 /var/lib/gromox 2>/dev/null || true

chown -R redis:redis /var/lib/redis /run/redis /var/log/redis 2>/dev/null || true
chown -R _rspamd:_rspamd /var/lib/rspamd /run/rspamd /var/log/rspamd 2>/dev/null || true
chown -R grommunio:grommunio /var/lib/grommunio-admin-common /var/lib/grommunio-admin-api /etc/grommunio-admin-api /run/grommunio-admin-api 2>/dev/null || true
chown -R Debian-exim:Debian-exim /var/spool/exim4 /var/log/exim4 2>/dev/null || true
chown root:root /etc/exim4/exim4.conf 2>/dev/null || true
chmod 0644 /etc/exim4/exim4.conf 2>/dev/null || true
chmod 0750 /var/spool/exim4 2>/dev/null || true
chmod 0755 /var/log/exim4 2>/dev/null || true
chmod 0644 /var/log/exim4/* 2>/dev/null || true
chmod 0644 /var/log/php*.log 2>/dev/null || true
chown grommunio:grommunio /var/log/grommunio-admin-api.log 2>/dev/null || true
chmod 0644 /var/log/grommunio-admin-api.log 2>/dev/null || true
chmod 0775 /var/log/gromox 2>/dev/null || true
chmod 0644 /var/log/gromox/*.log 2>/dev/null || true
chown -R www-data:www-data /var/lib/nginx /var/log/nginx /var/log/grommunio-sync /var/lib/grommunio-sync /var/lib/grommunio-web /run/php /var/lib/php/sessions 2>/dev/null || true
chmod 0770 /var/lib/grommunio-web/session /var/lib/grommunio-web/tmp /var/lib/php/sessions 2>/dev/null || true

# 8. Trigger background mailbox creation defaults initialization
if [ -x /usr/local/bin/grommunio-init-defaults.py ] || [ -f /usr/local/bin/grommunio-init-defaults.py ]; then
    python3 /usr/local/bin/grommunio-init-defaults.py &
fi

echo "==> Configuration complete. Starting Supervisor..."
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
