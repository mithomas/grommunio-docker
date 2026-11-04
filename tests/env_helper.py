#!/usr/bin/env python3
"""
Environment Helper for Grommunio Verification Tests
Parses environment variables and secrets from os.environ, .env.secrets, and .env
strictly complying with Docker Compose quoting and escaping rules:
- Single quotes ('...'): treated literally, preserves $, #, and ", converts \' to '
- Double quotes ("..."): strips outer quotes, converts \" to " and \\ to \
- Unquoted: strips inline comments starting with '#'
"""
import os
import re

def parse_env_value(raw_val: str) -> str:
    """Parses a raw dotenv value string according to Docker Compose semantics."""
    val = raw_val.strip()
    if not val:
        return ""

    # Single-quoted value: literal, no variable interpolation
    if val.startswith("'") and val.endswith("'") and len(val) >= 2:
        inner = val[1:-1]
        return inner.replace(r"\'", "'")

    # Double-quoted value
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        inner = val[1:-1]
        inner = inner.replace(r'\"', '"').replace(r"\\", "\\")
        return inner

    # Unquoted value: strip trailing inline comments separated by whitespace
    val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
    return val

def load_env_secret(var_name: str, default: str = "") -> str:
    """Loads a configuration secret from environment, .env.secrets, or .env."""
    env_val = os.environ.get(var_name)
    if env_val is not None and env_val != "":
        return env_val

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fname in (".env.secrets", ".env"):
        fpath = os.path.join(root_dir, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or not line or "=" not in line:
                            continue
                        key, raw_val = line.split("=", 1)
                        if key.strip() == var_name:
                            return parse_env_value(raw_val)
            except OSError:
                pass

    return default
