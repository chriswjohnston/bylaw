# Nipissing Township By-Law Archive

A public archive of every by-law passed by Nipissing Township Council — organized by year, searchable, and automatically updated.

**Live site:** [bylaws.chriswjohnston.ca](https://bylaws.chriswjohnston.ca) *(or wherever you deploy it)*

## How It Works

1. **Scraper** (`scraper/scrape.py`) runs every 2 weeks via GitHub Actions
2. It pulls from 3 sources:
   - The township's [By-Laws page](https://nipissingtownship.com/municipal-information/by-laws/) — direct listings
   - [Council agenda packages](https://nipissingtownship.com/council-meeting-dates-agendas-minutes/) (PDFs) — finds proposed by-laws
   - Council meeting minutes (PDFs) — checks if by-laws were approved and extracts vote records
3. Extracted by-law PDFs are saved to `site/bylaws/`
4. All data is stored in `site/bylaws-data.json`
5. The static site (`site/index.html`) reads the JSON and displays everything

## Project Structure

```
├── .github/workflows/
│   └── scrape.yml          # GitHub Actions — runs every 2 weeks
├── scraper/
│   ├── scrape.py           # Main scraper script
│   └── requirements.txt    # Python dependencies
└── site/
    ├── index.html           # The archive website
    ├── bylaws-data.json     # All by-law data (auto-generated)
    └── bylaws/              # Extracted by-law PDFs by year
        ├── 2024/
        ├── 2025/
        └── 2026/
```

## Setup

### 1. Clone and push to GitHub

```bash
git init
git add .
git commit -m "Initial by-law archive"
git remote add origin git@github.com:YOUR_USER/nipissing-bylaw-archive.git
git push -u origin main
```

### 2. Enable GitHub Pages

Go to **Settings → Pages** and set:
- Source: `Deploy from a branch`
- Branch: `main`
- Folder: `/site`

### 3. Run the scraper manually (first time)

```bash
cd scraper
pip install -r requirements.txt
cd ..
python scraper/scrape.py
```

Or trigger it from GitHub: **Actions → Scrape Nipissing By-Laws → Run workflow**

### 4. Custom domain (optional)

Create `site/CNAME` with your domain:
```
bylaws.chriswjohnston.ca
```

Then configure DNS with a CNAME record pointing to `YOUR_USER.github.io`.

## Running Locally

```bash
# Install dependencies
pip install -r scraper/requirements.txt

# Run the scraper
python scraper/scrape.py

# Serve the site locally
cd site && python -m http.server 8000
# Visit http://localhost:8000
```

## Data Format

Each by-law in `bylaws-data.json`:

```json
{
  "number": "2024-33",
  "year": 2024,
  "title": "User Fees",
  "date_passed": null,
  "pdf_url": "https://nipissingtownship.com/wp-content/uploads/.../User-Fee-By-Law-2024-33.pdf",
  "page_url": null,
  "source": "bylaws_page",
  "status": "approved",
  "votes": "Moved by Smith, Seconded by Jones",
  "meeting_date": "2024-07-16",
  "agenda_package_url": "https://...",
  "minutes_url": "https://..."
}
```

## Notes

- By-laws in agenda packages are **not yet approved** — the scraper checks minutes for confirmation
- Vote records are extracted when the minutes contain "Moved by / Seconded by" patterns
- The scraper extracts individual by-law pages from agenda packages as separate PDFs
- Previously archived by-laws are preserved even if the township removes them

---

*Chris Johnston — Candidate for Nipissing Township Council · Municipal Election October 2026*
