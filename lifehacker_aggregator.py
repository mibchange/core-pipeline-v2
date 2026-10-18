import os
import json
import base64
import random
import requests
import feedparser
from google import genai

# Configuration from GitHub Secrets
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

RSS_FEED_URL = "https://lifehacker.com/feed/rss"
HISTORY_FILE = "lifehacker_history.txt"
WP_CATEGORY_ID = 42  # Category 42 for Lifehacker Aggregation

def get_gemini_client():
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")
    return genai.Client(api_key=GEMINI_API_KEY.strip())

def load_history():
    """Loads previously processed Lifehacker URLs and GUIDs."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_to_history(entry_identifier):
    """Saves published URL/GUID to local history log."""
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{entry_identifier}\n")

def clean_url(raw_url):
    """Strips query parameters and trailing slashes for robust deduplication."""
    if not raw_url:
        return ""
    clean = raw_url.split("?")[0].split("#")[0].strip().rstrip("/")
    return clean

def get_pexels_image_url(search_query):
    """
    Searches Pexels for relevant landscape stock photos.
    Fetches up to 5 results and picks one randomly to avoid duplicate images.
    """
    if not PEXELS_API_KEY:
        print("Missing PEXELS_API_KEY secret. Skipping Pexels image lookup.")
        return None

    try:
        headers = {"Authorization": PEXELS_API_KEY}
        url = f"https://api.pexels.com/v1/search?query={search_query}&per_page=5&orientation=landscape"
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            data = res.json()
            photos = data.get("photos", [])
            if photos:
                selected_photo = random.choice(photos)
                image_url = selected_photo["src"]["large"]
                print(f"Pexels image found for '{search_query}': {image_url}")
                return image_url
            else:
                print(f"No Pexels photos found for query: '{search_query}'. Trying fallback...")
                fallback_url = "https://api.pexels.com/v1/search?query=workspace productivity&per_page=5&orientation=landscape"
                fallback_res = requests.get(fallback_url, headers=headers, timeout=10)
                if fallback_res.status_code == 200:
                    fallback_photos = fallback_res.json().get("photos", [])
                    if fallback_photos:
                        return random.choice(fallback_photos)["src"]["large"]
        else:
            print(f"Pexels API error. Status: {res.status_code}, Response: {res.text}")
    except Exception as e:
        print(f"Pexels fetch error: {e}")
    return None

def upload_image_to_wordpress(image_url):
    """Uploads the Pexels image directly to WordPress Media Library."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        img_res = requests.get(image_url, headers=headers, timeout=10)
        if img_res.status_code == 200:
            filename = f"lh_pexels_{image_url.split('/')[-1].split('?')[0]}.jpg"
            
            credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
            token = base64.b64encode(credentials.encode()).decode("utf-8")
            
            media_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/media"
            media_headers = {
                "Authorization": f"Basic {token}",
                "User-Agent": "Mozilla/5.0",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": img_res.headers.get("Content-Type", "image/jpeg")
            }
            
            upload_res = requests.post(media_endpoint, headers=media_headers, data=img_res.content, timeout=15)
            if upload_res.status_code in [200, 201]:
                media_id = upload_res.json().get("id")
                print(f"Uploaded photo to WP Media Library. Media ID: {media_id}")
                return media_id
            else:
                print(f"Failed to upload image. Status: {upload_res.status_code}, Response: {upload_res.text[:150]}")
    except Exception as e:
        print(f"Image upload exception: {e}")
    return None

def generate_teaser_and_image_prompt(original_title, original_summary):
    """
    Uses Gemini-3.8-flash to generate an engaging short teaser and highly specific, 
    visually descriptive Pexels search terms.
    """
    client = get_gemini_client()
    
    prompt = f"""
    You are an editorial assistant aggregating content for BrunchPress.
    
    Article Title: {original_title}
    Article Summary: {original_summary[:1500]}
    
    Tasks:
    1. TEASER: Write a short, captivating 2-sentence teaser summary to introduce this Lifehacker article.
    2. IMAGE KEYWORDS: You are a search optimization assistant. Read the headline and summary and STRICTLY IGNORE broad terms like "technology", "productivity", "lifehacker", "tips", or "laptop". 
       Identify the most unique specific object, action, or subject in the text. Provide exactly TWO words that are visually descriptive for stock photos (e.g., if the story is about cooking hacks, use "kitchen spices"; if about saving money, use "piggy bank"; if about phone battery, use "charging cable").

    Return ONLY a raw JSON object without markdown tags:
    {{
      "teaser": "Engaging two-sentence intro...",
      "image_query": "two specific keywords"
    }}
    """
    
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
    )
    
    response_text = response.text.strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:-3].strip()
    elif response_text.startswith("```"):
        response_text = response_text[3:-3].strip()
        
    return json.loads(response_text)

def post_to_wordpress(title, original_url, teaser_text, featured_media_id=None):
    """Posts aggregated story to Category 42 with prefixed headline and direct link button."""
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    
    prefixed_headline = f"From LifeHacker site: {title}"

    content_html = f"""
    <p>{teaser_text}</p>
    <p style="margin-top: 20px;">
        <a href="{original_url}" target="_blank" rel="noopener noreferrer" style="background-color: #111; color: #fff; padding: 10px 18px; text-decoration: none; border-radius: 4px; display: inline-block; font-weight: bold;">
            Read Full Story on Lifehacker &rarr;
        </a>
    </p>
    """
    
    body = {
        "title": prefixed_headline,
        "content": content_html,
        "status": "publish",
        "categories": [WP_CATEGORY_ID]
    }
    
    if featured_media_id:
        body["featured_media"] = featured_media_id
    
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    if res.status_code in [200, 201]:
        print(f"Successfully published: {prefixed_headline}")
        return True
    else:
        print(f"Failed to publish. Status: {res.status_code}, Response: {res.text[:150]}")
        return False

def run_pipeline():
    history = load_history()
    feed = feedparser.parse(RSS_FEED_URL)
    
    # -------------------------------------------------------------------
    # ZERO-COST GUARD CLAUSE
    # Pre-filter entries locally before calling Gemini or Pexels
    # -------------------------------------------------------------------
    unprocessed_entries = []
    for entry in feed.entries:
        original_url = entry.link
        normalized_url = clean_url(original_url)
        entry_guid = getattr(entry, 'id', '')

        if original_url not in history and normalized_url not in history and (not entry_guid or entry_guid not in history):
            unprocessed_entries.append(entry)

    if not unprocessed_entries:
        print("No new Lifehacker stories found. Pipeline exiting at $0.00 cost.")
        return

    print(f"Found {len(unprocessed_entries)} new Lifehacker stories out of {len(feed.entries)} total RSS items.")
    
    processed_count = 0
    for entry in unprocessed_entries:
        if processed_count >= 2:
            print("Batch limit of 2 new stories reached. Ending run.")
            break

        original_url = entry.link
        normalized_url = clean_url(original_url)
        entry_guid = getattr(entry, 'id', '')
        entry_title = entry.title
        summary = getattr(entry, 'summary', '')
            
        print(f"\nProcessing Lifehacker story: {entry_title}")

        try:
            ai_data = generate_teaser_and_image_prompt(entry_title, summary)
            teaser = ai_data.get("teaser", summary[:200])
            image_query = ai_data.get("image_query", "desk setup")
        except Exception as e:
            print(f"Gemini processing error: {e}")
            teaser = summary[:200]
            image_query = "desk setup"

        # Pexels photo lookup & WordPress media upload
        image_url = get_pexels_image_url(image_query)
        media_id = upload_image_to_wordpress(image_url) if image_url else None
            
        success = post_to_wordpress(entry_title, original_url, teaser, featured_media_id=media_id)
        if success:
            save_to_history(original_url)
            if normalized_url:
                save_to_history(normalized_url)
            if entry_guid:
                save_to_history(entry_guid)
                
            processed_count += 1
            print(f"Successfully published aggregated story ({processed_count}/2)!")

if __name__ == "__main__":
    run_pipeline()
