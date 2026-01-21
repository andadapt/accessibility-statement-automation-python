# cli.py
import click
import time
import csv
import os
import re
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import db
from scraper import fetch_html, extract_sections


# -------------------------------------------------
# Helpers
# -------------------------------------------------

def sanitize_table_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower()
    if not name:
        name = "table"
    if name[0].isdigit():
        name = f"t_{name}"
    return name


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def ensure_table(conn, table_name: str):
    conn.execute(f"""
    CREATE TABLE IF NOT EXISTS {quote_ident(table_name)} (
        id INTEGER PRIMARY KEY,
        product_name TEXT UNIQUE,
        portfolio TEXT,
        url TEXT,
        fetched_at TEXT,
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


def upsert_row(conn, table_name: str, product_name: str, data: dict):
    if not product_name:
        click.echo(f"⚠️  Skipping upsert: Invalid product_name '{product_name}'")
        return

    fields = ["product_name"] + list(data.keys())
    values = [product_name] + list(data.values())

    placeholders = ", ".join(["?"] * len(fields))
    updates = ", ".join([f"{quote_ident(f)}=excluded.{quote_ident(f)}" for f in data.keys()])

    conn.execute(
        f"""
        INSERT INTO {quote_ident(table_name)} ({", ".join(map(quote_ident, fields))})
        VALUES ({placeholders})
        ON CONFLICT(product_name) DO UPDATE SET {updates}
        """,
        values,
    )
    conn.commit()


def dump_table_to_json(conn, table_name: str, out_path: Path):
    rows = conn.execute(f"SELECT * FROM {quote_ident(table_name)}").fetchall()
    data = [dict(r) for r in rows]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def scrape_table(conn, table_name: str):
    rows = conn.execute(
        f"SELECT product_name, url FROM {quote_ident(table_name)}"
    ).fetchall()

    url_map = {}
    skipped = 0

    for row in rows:
        if row["url"]:
            url_map.setdefault(row["url"], []).append(row["product_name"])
        else:
            skipped += 1

    for url, products in url_map.items():
        scraped_date = datetime.now(ZoneInfo("Europe/London")).strftime("%d/%m/%Y")

        click.echo(f"\n🔗 [{table_name}] Scraping {url}")
        html = fetch_html(url)

        if not html:
            status = {"status": "failed", "fetched_at": scraped_date}
        else:
            data = extract_sections(html)
            if not any(data.values()):
                status = {"status": "no_content", "fetched_at": scraped_date}
            else:
                status = {**data, "status": "success", "fetched_at": scraped_date}

        for product in products:
            upsert_row(conn, table_name, product, status)

    return len(url_map), skipped


def print_last_review_summary(conn, table_name: str):
    """
    Print how many rows per portfolio have a non-empty last_review.
    """
    rows = conn.execute(f"""
        SELECT
            portfolio,
            COUNT(*) AS count
        FROM {quote_ident(table_name)}
        WHERE last_review IS NOT NULL
          AND last_review != ''
        GROUP BY portfolio
        ORDER BY portfolio
    """).fetchall()

    click.echo(f"\n📊 Last review summary (table: {table_name})")

    if not rows:
        click.echo("  (no rows with last_review)")
        return

    for row in rows:
        portfolio = row["portfolio"] or "(no portfolio)"
        click.echo(f"- {portfolio}: {row['count']}")


# -------------------------------------------------
# CLI
# -------------------------------------------------

@click.group()
def cli():
    """Accessibility Statement Scraper CLI"""


@cli.command("run-all")
@click.option("--db-path", default="scraped_content.db")
def run_all(db_path):
    """
    Monthly command:
    - Wipes DB
    - Imports all CSVs from inputs/
    - Creates one table per CSV
    - Scrapes all tables
    - Prints per-portfolio last_review counts
    - Exports outputs/<table>.json
    """
    base_dir = Path(__file__).resolve().parent
    inputs_dir = base_dir / "inputs"
    outputs_dir = base_dir / "outputs"

    if not inputs_dir.exists():
        click.echo("❌ inputs/ folder not found")
        return

    csv_files = sorted(inputs_dir.glob("*.csv"))
    if not csv_files:
        click.echo("❌ No CSV files found in inputs/")
        return

    # Wipe database
    db_file = Path(db_path)
    if not db_file.is_absolute():
        db_file = base_dir / db_file

    if db_file.exists():
        db_file.unlink()
        click.echo("🧹 Database wiped")

    conn = db.connect(str(db_file))

    tables = []

    # -----------------------------------------
    # Import CSVs
    # -----------------------------------------
    for csv_path in csv_files:
        table = sanitize_table_name(csv_path.stem)
        tables.append(table)

        click.echo(f"\n📥 Importing {csv_path.name} → {table}")
        ensure_table(conn, table)

        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            for i, row in enumerate(reader, start=1):

                # -------- NORMALISE HEADERS & VALUES --------
                row_norm = {}
                for k, v in row.items():
                    if k is None:
                        continue
                    nk = k.replace("\ufeff", "").replace("\u00a0", " ").strip()
                    row_norm[nk] = (v or "").strip()
                # --------------------------------------------

                product_name = row_norm.get("Product Name", "")
                portfolio = row_norm.get("Portfolio", "")
                url = row_norm.get("Statement URL", "")

                if url.lower() in {"", "null", "none", "na", "n/a", "working"}:
                    url = ""

                status = "pending" if url else "no_url"

                if not product_name:
                    click.echo(f"⚠️  {csv_path.name} row {i}: Missing product name, skipping")
                    continue

                upsert_row(
                    conn,
                    table,
                    product_name,
                    {
                        "portfolio": portfolio,
                        "url": url or None,
                        "status": status,
                    },
                )

        click.echo(f"✅ Imported {table}")

    # -----------------------------------------
    # Scrape
    # -----------------------------------------
    click.echo("\n🚀 Starting scrape")
    for table in tables:
        scraped, skipped = scrape_table(conn, table)
        click.echo(f"✅ {table}: scraped {scraped} URLs, skipped {skipped}")

    # -----------------------------------------
    # Last review summary (per portfolio)
    # -----------------------------------------
    click.echo("\n📌 Last reviewed counts by portfolio")
    for table in tables:
        print_last_review_summary(conn, table)

    # -----------------------------------------
    # Export
    # -----------------------------------------
    click.echo("\n📤 Exporting JSON")
    for table in tables:
        out_file = outputs_dir / f"{table}.json"
        dump_table_to_json(conn, table, out_file)
        click.echo(f"✅ {out_file}")

    click.echo("\n🎉 run-all complete")


if __name__ == "__main__":
    cli()
