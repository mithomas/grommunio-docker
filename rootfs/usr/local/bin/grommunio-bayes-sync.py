#!/usr/bin/env python3
"""
grommunio-bayes-sync.py
High-performance batched Bayes training engine for Rspamd and Grommunio.
1. Automatically learns messages in Junk E-mail (parent_fid = 23 / 0x17) as SPAM.
2. Automatically learns messages moved OUT of Junk E-mail as HAM.
3. Automatically prunes records for messages moved to Trash (27) or expunged.

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

# Gromox / MAPI folder identifiers
FOLDER_JUNK = 23   # MAPI 0x17: Junk E-mail
FOLDER_TRASH = 27  # MAPI 0x1B: Deleted Items / Trash


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
        data = None
        if os.path.isfile(eml_file):
            try:
                with open(eml_file, "rb") as f_eml:
                    data = f_eml.read()
            except OSError as exc:
                logger.warning("Could not read %s: %s", eml_file, exc)
                return False
        else:
            exm_proc = subprocess.run(
                ["gromox-exm2eml", "-u", username, str(msg_id)],
                capture_output=True,
                timeout=20,
            )
            if exm_proc.returncode != 0 or not exm_proc.stdout:
                logger.warning(
                    "gromox-exm2eml failed for %s msg %s (exit %s)",
                    username,
                    msg_id,
                    exm_proc.returncode,
                )
                return False
            data = exm_proc.stdout

        if not data:
            return False

        res = subprocess.run(cmd, input=data, capture_output=True, timeout=20)
        return res.returncode == 0
    except Exception as exc:
        logger.warning("Error executing rspamc for %s msg %s: %s", username, msg_id, exc)
        return False


def sync_mailbox(conn, user_db):
    exmdb_dir = os.path.dirname(user_db)
    mailbox_dir = os.path.dirname(exmdb_dir)
    username = os.path.basename(mailbox_dir)

    try:
        escaped_db = os.path.abspath(user_db).replace("'", "''")
        conn.execute(f"ATTACH DATABASE 'file:{escaped_db}?mode=ro' AS userdb")
    except sqlite3.OperationalError as exc:
        logger.warning("Could not attach %s: %s", user_db, exc)
        return

    try:
        cursor = conn.cursor()

        # 1. Detect unlearned messages in Junk E-mail (parent_fid = 23)
        cursor.execute(f"""
            SELECT m.message_id, m.mid_string
            FROM userdb.messages m
            LEFT JOIN learned_messages l
              ON l.username = ? AND l.message_id = m.message_id
            WHERE m.parent_fid = {FOLDER_JUNK}
              AND (l.learned_type IS NULL OR l.learned_type != 'spam');
        """, (username,))
        unlearned_spam = cursor.fetchall()

        for msg_id, mid_str in unlearned_spam:
            if not msg_id:
                continue
            eml_file = os.path.join(mailbox_dir, "eml", mid_str or "")
            if learn_message("spam", eml_file, username, msg_id):
                conn.execute("""
                    INSERT OR REPLACE INTO learned_messages (username, message_id, mid_string, learned_type, learned_at)
                    VALUES (?, ?, ?, 'spam', datetime('now'))
                """, (username, msg_id, mid_str))
                conn.commit()
                logger.info("Learned SPAM for %s (msg %s)", username, msg_id)

        # 2. Detect messages moved OUT of Junk E-mail or expunged
        cursor.execute(f"""
            SELECT l.message_id, l.mid_string, m.parent_fid
            FROM learned_messages l
            LEFT JOIN userdb.messages m
              ON m.message_id = l.message_id
            WHERE l.username = ? AND l.learned_type = 'spam'
              AND (m.parent_fid IS NULL OR m.parent_fid != {FOLDER_JUNK});
        """, (username,))
        previously_spam = cursor.fetchall()

        for msg_id, mid_str, parent_fid in previously_spam:
            if not msg_id:
                continue

            if parent_fid is None or parent_fid == FOLDER_TRASH:
                conn.execute("""
                    DELETE FROM learned_messages WHERE username = ? AND message_id = ?
                """, (username, msg_id))
                conn.commit()
            else:
                eml_file = os.path.join(mailbox_dir, "eml", mid_str or "")
                if learn_message("ham", eml_file, username, msg_id):
                    conn.execute("""
                        INSERT OR REPLACE INTO learned_messages (username, message_id, mid_string, learned_type, learned_at)
                        VALUES (?, ?, ?, 'ham', datetime('now'))
                    """, (username, msg_id, mid_str))
                    conn.commit()
                    logger.info("Learned HAM for %s (msg %s moved to folder %s)", username, msg_id, parent_fid)

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
        pattern = os.path.join(GROMOX_USER_DIR, "*", "*", "exchange.sqlite3")
        for user_db in sorted(glob.glob(pattern)):
            sync_mailbox(conn, user_db)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
