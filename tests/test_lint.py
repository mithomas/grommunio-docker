#!/usr/bin/env python3
"""
Verification Test: Static Lint Gate & Fail-Safe Verifications
1. Fail-Safe entrypoint commands: fails if gromox-dbop or grommunio-admin passwd
   uses '|| true' or '2>/dev/null' to swallow errors; verified on violating samples.
2. ShellCheck compliance (-S warning) on rootfs/entrypoint.sh and rootfs/usr/local/bin/*.sh.
3. Python compilation (py_compile) on rootfs/**/*.py and tests/*.py without emitting pycache.
4. Docker Hub README size limit (<= 25,000 bytes both raw and after GitHub URL completion).
"""
import glob
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile

from env_helper import ROOT_DIR, SKIP_EXIT

DOCKERHUB_README_LIMIT = 25000
GITHUB_REPO = "mithomas/grommunio-docker"
GITHUB_BLOB_PREFIX = f"https://github.com/{GITHUB_REPO}/blob/" + ("0" * 40) + "/"
GITHUB_RAW_PREFIX = f"https://github.com/{GITHUB_REPO}/raw/" + ("0" * 40) + "/"

ENTRYPOINT_PATH = os.path.join(ROOT_DIR, "rootfs", "entrypoint.sh")
README_PATH = os.path.join(ROOT_DIR, "README.md")
SHELL_SCRIPTS = [
    os.path.join(ROOT_DIR, "rootfs", "entrypoint.sh"),
    *sorted(glob.glob(os.path.join(ROOT_DIR, "rootfs", "usr", "local", "bin", "*.sh"))),
]


def check_entrypoint_fail_safe(content: str) -> None:
    for line_no, line in enumerate(content.splitlines(), start=1):
        code = line.split("#", 1)[0]
        if "gromox-dbop" in code and "|| true" in code:
            raise ValueError(f"Line {line_no}: gromox-dbop masked with || true: {line.strip()}")
        if "grommunio-admin passwd" in code:
            if "|| true" in code:
                raise ValueError(f"Line {line_no}: grommunio-admin passwd masked with || true: {line.strip()}")
            if "2>/dev/null" in code:
                raise ValueError(f"Line {line_no}: grommunio-admin passwd error masked with 2>/dev/null: {line.strip()}")


def test_entrypoint_fail_safe():
    print("    [>] 1. Validating entrypoint fail-safe commands and violation detection...")
    # Verify checker detects violations on synthetic samples
    for sample, label in [
        ("gromox-dbop -U || true", "gromox-dbop || true"),
        ("grommunio-admin passwd --password-stdin 2>/dev/null", "passwd 2>/dev/null"),
        ("grommunio-admin passwd || true", "passwd || true"),
    ]:
        try:
            check_entrypoint_fail_safe(sample)
            print(f"[-] FAIL: Check failed to reject violating sample: {label}", file=sys.stderr)
            return False
        except ValueError:
            pass

    with open(ENTRYPOINT_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    try:
        check_entrypoint_fail_safe(content)
    except ValueError as err:
        print(f"[-] FAIL: {err}", file=sys.stderr)
        return False

    print("    [+] Entrypoint fail-safe verified: no masked DB init, migration, or password errors.")
    return True


def test_py_compile():
    print("    [>] 2. Validating Python syntax via py_compile...")
    py_files = sorted(set(
        glob.glob(os.path.join(ROOT_DIR, "rootfs", "**", "*.py"), recursive=True) +
        glob.glob(os.path.join(ROOT_DIR, "tests", "*.py"))
    ))
    with tempfile.TemporaryDirectory() as tmp_dir:
        for idx, path in enumerate(py_files):
            cfile = os.path.join(tmp_dir, f"{idx}.pyc")
            try:
                py_compile.compile(path, cfile=cfile, doraise=True)
            except py_compile.PyCompileError as ex:
                print(f"[-] FAIL: Python syntax error in {path}:\n{ex}", file=sys.stderr)
                return False
    print(f"    [+] Successfully compiled {len(py_files)} Python files without errors or pycache artifacts.")
    return True


def expand_dockerhub_urls(markdown: str) -> str:
    """Simulate peter-evans/dockerhub-description enable-url-completion: true."""
    pattern = re.compile(r"(!?\[[^\]]*\])\(([^)]+)\)")

    def _replace(match: re.Match) -> str:
        prefix, target = match.group(1), match.group(2).strip()
        if re.match(r"^(https?://|mailto:|#)", target, re.IGNORECASE):
            return match.group(0)
        rel = target.lstrip("./")
        base = GITHUB_RAW_PREFIX if prefix.startswith("!") else GITHUB_BLOB_PREFIX
        return f"{prefix}({base}{rel})"

    return pattern.sub(_replace, markdown)


def check_readme_size(content: str) -> tuple[int, int]:
    raw_bytes = len(content.encode("utf-8"))
    expanded_bytes = len(expand_dockerhub_urls(content).encode("utf-8"))
    if raw_bytes > DOCKERHUB_README_LIMIT:
        raise ValueError(
            f"README.md raw size is {raw_bytes} bytes, exceeding Docker Hub "
            f"{DOCKERHUB_README_LIMIT} byte limit by {raw_bytes - DOCKERHUB_README_LIMIT} bytes"
        )
    if expanded_bytes > DOCKERHUB_README_LIMIT:
        raise ValueError(
            f"README.md post-URL-completion size is {expanded_bytes} bytes (raw: {raw_bytes} bytes), "
            f"exceeding Docker Hub {DOCKERHUB_README_LIMIT} byte limit by {expanded_bytes - DOCKERHUB_README_LIMIT} bytes"
        )
    return raw_bytes, expanded_bytes


def test_readme_size():
    print("    [>] 3. Validating README.md size against Docker Hub limit (25,000 bytes raw & URL-completed)...")
    # Verify synthetic sample that fits raw (24,950 bytes) but overflows after URL completion is caught
    synthetic = ("a" * 24930) + "[doc](README.md)"
    try:
        check_readme_size(synthetic)
        print("[-] FAIL: Check failed to reject README sample overflowing after URL completion", file=sys.stderr)
        return False
    except ValueError:
        pass

    with open(README_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    try:
        raw_bytes, expanded_bytes = check_readme_size(content)
    except ValueError as err:
        print(f"[-] FAIL: {err}", file=sys.stderr)
        return False

    print(
        f"    [+] README.md size verified: {raw_bytes} bytes raw, "
        f"{expanded_bytes} bytes after URL completion (<= {DOCKERHUB_README_LIMIT})."
    )
    return True


def test_shellcheck():
    print("    [>] 4. Validating shell scripts with shellcheck -S warning...")
    sc_bin = shutil.which("shellcheck")
    ci = os.environ.get("CI", "").lower() in ("1", "true", "yes")
    if not sc_bin:
        if ci:
            print("[-] FAIL: shellcheck is required under CI but was not found on PATH.", file=sys.stderr)
            return False
        print("    [!] NOTICE: shellcheck not installed locally; skipping check.")
        return SKIP_EXIT

    cmd = [sc_bin, "-S", "warning", *SHELL_SCRIPTS]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[-] FAIL: shellcheck reported warnings:\n{res.stdout or res.stderr}", file=sys.stderr)
        return False
    print(f"    [+] All {len(SHELL_SCRIPTS)} shell scripts passed shellcheck (-S warning).")
    return True


def main():
    print("==> Checking Static Lint Gate & Fail-Safe Verifications...")
    if not test_entrypoint_fail_safe():
        return 1
    if not test_py_compile():
        return 1
    if not test_readme_size():
        return 1

    sc_result = test_shellcheck()
    if sc_result is False:
        return 1
    if sc_result == SKIP_EXIT:
        return SKIP_EXIT

    print("[+] SUCCESS: All static lint and fail-safe verifications passed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
