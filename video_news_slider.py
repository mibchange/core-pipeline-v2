import os
import re
import json
import base64
import requests
import feedparser
from google import genai

# Configuration from GitHub Secrets
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")

YOUTUBE_RSS_URL = "https://rss.app/feeds/E2WvJe9Ayyma7Zzn.xml"

def get_gemini_client():
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")
    return genai.Client(api_key=GEMINI_API_KEY.strip())

def extract_youtube_id(url_or_guid_or_text):
    patterns = [
        r"(?:v=|\/vi\/|\/videos\/|\/embed\/|\/shorts\/|youtu\.be\/|\/v\/|yt:video:)([a-zA-Z0-9_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, str(url_or_guid_or_text))
        if match:
            return match.group(1)
    return None

def fetch_recent_wp_video_posts():
    """Fetches titles and Video IDs of recent Category 32 posts for deduplication checks."""
    if not WP_URL or not WP_USER or not WP_APP_PASSWORD:
        print("CRITICAL: Missing WordPress credentials in environment variables!")
        return []

    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Mozilla/5.0"
    }
    params = {
        "categories": 32,
        "per_page": 30,
        "status": "publish,draft,future,private"
    }
    
    try:
        res = requests.get(api_endpoint, headers=headers, params=params, timeout=12)
        if res.status_code == 200:
            return res.json()
        else:
            print(f"CRITICAL ERROR: WP API returned status {res.status_code}.")
            return []
    except Exception as e:
        print(f"CRITICAL EXCEPTION connecting to WP: {e}")
        return []

def is_video_id_published(video_id, recent_posts):
    """Tier 1 Check: Checks if exact video_id exists in WP content/excerpt/title."""
    for post in recent_posts:
        content = post.get("content", {}).get("rendered", "")
        excerpt = post.get("excerpt", {}).get("rendered", "")
        title = post.get("title", {}).get("rendered", "")
        
        if video_id in content or video_id in excerpt or video_id in title:
            return True
    return False

def is_semantic_duplicate_story(incoming_title, recent_posts):
    """
    Tier 2 Check: Uses Gemini to evaluate duplicate stories with minimal tokens.
    """
    if not GEMINI_API_KEY:
        return False

    # Limit to top 15 recent titles to save input tokens
    existing_titles = [post.get("title", {}).get("rendered", "") for post in recent_posts if post.get("title", {}).get("rendered", "")] [:15]
    
    if not existing_titles:
        return False

    client = get_gemini_client()

    # Stripped down, token-efficient prompt (no verbose explanations)
    prompt = f"""Compare incoming news title to existing titles. Are they covering the exact same specific real-world event/speech?

Incoming: "{incoming_title}"
Existing: {json.dumps(existing_titles)}

Return ONLY valid raw JSON, no markdown, no explanations:
{{"is_duplicate": true/false}}"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        
        response_text = response.text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:-3].strip()
        elif response_text.startswith("```"):
            response_text = response_text[3:-3].strip()
            
        data = json.loads(response_text)
        return data.get("is_duplicate", False)

    except Exception:
        return False

def get_youtube_thumbnail_url(video_id):
    maxres_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    try:
        res = requests.head(maxres_url, timeout=5)
        if res.status_code == 200:
            return maxres_url
    except Exception:
        pass
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

def upload_thumbnail_to_wordpress(image_url, video_id):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        img_res = requests.get(image_url, headers=headers, timeout=10)
        if img_res.status_code == 200:
            filename = f"yt_cover_{video_id}.jpg"
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
                return media_id
    except Exception:
        pass
    return None

def post_to_wordpress(title, video_id):
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    
    clean_yt_link = f"https://www.youtube.com/watch?v={video_id}"

    clean_slug = re.sub(r'[^a-zA-Z0-9\s-]', '', title).strip().lower()
    clean_slug = re.sub(r'[\s-]+', '-', clean_slug)[:60]

    thumb_url = get_youtube_thumbnail_url(video_id)
    featured_media_id = upload_thumbnail_to_wordpress(thumb_url, video_id)

    body = {
        "title": title,
        "slug": f"{clean_slug}-{video_id.lower()}",
        "content": f'<!-- YT:{video_id} --><p><iframe width="100%" height="400" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe></p>',
        "excerpt": clean_yt_link,
        "status": "publish",
        "categories": [32]
    }

    if featured_media_id:
        body["featured_media"] = featured_media_id

    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    return res.status_code in [200, 201]

def run_video_pipeline():
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(YOUTUBE_RSS_URL, headers=headers, timeout=12)
        if response.status_code == 200:
            feed = feedparser.parse(response.text)
        else:
            return
    except Exception:
        return

    recent_posts = fetch_recent_wp_video_posts()

    candidates = []
    for entry in feed.entries:
        video_url = getattr(entry, 'link', '')
        entry_title = getattr(entry, 'title', '')
        summary = getattr(entry, 'summary', '')
        guid = getattr(entry, 'id', '')

        video_id = extract_youtube_id(video_url) or extract_youtube_id(guid) or extract_youtube_id(summary)
        if video_id:
            candidates.append({
                "video_id": video_id,
                "title": entry_title
            })

    # Tier 1: Zero-cost guard clause for exact video ID match
    new_candidates = [c for c in candidates if not is_video_id_published(c["video_id"], recent_posts)]

    if not new_candidates:
        return

    for item in new_candidates:
        video_id = item["video_id"]
        entry_title = item["title"]

        # Tier 2: Token-optimized semantic check
        if is_semantic_duplicate_story(entry_title, recent_posts):
            continue
        
        if post_to_wordpress(entry_title, video_id):
            break

if __name__ == "__main__":
    run_video_pipeline()
