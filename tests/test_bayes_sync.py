#!/usr/bin/env python3
"""
Verification Test: Bayes Synchronization Engine
Runs rootfs/usr/local/bin/grommunio-bayes-sync.py against fixtures that mirror a real Gromox store
(<user_dir>/<domain>/<mailbox>/exmdb/exchange.sqlite3, real `messages` schema, real folder ids)
with stub `rspamc` / `gromox-exm2eml` binaries, and asserts on what gets learned and recorded.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

from env_helper import container_exec, ensure_testuser, mailbox_contains, mailbox_dir, send_mail, wait_until

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "rootfs", "usr", "local", "bin", "grommunio-bayes-sync.py")

# Folder ids as allocated in a real Gromox private store.
INBOX, DELETED_ITEMS, JUNK = 13, 11, 23
PR_SMTP_ADDRESS = 0x39FE001F

MESSAGES_DDL = """
CREATE TABLE messages (
    message_id INTEGER PRIMARY KEY, parent_fid INTEGER, parent_attid INTEGER,
    is_deleted INTEGER DEFAULT 0, is_associated INTEGER,
    change_number INTEGER UNIQUE NOT NULL, read_cn INTEGER UNIQUE DEFAULT NULL,
    read_state INTEGER DEFAULT 0, message_size INTEGER NOT NULL,
    group_id INTEGER DEFAULT NULL, timer_id INTEGER DEFAULT NULL, mid_string TEXT DEFAULT NULL
)"""
STORE_PROPS_DDL = "CREATE TABLE store_properties (proptag INTEGER UNIQUE NOT NULL, propval BLOB NOT NULL)"


class Fixture:
    def __init__(self, tmpdir):
        self.tmp = tmpdir
        self.user_dir = os.path.join(tmpdir, "user")
        self.state_db = os.path.join(tmpdir, "bayes_learned.sqlite3")
        self.bin_dir = os.path.join(tmpdir, "bin")
        self.rspamc_log = os.path.join(tmpdir, "rspamc.log")
        self.rspamc_args = os.path.join(tmpdir, "rspamc.args")
        self.exm2eml_log = os.path.join(tmpdir, "exm2eml.log")
        self.rspamc_fail = os.path.join(tmpdir, "rspamc_fail")
        self.exm2eml_fail = os.path.join(tmpdir, "exm2eml_fail")
        self._cn = 0
        os.makedirs(self.bin_dir)
        # Logs "<mode>|<Subject>" per accepted call; fails when flagged or when payload has REJECT_ME.
        self._write_bin("rspamc", f"""#!/bin/sh
payload=$(cat)
[ -f "{self.rspamc_fail}" ] && exit 1
case "$payload" in *REJECT_ME*) exit 1;; esac
for a; do mode=$a; done
printf '%s\\n' "$*" >> "{self.rspamc_args}"
printf '%s|%s\\n' "$mode" "$(printf '%s' "$payload" | sed -n 's/^Subject: //p' | head -1)" >> "{self.rspamc_log}"
""")
        self._write_bin("gromox-exm2eml", f"""#!/bin/sh
echo "$@" >> "{self.exm2eml_log}"
[ -f "{self.exm2eml_fail}" ] && exit 1
printf 'From: a@b.example\\nSubject: Exported %s\\n\\nbody\\n' "$3"
""")

    def _write_bin(self, name, content):
        path = os.path.join(self.bin_dir, name)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)

    def mailbox(self, domain, name, address):
        mbox = os.path.join(self.user_dir, domain, name)
        os.makedirs(os.path.join(mbox, "exmdb"))
        os.makedirs(os.path.join(mbox, "eml"))
        conn = sqlite3.connect(os.path.join(mbox, "exmdb", "exchange.sqlite3"))
        conn.execute(MESSAGES_DDL)
        conn.execute(STORE_PROPS_DDL)
        conn.execute("INSERT INTO store_properties VALUES (?, ?)", (PR_SMTP_ADDRESS, address))
        conn.commit()
        return Mailbox(self, mbox, conn, f"{domain}/{name}")

    def run(self):
        env = dict(os.environ, BAYES_STATE_DB=self.state_db, GROMOX_USER_DIR=self.user_dir,
                   PATH=f"{self.bin_dir}:{os.environ.get('PATH', '')}")
        res = subprocess.run([sys.executable, SCRIPT], env=env, capture_output=True, text=True)
        assert res.returncode == 0, f"sync exited {res.returncode}: {res.stderr}"

    def calls(self):
        """Accepted rspamc calls since the last call to calls(), as ['learn_spam|Subject', ...]."""
        if not os.path.exists(self.rspamc_log):
            return []
        with open(self.rspamc_log) as f:
            out = f.read().splitlines()
        os.remove(self.rspamc_log)
        return out

    def state(self, mailbox=None):
        conn = sqlite3.connect(self.state_db)
        try:
            q = "SELECT mailbox, message_id, learned_type FROM learned_messages"
            args = ()
            if mailbox:
                q += " WHERE mailbox = ?"
                args = (mailbox,)
            return sorted(conn.execute(q + " ORDER BY 1, 2", args).fetchall())
        finally:
            conn.close()


class Mailbox:
    def __init__(self, fx, path, conn, key):
        self.fx, self.path, self.conn, self.key = fx, path, conn, key

    def add(self, msg_id, fid, subject, eml=True, deleted=0, associated=0):
        mid = f"R-{msg_id % 256:02x}/m{msg_id}"
        self.fx._cn += 1
        self.conn.execute(
            "INSERT INTO messages (message_id, parent_fid, is_deleted, is_associated, change_number, "
            "message_size, mid_string) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (msg_id, fid, deleted, associated, self.fx._cn, mid if eml else None),
        )
        self.conn.commit()
        if eml:
            eml_path = os.path.join(self.path, "eml", mid)
            os.makedirs(os.path.dirname(eml_path), exist_ok=True)
            with open(eml_path, "w") as f:
                f.write(f"Subject: {subject}\n\nbody {subject}\n")

    def update(self, msg_id, **cols):
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.conn.execute(f"UPDATE messages SET {sets} WHERE message_id = ?", (*cols.values(), msg_id))
        self.conn.commit()

    def delete(self, msg_id):
        self.conn.execute("DELETE FROM messages WHERE message_id = ?", (msg_id,))
        self.conn.commit()


def with_fixture(fn):
    def wrapper():
        with tempfile.TemporaryDirectory() as tmp:
            fn(Fixture(tmp))
        print(f"    [+] {fn.__name__}")
    wrapper.__name__ = fn.__name__
    return wrapper


@with_fixture
def test_junk_learned_as_spam_once_per_mailbox(fx):
    mb = fx.mailbox("example.com", "testuser~1", "testuser@example.com")
    mb.add(1048576, JUNK, "Spam A")
    mb.add(1048577, INBOX, "Normal mail")
    fx.run()
    assert fx.calls() == ["learn_spam|Spam A"]
    assert fx.state() == [("example.com/testuser~1", 1048576, "spam")]
    fx.run()
    assert fx.calls() == [], "unchanged mailbox must not trigger any further learning"


@with_fixture
def test_learning_targets_internal_controller_not_password_protected_frontend(fx):
    mb = fx.mailbox("example.com", "testuser", "testuser@example.com")
    mb.add(1, JUNK, "Spam")
    fx.run()
    with open(fx.rspamc_args) as f:
        assert f.read().split() == ["-h", "127.0.0.1:11335", "learn_spam"]


@with_fixture
def test_junk_to_inbox_learns_ham(fx):
    mb = fx.mailbox("example.com", "testuser", "testuser@example.com")
    mb.add(1, JUNK, "Wrongly filed")
    fx.run()
    fx.calls()
    mb.update(1, parent_fid=INBOX)
    fx.run()
    assert fx.calls() == ["learn_ham|Wrongly filed"]
    assert fx.state() == [("example.com/testuser", 1, "ham")]
    fx.run()
    assert fx.calls() == []


@with_fixture
def test_junk_to_deleted_items_or_expunged_is_pruned_without_ham(fx):
    mb = fx.mailbox("example.com", "testuser", "testuser@example.com")
    for i in (1, 2, 3):
        mb.add(i, JUNK, f"Spam {i}")
    fx.run()
    fx.calls()
    mb.update(1, parent_fid=DELETED_ITEMS)
    mb.update(2, is_deleted=1)
    mb.delete(3)
    fx.run()
    assert fx.calls() == [], "deleted/expunged messages must not be learned as ham"
    assert fx.state() == []


@with_fixture
def test_soft_deleted_and_fai_in_junk_are_not_learned(fx):
    mb = fx.mailbox("example.com", "testuser", "testuser@example.com")
    mb.add(1, JUNK, "Soft deleted", deleted=1)
    mb.add(2, JUNK, "Folder associated item", associated=1)
    mb.add(3, JUNK, "Real spam")
    fx.run()
    assert fx.calls() == ["learn_spam|Real spam"]
    assert [r[1] for r in fx.state()] == [3]


@with_fixture
def test_same_message_id_in_two_mailboxes_is_tracked_independently(fx):
    a = fx.mailbox("example.com", "alice", "alice@example.com")
    b = fx.mailbox("example.com", "bob", "bob@example.com")
    a.add(1048576, JUNK, "Alice spam")
    b.add(1048576, JUNK, "Bob spam")
    fx.run()
    assert sorted(fx.calls()) == ["learn_spam|Alice spam", "learn_spam|Bob spam"]
    b.update(1048576, parent_fid=INBOX)
    fx.run()
    assert fx.calls() == ["learn_ham|Bob spam"]
    assert fx.state() == [("example.com/alice", 1048576, "spam"), ("example.com/bob", 1048576, "ham")]


@with_fixture
def test_rspamd_failure_is_retried_next_run(fx):
    mb = fx.mailbox("example.com", "testuser", "testuser@example.com")
    mb.add(1, JUNK, "Spam")
    open(fx.rspamc_fail, "w").close()
    fx.run()
    assert fx.state() == []
    os.remove(fx.rspamc_fail)
    fx.run()
    assert fx.calls() == ["learn_spam|Spam"]
    assert fx.state() == [("example.com/testuser", 1, "spam")]


@with_fixture
def test_failed_ham_learning_is_retried(fx):
    mb = fx.mailbox("example.com", "testuser", "testuser@example.com")
    mb.add(1, JUNK, "Maybe ham")
    fx.run()
    mb.update(1, parent_fid=INBOX)
    open(fx.rspamc_fail, "w").close()
    fx.run()
    assert fx.state() == [("example.com/testuser", 1, "spam")], "failed ham learn must keep the spam row"
    os.remove(fx.rspamc_fail)
    fx.run()
    assert fx.state() == [("example.com/testuser", 1, "ham")]


@with_fixture
def test_missing_eml_exports_using_store_smtp_address(fx):
    mb = fx.mailbox("example.com", "testuser~1", "testuser@example.com")
    mb.add(7, JUNK, "ignored", eml=False)
    open(fx.exm2eml_fail, "w").close()
    fx.run()
    assert fx.state() == [], "export failure must leave the message unlearned"
    os.remove(fx.exm2eml_fail)
    fx.run()
    assert fx.calls() == ["learn_spam|Exported 7"]
    with open(fx.exm2eml_log) as f:
        lines = f.read().splitlines()
    assert lines[-1] == "-u testuser@example.com 7", f"export must use the store address, got {lines[-1]!r}"


@with_fixture
def test_missing_eml_without_store_address_is_skipped(fx):
    mb = fx.mailbox("example.com", "testuser", "testuser@example.com")
    mb.conn.execute("DELETE FROM store_properties")
    mb.conn.commit()
    mb.add(7, JUNK, "ignored", eml=False)
    fx.run()
    assert fx.calls() == [] and fx.state() == []
    assert not os.path.exists(fx.exm2eml_log)


@with_fixture
def test_progress_is_committed_incrementally(fx):
    mb = fx.mailbox("example.com", "testuser", "testuser@example.com")
    mb.add(1, JUNK, "Good spam")
    mb.add(2, JUNK, "REJECT_ME")
    fx.run()
    assert fx.state() == [("example.com/testuser", 1, "spam")]


@with_fixture
def test_large_mailbox_unchanged_triggers_no_learning(fx):
    mb = fx.mailbox("example.com", "testuser", "testuser@example.com")
    mb.conn.executemany(
        "INSERT INTO messages (message_id, parent_fid, is_deleted, is_associated, change_number, message_size, mid_string) "
        "VALUES (?, ?, 0, 0, ?, 1, ?)",
        [(i, JUNK, i, f"R-00/m{i}") for i in range(1, 1001)],
    )
    mb.conn.commit()
    os.makedirs(os.path.join(mb.path, "eml", "R-00"))
    for i in range(1, 1001):
        with open(os.path.join(mb.path, "eml", "R-00", f"m{i}"), "w") as f:
            f.write(f"Subject: s{i}\n\nx\n")
    fx.run()
    assert len(fx.calls()) == 1000
    fx.run()
    assert fx.calls() == []


def test_live_bayes_lifecycle():
    # 1. Check container availability
    res = container_exec("true")
    if res.returncode != 0:
        if os.environ.get("CI", "").lower() in ("1", "true", "yes"):
            raise AssertionError("Grommunio container is not running under CI")
        print("    [!] Container not running locally, skipping live Bayes test.")
        return

    # 2. Ensure test user mailbox exists
    rcpt = ensure_testuser()
    mbox_dir = mailbox_dir(rcpt)
    assert mbox_dir, f"Could not find mailbox directory for {rcpt}"
    user_db = f"{mbox_dir}/exmdb/exchange.sqlite3"

    # 3. Inject message 1 (for spam -> ham transition)
    sub1 = f"LiveBayes1-{os.urandom(4).hex()}"
    send_mail(rcpt, subject=sub1, body="Message 1 for live Bayes ham transition.")
    assert wait_until(lambda: mailbox_contains(rcpt, sub1), timeout=15, interval=1.0), "Message 1 not delivered"

    # 4. Inject message 2 (for spam -> deleted items pruning)
    sub2 = f"LiveBayes2-{os.urandom(4).hex()}"
    send_mail(rcpt, subject=sub2, body="Message 2 for live Bayes pruning.")
    assert wait_until(lambda: mailbox_contains(rcpt, sub2), timeout=15, interval=1.0), "Message 2 not delivered"

    def find_id(subject):
        find_script = f"""
import sqlite3
c = sqlite3.connect("file:{user_db}?mode=ro", uri=True)
row = c.execute("SELECT m.message_id FROM message_properties p JOIN messages m USING (message_id) WHERE p.proptag = 236781599 AND p.propval LIKE '%{subject}%'").fetchone()
print(row[0] if row else "")
"""
        res = container_exec("python3", "-", input=find_script)
        assert res.returncode == 0 and res.stdout.strip(), f"Could not find message_id for {subject}: {res.stderr}"
        return int(res.stdout.strip())

    id1 = find_id(sub1)
    id2 = find_id(sub2)

    def get_type(mid):
        check_script = f"""
import sqlite3
c = sqlite3.connect("file:/var/lib/rspamd/bayes_learned.sqlite3?mode=ro", uri=True)
row = c.execute("SELECT learned_type FROM learned_messages WHERE message_id = {mid}").fetchone()
print(row[0] if row else "NONE")
"""
        res = container_exec("python3", "-", input=check_script)
        return res.stdout.strip() if res.returncode == 0 else "ERROR"

    try:
        # Move both messages to Junk (parent_fid = 23)
        move_to_junk = f"""
import sqlite3
c = sqlite3.connect("{user_db}")
max_cn = c.execute("SELECT COALESCE(MAX(change_number), 0) FROM messages").fetchone()[0]
c.execute("UPDATE messages SET parent_fid = 23, change_number = ? WHERE message_id = ?", (max_cn + 1, {id1}))
c.execute("UPDATE messages SET parent_fid = 23, change_number = ? WHERE message_id = ?", (max_cn + 2, {id2}))
c.commit()
"""
        res_m = container_exec("python3", "-", input=move_to_junk)
        assert res_m.returncode == 0, f"Failed to move messages to Junk: {res_m.stderr}"

        # Run bayes sync
        sync_res = container_exec("python3", "/usr/local/bin/grommunio-bayes-sync.py")
        assert sync_res.returncode == 0, f"bayes-sync failed: {sync_res.stderr}"

        # Both should be learned as spam
        assert get_type(id1) == "spam", f"Expected 'spam' for msg 1, got {get_type(id1)}"
        assert get_type(id2) == "spam", f"Expected 'spam' for msg 2, got {get_type(id2)}"

        # Case 1: Move msg 1 from Junk to Inbox (parent_fid = 13) -> re-learned as ham
        move_inbox = f"""
import sqlite3
c = sqlite3.connect("{user_db}")
max_cn = c.execute("SELECT COALESCE(MAX(change_number), 0) FROM messages").fetchone()[0]
c.execute("UPDATE messages SET parent_fid = 13, change_number = ? WHERE message_id = ?", (max_cn + 1, {id1}))
c.commit()
"""
        res_m1 = container_exec("python3", "-", input=move_inbox)
        assert res_m1.returncode == 0, f"Failed to move msg 1 to Inbox: {res_m1.stderr}"

        # Case 2: Move msg 2 from Junk to Deleted Items (parent_fid = 11) -> pruned
        move_del = f"""
import sqlite3
c = sqlite3.connect("{user_db}")
max_cn = c.execute("SELECT COALESCE(MAX(change_number), 0) FROM messages").fetchone()[0]
c.execute("UPDATE messages SET parent_fid = 11, change_number = ? WHERE message_id = ?", (max_cn + 1, {id2}))
c.commit()
"""
        res_m2 = container_exec("python3", "-", input=move_del)
        assert res_m2.returncode == 0, f"Failed to move msg 2 to Deleted Items: {res_m2.stderr}"

        # Run bayes sync
        sync_res2 = container_exec("python3", "/usr/local/bin/grommunio-bayes-sync.py")
        assert sync_res2.returncode == 0, f"bayes-sync failed: {sync_res2.stderr}"

        # Assert msg 1 re-learned as ham, msg 2 pruned
        assert get_type(id1) == "ham", f"Expected 'ham' for msg 1, got {get_type(id1)}"
        assert get_type(id2) == "NONE", f"Expected pruned (NONE) for msg 2, got {get_type(id2)}"

    finally:
        cleanup_script = f"""
import sqlite3
try:
    c = sqlite3.connect("{user_db}")
    c.execute("DELETE FROM messages WHERE message_id IN ({id1}, {id2})")
    c.execute("DELETE FROM message_properties WHERE message_id IN ({id1}, {id2})")
    c.commit()
except Exception:
    pass

try:
    s = sqlite3.connect("/var/lib/rspamd/bayes_learned.sqlite3")
    s.execute("DELETE FROM learned_messages WHERE message_id IN ({id1}, {id2})")
    s.commit()
except Exception:
    pass
"""
        container_exec("python3", "-", input=cleanup_script)

    print("    [+] test_live_bayes_lifecycle")


def main():
    print("==> Checking Bayes Synchronization Engine...")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            print(f"    [-] {t.__name__}: {exc}", file=sys.stderr)
            return 1
    print("[+] SUCCESS: Bayes synchronization verified.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
