#!/bin/bash
# Grommunio periodic log rotation supervisor runner
set -eo pipefail

LOGROTATE_INTERVAL="${LOGROTATE_INTERVAL:-3600}"
STATUS_FILE="/var/log/logrotate.status"
CONF_FILE="/etc/logrotate.d/grommunio"

echo "==> Grommunio log rotation runner started (interval: ${LOGROTATE_INTERVAL}s, status: ${STATUS_FILE})..."

# Ensure status file directory exists
mkdir -p /var/log

# Trap signals for graceful shutdown
running=1
trap 'running=0; exit 0' SIGTERM SIGINT

while [ "$running" -eq 1 ]; do
    if [ -f "${CONF_FILE}" ]; then
        logrotate -s "${STATUS_FILE}" "${CONF_FILE}" 2>&1 || {
            echo "WARNING: logrotate returned non-zero exit code" >&2
        }
    fi
    sleep "${LOGROTATE_INTERVAL}" &
    wait $! || true
done
