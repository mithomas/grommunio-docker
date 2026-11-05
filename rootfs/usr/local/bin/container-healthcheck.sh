#!/bin/sh
set -e

# 1. Check Supervisor status: ensure daemon is responsive and all registered services are RUNNING
STATUS=$(supervisorctl status 2>/dev/null || true)
if [ -z "$STATUS" ]; then
    echo "HEALTHCHECK FAILED: supervisorctl produced no output or daemon not responding" >&2
    exit 1
fi

NOT_RUNNING=$(echo "$STATUS" | awk 'NF > 1 && $2 != "RUNNING" { print $1, $2 }')
if [ -n "$NOT_RUNNING" ]; then
    echo "HEALTHCHECK FAILED: services not in RUNNING state:" >&2
    echo "$NOT_RUNNING" >&2
    exit 1
fi

# 2. Check HTTP probes on fixed internal listeners (Webmail :8080, Admin API :8081, Rspamd :11334)
if ! curl -fs -o /dev/null --max-time 3 "http://127.0.0.1:8080/"; then
    echo "HEALTHCHECK FAILED: Webmail probe on port 8080 failed" >&2
    exit 1
fi

if ! curl -fs -o /dev/null --max-time 3 "http://127.0.0.1:8081/api/v1/status"; then
    echo "HEALTHCHECK FAILED: Admin API status probe on port 8081 failed" >&2
    exit 1
fi

if ! curl -fs -o /dev/null --max-time 3 "http://127.0.0.1:11334/"; then
    echo "HEALTHCHECK FAILED: Rspamd probe on port 11334 failed" >&2
    exit 1
fi

# 3. Check persistent volume storage capacity (/var/log and /var/lib/gromox)
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
