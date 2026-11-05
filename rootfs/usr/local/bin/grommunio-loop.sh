#!/bin/bash
# ==============================================================================
# grommunio-loop.sh
# Generic loop runner for periodic background jobs in container.
# Usage: grommunio-loop.sh [--initial-delay SECS] INTERVAL|VAR_NAME CMD [ARGS...]
# ==============================================================================
set -eo pipefail

INITIAL_DELAY=0
if [ "$1" = "--initial-delay" ]; then
    INITIAL_DELAY="$2"
    shift 2
fi

INTERVAL="$1"
shift

# If interval is passed as an env var name, expand its value
if [[ "${INTERVAL}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && [ -n "${!INTERVAL}" ]; then
    INTERVAL="${!INTERVAL}"
fi
INTERVAL="${INTERVAL:-300}"

SLEEP_PID=""
cleanup() {
    [ -n "${SLEEP_PID}" ] && kill "${SLEEP_PID}" 2>/dev/null || true
    exit 0
}
trap cleanup TERM INT

if [ "${INITIAL_DELAY}" -gt 0 ] 2>/dev/null; then
    sleep "${INITIAL_DELAY}" >/dev/null 2>&1 &
    SLEEP_PID=$!
    wait "${SLEEP_PID}" || true
    SLEEP_PID=""
fi

while true; do
    if ! "$@"; then
        echo "[loop] WARNING: Command '$*' returned non-zero exit code $?" >&2
    fi
    sleep "${INTERVAL}" >/dev/null 2>&1 &
    SLEEP_PID=$!
    wait "${SLEEP_PID}" || true
    SLEEP_PID=""
done
