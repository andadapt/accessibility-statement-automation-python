# scraper.py
"""
Scraper with fast cookie handling.

Console output:
- Minimal (success/fail per URL)

Logs:
- logs/run_YYYY-MM-DD.log   (human readable)
- logs/run_YYYY-MM-DD.jsonl (machine readable, JSON Lines)

Supports optional context passed by caller:
- context = {"table": "...", "product_names": ["...", "..."]}

Dependencies:
    pip install playwright beautifulsoup4 lxml python-dateutil rapidfuzz
    playwright install
"""

from bs4 import BeautifulSoup
import dateutil.parser as date_parser
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time
import logging
import re
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any


# -------------------------
# Logging setup
# -------------------------
class ContextFilter(logging.Filter):
    """Ensure url/product_names/table always exist on log records."""
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "url"):
            record.url = None
        if not hasattr(record, "product_names"):
            record.product_names = None
        if not hasattr(record, "table"):
            record.table = None
        return True


class JsonLineFormatter(logging.Formatter):
    def __init__(self, tz: str):
        super().__init__()
        self.tz = tz

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(ZoneInfo(self.tz)).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "url": record.url,
            "product_names": record.product_names,
            "table": record.table,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    log_dir: str = "logs",
    tz: str = "Europe/London",
    also_json: bool = True,
):
    os.makedirs(log_dir, exist_ok=True)
    today = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")

    text_log_path = os.path.join(log_dir, f"run_{today}.log")
    json_log_path = os.path.join(log_dir, f"run_{today}.jsonl")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    for h in list(root.handlers):
        root.removeHandler(h)

    context_filter = ContextFilter()

    # Text log
    file_handler = logging.FileHandler(text_log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] "
            "table=%(table)s url=%(url)s products=%(product_names)s :: %(message)s"
        )
    )
    root.addHandler(file_handler)

    # JSONL log
    if also_json:
        json_handler = logging.FileHandler(json_log_path, encoding="utf-8")
        json_handler.setLevel(logging.DEBUG)
        json_handler.addFilter(context_filter)
        json_handler.setFormatter(JsonLineFormatter(tz))
        root.addHandler(json_handler)

    # Console (minimal)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)


# Initialise logging immediately
setup_logging()

log = logging.getLogger("scraper")


def _ctx(context: Optional[Dict[str, Any]], url: str) -> Dict[str, Any]:
    context = context or {}
    product_names = context.get("product_names")
    table = context.get("table")

    if isinstance(product_names, list) and len(product_names) > 50:
        product_names = product_names[:50] + [f"...(+{len(product_names) - 50} more)"]

    return {
        "url": url,
        "product_names": product_names,
        "table": table,
    }


# -------------------------
# Cookie handling
# -------------------------
def handle_cookie_banner(page, *, extra: Dict[str, Any]) -> bool:
    selectors = [
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept')",
    ]
    texts = ["Accept", "OK", "Agree"]

    for sel in selectors:
        try:
            locator = page.locator(sel)
            if locator.count() > 0 and locator.first.is_visible():
                locator.first.click(timeout=2000)
                log.debug(f"Clicked cookie accept button: {sel}", extra=extra)
                return True
        except Exception as e:
            log.debug(f"Cookie selector failed ({sel}): {e}", extra=extra)

    for txt in texts:
        try:
            page.get_by_text(txt, exact=True).click(timeout=2000)
            log.debug(f"Clicked cookie consent text: '{txt}'", extra=extra)
            return True
        except Exception as e:
            log.debug(f"Cookie text failed ({txt}): {e}", extra=extra)

    log.debug("No cookie banner handled.", extra=extra)
    return False


# -------------------------
# Scraping
# -------------------------
def fetch_html(
    url: str,
    timeout: int = 60000,
    headless: bool = True,
    debug: bool = False,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    start_time = time.time()
    extra = _ctx(context, url)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context_pw = browser.new_context()
            page = context_pw.new_page()

            log.debug(f"Navigating (headless={headless})", extra=extra)

            try:
                page.goto(url, wait_until="networkidle", timeout=timeout)
            except PlaywrightTimeoutError:
                log.warning("page.goto timed out; continuing", extra=extra)

            page.wait_for_timeout(200)
            handle_cookie_banner(page, extra=extra)
            page.wait_for_timeout(300)

            html = page.content()

            elapsed = time.time() - start_time
            logging.info(f"✅ SUCCESS: {url} ({elapsed:.2f}s)")

            context_pw.close()
            browser.close()
            log.debug(f"Fetch complete in {elapsed:.2f}s", extra=extra)
            return html

    except Exception as e:
        elapsed = time.time() - start_time
        logging.info(f"❌ FAILED:  {url} ({elapsed:.2f}s)")
        log.exception(f"Error fetching URL: {e}", extra=extra)
        return None


# -------------------------
# Extraction
# -------------------------
def extract_sections(html: str, debug: bool = False, context: Optional[Dict[str, Any]] = None) -> dict:
    extra = _ctx(context, context["url"] if context and "url" in context else "unknown")
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
            "not compliant", "partially compliant",
        ],
    }

    def match_heading(text, targets):
        return any(t in text.lower() for t in targets)

    for heading in headings:
        text = heading.get_text(strip=True).lower()
        for key, keywords in heading_map.items():
            if key == "non_accessible" and match_heading(text, keywords):
                results[key] = "\n".join(s.get_text(strip=True) for s in heading.find_all_next())
                break
            elif match_heading(text, keywords):
                content = []
                for s in heading.find_all_next():
                    if s.name and s.name.startswith("h"):
                        break
                    content.append(s.get_text(strip=True))
                results[key] = "\n".join(content)
                break

    results["feedback_present"] = "yes" if results.get("feedback") else "no"
    results["enforcement_present"] = "yes" if results.get("enforcement") else "no"

    results["last_review"] = extract_last_review_date(results.get("preparation", "") or "")
    results["wcag"] = extract_wcag_version(results.get("compliance_status", "") or "")
    results["compliance_level"] = extract_compliance_level(results.get("compliance_status", "") or "")
    results["issue_text"] = results.get("non_accessible", "").strip() or None

    log.debug("Extraction complete", extra=extra)
    return results


def extract_last_review_date(text: str) -> Optional[str]:
    try:
        pattern = r"(last reviewed(?: on)?|reviewed(?: on)?|last updated|updated(?: on)?)\s*[:\-]?\s*(.*)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(2).split("\n")[0][:200]
            parsed = date_parser.parse(candidate, fuzzy=True)
            return parsed.date().isoformat()
    except Exception:
        pass
    return None


def extract_wcag_version(text: str) -> Optional[str]:
    for v in ("2.2", "2.1", "2.0"):
        if v in (text or ""):
            return v
    return None


def extract_compliance_level(text: str) -> Optional[str]:
    t = (text or "").lower()
    if "fully" in t and "compliant" in t:
        return "Fully Compliant"
    if "partial" in t:
        return "Partially Compliant"
    if "not compliant" in t:
        return "Not Compliant"
    return None
