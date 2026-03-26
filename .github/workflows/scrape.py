#!/usr/bin/env python3
"""
Nipissing Township By-Law Scraper

Scrapes by-laws from:
1. The township's by-laws page (direct listings)
2. Council agenda packages (PDFs containing proposed by-laws)
3. Council meeting minutes (PDFs to check approval status and vote records)

Designed to run via GitHub Actions every 2 weeks.

Dependencies:
  pip install requests beautifulsoup4 pymupdf anthropic
"""

import json
import os
import re
import sys
import hashlib
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Run: pip install pymupdf")
    sys.exit(1)

# Optional: Anthropic API for AI summaries
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    print("NOTE: anthropic package not installed. AI summaries will be skipped.")
    print("      Install with: pip install anthropic")


# --- Configuration ---
BASE_URL = "https://nipissingtownship.com"
BYLAWS_PAGE = f"{BASE_URL}/municipal-information/by-laws/"
COUNCIL_PAGE = f"{BASE_URL}/council-meeting-dates-agendas-minutes/"
DATA_FILE = Path("site/bylaws-data.json")
PDF_DIR = Path("site/bylaws")
HEADERS = {
    "User-Agent": "NipissingBylawArchiver/1.0 (github.com/chriswjohnston; civic transparency project)"
}


def load_existing_data():
    """Load existing by-law data if available."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"last_updated": None, "source": BYLAWS_PAGE, "bylaws": []}


def save_data(data):
    """Save by-law data to JSON."""
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(data['bylaws'])} by-laws to {DATA_FILE}")


def fetch_page(url):
    """Fetch a web page and return BeautifulSoup object."""
    print(f"  Fetching: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def download_pdf(url, dest_dir):
    """Download a PDF to dest_dir. Returns local path or None."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename = url.split("/")[-1]
    # Sanitize filename
    filename = re.sub(r'[^\w\-\.]', '_', filename)
    local_path = dest_dir / filename

    if local_path.exists():
        return local_path

    try:
        print(f"  Downloading: {filename}")
        resp = requests.get(url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)
        return local_path
    except Exception as e:
        print(f"  WARNING: Failed to download {url}: {e}")
        return None


def extract_pdf_text(pdf_path):
    """Extract all text from a PDF using PyMuPDF."""
    try:
        doc = fitz.open(str(pdf_path))
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text
    except Exception as e:
        print(f"  WARNING: Failed to read PDF {pdf_path}: {e}")
        return ""


# --- By-Law Number Parsing ---

BYLAW_PATTERN = re.compile(
    r'[Bb]y[\-\s]?[Ll]aw\s*(?:No\.?\s*)?(\d{4}[\-–]\d{1,3})',
    re.IGNORECASE
)
BYLAW_NUMBER_PATTERN = re.compile(
    r'(\d{4}[\-–]\d{1,3})'
)


def find_bylaw_numbers_in_text(text):
    """Find all by-law numbers mentioned in text."""
    # Look for explicit "By-Law No. YYYY-NN" patterns
    explicit = BYLAW_PATTERN.findall(text)
    # Normalize dashes
    results = set()
    for num in explicit:
        results.add(num.replace("–", "-"))
    return results


def extract_bylaw_title_from_text(text, bylaw_number):
    """Try to extract the title/subject of a by-law from surrounding text."""
    # Look for patterns like "By-Law 2024-33 - Title" or "By-Law 2024-33 Title"
    escaped = re.escape(bylaw_number).replace(r'\-', r'[\-–]')
    patterns = [
        # "By-Law YYYY-NN - Title" or "By-Law YYYY-NN to Title"
        rf'[Bb]y[\-\s]?[Ll]aw\s*(?:No\.?\s*)?{escaped}\s*[\-–:]\s*(.{{5,120}}?)[\n\r\.]',
        rf'[Bb]y[\-\s]?[Ll]aw\s*(?:No\.?\s*)?{escaped}\s+(?:to\s+|being\s+a\s+by[\-\s]?law\s+to\s+)(.{{5,120}}?)[\n\r\.]',
        # "YYYY-NN - Title"
        rf'{escaped}\s*[\-–:]\s*(.{{5,120}}?)[\n\r\.]',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            # Clean up
            title = re.sub(r'\s+', ' ', title)
            if len(title) > 5:
                return title
    return None


# --- Scrape the By-Laws Page ---

def scrape_bylaws_page():
    """Scrape the township's by-laws listing page."""
    print("\n=== Scraping By-Laws Page ===")
    soup = fetch_page(BYLAWS_PAGE)
    bylaws = []

    # Find all links in the content area
    content = soup.find("div", class_="entry-content") or soup.find("article") or soup
    links = content.find_all("a", href=True)

    for link in links:
        href = link.get("href", "")
        text = link.get_text(strip=True)

        if not text:
            continue

        # Try to extract by-law number from the link text
        # Patterns: "2024-33 User Fees", "1088 Off Road Vehicle...", etc.
        num_match = re.match(r'^(\d{4}[\-–]\d{1,3})\s+(.+)', text)
        if not num_match:
            num_match = re.match(r'^(\d{3,4})\s+(.+)', text)
        if not num_match:
            # Try from the URL
            url_match = re.search(r'/(\d{4}[\-–]\d{1,3})', href)
            if url_match:
                num_match = type('Match', (), {
                    'group': lambda self, n: url_match.group(1) if n == 1 else text
                })()

        if not num_match:
            continue

        number = num_match.group(1).replace("–", "-")
        title = num_match.group(2) if hasattr(num_match, 'group') and callable(num_match.group) else text

        # Clean title
        title = re.sub(r'\s+', ' ', title).strip()
        if title.startswith("- "):
            title = title[2:]

        # Determine year
        year_match = re.match(r'(\d{4})', number)
        if year_match:
            year = int(year_match.group(1))
        else:
            year = None

        # Determine URL type
        pdf_url = None
        page_url = None
        if href.endswith('.pdf') or href.endswith('.docx'):
            pdf_url = urljoin(BASE_URL, href)
        else:
            page_url = urljoin(BASE_URL, href)

        bylaws.append({
            "number": number,
            "year": year,
            "title": title,
            "date_passed": None,
            "pdf_url": pdf_url,
            "page_url": page_url,
            "source": "bylaws_page",
            "status": "approved",
            "votes": None,
            "meeting_date": None,
            "agenda_package_url": None,
            "minutes_url": None,
        })

    print(f"  Found {len(bylaws)} by-laws on the by-laws page")
    return bylaws


# --- Scrape Council Meeting Links ---

def scrape_council_meetings():
    """Scrape the council meetings page for agenda package and minutes links."""
    print("\n=== Scraping Council Meetings Page ===")
    soup = fetch_page(COUNCIL_PAGE)
    meetings = []

    content = soup.find("div", class_="entry-content") or soup.find("article") or soup
    # The page has text with dates and links inline
    text_content = str(content)

    # Parse meeting entries - they follow a pattern of date followed by links
    # e.g., "January 6, 2026 (Agenda) (Minutes) (Agenda Package)"
    date_pattern = re.compile(
        r'(?:Special\s+Meeting\s+)?'
        r'([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})',
        re.IGNORECASE
    )

    # Split content by lines/paragraphs and find dates with their associated links
    lines = text_content.split('<br')
    for line in lines:
        line_soup = BeautifulSoup(line, "html.parser")
        line_text = line_soup.get_text()

        date_match = date_pattern.search(line_text)
        if not date_match:
            continue

        date_str = date_match.group(1).replace(",", "")
        try:
            meeting_date = datetime.strptime(date_str, "%B %d %Y")
        except ValueError:
            try:
                meeting_date = datetime.strptime(date_str, "%B %d %Y")
            except ValueError:
                continue

        is_special = "special" in line_text.lower()

        links = line_soup.find_all("a", href=True)
        agenda_url = None
        minutes_url = None
        package_url = None

        for a in links:
            href = a.get("href", "")
            link_text = a.get_text(strip=True).lower()

            if "agenda package" in link_text or "council-agenda-package" in href.lower() or "council-package" in href.lower() or "agenda-package" in href.lower():
                package_url = urljoin(BASE_URL, href)
            elif "minutes" in link_text:
                minutes_url = urljoin(BASE_URL, href)
            elif "agenda" in link_text:
                agenda_url = urljoin(BASE_URL, href)

        meetings.append({
            "date": meeting_date.strftime("%Y-%m-%d"),
            "date_display": meeting_date.strftime("%B %d, %Y"),
            "is_special": is_special,
            "agenda_url": agenda_url,
            "minutes_url": minutes_url,
            "package_url": package_url,
            "year": meeting_date.year,
        })

    # Sort by date
    meetings.sort(key=lambda m: m["date"])
    print(f"  Found {len(meetings)} council meetings")
    print(f"  With agenda packages: {sum(1 for m in meetings if m['package_url'])}")
    print(f"  With minutes: {sum(1 for m in meetings if m['minutes_url'])}")
    return meetings


# --- Extract By-Laws from Agenda Packages ---

def extract_bylaws_from_package(pdf_path, meeting):
    """Extract by-law information from an agenda package PDF."""
    text = extract_pdf_text(pdf_path)
    if not text:
        return []

    bylaw_numbers = find_bylaw_numbers_in_text(text)
    bylaws = []

    for number in bylaw_numbers:
        title = extract_bylaw_title_from_text(text, number)
        year_match = re.match(r'(\d{4})', number)
        year = int(year_match.group(1)) if year_match else meeting.get("year")

        bylaws.append({
            "number": number,
            "year": year,
            "title": title or f"By-Law {number}",
            "date_passed": None,
            "pdf_url": None,
            "page_url": None,
            "source": "agenda_package",
            "status": "pending",
            "votes": None,
            "meeting_date": meeting["date"],
            "agenda_package_url": meeting.get("package_url"),
            "minutes_url": meeting.get("minutes_url"),
        })

    return bylaws


# --- Check Minutes for Approval ---

def check_approval_in_minutes(pdf_path, bylaw_number, meeting):
    """Check meeting minutes to see if a by-law was approved and extract vote info."""
    text = extract_pdf_text(pdf_path)
    if not text:
        return None, None

    escaped = re.escape(bylaw_number).replace(r'\-', r'[\-–]')

    # Check if the by-law number appears in the minutes
    if not re.search(escaped, text, re.IGNORECASE):
        return None, None

    # Look for approval indicators near the by-law number
    # Common patterns in Ontario municipal minutes:
    approval_patterns = [
        rf'{escaped}.*?(?:carried|approved|passed|adopted)',
        rf'(?:carried|approved|passed|adopted).*?{escaped}',
        rf'{escaped}.*?(?:three readings|third reading|final reading)',
        rf'(?:moved|motion).*?{escaped}.*?(?:carried|approved|passed)',
    ]

    defeated_patterns = [
        rf'{escaped}.*?(?:defeated|denied|rejected|failed|lost)',
        rf'(?:defeated|denied|rejected|failed|lost).*?{escaped}',
    ]

    status = None

    for pattern in defeated_patterns:
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            status = "defeated"
            break

    if not status:
        for pattern in approval_patterns:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                status = "approved"
                break

    if not status:
        # If the by-law appears in minutes but no clear status, check broader context
        # Look for "Carried" near the by-law reference
        bylaw_pos = re.search(escaped, text, re.IGNORECASE)
        if bylaw_pos:
            surrounding = text[max(0, bylaw_pos.start() - 500):bylaw_pos.end() + 500]
            if re.search(r'\bcarried\b', surrounding, re.IGNORECASE):
                status = "approved"

    # Extract vote names
    votes = extract_votes(text, bylaw_number)

    return status, votes


def extract_votes(text, bylaw_number):
    """Extract voter names from minutes near the by-law reference."""
    escaped = re.escape(bylaw_number).replace(r'\-', r'[\-–]')

    bylaw_pos = re.search(escaped, text, re.IGNORECASE)
    if not bylaw_pos:
        return None

    # Get text around the by-law reference (larger window for vote info)
    start = max(0, bylaw_pos.start() - 300)
    end = min(len(text), bylaw_pos.end() + 800)
    surrounding = text[start:end]

    # Common vote patterns in Ontario municipal minutes
    vote_patterns = [
        # "Moved by X, Seconded by Y"
        r'[Mm]oved\s+by\s+(?:Councillor\s+|Deputy\s+Mayor\s+|Mayor\s+)?(\w+[\w\s]*?)\s*[,;]\s*[Ss]econded\s+by\s+(?:Councillor\s+|Deputy\s+Mayor\s+|Mayor\s+)?(\w+[\w\s]*?)(?:\.|,|\s+that)',
        # "Yea: Name, Name  Nay: Name"
        r'[Yy]ea[s]?\s*:\s*(.+?)(?:\s*[Nn]ay|\s*[Cc]arried|\n)',
    ]

    for pattern in vote_patterns:
        match = re.search(pattern, surrounding, re.IGNORECASE)
        if match:
            groups = match.groups()
            names = [g.strip() for g in groups if g and g.strip()]
            if names:
                if len(names) == 2:
                    return f"Moved by {names[0]}, Seconded by {names[1]}"
                else:
                    return ", ".join(names)

    return None


# --- Extract individual by-law PDFs from agenda package ---

def extract_bylaw_pdf_from_package(package_path, bylaw_number, dest_dir):
    """Try to extract by-law pages from an agenda package as a separate PDF."""
    try:
        doc = fitz.open(str(package_path))
        escaped = re.escape(bylaw_number).replace(r'\-', r'[\-–]')

        bylaw_pages = []
        in_bylaw = False

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()

            if re.search(rf'[Bb]y[\-\s]?[Ll]aw\s*(?:No\.?\s*)?{escaped}', text):
                in_bylaw = True
                bylaw_pages.append(page_num)
            elif in_bylaw:
                # Check if this page is still part of the same by-law
                # Stop if we hit a new section or by-law
                if re.search(r'[Bb]y[\-\s]?[Ll]aw\s*(?:No\.?\s*)?\d{4}[\-–]\d', text) and not re.search(escaped, text):
                    in_bylaw = False
                elif re.search(r'(?:^|\n)\s*\d+\.\s+[A-Z]', text):
                    # New agenda item
                    in_bylaw = False
                else:
                    bylaw_pages.append(page_num)

        if bylaw_pages:
            dest_dir = Path(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)

            safe_num = bylaw_number.replace("/", "-")
            output_path = dest_dir / f"By-Law-{safe_num}.pdf"

            new_doc = fitz.open()
            for pn in bylaw_pages:
                new_doc.insert_pdf(doc, from_page=pn, to_page=pn)
            new_doc.save(str(output_path))
            new_doc.close()
            doc.close()

            print(f"    Extracted By-Law {bylaw_number} ({len(bylaw_pages)} pages)")
            return str(output_path)

        doc.close()
    except Exception as e:
        print(f"    WARNING: Could not extract by-law PDF: {e}")

    return None


# --- AI Summarization ---

def generate_ai_summary(bylaw, pdf_text=None):
    """Generate an AI summary and key points for a by-law using the Anthropic API."""
    if not ANTHROPIC_AVAILABLE:
        return None, None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, None

    # Build context from available info
    context_parts = []
    context_parts.append(f"By-Law Number: {bylaw['number']}")
    context_parts.append(f"Title: {bylaw['title']}")
    if bylaw.get("year"):
        context_parts.append(f"Year: {bylaw['year']}")
    if bylaw.get("status"):
        context_parts.append(f"Status: {bylaw['status']}")

    if pdf_text and len(pdf_text.strip()) > 50:
        # Truncate very long PDFs to fit in context
        max_chars = 12000
        if len(pdf_text) > max_chars:
            pdf_text = pdf_text[:max_chars] + "\n\n[... remainder truncated ...]"
        context_parts.append(f"\nFull text of the by-law:\n{pdf_text}")

    context = "\n".join(context_parts)

    prompt = f"""You are summarizing a municipal by-law for the Township of Nipissing, Ontario, Canada.
Provide a clear, plain-language summary that a resident with no legal background can understand.

{context}

Respond in this exact JSON format and nothing else:
{{
  "summary": "A 2-3 sentence plain-language summary of what this by-law does and why it matters to residents.",
  "key_points": ["Point 1 — a specific detail, rule, or requirement", "Point 2", "Point 3"]
}}

Guidelines:
- Keep the summary under 60 words
- Include 3-5 key points, each under 20 words
- Focus on what residents need to know: what's regulated, what's required, what penalties exist
- Use plain language, avoid legal jargon
- If limited text is available, summarize based on the title and any context provided"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text.strip()
        # Clean any markdown fences
        response_text = re.sub(r'^```json\s*', '', response_text)
        response_text = re.sub(r'\s*```$', '', response_text)

        parsed = json.loads(response_text)
        summary = parsed.get("summary", "")
        key_points = parsed.get("key_points", [])

        if summary:
            return summary, key_points
    except json.JSONDecodeError as e:
        print(f"    WARNING: Failed to parse AI response for {bylaw['number']}: {e}")
    except Exception as e:
        print(f"    WARNING: AI summary failed for {bylaw['number']}: {e}")

    return None, None


def generate_summaries_for_bylaws(bylaws):
    """Generate AI summaries for by-laws that don't already have one."""
    if not ANTHROPIC_AVAILABLE or not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n=== Skipping AI Summaries (no API key) ===")
        print("  Set ANTHROPIC_API_KEY environment variable to enable AI summaries")
        return

    needs_summary = [b for b in bylaws if not b.get("ai_summary")]
    if not needs_summary:
        print("\n=== AI Summaries: All by-laws already have summaries ===")
        return

    print(f"\n=== Generating AI Summaries ({len(needs_summary)} by-laws) ===")

    generated = 0
    for bylaw in needs_summary:
        pdf_text = None

        # Try to get PDF text
        if bylaw.get("pdf_url"):
            pdf_path = download_pdf(bylaw["pdf_url"], "temp_pdfs/bylaws")
            if pdf_path:
                pdf_text = extract_pdf_text(pdf_path)

        # If no PDF text, try the page URL (some are HTML pages)
        if not pdf_text and bylaw.get("page_url"):
            try:
                soup = fetch_page(bylaw["page_url"])
                content = soup.find("div", class_="entry-content") or soup.find("article")
                if content:
                    pdf_text = content.get_text(separator="\n", strip=True)
            except Exception:
                pass

        summary, key_points = generate_ai_summary(bylaw, pdf_text)

        if summary:
            bylaw["ai_summary"] = summary
            bylaw["ai_key_points"] = key_points or []
            generated += 1
            print(f"  ✓ {bylaw['number']}: {summary[:80]}...")
        else:
            # Generate a basic summary from the title alone
            summary, key_points = generate_ai_summary(bylaw, None)
            if summary:
                bylaw["ai_summary"] = summary
                bylaw["ai_key_points"] = key_points or []
                generated += 1
                print(f"  ✓ {bylaw['number']} (title only): {summary[:80]}...")
            else:
                print(f"  ✗ {bylaw['number']}: No summary generated")

        # Rate limit: slight delay between API calls
        time.sleep(0.5)

    print(f"\n  Generated {generated} new summaries")


# --- Main Pipeline ---

def merge_bylaws(existing, new_bylaws):
    """Merge new by-laws into existing list, avoiding duplicates."""
    existing_map = {}
    for b in existing:
        existing_map[b["number"]] = b

    for b in new_bylaws:
        num = b["number"]
        if num in existing_map:
            old = existing_map[num]
            # Update fields that are empty in existing
            if not old.get("title") or old["title"] == f"By-Law {num}":
                if b.get("title") and b["title"] != f"By-Law {num}":
                    old["title"] = b["title"]
            if not old.get("pdf_url") and b.get("pdf_url"):
                old["pdf_url"] = b["pdf_url"]
            if not old.get("page_url") and b.get("page_url"):
                old["page_url"] = b["page_url"]
            if not old.get("votes") and b.get("votes"):
                old["votes"] = b["votes"]
            if not old.get("meeting_date") and b.get("meeting_date"):
                old["meeting_date"] = b["meeting_date"]
            if not old.get("minutes_url") and b.get("minutes_url"):
                old["minutes_url"] = b["minutes_url"]
            if not old.get("agenda_package_url") and b.get("agenda_package_url"):
                old["agenda_package_url"] = b["agenda_package_url"]
            # Preserve AI summaries
            if not old.get("ai_summary") and b.get("ai_summary"):
                old["ai_summary"] = b["ai_summary"]
            if not old.get("ai_key_points") and b.get("ai_key_points"):
                old["ai_key_points"] = b["ai_key_points"]
            # Update status only if upgrading from pending
            if old.get("status") == "pending" and b.get("status") in ("approved", "defeated"):
                old["status"] = b["status"]
        else:
            existing_map[num] = b

    return list(existing_map.values())


def run():
    """Main scraper pipeline."""
    print("=" * 60)
    print("Nipissing Township By-Law Scraper")
    print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Load existing data
    data = load_existing_data()
    existing_bylaws = data.get("bylaws", [])
    print(f"\nExisting by-laws in database: {len(existing_bylaws)}")

    # Step 1: Scrape the by-laws listing page
    bylaws_page_results = scrape_bylaws_page()

    # Step 2: Scrape council meetings for agenda packages and minutes
    meetings = scrape_council_meetings()

    # Step 3: Download and parse agenda packages for by-laws
    print("\n=== Processing Agenda Packages ===")
    package_bylaws = []
    for meeting in meetings:
        if not meeting.get("package_url"):
            continue

        pdf_path = download_pdf(meeting["package_url"], "temp_pdfs/packages")
        if not pdf_path:
            continue

        found = extract_bylaws_from_package(pdf_path, meeting)
        if found:
            print(f"  {meeting['date_display']}: Found {len(found)} by-law(s): {', '.join(b['number'] for b in found)}")

            # Try to extract individual by-law PDFs
            for bylaw in found:
                extracted_pdf = extract_bylaw_pdf_from_package(
                    pdf_path, bylaw["number"], PDF_DIR / str(bylaw["year"])
                )
                if extracted_pdf:
                    # Use relative path for the site
                    bylaw["pdf_url"] = extracted_pdf.replace("site/", "")

        package_bylaws.extend(found)

    print(f"\nTotal by-laws found in agenda packages: {len(package_bylaws)}")

    # Step 4: Check minutes for approval status
    print("\n=== Checking Minutes for Approval ===")
    all_pending = [b for b in (existing_bylaws + package_bylaws) if b.get("status") == "pending"]
    minutes_cache = {}

    for meeting in meetings:
        if not meeting.get("minutes_url"):
            continue

        # Download minutes PDF once
        pdf_path = download_pdf(meeting["minutes_url"], "temp_pdfs/minutes")
        if pdf_path:
            minutes_cache[meeting["date"]] = pdf_path

    # Check each pending by-law against all available minutes
    for bylaw in package_bylaws:
        if bylaw.get("status") != "pending":
            continue

        # First check the meeting's own minutes
        meeting_date = bylaw.get("meeting_date")
        if meeting_date and meeting_date in minutes_cache:
            status, votes = check_approval_in_minutes(
                minutes_cache[meeting_date], bylaw["number"],
                {"date": meeting_date}
            )
            if status:
                bylaw["status"] = status
                if votes:
                    bylaw["votes"] = votes
                print(f"  By-Law {bylaw['number']}: {status}" + (f" ({votes})" if votes else ""))
                continue

        # If not found in its own meeting minutes, check subsequent meetings
        for date_key in sorted(minutes_cache.keys()):
            if meeting_date and date_key <= meeting_date:
                continue
            status, votes = check_approval_in_minutes(
                minutes_cache[date_key], bylaw["number"],
                {"date": date_key}
            )
            if status:
                bylaw["status"] = status
                bylaw["minutes_url"] = next(
                    (m["minutes_url"] for m in meetings if m["date"] == date_key), None
                )
                if votes:
                    bylaw["votes"] = votes
                print(f"  By-Law {bylaw['number']}: {status} (found in {date_key} minutes)" +
                      (f" ({votes})" if votes else ""))
                break

    # Step 5: Merge everything together
    print("\n=== Merging Results ===")
    all_bylaws = merge_bylaws(existing_bylaws, bylaws_page_results)
    all_bylaws = merge_bylaws(all_bylaws, package_bylaws)

    # Sort by year and number
    all_bylaws.sort(key=lambda b: (b.get("year") or 0, b.get("number", "")))

    data["bylaws"] = all_bylaws

    # Step 6: Generate AI summaries for by-laws that need them
    generate_summaries_for_bylaws(all_bylaws)

    # Step 7: Save
    save_data(data)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total by-laws: {len(all_bylaws)}")
    print(f"  Approved: {sum(1 for b in all_bylaws if b.get('status') == 'approved')}")
    print(f"  Pending:  {sum(1 for b in all_bylaws if b.get('status') == 'pending')}")
    print(f"  Defeated: {sum(1 for b in all_bylaws if b.get('status') == 'defeated')}")
    print(f"  With PDF: {sum(1 for b in all_bylaws if b.get('pdf_url'))}")
    print(f"  With votes: {sum(1 for b in all_bylaws if b.get('votes'))}")
    print(f"  With AI summary: {sum(1 for b in all_bylaws if b.get('ai_summary'))}")

    years = sorted(set(b.get("year") for b in all_bylaws if b.get("year")))
    for year in years:
        count = sum(1 for b in all_bylaws if b.get("year") == year)
        print(f"    {year}: {count} by-laws")

    # Cleanup temp PDFs
    import shutil
    if Path("temp_pdfs").exists():
        shutil.rmtree("temp_pdfs")
        print("\nCleaned up temp PDF downloads")

    print("\nDone!")


if __name__ == "__main__":
    run()
