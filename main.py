import os
import re
import json
import base64
import requests
import feedparser
import trafilatura
from bs4 import BeautifulSoup
from datetime import datetime
from difflib import SequenceMatcher
from google import genai

# Configuration from GitHub Secrets
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

RSS_FEED_URL = "https://news.google.com/rss/search?q=Sri+Lanka+news&hl=en-US&gl=US&ceid=US:en"
HISTORY_FILE = "published_history.txt"
CURRENT_YEAR = 2026  # Enforces fresh content validation

def load_history():
    """Loads previously processed URLs and titles."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_to_history(entry_id):
    """Saves URL or title identifier to local history log."""
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{entry_id}\n")

def fetch_recent_wordpress_titles():
    """Fetches the last 60 published post titles directly from WordPress to prevent duplicate topics."""
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts?per_page=60&_fields=title"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        res = requests.get(api_endpoint, headers=headers, timeout=10)
        if res.status_code == 200:
            posts = res.json()
            titles = [p['title']['rendered'] for p in posts]
            return titles
    except Exception as e:
        print(f"Failed to fetch recent WordPress titles: {e}")
    return []

def is_title_similar(new_title, recent_titles, threshold=0.65):
    """Fuzzy matching check against existing titles."""
    for existing in recent_titles:
        similarity = SequenceMatcher(None, new_title.lower(), existing.lower()).ratio()
        if similarity >= threshold:
            print(f"Duplicate detected via fuzzy match ({similarity:.2f}): '{new_title}' matches '{existing}'")
            return True
    return False

def is_semantic_duplicate(new_title, new_summary, recent_titles):
    """Asks Gemini Flash if the new story is a duplicate coverage of an already published event."""
    if not recent_titles:
        return False
        
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    
    recent_list_str = "\n".join([f"- {t}" for t in recent_titles[:35]])
    prompt = f"""
    You are a strict news editor screening incoming stories for duplicate coverage.
    
    Recently Published Headlines:
    {recent_list_str}
    
    Incoming Story Title: "{new_title}"
    Incoming Story Summary: "{new_summary[:300]}"
    
    Question: Strictly analyze if the INCOMING story reports on the SAME underlying event, incident, court proceeding, or official announcement as ANY of the recent headlines. 
    Ignore differences in headline phrasing or writing style—focus on core subjects (entities, names, places, and specific news developments).
    
    Return ONLY a raw JSON object:
    {{
      "is_duplicate": true or false,
      "reason": "Brief explanation"
    }}
    """
    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
            
        data = json.loads(text)
        if data.get("is_duplicate"):
            print(f"Gemini Semantic Duplicate Check: REJECTED ('{new_title}') -> Reason: {data.get('reason')}")
            return True
    except Exception as e:
        print(f"Semantic check error: {e}")
    return False

def resolve_google_url(google_url):
    """Resolves redirected Google News URLs and cleans tracking parameters."""
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        res = session.get(google_url, allow_redirects=True, timeout=8)
        clean_url = res.url.split("?")[0]  # Strips tracking query parameters
        return clean_url
    except Exception as e:
        print(f"URL resolve fallback used: {e}")
        return google_url.split("?")[0]

def check_entry_date(entry, raw_html_downloaded):
    """
    Validates publication year to block old recycled stories (e.g. 2015/2018 news indexed recently).
    Allows current year articles, recent stories, and undated fresh content.
    """
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        pub_year = entry.published_parsed.tm_year
        if pub_year < CURRENT_YEAR:
            print(f"Date Check Failed: RSS publication year is {pub_year} (Older than {CURRENT_YEAR}).")
            return False

    if raw_html_downloaded:
        metadata = trafilatura.extract_metadata(raw_html_downloaded)
        if metadata and metadata.date:
            try:
                date_str = metadata.date.split("T")[0]
                pub_year = datetime.strptime(date_str, "%Y-%m-%d").year
                if pub_year < CURRENT_YEAR:
                    print(f"Date Check Failed: Article page metadata year is {pub_year} (Older than {CURRENT_YEAR}).")
                    return False
            except Exception as e:
                print(f"Could not parse page metadata date: {e}")

    return True

def extract_article_content(url):
    """
    Robust scraper featuring trafilatura + custom BeautifulSoup fallback for
    sites like ft.lk and adaderana.lk to bypass ad pop-ups and custom structures.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    try:
        # Primary: Trafilatura Extraction
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text_content = trafilatura.extract(downloaded)
            if text_content and len(text_content.strip()) > 150:
                return text_content, downloaded

        # Secondary: BeautifulSoup Fallback for AdaDerana / FT.lk
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # Remove scripts, styles, and headers/footers
            for element in soup(["script", "style", "iframe", "header", "footer", "nav", "aside"]):
                element.decompose()

            paragraphs = soup.find_all('p')
            body_text = "\n".join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
            
            if len(body_text) > 150:
                print(f"BeautifulSoup fallback extracted content for: {url}")
                return body_text, res.text

    except Exception as e:
        print(f"Extraction error for {url}: {e}")
        
    return None, None

def get_pexels_image_url(search_query):
    if not PEXELS_API_KEY:
        print("Missing PEXELS_API_KEY secret. Skipping Pexels image lookup.")
        return None

    try:
        headers = {"Authorization": PEXELS_API_KEY}
        url = f"https://api.pexels.com/v1/search?query={search_query}&per_page=1&orientation=landscape"
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            data = res.json()
            photos = data.get("photos", [])
            if photos:
                image_url = photos[0]["src"]["large"]
                print(f"Pexels image found for '{search_query}': {image_url}")
                return image_url
            else:
                print(f"No Pexels photos found for query: '{search_query}'")
        else:
            print(f"Pexels API error. Status: {res.status_code}, Response: {res.text}")
    except Exception as e:
        print(f"Pexels fetch error: {e}")
    return None

def upload_image_to_wordpress(image_url):
    try:
        img_res = requests.get(image_url, timeout=10)
        if img_res.status_code == 200:
            filename = f"pexels_{image_url.split('/')[-1].split('?')[0]}.jpg"
            
            credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
            token = base64.b64encode(credentials.encode()).decode("utf-8")
            
            media_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/media"
            media_headers = {
                "Authorization": f"Basic {token}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": img_res.headers.get("Content-Type", "image/jpeg")
            }
            
            upload_res = requests.post(media_endpoint, headers=media_headers, data=img_res.content, timeout=15)
            if upload_res.status_code in [200, 201]:
                media_id = upload_res.json().get("id")
                print(f"Uploaded photo to WP Media Library. Media ID: {media_id}")
                return media_id
            else:
                print(f"Failed to upload image. Status: {upload_res.status_code}, Response: {upload_res.text}")
    except Exception as e:
        print(f"Image upload exception: {e}")
    return None

def rewrite_with_gemini(raw_text, original_title):
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    
    prompt = f"""
    You are an expert senior news editor writing for BrunchPress.
    Craft a polished, original, and authoritative news article based on the provided source text.

    EDITORIAL & ACCURACY RULES:
    1. NO SOURCE CITATIONS OR ATTRIBUTION:
       - NEVER write "according to [Website]", "reported by [Publication]", or include external links. Write as an original BrunchPress news report.
    2. STRICT FACTUAL ACCURACY:
       - Use ONLY facts from the source text. NEVER invent names, official titles, or unverified details.
    3. FULL CONTEXT & SPECIFICS:
       - Include all key names, exact locations (towns/cities), state institutions, and official designations (e.g., if Johnston Fernando is named, state full name and role).

    RELEVANCE GATE & COMMENTARY STYLE:
    - Determine if the story is ROUTINE or HIGH-IMPACT.
    - ROUTINE NEWS (e.g. ambassador appointments, match results, routine court adjournments, simple notices): Keep crisp, direct, and factual. DO NOT add forced commentary, analytical opinion, or unnecessary grandstanding.
    - HIGH-IMPACT NEWS (e.g. economic policy, major legal decisions, national political shifts): Include 1 natural, well-reasoned paragraph offering practical context or broader implications.
    - BANNED CLICHÉS: NEVER use phrases like "only time will tell", "a step in the right direction", "it remains to be seen", "in a surprising move", or "serves as a stark reminder". Write like a seasoned human journalist.

    HISTORICAL CHECK:
    - If the raw text is clearly an old archived story from past years, output EXACTLY:
      {{"title": "SERVER ERROR", "content": "SERVER ERROR", "image_query": "none"}}

    FORMATTING REQUIREMENTS:
    - Return ONLY a raw JSON object without markdown formatting.
    - JSON Schema:
      {{
        "title": "A strong, engaging, newsroom headline",
        "content": "<p>Comprehensive opening paragraph covering who, what, when, where, and context...</p><p>Detailed body paragraphs with facts and background...</p><h2>Key Developments</h2><ul><li>Takeaway 1</li><li>Takeaway 2</li></ul><p>Concluding paragraph (routine news stays purely factual; high-impact news adds authentic analysis)...</p>",
        "image_query": "2 to 3 concise English keywords for stock photo search",
        "meta_title": "SEO title under 60 chars ending with | BrunchPress",
        "meta_description": "Engaging news summary under 155 chars for search engines",
        "focus_keyword": "Primary 2-3 word topic keyword, comma-separated if multiple keywords"
      }}

    Original Title: {original_title}
    Raw Source Text:
    {raw_text[:4500]}
    """
    
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )
    
    response_text = response.text.strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:-3].strip()
    elif response_text.startswith("```"):
        response_text = response_text[3:-3].strip()
        
    return json.loads(response_text)

def post_to_wordpress(title, content_html, article_data, featured_media_id=None):
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # Extract focus keyword cleanly (convert lists/arrays to a single string if needed)
    raw_keyword = article_data.get("focus_keyword", "Sri Lanka news")
    if isinstance(raw_keyword, list):
        focus_keyword_str = ", ".join(raw_keyword)
    else:
        focus_keyword_str = str(raw_keyword).strip()
    
    # Rank Math SEO meta fields payload
    meta_payload = {
        "rank_math_title": article_data.get("meta_title", f"{title} | BrunchPress"),
        "rank_math_description": article_data.get("meta_description", ""),
        "rank_math_focus_keyword": focus_keyword_str
    }

    body = {
        "title": title,
        "content": content_html,
        "status": "publish",
        "categories": [18],  # Assigns to Category 18 while appearing on Front Page automatically
        "meta": meta_payload
    }
    
    if featured_media_id:
        body["featured_media"] = featured_media_id
    
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    if res.status_code in [200, 201]:
        print(f"Successfully published to Category 18 & Front Page with Rank Math Meta: {title}")
        return True
    else:
        print(f"Failed to publish. Status: {res.status_code}, Response: {res.text}")
        return False

def run_pipeline():
    history = load_history()
    recent_wp_titles = fetch_recent_wordpress_titles()
    feed = feedparser.parse(RSS_FEED_URL)
    
    print(f"Found {len(feed.entries)} items in feed. Fetched {len(recent_wp_titles)} recent titles from WordPress.")
    
    processed_count = 0
    # Process up to 5 unique stories per execution run
    for entry in feed.entries:
        if processed_count >= 5:
            print("Batch target of 5 stories reached. Ending run.")
            break

        raw_url = entry.link
        entry_title = entry.title
        summary = getattr(entry, 'summary', '')

        # 1. URL History Check
        if raw_url in history:
            continue
            
        # 2. Fuzzy Title Match Check
        if is_title_similar(entry_title, recent_wp_titles):
            save_to_history(raw_url)
            continue

        # 3. Gemini Semantic Duplicate Gatekeeper
        if is_semantic_duplicate(entry_title, summary, recent_wp_titles):
            save_to_history(raw_url)
            continue
            
        print(f"\nProcessing unique story ({processed_count + 1}/5): {entry_title}")
        target_url = resolve_google_url(raw_url)
        
        raw_text, raw_
