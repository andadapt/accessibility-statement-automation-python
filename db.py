# db.py
import os
import sqlite3

DEFAULT_DB = os.environ.get("SCRAPER_DB", "scraped_content.db")


def connect(db_path: str = DEFAULT_DB):
    """Connect to the SQLite database with sane defaults."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: str = DEFAULT_DB):
    """
    Initialize the database with the legacy 'pages' table.
    (Used by init/import-links/batch commands.)
    """
    conn = connect(db_path)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS pages (
        id INTEGER PRIMARY KEY,
        product_name TEXT UNIQUE,
        portfolio TEXT,
        url TEXT,
        fetched_at TEXT,          -- DD/MM/YYYY
        feedback TEXT,
        enforcement TEXT,
        compliance_status TEXT,
        preparation TEXT,
        non_accessible TEXT,
        feedback_present TEXT,
        enforcement_present TEXT,
        last_review TEXT,
        wcag TEXT,
        compliance_level TEXT,
        issue_text TEXT,
        status TEXT DEFAULT ''
    );
    """)
    conn.commit()
    return conn


def upsert_page(conn, product_name: str, data: dict):
    """Insert or update a page record based on product_name (unique)."""

    if not product_name:
        print(f"⚠️  Skipping upsert: Invalid product_name '{product_name}'")
        return

    fields = ["product_name"] + list(data.keys())
    values = [product_name] + list(data.values())

    placeholders = ", ".join(["?"] * len(fields))
    updates = ", ".join([f"{field}=excluded.{field}" for field in data.keys()])

    try:
        conn.execute(f"""
            INSERT INTO pages ({", ".join(fields)})
            VALUES ({placeholders})
            ON CONFLICT(product_name) DO UPDATE SET {updates}
        """, values)
        conn.commit()
    except sqlite3.Error as e:
        print(f"❗ Database error during upsert for '{product_name}': {e}")
