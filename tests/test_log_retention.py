#!/usr/bin/env python3
"""
Verification Test: Admin UI Log Reader & Log Rotation
1. FileReader (the Admin UI log backend) parses every shipped log format into level and time.
2. Untimestamped continuation lines inherit the preceding entry's time (so 'after' polling stays correct).
3. A tail longer than the active file continues into the rotated '.1' file in chronological order.
4. In-container logrotate accepts the shipped configuration (dry run).
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import types
from datetime import datetime
from unittest import mock

from env_helper import ROOT_DIR

FILE_LOGS_PATH = os.path.join(ROOT_DIR, "rootfs", "usr", "share", "grommunio-admin-api", "tools", "file_logs.py")


def load_file_reader():
    """Imports the shipped file_logs.py with minimal stand-ins for the admin API's tools package."""
    class LogReader:
        rreg = {}

        @classmethod
        def register(cls, name):
            def decorator(klass):
                cls.rreg[name] = klass
                return klass
            return decorator

    tools = types.ModuleType("tools")
    config = types.ModuleType("tools.config")
    config.Config = {"logs": {}}
    logs = types.ModuleType("tools.logs")
    logs.LogReader = LogReader
    tools.config, tools.logs = config, logs

    with mock.patch.dict(sys.modules, {"tools": tools, "tools.config": config, "tools.logs": logs}):
        spec = importlib.util.spec_from_file_location("file_logs_under_test", FILE_LOGS_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module.FileReader


def write(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def test_formats_are_parsed():
    print("    [>] 1. Validating parsing of every supported log format...")
    reader = load_file_reader()("/nonexistent")
    default = datetime(2000, 1, 1)
    cases = [
        ("<4>2026-11-05T10:00:00 gromox warning text", 4, "2026-11-05 10:00:00.000000", "gromox warning text"),
        ("2026-11-05 10:00:01 1abc-000 <= a@b.example", 6, "2026-11-05 10:00:01.000000", "1abc-000 <= a@b.example"),
        ("2026-11-05 10:00:01 1abc-000 ** a@b.example: rejected", 3, "2026-11-05 10:00:01.000000", None),
        ("2026/11/05 10:00:02 [error] 12#12: upstream failed", 3, "2026-11-05 10:00:02.000000", "12#12: upstream failed"),
        ("[05-Nov-2026 10:00:03] WARNING: pool busy", 4, "2026-11-05 10:00:03.000000", "pool busy"),
        ("[Thu Nov  5 10:00:04 2026] GET /api/v1/status", 6, "2026-11-05 10:00:04.000000", "GET /api/v1/status"),
        ("no timestamp here", 6, "2000-01-01 00:00:00.000000", "no timestamp here"),
    ]
    for line, level, time_str, message in cases:
        entry, _ = reader._parse_entry(line, default)
        assert entry["level"] == level, f"{line!r}: level {entry['level']} != {level}"
        assert entry["time"] == time_str, f"{line!r}: time {entry['time']} != {time_str}"
        if message is not None:
            assert entry["message"] == message, f"{line!r}: message {entry['message']!r}"
    print("    [+] Gromox, Exim/ISO, Nginx, PHP-FPM, uWSGI and untimestamped lines parse correctly.")
    return True


def test_continuation_lines_and_after_filter():
    print("    [>] 2. Validating continuation lines and the 'after' filter...")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mainlog")
        write(path, [
            "2026-11-05 10:00:01 first message",
            "2026-11-05 10:00:02 TLS error",
            "  Suggested action: install a certificate",
            "2026-11-05 10:00:03 third message",
        ])
        reader = load_file_reader()(path)
        entries = reader.tail(n=10)
        continuation = entries[2]
        assert continuation["time"] == "2026-11-05 10:00:02.000000", (
            f"Continuation line took time {continuation['time']} instead of the preceding entry's"
        )
        newer = reader.tail(n=10, after=datetime(2026, 11, 5, 10, 0, 2))
        assert [e["message"] for e in newer] == ["third message"], f"'after' returned {[e['message'] for e in newer]}"
    print("    [+] Continuation lines inherit the preceding time; 'after' returns only newer entries.")
    return True


def test_tail_continues_into_rotated_file():
    print("    [>] 3. Validating tail continuity across log rotation...")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mainlog")
        write(path + ".1", [f"2026-11-05 09:00:0{i} rot-{i}" for i in range(1, 6)])
        write(path, [f"2026-11-05 10:00:0{i} act-{i}" for i in range(1, 3)])
        reader = load_file_reader()(path)

        messages = [e["message"] for e in reader.tail(n=4)]
        assert messages == ["rot-4", "rot-5", "act-1", "act-2"], f"Unexpected tail across rotation: {messages}"

        messages = [e["message"] for e in reader.tail(n=2)]
        assert messages == ["act-1", "act-2"], f"Rotated file used although active file suffices: {messages}"

        assert load_file_reader()(os.path.join(tmp, "missing.log")).tail(n=5) == []
    print("    [+] Tail bridges the rotated '.1' file chronologically and ignores it when not needed.")
    return True


def test_in_container_logrotate_execution():
    print("    [>] 4. Validating in-container logrotate debug dry-run...")
    check_script = r'''
import subprocess
import sys

which_res = subprocess.run(["which", "logrotate"], capture_output=True, text=True)
if which_res.returncode != 0:
    print("FAIL: logrotate is not installed in the container image", file=sys.stderr)
    sys.exit(1)

d_res = subprocess.run(["logrotate", "-d", "-s", "/var/log/logrotate.status", "/etc/logrotate.d/grommunio"],
                       capture_output=True, text=True)
if d_res.returncode != 0:
    print(f"FAIL: logrotate dry-run returned error:\n{d_res.stderr or d_res.stdout}", file=sys.stderr)
    sys.exit(1)

print("OK_CONTAINER_LOGROTATE")
'''
    res = subprocess.run(
        ["docker", "exec", "-i", "grommunio", "python3", "-c", check_script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if res.returncode != 0:
        print(f"    [-] In-container logrotate check failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        return False
    print("    [+] In-container logrotate syntax and debug evaluation verified.")
    return True


def main():
    print("==> Checking Admin UI Log Reader & Log Rotation...")
    for check in (test_formats_are_parsed, test_continuation_lines_and_after_filter,
                  test_tail_continues_into_rotated_file, test_in_container_logrotate_execution):
        if not check():
            return 1
    print("    [+] ALL LOG READER & ROTATION VERIFICATIONS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
