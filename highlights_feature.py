import os
import json
import base64
import requests
import trafilatura
from bs4 import BeautifulSoup
from google import genai
import gspread
from google.oauth2.service_account import Credentials

# Configuration from GitHub Secrets
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

# Set the target WordPress Category ID for HIGHLIGHTS (Category ID 47)
WP_CATEGORY_ID = 47 

def fetch_urls_from_private_sheet():
    if not GOOGLE_CREDENTIALS:
        print("=== STEP 1 ERROR: Missing GOOGLE_CREDENTIALS secret ===")
        return []
        
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    
    # Target Google Sheet via exact Spreadsheet ID
    SPREADSHEET_ID = "1prryBCnTg8f3p3EihipqzJdngcKrokjf77sTvbMkIwo"
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    
    urls = []
    rows = sheet.get_all_values()
    for row in rows:
        for cell in row:
            cell_clean = cell.strip()
            if cell_clean.startswith("http://") or cell_clean.startswith("https://"):
                clean_url = cell_clean.split("?")[0]
                urls.append(clean_url)
                
    print(f"=== STEP 1 DEBUG: Found {len(urls)} URLs in Google Sheet ID '{SPREADSHEET_ID}' ===")
    return urls

def extract_article_content(url):
    """
    Scrapes article text using Trafilatura with a BeautifulSoup fallback.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=12)
        
        if response.status_code == 200:
            extracted_text = trafilatura.extract(response.text)
            if extracted_text and len(extracted_text.strip()) > 150:
                print(f"=== STEP 2 DEBUG: Trafilatura extracted {len(extracted_text)} chars from {url} ===")
                return extracted_text

            soup = BeautifulSoup(response.text, 'html.parser')
            for element in soup(["script", "style", "iframe", "header", "footer", "nav", "aside"]):
                element.decompose()

            paragraphs = soup.find_all('p')
            body_text = "\n".join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
            
            if len(body_text) > 150:
                print(f"=== STEP 2 DEBUG: BeautifulSoup extracted {len(body_text)} chars from {url} ===")
                return body_text
        else:
            print(f"=== STEP 2 DEBUG: HTTP {response.status_code} error fetching {url} ===")
            
        print(f"=== STEP 2 DEBUG: Extraction returned EMPTY text for {url} ===")
    except Exception as e:
        print(f"=== STEP 2 DEBUG: Extraction error for {url}: {e} ===")
    return None

def get_pexels_image_url(search_query):
    if not PEXELS_API_KEY:
        return None
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        url = f"https://api.pexels.com/v1/search?query={search_query}&per_page=1&orientation=landscape"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            photos = res.json().get("photos", [])
            if photos:
                return photos[0]["src"]["large"]
    except Exception as e:
        print(f"Pexels fetch error: {e}")
    return None

def upload_image_to_wordpress(image_url):
    try:
        img_res = requests.get(image_url, timeout=10)
        if img_res.status_code == 200:
            filename = f"feature_{image_url.split('/')[-1].split('?')[0]}.jpg"
            credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
            token = base64.b64encode(credentials.encode()).decode("utf-8")
            media_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/media"
            media_headers = {
                "Authorization": f"Basic {token}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": img_res.headers.get("Content-Type", "image/jpeg")
            }
            upload_res = requests.post(media_endpoint, headers=media_headers, data=img_res.content, timeout=15)
            if upload_res.status_code in [200, 201]:
                return upload_res.json().get("id")
            else:
                print(f"WP Media Upload Error ({upload_res.status_code}): {upload_res.text}")
    except Exception as e:
        print(f"Image upload exception: {e}")
    return None

def write_investigative_feature_with_gemini(raw_text, target_url):
    """
    Uses Gemini to craft an incisive, investigative, and politically compelling feature article.
    """
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    
    prompt = f"""
    You are an acclaimed investigative journalist, veteran political analyst, and master narrative writer. 
    Analyze the raw factual material extracted from URL ({target_url}) and transform it into a real humdinger of an INVESTIGATIVE FEATURE AND OPINION PIECE.

    EDITORIAL & STYLISTIC INSTRUCTIONS:
    1. TONAL DIRECTION: Write an incisive, gripping, and deeply analytical piece. Unpack the political, economic, or social realities underlying the facts. Make it simple and accessible to a broad audience, yet punchy, provocative, and razor-sharp in its insights.
    2. OPENING HOOK: Start with a powerful, gripping sentence or evocative scenario that immediately pulls the reader into the heart of the issue or political/social tension.
    3. NARRATIVE ANGLE: Focus on the human impact, power dynamics, policy fallout, or institutional shortcomings concealed behind the basic news facts.
    4. STRUCTURE & HEADINGS: Use clear, engaging HTML subheadings (<h2> tags) to guide the reader through your analysis, key findings, and broader societal or political implications.
    5. NO SOURCE ATTRIBUTION: Never write "according to [Website]", "reports state", or cite source links. Speak directly and authoritatively in your own fearless journalistic voice.
    6. FACTUAL RIGOR: Keep all core facts, numbers, dates, figures, and names strictly accurate based on the raw text—do NOT invent fabricated facts, but feel free to offer sharp editorial commentary and analysis on what those facts mean.

    FORMATTING REQUIREMENTS:
    - Return ONLY a valid JSON object. Do not wrap in markdown syntax like ```json.
    - Fields required in JSON:
      "title": "A sharp, gripping, provocative headline that demands attention"
      "content": "<p>Gripping opening scene or hard-hitting statement...</p><p>Contextualizing paragraphs explaining the core narrative and facts...</p><h2>Subheading 1</h2><p>Incisive investigative analysis and political/social commentary...</p><h2>Subheading 2</h2><p>Broader impacts, power dynamics, and societal consequences...</p><p>A strong, memorable, and thought-provoking closing paragraph...</p>"
      "image_query": "2 to 3 punchy English keywords for stock photo search (e.g. 'sri lanka parliament', 'colombo protest', 'sri lanka economy')"

    Raw Source Material:
    {raw_text[:6000]}
    """
    
    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    response_text = response.text.strip()
    
    if response_text.startswith("```json"):
        response_text = response_text[7:-3].strip()
    elif response_text.startswith("```"):
        response_text = response_text[3:-3].strip()
        
    return json.loads(response_text)

def post_to_wordpress(title, content_html, featured_media_id=None):
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    body = {
        "title": title, 
        "content": content_html, 
        "status": "publish",
        "categories": [WP_CATEGORY_ID]
    }
    if featured_media_id:
        body["featured_media"] = featured_media_id
        
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    if res.status_code in [200, 201]:
        return True
    else:
        print(f"=== STEP 3 ERROR: WP Post Failed ({res.status_code}): {res.text} ===")
        return False

def run_feature_pipeline():
    urls = fetch_urls_from_private_sheet()
    
    if not urls:
        print("=== STEP 1 DEBUG: No URLs retrieved. Exiting feature pipeline. ===")
        return

    processed_count = 0
    for url in urls:
        if processed_count >= 5:
            print("Batch target limit of 5 feature stories reached.")
            break

        print(f"\nProcessing Feature URL ({processed_count + 1}/5): {url}")
        raw_text = extract_article_content(url)
        
        if not raw_text or len(raw_text) < 100:
            print(f"=== STEP 2 WARNING: Insufficient text extracted from {url}. Skipping. ===")
            continue

        try:
            print("=== Drafting investigative feature with Gemini... ===")
            article_data = write_investigative_feature_with_gemini(raw_text, url)
        except Exception as e:
            print(f"=== Gemini Feature Writing Error for {url}: {e} ===")
            continue

        image_query = article_data.get("image_query", "sri lanka news")
        image_url = get_pexels_image_url(image_query)
        media_id = upload_image_to_wordpress(image_url) if image_url else None

        print(f"=== STEP 3 DEBUG: Attempting WordPress post for '{article_data['title']}' ===")
        if post_to_wordpress(article_data["title"], article_data["content"], featured_media_id=media_id):
            processed_count += 1
            print(f"=== STEP 3 SUCCESS: Published HIGHLIGHTS feature {processed_count}/5 ('{article_data['title']}') to WordPress! ===")

if __name__ == "__main__":
    run_feature_pipeline()
