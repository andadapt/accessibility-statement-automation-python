# cli.py
import click
import time
import csv
import db
from scraper import fetch_html, extract_sections


@click.group()
def cli():
    """Accessibility Statement Scraper CLI"""


@cli.command()
@click.option("--db-path", default="scraped_content.db")
def init(db_path):
    """Initialize the database."""
    db.init_db(db_path)
    click.echo(f"✅ Database initialized at {db_path}")


@cli.command()
@click.argument("links_file", type=click.Path(exists=True))
@click.option("--db-path", default="scraped_content.db")
def import_links(links_file, db_path):
    """Import product, portfolio, and optional URL data from a CSV file."""
    conn = db.init_db(db_path)

    with open(links_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            product_name = row.get("Product Name", "").strip()
            portfolio = row.get("Portfolio", "").strip()
            url = row.get("Statement URL", "").strip()

            status = "pending" if url else "no_url"

            if not product_name:
                click.echo(f"⚠️ Row {i}: Missing product name, skipping")
                continue

            db.upsert_page(conn, product_name, {
                "portfolio": portfolio,
                "url": url or None,
                "status": status,
            })
            click.echo(f"✅ Imported row {i}: '{product_name}' ({status})")

    click.echo("\n📥 Import complete.")


@cli.command()
@click.option("--db-path", default="scraped_content.db")
def batch(db_path):
    """Scrape unique URLs and update all associated products."""
    conn = db.connect(db_path)

    rows = conn.execute("SELECT product_name, url FROM pages").fetchall()
    if not rows:
        click.echo("⚠️  No rows found in the database. Run `import-links` first.")
        return

    url_to_products = {}
    for row in rows:
        product_name, url = row["product_name"], row["url"]
        if url:
            url_to_products.setdefault(url, []).append(product_name)

    skipped = sum(1 for row in rows if not row["url"])
    scraped_count = 0

    for url, product_names in url_to_products.items():
        click.echo(f"\n🔗 Scraping: {url}")
        html = fetch_html(url)

        if not html:
            click.echo("⚠️  Failed to fetch page.")
            status = {"status": "failed", "fetched_at": int(time.time())}
        else:
            scraped_data = extract_sections(html)
            if not any(scraped_data.values()):
                click.echo("⚠️  Scrape yielded no content.")
                status = {"status": "no_content", "fetched_at": int(time.time())}
            else:
                click.echo("✅ Successfully scraped content.")
                status = {
                    **scraped_data,
                    "status": "success",
                    "fetched_at": int(time.time())
                }

        for product_name in product_names:
            if not product_name:
                click.echo(f"⚠️ Skipping update due to missing product_name for URL: {url}")
                continue
            db.upsert_page(conn, product_name, status)

        scraped_count += 1

    click.echo(f"\n🎉 Batch scrape complete! Scraped {scraped_count} unique URLs, Skipped (no URL): {skipped}")


@cli.command()
@click.option("--db-path", default="scraped_content.db")
def validate(db_path):
    """Check for potential bad rows (empty or null product_name)."""
    conn = db.connect(db_path)
    rows = conn.execute("SELECT * FROM pages WHERE product_name IS NULL OR product_name = ''").fetchall()
    if not rows:
        click.echo("✅ No bad rows found.")
    else:
        click.echo(f"❗ Found {len(rows)} bad rows (missing product_name):")
        for row in rows[:10]:
            print(dict(row))


@cli.command()
@click.option("--db-path", default="scraped_content.db")
def count(db_path):
    """Show detailed breakdown of rows and scraped fields."""
    conn = db.connect(db_path)

    total = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    statuses = conn.execute("""
        SELECT status, COUNT(*) AS count FROM pages GROUP BY status
    """).fetchall()

    def get_count(column, value="yes"):
        return conn.execute(f"""
            SELECT COUNT(*) FROM pages
            WHERE {column} = ?
        """, (value,)).fetchone()[0]

    last_review_count = conn.execute("""
        SELECT COUNT(*) FROM pages
        WHERE last_review IS NOT NULL AND last_review != ''
    """).fetchone()[0]

    wcag_count = conn.execute("""
        SELECT COUNT(*) FROM pages
        WHERE wcag IS NOT NULL AND wcag != ''
    """).fetchone()[0]

    compliance_full = conn.execute("""
        SELECT COUNT(*) FROM pages WHERE compliance_level = 'Fully Compliant'
    """).fetchone()[0]

    compliance_partial = conn.execute("""
        SELECT COUNT(*) FROM pages WHERE compliance_level = 'Partially Compliant'
    """).fetchone()[0]

    compliance_not = conn.execute("""
        SELECT COUNT(*) FROM pages WHERE compliance_level = 'Not Compliant'
    """).fetchone()[0]

    click.echo("\n📊 Database Summary:\n")
    click.echo(f"Total products: {total}\n")

    click.echo("Status breakdown:")
    for row in statuses:
        label = row['status'] or 'unknown'
        click.echo(f"- {label.title()}: {row['count']}")

    click.echo(f"\n✅ Feedback section present: {get_count('feedback_present')} / {total}")
    click.echo(f"✅ Enforcement section present: {get_count('enforcement_present')} / {total}")
    click.echo(f"📅 Last reviewed date found: {last_review_count} / {total}")
    click.echo(f"📜 WCAG version detected: {wcag_count} / {total}\n")

    click.echo("Compliance levels:")
    click.echo(f"- Fully compliant: {compliance_full}")
    click.echo(f"- Partially compliant: {compliance_partial}")
    click.echo(f"- Not compliant: {compliance_not}")

    click.echo("\n✅ Count complete.")


@cli.command()
@click.option("--db-path", default="scraped_content.db")
def report(db_path):
    """Show completion stats for key scraped fields."""
    conn = db.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    def get_count(column):
        return conn.execute(f"""
            SELECT COUNT(*) FROM pages
            WHERE {column} IS NOT NULL AND {column} != ''
        """).fetchone()[0]

    metrics = {
        "last_review": get_count("last_review"),
        "wcag": get_count("wcag"),
        "compliance_level": get_count("compliance_level"),
        "successful_scrapes": conn.execute(
            "SELECT COUNT(*) FROM pages WHERE status = 'success'"
        ).fetchone()[0],
    }

    click.echo("\n📊 Scrape Report:\n")
    for key, value in metrics.items():
        pct = round((value / total) * 100, 2) if total else 0
        click.echo(f"- {key.replace('_', ' ').title()}: {value} / {total} ({pct}%)")

    click.echo("\n✅ Report complete.")


@cli.command()
@click.option("--db-path", default="scraped_content.db")
def summary(db_path):
    """Show a breakdown of scrape results by status."""
    conn = db.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    counts = conn.execute("""
        SELECT status, COUNT(*) AS count
        FROM pages
        GROUP BY status
    """).fetchall()

    click.echo("\n📊 Scrape Summary:\n")
    click.echo(f"Total products: {total}\n")

    for row in counts:
        label = row['status'] or 'unknown'
        pct = round((row['count'] / total) * 100, 2)
        click.echo(f"- {label.title()}: {row['count']} ({pct}%)")

    click.echo("\n✅ Summary complete.")


if __name__ == "__main__":
    cli()
