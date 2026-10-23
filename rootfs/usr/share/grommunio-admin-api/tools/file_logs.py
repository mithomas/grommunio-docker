# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Grommunio Admin API - File-based Log Reader Extension for Containers

import logging
import os
import re
from datetime import datetime

from tools.config import Config
from tools.logs import LogReader

logger = logging.getLogger("file_logs")

UNIT_MAP = {
    "postfix.service": "/var/log/exim4/mainlog",
    "exim.service": "/var/log/exim4/mainlog",
    "exim4.service": "/var/log/exim4/mainlog",
    "grommunio-antispam.service": "/var/log/rspamd/rspamd.log",
    "gromox-http.service": "/var/log/gromox/http.log",
    "gromox-delivery.service": "/var/log/gromox/delivery.log",
    "gromox-delivery-queue.service": "/var/log/gromox/smtp.log",
    "gromox-event.service": "/var/log/gromox/event.log",
    "gromox-timer.service": "/var/log/gromox/timer.log",
    "gromox-midb.service": "/var/log/gromox/midb.log",
    "gromox-zcore.service": "/var/log/gromox/zcore.log",
    "nginx.service": "/var/log/nginx/error.log",
    "php8.4-fpm.service": "/var/log/php8.4-fpm.log",
    "grommunio-admin-api.service": "/var/log/grommunio-admin-api.log",
}


@LogReader.register("file")
class FileReader:
    """Reader class for file logs."""

    def __init__(self, path):
        if path in UNIT_MAP:
            self.path = UNIT_MAP[path]
        else:
            self.path = path

    def _parse_entry(self, line, default_dt):
        line = line.strip()
        if not line:
            return None
        level = 6
        dt = None
        msg = line

        # 1. Gromox: <(\d+)>(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s*(.*)
        m = re.match(r"^<(\d+)>(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*(.*)$", line)
        if m:
            level = int(m.group(1))
            try:
                dt = datetime.fromisoformat(m.group(2))
            except Exception:
                dt = None
            msg = m.group(3)
        else:
            # 2. ISO or standard YYYY-MM-DD HH:MM:SS:
            m = re.match(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(.*)$", line)
            if m:
                dt_str = m.group(1).replace("T", " ")
                try:
                    dt = datetime.fromisoformat(dt_str)
                except Exception:
                    dt = None
                msg = m.group(2)
                lower = line.lower()
                if any(w in lower for w in ["panic", "crit", "fatal", "error", "rejected", "failed", " 550 "]):
                    level = 3
                elif any(w in lower for w in ["warn", "warning"]):
                    level = 4
                elif "notice" in lower:
                    level = 5
            else:
                # 3. Nginx: YYYY/MM/DD HH:MM:SS [level] ...
                m = re.match(r"^(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s+\[([a-z]+)\]\s*(.*)$", line)
                if m:
                    try:
                        dt = datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S")
                    except Exception:
                        dt = None
                    lvl_name = m.group(2)
                    lvl_map = {"emerg": 0, "alert": 1, "crit": 2, "error": 3, "warn": 4, "notice": 5, "info": 6, "debug": 7}
                    level = lvl_map.get(lvl_name, 6)
                    msg = m.group(3)
                else:
                    # 4. PHP-FPM: [DD-Mon-YYYY HH:MM:SS] LEVEL: ...
                    m = re.match(r"^\[(\d{2}-[A-Za-z]{3}-\d{4}\s+\d{2}:\d{2}:\d{2})\]\s*(?:([A-Z]+):\s*)?(.*)$", line)
                    if m:
                        try:
                            dt = datetime.strptime(m.group(1), "%d-%b-%Y %H:%M:%S")
                        except Exception:
                            dt = None
                        lvl_name = (m.group(2) or "").lower()
                        lvl_map = {"alert": 1, "error": 3, "warning": 4, "notice": 5, "debug": 7}
                        level = lvl_map.get(lvl_name, 6)
                        msg = m.group(3)
                    else:
                        # 5. uWSGI: [Day Mon DD HH:MM:SS YYYY] ...
                        m = re.match(r"^\[(?:Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+([A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4})\]\s*(.*)$", line)
                        if m:
                            try:
                                dt = datetime.strptime(m.group(1), "%b %d %H:%M:%S %Y")
                            except Exception:
                                dt = None
                            msg = m.group(2)

        if dt is None:
            dt = default_dt
        return (dict(
            level=level,
            message=msg,
            time=dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
            runtime=0.0
        ), dt)

    def tail(self, n=10, skip=0, after=None):
        """Get log tail from file.

        Parameters
        ----------
        n : int, optional
            Number of lines to return.
        skip : int, optional
            Number of lines to skip.
        after : datetime, optional
            Return all lines after given time point. Overrides `n` and `skip`.

        Returns
        -------
        list
            List of log file entries matching OpenAPI precTime schema.
        """
        if not os.path.exists(self.path):
            return []
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(self.path))
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as exc:
            logger.warning("Failed to read log file %s: %s", self.path, exc)
            return []

        if len(lines) > 10000:
            lines = lines[-10000:]

        entries = []
        for line in lines:
            entry = self._parse_entry(line, mtime)
            if entry:
                entries.append(entry)

        if after is not None:
            res = [e[0] for e in entries if e[1] > after]
            if n and n > 0:
                res = res[-n:]
            return res

        if skip > 0:
            entries = entries[:-skip]
        if n > 0:
            entries = entries[-n:]
        return [e[0] for e in entries]


# Wrap JournaldReader in LogReader to fallback to FileReader if journald has 0 entries
if "journald" in LogReader.rreg:
    OrigJournaldReader = LogReader.rreg["journald"]

    class FallbackJournaldReader(OrigJournaldReader):
        def __init__(self, unit):
            super().__init__(unit)
            self.unit = unit
            self.file_reader = FileReader(UNIT_MAP.get(unit, unit))

        def tail(self, n=10, skip=0, after=None):
            try:
                res = super().tail(n=n, skip=skip, after=after)
                if res:
                    return res
            except Exception:
                pass
            return self.file_reader.tail(n=n, skip=skip, after=after)

    LogReader.rreg["journald"] = FallbackJournaldReader


def sanitize_config():
    """Sanitize Config['logs'] to remove Postfix and ensure Exim/file targets."""
    logs = Config.get("logs", {})
    if "Postfix" in logs:
        del logs["Postfix"]
    if "Exim" not in logs:
        logs["Exim"] = {"format": "file", "source": "/var/log/exim4/mainlog"}


sanitize_config()
logger.info("File-based log reader extension loaded and initialized.")
