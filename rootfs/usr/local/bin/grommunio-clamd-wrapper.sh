#!/bin/bash
# shellcheck shell=bash
# ==============================================================================
# grommunio-clamd-wrapper.sh
# Memory-tuned launcher for ClamAV daemon (clamd).
# - Applies glibc heap arena and trim bounds so temporary signature unpack
#   buffers are immediately returned to the OS kernel.
# - Seeds a minimal bootstrap EICAR signature if /var/lib/clamav does not yet
#   contain downloaded .cvd/.cld archives, allowing clamd to start in <1s while
#   freshclam downloads the full official signature databases in the background.
# ==============================================================================
set -eo pipefail

export MALLOC_ARENA_MAX=1
export MALLOC_MMAP_THRESHOLD_=65536
export MALLOC_TRIM_THRESHOLD_=65536

DB_DIR="/var/lib/clamav"
BOOTSTRAP_SIG="${DB_DIR}/eicar-bootstrap.ndb"

mkdir -p /run/clamav "${DB_DIR}" /var/log/clamav
chown clamav:clamav /run/clamav "${DB_DIR}" /var/log/clamav 2>/dev/null || true

has_official_db=false
for f in "${DB_DIR}"/*.cvd "${DB_DIR}"/*.cld; do
    if [ -f "${f}" ]; then
        has_official_db=true
        break
    fi
done

if [ "${has_official_db}" = "false" ] && [ ! -f "${BOOTSTRAP_SIG}" ]; then
    echo "[clamd-wrapper] No official .cvd/.cld database in ${DB_DIR} yet; seeding bootstrap EICAR signature so clamd can start immediately while freshclam downloads official databases..."
    printf 'Eicar-Test-Signature:0:*:58354f2150254041505b345c505a58353428505e2937434329377d2445494341522d5354414e444152442d414e544956495255532d544553542d46494c452124482b482a\n' > "${BOOTSTRAP_SIG}"
    chown clamav:clamav "${BOOTSTRAP_SIG}" 2>/dev/null || true
    chmod 0644 "${BOOTSTRAP_SIG}" 2>/dev/null || true
fi

exec /usr/sbin/clamd --foreground=true --config-file=/etc/clamav/clamd.conf
