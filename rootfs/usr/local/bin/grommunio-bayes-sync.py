#!/usr/bin/env python3
"""
grommunio-bayes-sync.py
High-performance batched Bayes training engine for Rspamd and Grommunio.
1. Automatically learns messages in Junk E-mail (parent_fid = 23 / 0x17) as SPAM.
2. Automatically learns messages moved OUT of Junk E-mail as HAM.
3. Automatically prunes records for messages moved to Trash (27) or expunged.

Performance design:
- Uses SQLite ATTACH DATABASE and set-based differential queries.
- Zero per-message sqlite3 CLI process forks.
- Bounded constant-time execution per mailbox when no messages changed folder.
- Atomic batch updates to state database.
"""
import glob
import logging
import os
import sqlite3
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("bayes-sync")

STATE_DB = os.environ.get("BAYES_STATE_DB", "/var/lib/rspamd/bayes_learned.sqlite3")
GROMOX_USER_DIR = os.environ.get("GROMOX_USER_DIR", "/var/lib/gromox/user")


def init_state_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS learned_messages (
            username TEXT,
            message_id INTEGER,
            mid_string TEXT,
            learned_type TEXT,
            learned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (username, message_id)
        );
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_learned_user_type
        ON learned_messages (username, learned_type);
    """)
    conn.commit()


def learn_message(learn_type, eml_file, username, msg_id):
    """Pipes message payload to rspamc learn_spam or learn_ham."""
    cmd = ["rspamc", f"learn_{learn_type}"]
    try:
        if os.path.isfile(eml_file):
            with open(eml_file, "rb") as f_eml:
                res = subprocess.run(cmd, stdin=f_eml, capture_output=True, timeout=20)
        else:
            exm_proc = subprocess.Popen(
                ["gromox-exm2eml", "-u", username, str(msg_id)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            res = subprocess.run(cmd, stdin=exm_proc.stdout, capture_output=True, timeout=20)
            exm_proc.wait()
        return res.returncode == 0
    except Exception as exc:
        logger.warning("[bayes-sync] Error executing rspamc for %s msg %s: %s", username, msg_id, exc)
        return False


def sync_mailbox(conn, user_db):
    exmdb_dir = os.path.dirname(user_db)
    mailbox_dir = os.path.dirname(exmdb_dir)
    username = os.path.basename(mailbox_dir)

    try:
        conn.execute("ATTACH DATABASE ? AS userdb", (user_db,))
    except sqlite3.OperationalError as exc:
        logger.warning("[bayes-sync] Could not attach %s: %s", user_db, exc)
        return

    try:
        to_insert_spam = []
        to_insert_ham = []
        to_delete = []

        # ----------------------------------------------------------------------
        # 1. Detect unlearned messages in Junk E-mail (parent_fid = 23)
        # ----------------------------------------------------------------------
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.message_id, m.mid_string
            FROM userdb.messages m
            LEFT JOIN learned_messages l
              ON l.username = ? AND l.message_id = m.message_id
            WHERE m.parent_fid = 23
              AND (l.learned_type IS NULL OR l.learned_type != 'spam');
        """, (username,))
        unlearned_spam = cursor.fetchall()

        for msg_id, mid_str in unlearned_spam:
            if not msg_id:
                continue
            eml_file = os.path.join(mailbox_dir, "eml", mid_str or "")
            if learn_message("spam", eml_file, username, msg_id):
                to_insert_spam.append((username, msg_id, mid_str))
                logger.info("[bayes-sync] Learned SPAM for %s (msg %s)", username, msg_id)

        # ----------------------------------------------------------------------
        # 2. Detect messages moved OUT of Junk E-mail or expunged
        # ----------------------------------------------------------------------
        cursor.execute("""
            SELECT l.message_id, l.mid_string, m.parent_fid
            FROM learned_messages l
            LEFT JOIN userdb.messages m
              ON m.message_id = l.message_id
            WHERE l.username = ? AND l.learned_type = 'spam';
        """, (username,))
        previously_spam = cursor.fetchall()

        for msg_id, mid_str, parent_fid in previously_spam:
            if not msg_id:
                continue

            if parent_fid is None:
                # Message was permanently expunged
                to_delete.append((username, msg_id))
            elif parent_fid == 23:
                # Still in Junk folder
                continue
            elif parent_fid == 27:
                # Moved to Deleted Items / Trash (27)
                to_delete.append((username, msg_id))
            else:
                # Moved to Inbox (19) or user folder: Learn as HAM
                eml_file = os.path.join(mailbox_dir, "eml", mid_str or "")
                if learn_message("ham", eml_file, username, msg_id):
                    to_insert_ham.append((username, msg_id, mid_str))
                    logger.info("[bayes-sync] Learned HAM for %s (msg %s moved to folder %s)", username, msg_id, parent_fid)

        # Commit batch changes
        if to_insert_spam:
            conn.executemany("""
                INSERT OR REPLACE INTO learned_messages (username, message_id, mid_string, learned_type, learned_at)
                VALUES (?, ?, ?, 'spam', datetime('now'))
            """, to_insert_spam)
        if to_insert_ham:
            conn.executemany("""
                INSERT OR REPLACE INTO learned_messages (username, message_id, mid_string, learned_type, learned_at)
                VALUES (?, ?, ?, 'ham', datetime('now'))
            """, to_insert_ham)
        if to_delete:
            conn.executemany("""
                DELETE FROM learned_messages WHERE username = ? AND message_id = ?
            """, to_delete)

        conn.commit()

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

    conn = sqlite3.connect(STATE_DB, timeout=30.0)
    try:
        init_state_db(conn)
        pattern = os.path.join(GROMOX_USER_DIR, "*", "*", "exchange.sqlite3")
        for user_db in sorted(glob.glob(pattern)):
            sync_mailbox(conn, user_db)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
