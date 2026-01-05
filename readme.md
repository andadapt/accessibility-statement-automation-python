# Accessibility Scraper CLI

A Python-based CLI tool to import, scrape, and analyze accessibility statements from product web pages using Playwright and SQLite.

---

## 🛠️ Setup

```powershell
python -m venv venv
venv\Scripts\Activate
pip install -r requirements.txt
## dumping to JSON
sqlite3 is required. If on windows isntall through scoop
sqlite3 scraped_content.db -cmd ".mode json" "select * from pages;" > pages.json
