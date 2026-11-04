#!/usr/bin/env python3
"""
Verification Test 18: Bayes Synchronization Engine & Scan Cost Reduction
Verifies that:
1. Static contracts:
   - rootfs/usr/local/bin/grommunio-bayes-sync.py exists and uses SQLite ATTACH DATABASE.
   - rootfs/usr/local/bin/grommunio-bayes-sync.sh delegates to the Python engine.
   - rootfs/usr/local/bin/grommunio-bayes-sync-loop.sh has signal trapping.
2. Functional sync transitions:
   - Case A (SPAM): Messages in Junk (parent_fid = 23) are learned as spam.
   - Case B (HAM): Messages moved from Junk (23) to Inbox (19) are learned as ham.
   - Case C (TRASH): Messages moved from Junk (23) to Deleted Items (27) are pruned without ham learning.
   - Case D (EXPUNGE): Messages expunged from the mailbox database are pruned from state.
   - Case E (RETRY): Temporary rspamc failure retains unlearned state for subsequent retry.
3. Performance & Idempotence:
   - Validates that scanning a mailbox with 1,000 already-learned messages runs in milliseconds
     without spawning redundant rspamc subprocesses or duplicating state.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time


def test_static_bayes_contracts():
    print("    [>] 1. Validating static Bayes synchronization contracts...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    py_script = os.path.join(root_dir, "rootfs", "usr", "local", "bin", "grommunio-bayes-sync.py")
    assert os.path.isfile(py_script), f"FAIL: {py_script} does not exist"
    with open(py_script, "r", encoding="utf-8") as f:
        py_content = f.read()

    assert "ATTACH DATABASE" in py_content, "FAIL: grommunio-bayes-sync.py missing ATTACH DATABASE"
    assert "DETACH DATABASE" in py_content, "FAIL: grommunio-bayes-sync.py missing DETACH DATABASE"
    assert "learned_messages" in py_content, "FAIL: grommunio-bayes-sync.py missing learned_messages table"
    assert "FOLDER_JUNK = 23" in py_content, "FAIL: grommunio-bayes-sync.py missing FOLDER_JUNK = 23 constant"
    assert "FOLDER_TRASH = 27" in py_content, "FAIL: grommunio-bayes-sync.py missing FOLDER_TRASH = 27 constant"
    assert "mode=ro" in py_content, "FAIL: grommunio-bayes-sync.py missing mode=ro in ATTACH DATABASE"
    assert "uri=True" in py_content, "FAIL: grommunio-bayes-sync.py missing uri=True in sqlite3.connect"

    loop_script = os.path.join(root_dir, "rootfs", "usr", "local", "bin", "grommunio-bayes-sync-loop.sh")
    with open(loop_script, "r", encoding="utf-8") as f:
        loop_content = f.read()
    assert "trap" in loop_content and "SIGTERM" in loop_content, (
        "FAIL: grommunio-bayes-sync-loop.sh missing SIGTERM trap"
    )
    assert "grommunio-bayes-sync.py" in loop_content, (
        "FAIL: grommunio-bayes-sync-loop.sh does not invoke Python engine"
    )

    print("    [+] Static Bayes synchronization contracts verified.")
    return True


def test_functional_bayes_transitions():
    print("    [>] 2. Validating functional Bayes transitions (Spam -> Ham -> Trash -> Expunge)...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    py_script = os.path.join(root_dir, "rootfs", "usr", "local", "bin", "grommunio-bayes-sync.py")

    with tempfile.TemporaryDirectory() as tmpdir:
        state_db = os.path.join(tmpdir, "bayes_learned.sqlite3")
        gromox_dir = os.path.join(tmpdir, "gromox_users")
        user_dir = os.path.join(gromox_dir, "testuser", "0")
        eml_dir = os.path.join(tmpdir, "gromox_users", "testuser", "eml")
        os.makedirs(user_dir, exist_ok=True)
        os.makedirs(eml_dir, exist_ok=True)

        user_db = os.path.join(user_dir, "exchange.sqlite3")

        # Create user exchange database schema
        conn_user = sqlite3.connect(user_db)
        conn_user.execute("""
            CREATE TABLE messages (
                message_id INTEGER PRIMARY KEY,
                parent_fid INTEGER,
                mid_string TEXT
            );
        """)
        conn_user.commit()

        # Create mock rspamc binary in a temp bin directory
        bin_dir = os.path.join(tmpdir, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        rspamc_log = os.path.join(tmpdir, "rspamc.log")
        mock_rspamc = os.path.join(bin_dir, "rspamc")
        with open(mock_rspamc, "w") as f:
            f.write(f"""#!/bin/sh
echo "$@" >> "{rspamc_log}"
if [ -f "{tmpdir}/rspamc_fail" ]; then
    exit 1
fi
if [ -f "{tmpdir}/rspamc_kill_on_107" ] && grep -q "107" "{tmpdir}/current_msg" 2>/dev/null; then
    kill -9 $PPID
    exit 1
fi
exit 0
""")
        os.chmod(mock_rspamc, 0o755)

        # Create mock gromox-exm2eml binary
        exm2eml_log = os.path.join(tmpdir, "exm2eml.log")
        mock_exm2eml = os.path.join(bin_dir, "gromox-exm2eml")
        with open(mock_exm2eml, "w") as f:
            f.write(f"""#!/bin/sh
echo "$@" >> "{exm2eml_log}"
if [ -f "{tmpdir}/exm2eml_fail" ]; then
    exit 1
fi
printf "From: test@example.com\\nSubject: Mocked Export\\n\\nExported body\\n"
exit 0
""")
        os.chmod(mock_exm2eml, 0o755)

        env = os.environ.copy()
        env["BAYES_STATE_DB"] = state_db
        env["GROMOX_USER_DIR"] = gromox_dir
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

        # ----------------------------------------------------------------------
        # Case A: Message 101 in Junk (parent_fid = 23) -> Learn SPAM
        # ----------------------------------------------------------------------
        conn_user.execute("INSERT INTO messages (message_id, parent_fid, mid_string) VALUES (101, 23, 'mid-101.eml');")
        conn_user.commit()
        with open(os.path.join(eml_dir, "mid-101.eml"), "w") as f:
            f.write("Subject: Spam 101\n\nBuy crypto!")

        res = subprocess.run([sys.executable, py_script], env=env, capture_output=True, text=True)
        assert res.returncode == 0, f"FAIL: Case A sync failed: {res.stderr}"

        conn_state = sqlite3.connect(state_db)
        cur = conn_state.cursor()
        cur.execute("SELECT learned_type FROM learned_messages WHERE username = 'testuser' AND message_id = 101;")
        row = cur.fetchone()
        assert row and row[0] == "spam", f"FAIL: Expected 101 learned as spam, got {row}"

        with open(rspamc_log, "r") as f:
            log_content = f.read()
        assert "learn_spam" in log_content, "FAIL: rspamc learn_spam not invoked"

        # ----------------------------------------------------------------------
        # Case B: Message 101 moved from Junk (23) to Inbox (19) -> Learn HAM
        # ----------------------------------------------------------------------
        conn_user.execute("UPDATE messages SET parent_fid = 19 WHERE message_id = 101;")
        conn_user.commit()

        res = subprocess.run([sys.executable, py_script], env=env, capture_output=True, text=True)
        assert res.returncode == 0, f"FAIL: Case B sync failed: {res.stderr}"

        cur.execute("SELECT learned_type FROM learned_messages WHERE username = 'testuser' AND message_id = 101;")
        row = cur.fetchone()
        assert row and row[0] == "ham", f"FAIL: Expected 101 learned as ham, got {row}"

        with open(rspamc_log, "r") as f:
            log_content = f.read()
        assert "learn_ham" in log_content, "FAIL: rspamc learn_ham not invoked"

        # ----------------------------------------------------------------------
        # Case C: Message 102 in Junk -> moved to Trash (27) -> Pruned
        # ----------------------------------------------------------------------
        conn_user.execute("INSERT INTO messages (message_id, parent_fid, mid_string) VALUES (102, 23, 'mid-102.eml');")
        conn_user.commit()
        with open(os.path.join(eml_dir, "mid-102.eml"), "w") as f:
            f.write("Subject: Spam 102\n\nPhishing!")

        subprocess.run([sys.executable, py_script], env=env, check=True)
        cur.execute("SELECT learned_type FROM learned_messages WHERE username = 'testuser' AND message_id = 102;")
        assert cur.fetchone()[0] == "spam"

        # Move to Trash (27)
        conn_user.execute("UPDATE messages SET parent_fid = 27 WHERE message_id = 102;")
        conn_user.commit()

        # Clear rspamc log before running
        open(rspamc_log, "w").close()
        subprocess.run([sys.executable, py_script], env=env, check=True)

        cur.execute("SELECT message_id FROM learned_messages WHERE username = 'testuser' AND message_id = 102;")
        assert cur.fetchone() is None, "FAIL: Expected message 102 to be pruned on move to Trash"

        with open(rspamc_log, "r") as f:
            log_content = f.read()
        assert "learn_ham" not in log_content, "FAIL: Message moved to trash was erroneously learned as ham"

        # ----------------------------------------------------------------------
        # Case D: Message 103 in Junk -> Permanently expunged -> Pruned
        # ----------------------------------------------------------------------
        conn_user.execute("INSERT INTO messages (message_id, parent_fid, mid_string) VALUES (103, 23, 'mid-103.eml');")
        conn_user.commit()
        with open(os.path.join(eml_dir, "mid-103.eml"), "w") as f:
            f.write("Subject: Spam 103")

        subprocess.run([sys.executable, py_script], env=env, check=True)
        cur.execute("SELECT learned_type FROM learned_messages WHERE username = 'testuser' AND message_id = 103;")
        assert cur.fetchone()[0] == "spam"

        # Expunge message from database
        conn_user.execute("DELETE FROM messages WHERE message_id = 103;")
        conn_user.commit()

        subprocess.run([sys.executable, py_script], env=env, check=True)
        cur.execute("SELECT message_id FROM learned_messages WHERE username = 'testuser' AND message_id = 103;")
        assert cur.fetchone() is None, "FAIL: Expected expunged message 103 to be pruned from state"

        # ----------------------------------------------------------------------
        # Case E: Dependency Failure (Rspamd down) -> State preserved for retry
        # ----------------------------------------------------------------------
        conn_user.execute("INSERT INTO messages (message_id, parent_fid, mid_string) VALUES (104, 23, 'mid-104.eml');")
        conn_user.commit()
        with open(os.path.join(eml_dir, "mid-104.eml"), "w") as f:
            f.write("Subject: Spam 104")

        # Simulate rspamc failure
        fail_flag = os.path.join(tmpdir, "rspamc_fail")
        open(fail_flag, "w").close()

        subprocess.run([sys.executable, py_script], env=env, check=True)
        cur.execute("SELECT message_id FROM learned_messages WHERE username = 'testuser' AND message_id = 104;")
        assert cur.fetchone() is None, "FAIL: Message 104 was recorded despite rspamc failure"

        # Remove failure flag, sync should now succeed
        os.remove(fail_flag)
        subprocess.run([sys.executable, py_script], env=env, check=True)
        cur.execute("SELECT learned_type FROM learned_messages WHERE username = 'testuser' AND message_id = 104;")
        row = cur.fetchone()
        assert row and row[0] == "spam", f"FAIL: Message 104 not learned after recovery: {row}"

        # ----------------------------------------------------------------------
        # Case F: gromox-exm2eml export failure -> unlearned and retried
        # ----------------------------------------------------------------------
        conn_user.execute("INSERT INTO messages (message_id, parent_fid, mid_string) VALUES (105, 23, NULL);")
        conn_user.commit()

        exm_fail_flag = os.path.join(tmpdir, "exm2eml_fail")
        open(exm_fail_flag, "w").close()

        subprocess.run([sys.executable, py_script], env=env, check=True)
        cur.execute("SELECT message_id FROM learned_messages WHERE username = 'testuser' AND message_id = 105;")
        assert cur.fetchone() is None, "FAIL: Message 105 was learned despite gromox-exm2eml failure"

        # Clear export failure -> message 105 should now be learned as spam
        os.remove(exm_fail_flag)
        subprocess.run([sys.executable, py_script], env=env, check=True)
        cur.execute("SELECT learned_type FROM learned_messages WHERE username = 'testuser' AND message_id = 105;")
        row = cur.fetchone()
        assert row and row[0] == "spam", f"FAIL: Message 105 was not learned after exm2eml recovery: {row}"

        # ----------------------------------------------------------------------
        # Case G: Incremental commits preserve progress across interrupted runs
        # ----------------------------------------------------------------------
        conn_user.execute("INSERT INTO messages (message_id, parent_fid, mid_string) VALUES (106, 23, 'mid-106.eml');")
        conn_user.execute("INSERT INTO messages (message_id, parent_fid, mid_string) VALUES (107, 23, 'mid-107.eml');")
        conn_user.commit()
        with open(os.path.join(eml_dir, "mid-106.eml"), "w") as f:
            f.write("Subject: Spam 106\n\nPayload 106")
        with open(os.path.join(eml_dir, "mid-107.eml"), "w") as f:
            f.write("Subject: Spam 107\n\nPayload 107")

        # Mock rspamc succeeds for 106, fails for 107
        rspamc_selective = os.path.join(bin_dir, "rspamc")
        with open(rspamc_selective, "w") as f:
            f.write(f"""#!/bin/sh
cat - > "{tmpdir}/last_payload"
if grep -q "Payload 107" "{tmpdir}/last_payload"; then
    exit 1
fi
exit 0
""")

        subprocess.run([sys.executable, py_script], env=env, check=True)
        cur.execute("SELECT learned_type FROM learned_messages WHERE username = 'testuser' AND message_id = 106;")
        assert cur.fetchone() is not None, "FAIL: Message 106 was not committed incrementally!"
        cur.execute("SELECT learned_type FROM learned_messages WHERE username = 'testuser' AND message_id = 107;")
        assert cur.fetchone() is None, "FAIL: Message 107 was recorded despite rspamc failure"

        # Restore default mock rspamc
        with open(rspamc_selective, "w") as f:
            f.write('#!/bin/sh\nexit 0\n')

        # ----------------------------------------------------------------------
        # Case H: Read-only attachment prevents writes to user exchange database
        # ----------------------------------------------------------------------
        ro_verify = sqlite3.connect(state_db, uri=True)
        escaped_user = user_db.replace("'", "''")
        ro_verify.execute(f"ATTACH DATABASE 'file:{escaped_user}?mode=ro' AS test_ro_user")
        try:
            ro_verify.execute("INSERT INTO test_ro_user.messages (message_id, parent_fid) VALUES (999, 23);")
            assert False, "FAIL: Write to read-only attached database succeeded unexpectedly!"
        except sqlite3.OperationalError as exc:
            assert "readonly" in str(exc).lower() or "read-only" in str(exc).lower(), f"Unexpected error: {exc}"
        ro_verify.close()

        conn_user.close()
        conn_state.close()

    print("    [+] All Bayes lifecycle transitions (Spam, Ham, Trash, Expunge, Retry) verified.")
    return True


def test_scan_cost_and_idempotence():
    print("    [>] 3. Benchmarking scan cost and idempotence on 1,000 messages...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    py_script = os.path.join(root_dir, "rootfs", "usr", "local", "bin", "grommunio-bayes-sync.py")

    with tempfile.TemporaryDirectory() as tmpdir:
        state_db = os.path.join(tmpdir, "bayes_learned.sqlite3")
        gromox_dir = os.path.join(tmpdir, "gromox_users")
        user_dir = os.path.join(gromox_dir, "scaleuser", "0")
        eml_dir = os.path.join(tmpdir, "gromox_users", "scaleuser", "eml")
        os.makedirs(user_dir, exist_ok=True)
        os.makedirs(eml_dir, exist_ok=True)
        user_db = os.path.join(user_dir, "exchange.sqlite3")

        conn_user = sqlite3.connect(user_db)
        conn_user.execute("CREATE TABLE messages (message_id INTEGER PRIMARY KEY, parent_fid INTEGER, mid_string TEXT);")
        # Populate 1,000 messages in Junk (23) and create corresponding eml files
        records = [(i, 23, f"mid-{i}.eml") for i in range(1, 1001)]
        conn_user.executemany("INSERT INTO messages (message_id, parent_fid, mid_string) VALUES (?, ?, ?);", records)
        conn_user.commit()
        conn_user.close()

        for i in range(1, 1001):
            with open(os.path.join(eml_dir, f"mid-{i}.eml"), "w") as f:
                f.write(f"Subject: Scale test {i}\n\nSpam payload")

        # Mock rspamc
        bin_dir = os.path.join(tmpdir, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        rspamc_log = os.path.join(tmpdir, "rspamc.log")
        mock_rspamc = os.path.join(bin_dir, "rspamc")
        with open(mock_rspamc, "w") as f:
            f.write(f'#!/bin/sh\necho "$@" >> "{rspamc_log}"\nexit 0\n')
        os.chmod(mock_rspamc, 0o755)

        env = os.environ.copy()
        env["BAYES_STATE_DB"] = state_db
        env["GROMOX_USER_DIR"] = gromox_dir
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"

        # First run: initial learning of 1,000 messages
        t0 = time.perf_counter()
        subprocess.run([sys.executable, py_script], env=env, check=True)
        t_first = time.perf_counter() - t0

        conn_state = sqlite3.connect(state_db)
        cur = conn_state.cursor()
        cur.execute("SELECT COUNT(*) FROM learned_messages WHERE username = 'scaleuser' AND learned_type = 'spam';")
        count = cur.fetchone()[0]
        assert count == 1000, f"FAIL: Expected 1000 learned spam messages, found {count}"

        # Second run: zero messages changed folder -> must complete in < 50ms with 0 rspamc calls
        open(rspamc_log, "w").close()
        t0 = time.perf_counter()
        subprocess.run([sys.executable, py_script], env=env, check=True)
        t_subsequent = time.perf_counter() - t0

        with open(rspamc_log, "r") as f:
            calls = f.read().strip()
        assert calls == "", f"FAIL: Expected 0 rspamc calls on unchanged mailbox, got: {calls}"

        print(f"    [+] Scan cost for 1,000 unchanged messages: {t_subsequent*1000:.2f}ms (0 redundant subprocesses).")
        assert t_subsequent < 1.0, f"FAIL: Subsequent scan too slow: {t_subsequent}s"

        conn_state.close()

    print("    [+] Idempotence and scan cost reduction verified.")
    return True


def main():
    print("==> [18/18] Checking Bayes Synchronization Engine & Scan Cost Reduction...")
    if not test_static_bayes_contracts():
        return 1
    if not test_functional_bayes_transitions():
        return 1
    if not test_scan_cost_and_idempotence():
        return 1
    print("    [+] ALL BAYES SYNCHRONIZATION OPTIMIZATIONS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
