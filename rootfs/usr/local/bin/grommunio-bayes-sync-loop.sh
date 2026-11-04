#!/bin/bash
# ==============================================================================
# grommunio-bayes-sync-loop.sh
# Supervisor-managed loop that periodically runs grommunio-bayes-sync.py
# ==============================================================================
set -eo pipefail

INTERVAL="${BAYES_SYNC_INTERVAL:-300}"

echo "[bayes-sync] Waiting for Rspamd and Redis to be ready..."
sleep 10 &
wait $! || true

# Signal trapping for immediate graceful shutdown on container stop
running=1
trap 'running=0; exit 0' SIGTERM SIGINT

while [ "$running" -eq 1 ]; do
    python3 /usr/local/bin/grommunio-bayes-sync.py || true
    sleep "${INTERVAL}" &
    wait $! || true
done
