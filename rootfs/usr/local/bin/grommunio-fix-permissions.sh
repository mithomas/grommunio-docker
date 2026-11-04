#!/bin/bash
# Grommunio persistent volume permissions & ownership repair utility
set -eo pipefail

echo "==> Starting Grommunio persistent volume permissions and ownership repair..."
START_TIME=$(date +%s)

# 1. Gromox mailbox storage & runtime paths
echo "    [>] Repairing /var/lib/gromox ownership (gromox:gromox)..."
chown -R gromox:gromox /var/lib/gromox 2>/dev/null || true
chmod 0770 /var/lib/gromox 2>/dev/null || true
if [ -d /run/gromox ]; then
    chown -R gromox:gromox /run/gromox 2>/dev/null || true
    chmod 0755 /run/gromox 2>/dev/null || true
fi
if [ -d /var/log/gromox ]; then
    chown -R gromox:gromox /var/log/gromox 2>/dev/null || true
    chmod 0775 /var/log/gromox 2>/dev/null || true
    chmod 0644 /var/log/gromox/*.log 2>/dev/null || true
fi

# 2. Redis persistent storage
if [ -d /var/lib/redis ]; then
    echo "    [>] Repairing /var/lib/redis ownership (redis:redis)..."
    chown -R redis:redis /var/lib/redis /var/log/redis 2>/dev/null || true
fi

# 3. Rspamd persistent storage
if [ -d /var/lib/rspamd ]; then
    echo "    [>] Repairing /var/lib/rspamd ownership (_rspamd:_rspamd)..."
    chown -R _rspamd:_rspamd /var/lib/rspamd /var/log/rspamd 2>/dev/null || true
fi

# 4. Admin API persistent state
if [ -d /var/lib/grommunio-admin-common ] || [ -d /var/lib/grommunio-admin-api ]; then
    echo "    [>] Repairing Admin API data ownership (grommunio:grommunio)..."
    chown -R grommunio:grommunio /var/lib/grommunio-admin-common /var/lib/grommunio-admin-api 2>/dev/null || true
fi

# 5. Exim mail spool
if [ -d /var/spool/exim4 ]; then
    echo "    [>] Repairing /var/spool/exim4 ownership (Debian-exim:Debian-exim)..."
    chown -R Debian-exim:Debian-exim /var/spool/exim4 /var/log/exim4 2>/dev/null || true
    chmod 0750 /var/spool/exim4 2>/dev/null || true
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
echo "==> Permissions repair completed successfully in ${ELAPSED} second(s)."
