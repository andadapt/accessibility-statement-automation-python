# scraper.py
from bs4 import BeautifulSoup
from rapidfuzz import process
import dateutil.parser as date_parser
from playwright.sync_api import sync_playwright


def fetch_html(url: str, timeout: int = 60000) -> str:
    """Fetches HTML content of a URL using Playwright."""
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        print(f"❗ Error fetching URL {url}: {e}")
        return None


def extract_sections(html: str) -> dict:
    """Extracts key sections and structured metadata from the scraped HTML."""
    soup = BeautifulSoup(html, "lxml")

    # We scrape by detecting headings and collecting content until next same-level heading
    headings = soup.find_all(["h1", "h2", "h3", "h4"])
    results = {}

    # Normalize headings
    heading_map = {
        "feedback": ["feedback", "contact", "reporting"],
        "enforcement": ["enforcement"],
        "compliance_status": ["compliance status"],
        "preparation": ["preparation"],
        "non_accessible": ["not accessible", "non accessible", "does not fully meet"],
    }

    def match_heading(text, target_list):
        return any(t in text.lower() for t in target_list)

    for idx, heading in enumerate(headings):
        text = heading.get_text(strip=True).lower()

        # Match section names
        for key, keywords in heading_map.items():
            if match_heading(text, keywords):
                content = []
                for sibling in heading.find_all_next():
                    if sibling.name and sibling.name.startswith("h"):
                        break  # Stop at the next heading
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

    return results


def extract_last_review_date(text: str) -> str:
    """Attempt to locate and parse the last reviewed date in a section."""
    try:
        import re
        pattern = r"(last reviewed(?: on)?|reviewed(?: on)?|last updated)\s*(.*)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return date_parser.parse(match.group(2).strip(), fuzzy=True).date().isoformat()
    except Exception:
        pass
    return None


def extract_wcag_version(text: str) -> str:
    for version in ["2.2", "2.1", "2.0"]:
        if version in text:
            return version
    return None


def extract_compliance_level(text: str) -> str:
    """Extracts compliance level from text."""
    text = text.lower()
    if "fully" in text and "compliant" in text:
        return "Fully Compliant"
    if "partially" in text or "partial" in text:
        return "Partially Compliant"
    if "not compliant" in text:
        return "Not Compliant"
    return None
