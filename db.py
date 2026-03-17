"""
Database layer: PostgreSQL (optional) or local SQLite fallback.

- DATABASE_URL in .env (e.g. postgresql://user:password@host:5432/dbname).
- user_id everywhere = Telegram chat_id (int).
- vocabulary table: id, user_id, word, meaning, created_at.
- vocabulary_examples table: cached examples per word.
- init_db() creates the table on startup; safe to call every time.
"""

import os
import logging
from contextlib import contextmanager
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def get_database_url():
    """Read DATABASE_URL from env. Required for vocabulary and future features."""
    url = os.getenv("DATABASE_URL")
    if not url:
        logger.warning("DATABASE_URL is not set; using local SQLite fallback.")
    return url


@contextmanager
def get_connection():
    """
    Context manager that yields a DB connection.
    - If DATABASE_URL is set: uses PostgreSQL.
    - Otherwise: uses local SQLite file `seashell.db`.
    """
    url = get_database_url()
    if url:
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
            logger.exception("PostgreSQL connection error: %s", e)
            raise
        return

    # SQLite fallback
    import sqlite3

    project_root = Path(__file__).resolve().parent
    db_path = project_root / "seashell.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """
    Create vocabulary table if it does not exist.
    Safe to call on every startup (IF NOT EXISTS).
    """
    with get_connection() as conn:
        if conn is None:
            return
        is_sqlite = conn.__class__.__module__.startswith("sqlite3")
        if is_sqlite:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS vocabulary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    meaning TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vocabulary_user_id ON vocabulary (user_id)")
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uniq_vocabulary_user_word ON vocabulary (user_id, lower(word))"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS vocabulary_examples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    word_id INTEGER NOT NULL REFERENCES vocabulary(id) ON DELETE CASCADE,
                    batch_id TEXT NOT NULL,
                    example_text TEXT NOT NULL,
                    served INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_vocab_examples_user_word_served ON vocabulary_examples (user_id, word_id, served, created_at)"
            )
        else:
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
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS uniq_vocabulary_user_word
                      ON vocabulary (user_id, lower(word));
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS vocabulary_examples (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        word_id INT NOT NULL REFERENCES vocabulary(id) ON DELETE CASCADE,
                        batch_id UUID NOT NULL,
                        example_text TEXT NOT NULL,
                        served BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_vocab_examples_user_word_served
                      ON vocabulary_examples (user_id, word_id, served, created_at);
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
            is_sqlite = conn.__class__.__module__.startswith("sqlite3")
            if is_sqlite:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT OR IGNORE INTO vocabulary (user_id, word, meaning)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, word, meaning),
                )
                return cur.rowcount > 0
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO vocabulary (user_id, word, meaning)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id, lower(word)) DO NOTHING
                        """,
                        (user_id, word, meaning),
                    )
                    return cur.rowcount > 0
    except Exception as e:
        logger.exception("add_word failed: %s", e)
        return False


def get_words(user_id: int, limit: int = 100, offset: int = 0):
    """
    Return list of (id, word, meaning, created_at) for the user, newest first.
    """
    try:
        with get_connection() as conn:
            if conn is None:
                return []
            is_sqlite = conn.__class__.__module__.startswith("sqlite3")
            if is_sqlite:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, word, meaning, created_at
                    FROM vocabulary
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    OFFSET ?
                    """,
                    (user_id, limit, offset),
                )
                return cur.fetchall()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, word, meaning, created_at
                        FROM vocabulary
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        OFFSET %s
                        """,
                        (user_id, limit, offset),
                    )
                    return cur.fetchall()
    except Exception as e:
        logger.exception("get_words failed: %s", e)
        return []


def get_word(user_id: int, word_id: int):
    """Return (id, word, meaning, created_at) for the user or None."""
    try:
        with get_connection() as conn:
            if conn is None:
                return None
            is_sqlite = conn.__class__.__module__.startswith("sqlite3")
            if is_sqlite:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, word, meaning, created_at
                    FROM vocabulary
                    WHERE user_id = ? AND id = ?
                    """,
                    (user_id, word_id),
                )
                return cur.fetchone()
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, word, meaning, created_at
                        FROM vocabulary
                        WHERE user_id = %s AND id = %s
                        """,
                        (user_id, word_id),
                    )
                    return cur.fetchone()
    except Exception as e:
        logger.exception("get_word failed: %s", e)
        return None


def delete_word(user_id: int, word_id: int) -> bool:
    """Delete a vocabulary entry by id if it belongs to the user."""
    try:
        with get_connection() as conn:
            if conn is None:
                return False
            is_sqlite = conn.__class__.__module__.startswith("sqlite3")
            if is_sqlite:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM vocabulary WHERE id = ? AND user_id = ?",
                    (word_id, user_id),
                )
                return cur.rowcount > 0
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM vocabulary WHERE id = %s AND user_id = %s",
                        (word_id, user_id),
                    )
                    return cur.rowcount > 0
    except Exception as e:
        logger.exception("delete_word failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Cached examples for study mode
# ---------------------------------------------------------------------------


def add_examples_batch(user_id: int, word_id: int, examples: list[str], batch_id: str | None = None) -> str | None:
    """
    Insert a batch of cached examples for a word. Returns batch_id on success.
    """
    cleaned = [ex.strip() for ex in (examples or []) if ex and ex.strip()]
    if not cleaned:
        return None
    batch_uuid = uuid.UUID(batch_id) if batch_id else uuid.uuid4()
    try:
        with get_connection() as conn:
            if conn is None:
                return None
            is_sqlite = conn.__class__.__module__.startswith("sqlite3")
            if is_sqlite:
                cur = conn.cursor()
                cur.executemany(
                    """
                    INSERT INTO vocabulary_examples (user_id, word_id, batch_id, example_text, served)
                    VALUES (?, ?, ?, ?, 0)
                    """,
                    [(user_id, word_id, str(batch_uuid), ex) for ex in cleaned],
                )
            else:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO vocabulary_examples (user_id, word_id, batch_id, example_text, served)
                        VALUES (%s, %s, %s, %s, FALSE)
                        """,
                        [(user_id, word_id, batch_uuid, ex) for ex in cleaned],
                    )
        return str(batch_uuid)
    except Exception as e:
        logger.exception("add_examples_batch failed: %s", e)
        return None


def pop_next_example(user_id: int, word_id: int):
    """
    Mark one unserved example as served and return (id, example_text, batch_id).
    """
    try:
        with get_connection() as conn:
            if conn is None:
                return None
            is_sqlite = conn.__class__.__module__.startswith("sqlite3")
            if is_sqlite:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, example_text, batch_id
                    FROM vocabulary_examples
                    WHERE user_id = ? AND word_id = ? AND served = 0
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                    """,
                    (user_id, word_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                ex_id = row[0]
                cur.execute(
                    "UPDATE vocabulary_examples SET served = 1 WHERE id = ?",
                    (ex_id,),
                )
                return row
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        WITH picked AS (
                            SELECT id
                            FROM vocabulary_examples
                            WHERE user_id = %s AND word_id = %s AND served = FALSE
                            ORDER BY created_at ASC, id ASC
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        UPDATE vocabulary_examples ve
                        SET served = TRUE
                        FROM picked
                        WHERE ve.id = picked.id
                        RETURNING ve.id, ve.example_text, ve.batch_id
                        """,
                        (user_id, word_id),
                    )
                    return cur.fetchone()
    except Exception as e:
        logger.exception("pop_next_example failed: %s", e)
        return None


def count_unserved_examples(user_id: int, word_id: int) -> int:
    """How many cached examples remain."""
    try:
        with get_connection() as conn:
            if conn is None:
                return 0
            is_sqlite = conn.__class__.__module__.startswith("sqlite3")
            if is_sqlite:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM vocabulary_examples
                    WHERE user_id = ? AND word_id = ? AND served = 0
                    """,
                    (user_id, word_id),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM vocabulary_examples
                        WHERE user_id = %s AND word_id = %s AND served = FALSE
                        """,
                        (user_id, word_id),
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row else 0
    except Exception as e:
        logger.exception("count_unserved_examples failed: %s", e)
        return 0
