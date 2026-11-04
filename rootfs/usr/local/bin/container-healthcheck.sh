#!/bin/sh
set -e

# 1. Check Supervisor status: ensure daemon is responsive and all registered services are RUNNING
STATUS=$(supervisorctl status 2>/dev/null || true)
if [ -z "$STATUS" ]; then
    echo "HEALTHCHECK FAILED: supervisorctl produced no output or daemon not responding" >&2
    exit 1
fi

# Filter out empty lines; verify that column 2 is 'RUNNING' for every service
NOT_RUNNING=$(echo "$STATUS" | awk 'NF > 1 && $2 != "RUNNING" { print $1, $2 }')
if [ -n "$NOT_RUNNING" ]; then
    echo "HEALTHCHECK FAILED: services not in RUNNING state:" >&2
    echo "$NOT_RUNNING" >&2
    exit 1
fi

# 2. Check local HTTP listener availability (internal Webmail / ActiveSync port 8080)
# Note: Internal services listen on fixed standard ports in the container network namespace,
# regardless of custom host-mapped ports (HTTP_PORT, ADMIN_PORT, RSPAMD_PORT) defined in .env.
INTERNAL_HTTP_PORT=8080
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${INTERNAL_HTTP_PORT}/" || true)

# Nginx responds with 200 or 301/302 redirect for webmail
case "$HTTP_STATUS" in
    200|301|302)
        ;;
    *)
        echo "HEALTHCHECK FAILED: HTTP probe on port ${INTERNAL_HTTP_PORT} returned status ${HTTP_STATUS}" >&2
        exit 1
        ;;
esac

# 3. Check Admin API status probe (internal port 8081)
INTERNAL_ADMIN_PORT=8081
ADMIN_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${INTERNAL_ADMIN_PORT}/api/v1/status" || true)
if [ "$ADMIN_STATUS" != "200" ]; then
    echo "HEALTHCHECK FAILED: Admin API status probe on port ${INTERNAL_ADMIN_PORT} returned status ${ADMIN_STATUS}" >&2
    exit 1
fi

# 4. Check Rspamd web interface probe (internal port 11334)
INTERNAL_RSPAMD_PORT=11334
RSPAMD_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${INTERNAL_RSPAMD_PORT}/" || true)
if [ "$RSPAMD_STATUS" != "200" ]; then
    echo "HEALTHCHECK FAILED: Rspamd probe on port ${INTERNAL_RSPAMD_PORT} returned status ${RSPAMD_STATUS}" >&2
    exit 1
fi

# 5. Check persistent volume storage capacity (/var/log and /var/lib/gromox)
# Fail if usage exceeds 95% or free space is below 100MB (102400 KB) to prevent silent mail delivery stoppage.
LOW_SPACE=$(df -Pk /var/log /var/lib/gromox 2>/dev/null | awk 'NR > 1 {
    pct = $5; sub(/%/, "", pct);
    avail_kb = $4;
    if (pct + 0 >= 95 || avail_kb < 102400) {
        printf "%s (usage: %s%%, free: %d MB)\n", $6, pct, int(avail_kb / 1024);
    }
}')
if [ -n "$LOW_SPACE" ]; then
    echo "HEALTHCHECK FAILED: critically low disk space on persistent volumes:" >&2
    echo "$LOW_SPACE" >&2
    exit 1
fi

exit 0
