"""
Database layer: PostgreSQL connection and vocabulary tables.

- DATABASE_URL in .env (e.g. postgresql://user:password@host:5432/dbname).
- user_id everywhere = Telegram chat_id (int).
- vocabulary table: id, user_id, word, meaning, created_at.
- init_db() creates the table on startup; safe to call every time.
"""

import os
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def get_database_url():
    """Read DATABASE_URL from env. Required for vocabulary and future features."""
    url = os.getenv("DATABASE_URL")
    if not url:
        logger.warning("DATABASE_URL is not set; vocabulary and DB features will fail.")
    return url


@contextmanager
def get_connection():
    """
    Context manager that yields a single PostgreSQL connection.
    Use for short-lived operations; connection is closed on exit.
    """
    url = get_database_url()
    if not url:
        yield None
        return
    try:
        import psycopg2
        conn = psycopg2.connect(url)
        conn.autocommit = False
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as e:
        logger.exception("Database connection error: %s", e)
        raise


def init_db():
    """
    Create vocabulary table if it does not exist.
    Safe to call on every startup (IF NOT EXISTS).
    """
    url = get_database_url()
    if not url:
        logger.warning("Skipping init_db: DATABASE_URL not set")
        return
    with get_connection() as conn:
        if conn is None:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vocabulary (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    word TEXT NOT NULL,
                    meaning TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_vocabulary_user_id ON vocabulary (user_id);
            """)
        logger.info("Database schema checked (vocabulary table).")


# ---------------------------------------------------------------------------
# Vocabulary API (used by vocabulary module)
# ---------------------------------------------------------------------------


def add_word(user_id: int, word: str, meaning: str = None) -> bool:
    """
    Add a word for the user. user_id is Telegram chat_id.
    Returns True if inserted, False on error or duplicate (same user_id + word).
    """
    if not word or not word.strip():
        return False
    word = word.strip()
    meaning = (meaning or "").strip() or None
    try:
        with get_connection() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vocabulary (user_id, word, meaning)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, word, meaning),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.exception("add_word failed: %s", e)
        return False


def get_words(user_id: int, limit: int = 100):
    """
    Return list of (id, word, meaning, created_at) for the user, newest first.
    """
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, word, meaning, created_at
                    FROM vocabulary
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return cur.fetchall()
    except Exception as e:
        logger.exception("get_words failed: %s", e)
        return []


def delete_word(user_id: int, word_id: int) -> bool:
    """Delete a vocabulary entry by id if it belongs to the user."""
    try:
        with get_connection() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM vocabulary WHERE id = %s AND user_id = %s",
                    (word_id, user_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.exception("delete_word failed: %s", e)
        return False
