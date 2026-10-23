#!/bin/sh
set -e

# 1. Check Supervisor status: ensure daemon is responsive and all registered services are RUNNING
STATUS=$(supervisorctl status 2>/dev/null)
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

# 2. Check local HTTP listener availability (Webmail / ActiveSync port)
HTTP_PORT="${HTTP_PORT:-8080}"
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${HTTP_PORT}/" || true)

# Nginx responds with 200 or 301/302 redirect for webmail
case "$HTTP_STATUS" in
    200|301|302)
        ;;
    *)
        echo "HEALTHCHECK FAILED: HTTP probe on port ${HTTP_PORT} returned status ${HTTP_STATUS}" >&2
        exit 1
        ;;
esac

# 3. Check Admin API status probe (port 8081)
ADMIN_PORT="${ADMIN_PORT:-8081}"
ADMIN_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${ADMIN_PORT}/api/v1/status" || true)
if [ "$ADMIN_STATUS" != "200" ]; then
    echo "HEALTHCHECK FAILED: Admin API status probe on port ${ADMIN_PORT} returned status ${ADMIN_STATUS}" >&2
    exit 1
fi

# 4. Check Rspamd web interface probe (port 11334)
RSPAMD_PORT="${RSPAMD_PORT:-11334}"
RSPAMD_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${RSPAMD_PORT}/" || true)
if [ "$RSPAMD_STATUS" != "200" ]; then
    echo "HEALTHCHECK FAILED: Rspamd probe on port ${RSPAMD_PORT} returned status ${RSPAMD_STATUS}" >&2
    exit 1
fi

exit 0
