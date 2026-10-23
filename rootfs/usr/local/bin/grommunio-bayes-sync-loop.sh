#!/bin/bash
# ==============================================================================
# grommunio-bayes-sync-loop.sh
# Supervisor-managed loop that periodically runs grommunio-bayes-sync.sh
# ==============================================================================

INTERVAL="${BAYES_SYNC_INTERVAL:-300}"

echo "[bayes-sync] Waiting for Rspamd and Redis to be ready..."
sleep 10

while true; do
    /usr/local/bin/grommunio-bayes-sync.sh || true
    sleep "${INTERVAL}"
done
