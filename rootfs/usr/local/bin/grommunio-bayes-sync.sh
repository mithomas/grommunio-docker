#!/bin/bash
# ==============================================================================
# grommunio-bayes-sync.sh
# Bidirectional automatic Bayes training for Rspamd and Grommunio:
# 1. Learns messages in Junk E-mail (parent_fid = 0x17 / 23) as SPAM.
# 2. Automatically learns messages moved OUT of Junk E-mail as HAM.
# ==============================================================================
set -eo pipefail

STATE_DB="/var/lib/rspamd/bayes_learned.sqlite3"
GROMOX_USER_DIR="/var/lib/gromox/user"

# Ensure state table exists
sqlite3 "${STATE_DB}" "CREATE TABLE IF NOT EXISTS learned_messages (
    username TEXT,
    message_id INTEGER,
    mid_string TEXT,
    learned_type TEXT,
    learned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (username, message_id)
);"

if [ ! -d "${GROMOX_USER_DIR}" ]; then
    exit 0
fi

# Find all user mailbox databases
find "${GROMOX_USER_DIR}" -mindepth 3 -maxdepth 3 -name "exchange.sqlite3" | while read -r USER_DB; do
    EXMDB_DIR=$(dirname "${USER_DB}")
    MAILBOX_DIR=$(dirname "${EXMDB_DIR}")
    USERNAME=$(basename "${MAILBOX_DIR}")

    # --------------------------------------------------------------------------
    # 1. Process messages currently in Junk E-mail (parent_fid = 23 / 0x17)
    # --------------------------------------------------------------------------
    sqlite3 -separator "|" "${USER_DB}" "SELECT message_id, mid_string FROM messages WHERE parent_fid = 23;" 2>/dev/null | while IFS="|" read -r MSG_ID MID_STR; do
        [ -z "${MSG_ID}" ] && continue

        PREV_TYPE=$(sqlite3 "${STATE_DB}" "SELECT learned_type FROM learned_messages WHERE username = '${USERNAME}' AND message_id = ${MSG_ID};" 2>/dev/null || true)

        if [ "${PREV_TYPE}" != "spam" ]; then
            EML_FILE="${MAILBOX_DIR}/eml/${MID_STR}"
            LEARN_SUCCESS=false

            if [ -f "${EML_FILE}" ]; then
                if rspamc learn_spam < "${EML_FILE}" >/dev/null 2>&1; then
                    LEARN_SUCCESS=true
                fi
            else
                if gromox-exm2eml -u "${USERNAME}" "${MSG_ID}" 2>/dev/null | rspamc learn_spam >/dev/null 2>&1; then
                    LEARN_SUCCESS=true
                fi
            fi

            if [ "${LEARN_SUCCESS}" = "true" ]; then
                sqlite3 "${STATE_DB}" "INSERT OR REPLACE INTO learned_messages (username, message_id, mid_string, learned_type, learned_at) VALUES ('${USERNAME}', ${MSG_ID}, '${MID_STR}', 'spam', datetime('now'));"
                echo "[bayes-sync] Learned SPAM for ${USERNAME} (msg ${MSG_ID})"
            fi
        fi
    done

    # --------------------------------------------------------------------------
    # 2. Detect messages moved OUT of Junk E-mail -> Learn as HAM
    # --------------------------------------------------------------------------
    sqlite3 -separator "|" "${STATE_DB}" "SELECT message_id, mid_string FROM learned_messages WHERE username = '${USERNAME}' AND learned_type = 'spam';" 2>/dev/null | while IFS="|" read -r MSG_ID MID_STR; do
        [ -z "${MSG_ID}" ] && continue

        CURRENT_FID=$(sqlite3 "${USER_DB}" "SELECT parent_fid FROM messages WHERE message_id = ${MSG_ID};" 2>/dev/null || true)

        if [ -n "${CURRENT_FID}" ]; then
            # If message is still in Junk (23), leave as spam
            if [ "${CURRENT_FID}" = "23" ]; then
                continue
            fi

            # If moved to Deleted Items / Trash (27 / 0x1b), user simply deleted it
            if [ "${CURRENT_FID}" = "27" ]; then
                sqlite3 "${STATE_DB}" "DELETE FROM learned_messages WHERE username = '${USERNAME}' AND message_id = ${MSG_ID};"
                continue
            fi

            # Message was moved to Inbox (19 / 0x13) or user folder: Learn as HAM
            EML_FILE="${MAILBOX_DIR}/eml/${MID_STR}"
            LEARN_SUCCESS=false

            if [ -f "${EML_FILE}" ]; then
                if rspamc learn_ham < "${EML_FILE}" >/dev/null 2>&1; then
                    LEARN_SUCCESS=true
                fi
            else
                if gromox-exm2eml -u "${USERNAME}" "${MSG_ID}" 2>/dev/null | rspamc learn_ham >/dev/null 2>&1; then
                    LEARN_SUCCESS=true
                fi
            fi

            if [ "${LEARN_SUCCESS}" = "true" ]; then
                sqlite3 "${STATE_DB}" "INSERT OR REPLACE INTO learned_messages (username, message_id, mid_string, learned_type, learned_at) VALUES ('${USERNAME}', ${MSG_ID}, '${MID_STR}', 'ham', datetime('now'));"
                echo "[bayes-sync] Learned HAM for ${USERNAME} (msg ${MSG_ID} moved to folder ${CURRENT_FID})"
            fi
        else
            # Message was permanently expunged
            sqlite3 "${STATE_DB}" "DELETE FROM learned_messages WHERE username = '${USERNAME}' AND message_id = ${MSG_ID};"
        fi
    done
done
