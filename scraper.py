# scraper.py
"""
Full scraper with robust cookie/banner handling for Playwright.
Dependencies:
    pip install playwright beautifulsoup4 lxml python-dateutil rapidfuzz
    # Then install browsers for Playwright:
    playwright install

Notes:
- Defaults to headless=True but you can run with headless=False for debugging.
- This file tries multiple strategies to accept or bypass cookie/modals:
    1) Click common cookie buttons/selectors (OneTrust, Cookiebot, generic Accept)
    2) Click buttons by visible text (Accept, Agree, Allow, Continue)
    3) Set common consent cookies via JS/localStorage (best-effort)
    4) Remove overlay elements (best-effort)
- Use the "debug" flag to save HTML & screenshot for inspection.
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
# Cookie / overlay handlers
# -------------------------
def _click_first_visible(page, selectors, timeout=2500):
    """Try to click the first selector that is visible & enabled."""
    for sel in selectors:
        try:
            locator = page.locator(sel)
            if locator.count() > 0:
                # Attempt click on first visible match
                for idx in range(locator.count()):
                    element = locator.nth(idx)
                    if element.is_visible():
                        element.click(timeout=timeout)
                        logging.info(f"Clicked selector: {sel}")
                        return True
        except Exception:
            # swallow and continue trying others
            continue
    return False


def _click_text_options(page, texts, timeout=2500):
    """
    Try to click elements matching common button text.
    Uses Playwright's text engine to find visible text nodes.
    """
    for t in texts:
        try:
            # Use :text-is or has-text depending on Playwright version; use has-text for reliability
            # Try exact first, then partial
            patterns = [f"button:has-text('{t}')", f"[role='button']:has-text('{t}')", f"text={t}"]
            for pat in patterns:
                try:
                    page.locator(pat).first.click(timeout=timeout)
                    logging.info(f"Clicked text-matched element: '{t}' via pattern {pat}")
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _set_common_consent_via_js(page):
    """
    Best-effort: set typical consent cookie keys / localStorage entries that
    frameworks often respect. This does not guarantee acceptance but helps.
    """
    scripts = [
        # OneTrust common cookie
        "document.cookie = 'OptanonConsent=true; path=/; max-age=31536000';",
        # Cookiebot
        "document.cookie = 'CookieConsent=true; path=/; max-age=31536000';",
        # Generic consent flag
        "localStorage.setItem('cookieConsent', 'true');",
        "localStorage.setItem('acceptCookies', 'true');",
        "sessionStorage.setItem('cookieConsent', 'true');",
    ]
    for script in scripts:
        try:
            page.evaluate(script)
        except Exception:
            continue


def _remove_overlay_elements(page, debug_selector_examples=None):
    """
    Remove elements that look like overlays/modals by common attributes.
    Use a conservative approach so we don't remove vital content accidentally.
    """
    js = r"""
    (selectors) => {
        const removed = [];
        for (const sel of selectors) {
            try {
                document.querySelectorAll(sel).forEach(el => {
                    // hide or remove safely
                    el.style.pointerEvents = 'none';
                    el.style.visibility = 'hidden';
                    el.style.opacity = '0';
                    // also remove if it's clearly a full-screen overlay
                    const rect = el.getBoundingClientRect && el.getBoundingClientRect();
                    if (rect && (rect.width >= window.innerWidth*0.6 || rect.height >= window.innerHeight*0.6)) {
                        el.remove();
                    }
                    removed.push(sel);
                });
            } catch (e) {}
        }
        return removed;
    }
    """
    selectors = [
        ".cookie-banner", ".cookie-consent", "#onetrust-banner-sdk", "#cookie-consent",
        ".eu-cookie-compliance", ".cc-window", ".cc-banner", ".cookieNotice", ".js-cookie-consent",
        ".cookieModal", ".cookie-popup", "[aria-label*='cookie']"
    ]
    if debug_selector_examples:
        selectors = selectors + debug_selector_examples
    try:
        removed = page.evaluate(js, selectors)
        if removed:
            logging.info(f"Attempted to hide/remove overlays for selectors: {removed}")
    except Exception:
        pass


def handle_cookie_banner(page, debug=False):
    """
    Orchestrates various strategies to accept/dismiss cookie banners and overlays.
    Returns True if any action likely removed the blocking banner (best-effort).
    """
    # quick heuristic: if body contains 'cookie' or 'consent' within first screen, try
    try:
        body_text = page.content()[:4000].lower()
    except Exception:
        body_text = ""

    # Strategy 1: click known selectors
    known_selectors = [
        "#onetrust-accept-btn-handler",          # OneTrust
        "#onetrust-accept-btn-handler",          # duplicate OK
        "button#acceptCookies",                  # generic
        "button#cookie-action-accept",
        ".onetrust-close-btn-handler",
        ".onetrust-accept-btn-handler",
        ".cn-accept-cookie",                     # some themes
        ".cc-btn.cc-allow",                      # cookieconsent
        ".cookie-accept", ".cookies-accept",
        ".cookie-consent__button", ".accept-all",
        ".js-accept-cookies", ".accept-cookies",
        "button[data-testid='accept']",          # testing ids
        "button[data-consent='accept']",
    ]

    if _click_first_visible(page, known_selectors):
        time.sleep(0.8)
        return True

    # Strategy 2: click by visible text
    texts = [
        "Accept all", "Accept all cookies", "Accept cookies", "Accept", "Agree", "I agree",
        "Allow all", "Allow cookies", "Yes, I agree", "Got it", "OK", "Continue"
    ]
    if _click_text_options(page, texts):
        time.sleep(0.8)
        return True

    # Strategy 3: try to find radio + confirm flow (some cookie widgets use radio + save)
    try:
        # click accept radio then save button heuristics
        accept_radios = page.locator("input[type='radio'][value*='accept'], input[type='checkbox'][name*='accept']")
        if accept_radios.count() > 0:
            for i in range(accept_radios.count()):
                try:
                    r = accept_radios.nth(i)
                    if r.is_enabled() and r.is_visible():
                        r.click()
                except Exception:
                    pass
            # try clicking Save/Confirm button
            _click_text_options(page, ["Save", "Save preferences", "Confirm", "OK", "Submit"])
            time.sleep(0.8)
            return True
    except Exception:
        pass

    # Strategy 4: set common consent cookies / localStorage flags
    _set_common_consent_via_js(page)
    # give it a moment and reload small part (not full reload)
    try:
        page.evaluate("document.dispatchEvent(new Event('cookieconsent:accept'))")
    except Exception:
        pass

    # Strategy 5: remove overlay elements (last resort)
    _remove_overlay_elements(page)

    # One final attempt: search DOM for "cookie" elements and click button inside them
    try:
        # fetch nodes with cookie mention via JS, attempt a click on anchored buttons inside them
        script = r"""
        () => {
            const containers = [];
            const all = document.querySelectorAll('body *');
            for (let i=0; i<all.length && containers.length < 20; i++) {
                const el = all[i];
                if (el.innerText && /cookie|consent|gdpr|privacy/i.test(el.innerText) && el.offsetParent !== null) {
                    containers.push(el);
                }
            }
            return containers.slice(0,6).map(c=> ({tag:c.tagName, html:c.innerHTML.slice(0,200)}));
        }
        """
        found = page.evaluate(script)
        if found:
            logging.debug(f"Found possible cookie containers (sample): {found}")
            # try clicking "button" elements inside page using JS
            try_click_js = r"""
            () => {
                const btnTextRegex = /(accept|agree|allow|ok|got it|yes|continue)/i;
                const clicks = [];
                const containers = [];
                const all = document.querySelectorAll('body *');
                for (let i=0;i<all.length;i++){
                    const el = all[i];
                    if (el.innerText && /cookie|consent|gdpr|privacy/i.test(el.innerText) && el.offsetParent !== null) {
                        containers.push(el);
                    }
                }
                containers.forEach(c=>{
                    const buttons = c.querySelectorAll('button, a, input[type=button], input[type=submit]');
                    for (const b of buttons) {
                        try {
                            const txt = (b.innerText || b.value || '').trim();
                            if (btnTextRegex.test(txt)) {
                                b.click();
                                clicks.push(txt);
                                break;
                            }
                        } catch(e){}
                    }
                });
                return clicks;
            }
            """
            clicks = page.evaluate(try_click_js)
            if clicks and len(clicks) > 0:
                logging.info(f"Clicked cookie buttons via JS with labels: {clicks}")
                return True
    except Exception:
        pass

    # If nothing worked:
    logging.info("No cookie banner action succeeded (best-effort).")
    return False


# -------------------------
# Scraping / extraction
# -------------------------
def fetch_html(url: str, timeout: int = 60000, headless: bool = True, debug: bool = False) -> Optional[str]:
    """Fetches rendered HTML content of a URL using Playwright, with cookie handling."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                java_script_enabled=True,
                bypass_csp=True,
            )
            page = context.new_page()
            logging.info(f"Navigating to {url} ... (headless={headless})")
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout)
            except PlaywrightTimeoutError:
                # If navigation times out, still try to work with whatever loaded
                logging.warning("Page.goto timed out - attempting to continue with partial content.")

            # small initial wait for JS widgets to appear
            time.sleep(0.8)

            # Attempt cookie handling
            try:
                banner_handled = handle_cookie_banner(page, debug=debug)
                if banner_handled:
                    logging.info("Cookie/banner handling attempted (likely accepted/removed).")
                else:
                    logging.info("Cookie/banner handling did not find an actionable element.")
            except Exception as e:
                logging.warning(f"Cookie handling raised exception: {e}")

            # Wait more to let page settle after clicking buttons
            time.sleep(1.2)

            # Extra wait for dynamic content (site-specific)
            page.wait_for_timeout(1500)

            # Try to ensure headings loaded (your extractor expects them)
            try:
                page.wait_for_selector("h1, h2, h3, h4, h5", timeout=5000)
                logging.info("Heading(s) detected on page.")
            except PlaywrightTimeoutError:
                logging.warning("No headings detected within timeout - proceeding anyway.")

            content = page.content()

            if debug:
                with open("debug.html", "w", encoding="utf-8") as f:
                    f.write(content)
                try:
                    page.screenshot(path="debug.png", full_page=True)
                except Exception:
                    # some pages don't allow full_page
                    page.screenshot(path="debug.png")
                logging.info("Saved debug.html and debug.png")

            context.close()
            browser.close()
            return content
    except Exception as e:
        logging.exception(f"Error fetching URL {url}: {e}")
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
            # sometimes the remainder contains a sentence; use fuzzy parse
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
    if "not compliant" in text_l or "not compliant" in text_l:
        return "Not Compliant"
    return None


# -------------------------
# CLI / example usage
# -------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape a page, attempt to accept cookie banners, and extract sections.")
    parser.add_argument("url", help="URL to scrape")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode (default: False for safety)")
    parser.add_argument("--debug", action="store_true", help="Save debug.html and debug.png")
    parser.add_argument("--timeout", type=int, default=60000, help="Navigation timeout in ms")
    args = parser.parse_args()

    # By default, run visible so it's easier to inspect if things fail; headless True if passed
    headless_setting = args.headless

    html = fetch_html(args.url, timeout=args.timeout, headless=headless_setting, debug=args.debug)
    if not html:
        logging.error("Failed to fetch or render HTML.")
        raise SystemExit(1)

    sections = extract_sections(html, debug=True)
    logging.info("Extraction result (sample):")
    for k, v in sections.items():
        if k not in ["issue_text", "non_accessible"]:
            logging.info(f"  {k}: {v}")
    # print important pieces
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
