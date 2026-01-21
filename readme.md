## Setup

### Prerequisites
- Python 3.10+ (recommended)
- Internet access (the scraper fetches URLs)

This project uses a virtual environment and installs dependencies from `requirements.txt`.

---

## Create and activate a virtual environment

### Windows (PowerShell)

```powershell
cd path\to\repo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then re-run the activate command.

### macOS

```bash
cd /path/to/repo
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## Run the scraper

The main monthly command is:

### Windows (PowerShell)

```powershell
python cli.py run-all
```

### macOS

```bash
python cli.py run-all
```

What `run-all` does:
1. Wipes the SQLite database (fresh run each time)
2. Imports every CSV file in `inputs/`
3. Creates one table per CSV (table name = CSV filename, sanitized)
4. Scrapes statement URLs (deduped by URL within each table; results applied to all matching rows)
5. Prints a summary of how many rows per portfolio have a `last_review` value
6. Exports one JSON file per table to `outputs/<table>.json`

---

## Input files

### Folder structure

Make sure these folders exist at the repo root (same level as `cli.py`):

```
inputs/
outputs/
```

Put your CSV files in `inputs/`.

### CSV format requirements

Each CSV in `inputs/` must:
- be comma-separated (`.csv`)
- have a header row with **exactly** these columns:

| Column name     | Required | Notes |
|----------------|----------|------|
| Product Name   | Yes      | Unique key per table (used for upserts) |
| Portfolio      | Yes      | Used for reporting/grouping |
| Statement URL  | Optional | If blank / `null` / `working`, the row is treated as “no URL” |

Example `inputs/enablers.csv`:

```csv
Product Name,Portfolio,Statement URL
ASPeL,Enablers,https://external.aspel.homeoffice.gov.uk/accessibility
Brandworkz - Digital Asset Manager,Enablers,
Building Prioritisation Tool,Enablers,https://bpt.homeoffice.gov.uk/accessibility
```

Notes:
- Rows with an empty (or missing) `Product Name` are skipped.
- `Statement URL` values of `null`, `none`, `n/a`, or `working` are treated as no URL.

---

## Output files

After `python cli.py run-all`, you’ll get one JSON file per input CSV:

```
outputs/
  enablers.json
  mbpt.json
  automation.json
  ...
```

### Output JSON format (example)

```json
[
  {
    "id": 1,
    "product_name": "ASPeL",
    "portfolio": "Enablers",
    "url": "https://external.aspel.homeoffice.gov.uk/accessibility",
    "fetched_at": "20/01/2026",
    "feedback": null,
    "enforcement": null,
    "compliance_status": null,
    "preparation": null,
    "non_accessible": null,
    "feedback_present": "no",
    "enforcement_present": "no",
    "last_review": null,
    "wcag": null,
    "compliance_level": null,
    "issue_text": null,
    "status": "success"
  },
  {
    "id": 2,
    "product_name": "Brandworkz - Digital Asset Manager",
    "portfolio": "Enablers",
    "url": null,
    "fetched_at": null,
    "feedback": null,
    "enforcement": null,
    "compliance_status": null,
    "preparation": null,
    "non_accessible": null,
    "feedback_present": null,
    "enforcement_present": null,
    "last_review": null,
    "wcag": null,
    "compliance_level": null,
    "issue_text": null,
    "status": "no_url"
  }
]
```

Key fields:
- `status`: `success`, `failed`, `no_content`, `no_url`, or `pending`
- `fetched_at`: date string `DD/MM/YYYY` when a scrape was attempted; `null` if not scraped
- scraped content fields may be `null` depending on what was found on the page

---

## (Optional) View the database with Datasette

```bash
datasette serve scraped_content.db
```

Open the local URL it prints (usually `http://127.0.0.1:8001/`).
