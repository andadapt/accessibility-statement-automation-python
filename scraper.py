# scraper.py
"""
Scraper with fast cookie handling, optional UI, and debug flag.
Run example:
    python scraper.py https://example.com --no-headless --debug
Dependencies:
    pip install playwright beautifulsoup4 lxml python-dateutil rapidfuzz
    playwright install
"""

from bs4 import BeautifulSoup
from rapidfuzz import process
import dateutil.parser as date_parser
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time
import logging
import re
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


# -------------------------
# Fast Cookie Handler
# -------------------------
def handle_cookie_banner(page):
    """
    Fast cookie handler: only tries a couple of known selectors and texts.
    """
    selectors = [
        "#onetrust-accept-btn-handler",         # OneTrust
        "button:has-text('Accept')",            # Generic
    ]
    texts = ["Accept", "OK", "Agree"]

    # Try selectors first
    for sel in selectors:
        try:
            locator = page.locator(sel)
            if locator.count() > 0 and locator.first.is_visible():
                locator.first.click(timeout=2000)
                logging.info(f"Clicked cookie accept button: {sel}")
                return True
        except Exception:
            pass

    # Try text-based matching
    for txt in texts:
        try:
            page.get_by_text(txt, exact=True).click(timeout=2000)
            logging.info(f"Clicked cookie consent text button: '{txt}'")
            return True
        except Exception:
            pass

    logging.info("No cookie banner action taken (fast mode).")
    return False


# -------------------------
# Scraping / Extraction
# -------------------------
def fetch_html(url: str, timeout: int = 60000, headless: bool = True, debug: bool = False) -> Optional[str]:
    """Fetches rendered HTML content of a URL using Playwright, with cookie handling."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context()
            page = context.new_page()

            logging.info(f"Navigating to {url} ... (headless={headless})")
            start_time = time.time()

            try:
                page.goto(url, wait_until="networkidle", timeout=timeout)
            except PlaywrightTimeoutError:
                logging.warning("Page.goto timed out - continuing with partial content.")

            # Short initial wait
            page.wait_for_timeout(200)

            # Fast cookie handling
            handle_cookie_banner(page)

            # Wait for page to settle
            page.wait_for_timeout(300)

            # Try to detect main content
            try:
                page.wait_for_selector("h1, h2, h3, h4, h5", timeout=5000)
                logging.info("Headings detected on page.")
            except PlaywrightTimeoutError:
                logging.warning("No headings detected within timeout.")

            content = page.content()

            if debug:
                with open("debug.html", "w", encoding="utf-8") as f:
                    f.write(content)
                page.screenshot(path="debug.png")
                logging.info("Saved debug.html and debug.png")

            elapsed = time.time() - start_time
            logging.info(f"✅ Scrape completed in {elapsed:.2f} seconds")

            context.close()
            browser.close()
            return content

    except Exception as e:
        logging.exception(f"❗ Error fetching URL {url}: {e}")
        return None


def extract_sections(html: str, debug: bool = True) -> dict:
    """Extracts key sections and structured metadata from the scraped HTML."""
    soup = BeautifulSoup(html, "lxml")
    results = {}

    headings = soup.find_all(["h1", "h2", "h3", "h4", "h5"])
    heading_map = {
        "feedback": ["feedback", "contact", "reporting"],
        "enforcement": ["enforcement"],
        "compliance_status": ["compliance status"],
        "preparation": ["preparation"],
        "non_accessible": [
            "non-accessible", "not accessible", "does not fully meet",
            "non compliance", "non-compliance", "content not accessible",
            "not compliant", "partially compliant"
        ],
    }

    def match_heading(text, target_list):
        return any(t in text.lower() for t in target_list)

    if debug:
        logging.info("\n=== DEBUG: All Headings Found ===")
        for heading in headings:
            logging.info(f"- '{heading.get_text(strip=True)}' | tag: {heading.name}")
        logging.info("=================================")

    for idx, heading in enumerate(headings):
        text = heading.get_text(strip=True).lower()
        for key, keywords in heading_map.items():
            if key == "non_accessible" and match_heading(text, keywords):
                if debug:
                    logging.info(f"Found 'non_accessible' heading: '{text}'")
                content = []
                for sibling in heading.find_all_next():
                    content.append(sibling.get_text(strip=True))
                results[key] = "\n".join(content)
                break
            elif match_heading(text, keywords):
                if debug:
                    logging.info(f"Found '{key}' heading: '{text}'")
                content = []
                for sibling in heading.find_all_next():
                    if sibling.name and sibling.name.startswith("h"):
                        break
                    content.append(sibling.get_text(strip=True))
                results[key] = "\n".join(content)
                break

    # Derive additional flags
    results["feedback_present"] = "yes" if results.get("feedback") else "no"
    results["enforcement_present"] = "yes" if results.get("enforcement") else "no"

    # Extract last reviewed date from "preparation" section
    prep = results.get("preparation", "")
    results["last_review"] = extract_last_review_date(prep)

    # Extract WCAG version and compliance level from "compliance_status" section
    comp_stat = results.get("compliance_status", "")
    results["wcag"] = extract_wcag_version(comp_stat)
    results["compliance_level"] = extract_compliance_level(comp_stat)

    # Extract issue text from non-accessible
    results["issue_text"] = results.get("non_accessible", "").strip() or None

    if debug:
        logging.info("\n=== DEBUG: Section Extraction Summary ===")
        for key, value in results.items():
            if key in heading_map:  # Only print section results
                char_count = len(value) if value else 0
                logging.info(f"- {key}: {char_count} chars extracted")
        logging.info("==========================================")

    return results


def extract_last_review_date(text: str) -> Optional[str]:
    """Attempt to locate and parse the last reviewed date in a section."""
    try:
        pattern = r"(last reviewed(?: on)?|reviewed(?: on)?|last updated|updated(?: on)?)\s*[:\-]?\s*(.*)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(2).strip().split("\n")[0][:200]
            parsed = date_parser.parse(candidate, fuzzy=True)
            return parsed.date().isoformat()
    except Exception:
        pass
    return None


def extract_wcag_version(text: str) -> Optional[str]:
    for version in ["2.2", "2.1", "2.0"]:
        if version in text:
            return version
    return None


def extract_compliance_level(text: str) -> Optional[str]:
    """Extracts compliance level from text."""
    text_l = (text or "").lower()
    if "fully" in text_l and "compliant" in text_l:
        return "Fully Compliant"
    if "partially" in text_l or "partial" in text_l:
        return "Partially Compliant"
    if "not compliant" in text_l:
        return "Not Compliant"
    return None


# -------------------------
# CLI / example usage
# -------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape a page, attempt to accept cookie banners, and extract sections.")
    parser.add_argument("url", help="URL to scrape")
    parser.add_argument("--no-headless", action="store_true", help="Run browser with UI (default: headless)")
    parser.add_argument("--debug", action="store_true", help="Save debug.html and debug.png")
    parser.add_argument("--timeout", type=int, default=60000, help="Navigation timeout in ms")
    args = parser.parse_args()

    headless_setting = not args.no_headless

    html = fetch_html(args.url, timeout=args.timeout, headless=headless_setting, debug=args.debug)
    if not html:
        logging.error("Failed to fetch or render HTML.")
        raise SystemExit(1)

    sections = extract_sections(html, debug=args.debug)
    logging.info("Extraction result (sample):")
    for k, v in sections.items():
        if k not in ["issue_text", "non_accessible"]:
            logging.info(f"  {k}: {v}")

    logging.info("=== Issue text (truncated) ===")
    issue = sections.get("issue_text") or ""
    logging.info(issue[:800])

    # Save JSON-ish output for downstream consumption
    try:
        import json
        with open("scrape_result.json", "w", encoding="utf-8") as f:
            json.dump(sections, f, ensure_ascii=False, indent=2)
        logging.info("Saved scrape_result.json")
    except Exception:
        logging.exception("Could not save json output.")
