#!/bin/bash
# ==============================================================================
# grommunio-fix-permissions.sh
# Grommunio persistent volume permissions & ownership repair and setup utility.
# Modes:
#   shallow   : Constant-time O(1) top-level ownership on /var/lib/gromox without
#               recursive mailbox traversal (used at container startup).
#   recursive : Full recursive ownership across all volumes and mailbox stores
#               (default for manual repair or GROMOX_FIX_PERMISSIONS=true).
# ==============================================================================
set -eo pipefail

MODE="${1:-recursive}"
ERRORS=0

log() {
    echo "    [>] $1"
}

# 1. Gromox mailbox storage
if [ -d /var/lib/gromox ]; then
    chown gromox:gromox /var/lib/gromox || ERRORS=$((ERRORS + 1))
    chmod 0770 /var/lib/gromox || ERRORS=$((ERRORS + 1))
    if [ "${MODE}" = "shallow" ]; then
        log "Applying shallow ownership on /var/lib/gromox (gromox:gromox)..."
        find /var/lib/gromox -mindepth 1 -maxdepth 1 ! -name queue -exec chown gromox:gromox {} + 2>/dev/null || true
        if [ -d /var/lib/gromox/queue ]; then
            chown -R gromox:gromox /var/lib/gromox/queue 2>/dev/null || true
        fi
    else
        log "Applying recursive ownership on /var/lib/gromox (gromox:gromox)..."
        chown -R gromox:gromox /var/lib/gromox || ERRORS=$((ERRORS + 1))
    fi
fi

# 2. Gromox runtime and configuration
if [ -d /run/gromox ]; then
    chown -R gromox:gromox /run/gromox 2>/dev/null || true
    chmod 0755 /run/gromox 2>/dev/null || true
fi
if [ -d /var/log/gromox ]; then
    chown -R gromox:gromox /var/log/gromox 2>/dev/null || true
    chmod 0775 /var/log/gromox 2>/dev/null || true
    chmod 0644 /var/log/gromox/*.log 2>/dev/null || true
fi
if [ -d /etc/gromox ]; then
    chown -R grommunio:gromoxcf /etc/gromox 2>/dev/null || true
    chmod 0775 /etc/gromox 2>/dev/null || true
    chmod 0660 /etc/gromox/* 2>/dev/null || true
fi

# 3. Redis persistent storage & runtime
for p in /var/lib/redis /run/redis /var/log/redis; do
    if [ -d "${p}" ]; then
        chown -R redis:redis "${p}" 2>/dev/null || true
    fi
done

# 4. Rspamd persistent storage & runtime
for p in /var/lib/rspamd /run/rspamd /var/log/rspamd; do
    if [ -d "${p}" ]; then
        chown -R _rspamd:_rspamd "${p}" 2>/dev/null || true
    fi
done

# 5. Admin API persistent state & configuration
for p in /var/lib/grommunio-admin-common /var/lib/grommunio-admin-api /etc/grommunio-admin-api /run/grommunio-admin-api; do
    if [ -d "${p}" ]; then
        chown -R grommunio:grommunio "${p}" 2>/dev/null || true
    fi
done
if [ -f /var/log/grommunio-admin-api.log ]; then
    chown grommunio:grommunio /var/log/grommunio-admin-api.log 2>/dev/null || true
    chmod 0644 /var/log/grommunio-admin-api.log 2>/dev/null || true
fi

# 6. Exim mail spool, logs, and configuration
if [ -d /var/spool/exim4 ]; then
    chown -R Debian-exim:Debian-exim /var/spool/exim4 2>/dev/null || true
    chmod 0750 /var/spool/exim4 2>/dev/null || true
fi
if [ -d /var/log/exim4 ]; then
    chown -R Debian-exim:Debian-exim /var/log/exim4 2>/dev/null || true
    chmod 0755 /var/log/exim4 2>/dev/null || true
    chmod 0644 /var/log/exim4/* 2>/dev/null || true
fi
if [ -f /etc/exim4/exim4.conf ]; then
    chown root:root /etc/exim4/exim4.conf 2>/dev/null || true
    chmod 0644 /etc/exim4/exim4.conf 2>/dev/null || true
fi

# 7. Nginx, PHP, and Webmail runtime
for p in /var/lib/nginx /var/log/nginx /var/log/grommunio-sync /var/lib/grommunio-sync /var/lib/grommunio-web /run/php /var/lib/php/sessions; do
    if [ -d "${p}" ]; then
        chown -R www-data:www-data "${p}" 2>/dev/null || true
    fi
done
for p in /var/lib/grommunio-web/session /var/lib/grommunio-web/tmp /var/lib/php/sessions; do
    if [ -d "${p}" ]; then
        chmod 0770 "${p}" 2>/dev/null || true
    fi
done
chmod 0644 /var/log/php*.log 2>/dev/null || true
chmod 0644 /etc/logrotate.d/* 2>/dev/null || true

if [ ${ERRORS} -gt 0 ]; then
    echo "==> Permissions repair encountered ${ERRORS} error(s)." >&2
    exit 1
fi

echo "==> Permissions repair (${MODE}) completed successfully."
exit 0
