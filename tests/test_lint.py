#!/usr/bin/env python3
"""
Verification Test: Static Lint Gate & Fail-Safe Verifications
1. Fail-Safe entrypoint commands: fails if gromox-dbop or grommunio-admin passwd
   uses '|| true' or '2>/dev/null' to swallow errors; verified on violating samples.
2. ShellCheck compliance (-S warning) on rootfs/entrypoint.sh and rootfs/usr/local/bin/*.sh.
3. Python compilation (py_compile) on rootfs/**/*.py and tests/*.py without emitting pycache.
4. Docker Hub README size limit (<= 25,000 bytes).
"""
import glob
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

from env_helper import ROOT_DIR, SKIP_EXIT

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


def test_readme_size():
    print("    [>] 3. Validating README.md size against Docker Hub limit (25,000 bytes)...")
    size = os.path.getsize(README_PATH)
    if size > 25000:
        print(f"[-] FAIL: README.md is {size} bytes, exceeding Docker Hub 25,000 byte limit by {size - 25000} bytes", file=sys.stderr)
        return False
    print(f"    [+] README.md size verified: {size} bytes (<= 25,000).")
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
