#!/usr/bin/env python3
"""
grommunio-bayes-sync.py
High-performance batched Bayes training engine for Rspamd and Grommunio.
1. Automatically learns messages in Junk E-mail as SPAM.
2. Automatically learns messages moved OUT of Junk E-mail as HAM.
3. Automatically prunes records for messages moved to Deleted Items, soft-deleted, or expunged.

Performance design:
- Uses SQLite ATTACH DATABASE with read-only mode and set-based differential queries.
- Zero per-message sqlite3 CLI process forks.
- Bounded constant-time execution per mailbox when no messages changed folder.
- Incremental commits per successful learn to prevent lost progress on interruption.
"""
import glob
import logging
import os
import sqlite3
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="[bayes-sync] %(message)s")
logger = logging.getLogger("bayes-sync")

STATE_DB = os.environ.get("BAYES_STATE_DB", "/var/lib/rspamd/bayes_learned.sqlite3")
GROMOX_USER_DIR = os.environ.get("GROMOX_USER_DIR", "/var/lib/gromox/user")
# Plain rspamc defaults to :11334, which is the password-protected nginx frontend.
RSPAMD_CONTROLLER = os.environ.get("RSPAMD_CONTROLLER", "127.0.0.1:11335")

# Private-store folder ids as allocated by Gromox (verified against a live store).
FOLDER_DELETED_ITEMS = 11
FOLDER_JUNK = 23

PR_SMTP_ADDRESS = 0x39FE001F


def init_state_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS learned_messages (
            mailbox TEXT,
            message_id INTEGER,
            mid_string TEXT,
            learned_type TEXT,
            learned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mailbox, message_id)
        );
    """)
    conn.commit()


def learn_message(learn_type, eml_file, address, msg_id):
    """Pipes message payload to rspamc learn_spam or learn_ham."""
    cmd = ["rspamc", "-h", RSPAMD_CONTROLLER, f"learn_{learn_type}"]
    try:
        if os.path.isfile(eml_file):
            try:
                with open(eml_file, "rb") as f_eml:
                    data = f_eml.read()
            except OSError as exc:
                logger.warning("Could not read %s: %s", eml_file, exc)
                return False
        elif address:
            exm_proc = subprocess.run(
                ["gromox-exm2eml", "-u", address, str(msg_id)],
                capture_output=True,
                timeout=20,
            )
            if exm_proc.returncode != 0 or not exm_proc.stdout:
                logger.warning(
                    "gromox-exm2eml failed for %s msg %s (exit %s)",
                    address,
                    msg_id,
                    exm_proc.returncode,
                )
                return False
            data = exm_proc.stdout
        else:
            logger.warning("No eml file and no store address for msg %s (%s)", msg_id, eml_file)
            return False

        if not data:
            return False

        res = subprocess.run(cmd, input=data, capture_output=True, timeout=20)
        if res.returncode != 0:
            logger.warning("rspamc learn_%s failed for msg %s (exit %s): %s", learn_type, msg_id,
                           res.returncode, (res.stderr or res.stdout).decode(errors="replace").strip()[:200])
            return False
        return True
    except Exception as exc:
        logger.warning("Error executing rspamc for %s msg %s: %s", address, msg_id, exc)
        return False


def store_address(conn):
    try:
        row = conn.execute(
            "SELECT propval FROM userdb.store_properties WHERE proptag = ?",
            (PR_SMTP_ADDRESS,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or not row[0]:
        return None
    val = row[0]
    return val.decode("utf-8", errors="ignore") if isinstance(val, bytes) else str(val)


def record(conn, mailbox, msg_id, mid_str, learned_type):
    conn.execute("""
        INSERT OR REPLACE INTO learned_messages (mailbox, message_id, mid_string, learned_type, learned_at)
        VALUES (?, ?, ?, ?, datetime('now'))
    """, (mailbox, msg_id, mid_str, learned_type))
    conn.commit()


def sync_mailbox(conn, user_db):
    # <user_dir>/<domain>/<mailbox>/exmdb/exchange.sqlite3
    mailbox_dir = os.path.dirname(os.path.dirname(user_db))
    mailbox = os.path.relpath(mailbox_dir, GROMOX_USER_DIR)

    try:
        escaped_db = os.path.abspath(user_db).replace("'", "''")
        conn.execute(f"ATTACH DATABASE 'file:{escaped_db}?mode=ro' AS userdb")
    except sqlite3.OperationalError as exc:
        logger.warning("Could not attach %s: %s", user_db, exc)
        return

    try:
        cursor = conn.cursor()
        address = store_address(conn)

        # 1. Unlearned, live, non-FAI messages in Junk E-mail
        cursor.execute(f"""
            SELECT m.message_id, m.mid_string
            FROM userdb.messages m
            LEFT JOIN learned_messages l
              ON l.mailbox = ? AND l.message_id = m.message_id
            WHERE m.parent_fid = {FOLDER_JUNK}
              AND COALESCE(m.is_deleted, 0) = 0 AND COALESCE(m.is_associated, 0) = 0
              AND (l.learned_type IS NULL OR l.learned_type != 'spam');
        """, (mailbox,))

        for msg_id, mid_str in cursor.fetchall():
            eml_file = os.path.join(mailbox_dir, "eml", mid_str or "")
            if learn_message("spam", eml_file, address, msg_id):
                record(conn, mailbox, msg_id, mid_str, "spam")
                logger.info("Learned SPAM for %s (msg %s)", mailbox, msg_id)

        # 2. Learned spam that left Junk, was soft-deleted, or was expunged
        cursor.execute(f"""
            SELECT l.message_id, l.mid_string, m.parent_fid, m.is_deleted
            FROM learned_messages l
            LEFT JOIN userdb.messages m
              ON m.message_id = l.message_id
            WHERE l.mailbox = ? AND l.learned_type = 'spam'
              AND (m.message_id IS NULL OR m.parent_fid != {FOLDER_JUNK} OR COALESCE(m.is_deleted, 0) = 1);
        """, (mailbox,))

        for msg_id, mid_str, parent_fid, is_deleted in cursor.fetchall():
            if parent_fid is None or is_deleted or parent_fid == FOLDER_DELETED_ITEMS:
                conn.execute(
                    "DELETE FROM learned_messages WHERE mailbox = ? AND message_id = ?",
                    (mailbox, msg_id),
                )
                conn.commit()
                continue

            eml_file = os.path.join(mailbox_dir, "eml", mid_str or "")
            if learn_message("ham", eml_file, address, msg_id):
                record(conn, mailbox, msg_id, mid_str, "ham")
                logger.info("Learned HAM for %s (msg %s moved to folder %s)", mailbox, msg_id, parent_fid)

    finally:
        try:
            conn.execute("DETACH DATABASE userdb")
        except sqlite3.OperationalError:
            pass


def main():
    if not os.path.isdir(GROMOX_USER_DIR):
        return 0

    state_dir = os.path.dirname(STATE_DB)
    if state_dir and not os.path.exists(state_dir):
        os.makedirs(state_dir, exist_ok=True)

    conn = sqlite3.connect(STATE_DB, timeout=30.0, uri=True)
    try:
        init_state_db(conn)
        pattern = os.path.join(GROMOX_USER_DIR, "*", "*", "exmdb", "exchange.sqlite3")
        for user_db in sorted(glob.glob(pattern)):
            sync_mailbox(conn, user_db)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
