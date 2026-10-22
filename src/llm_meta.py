"""
Gemini Files API upload and video metadata generation with strict validation.
"""
import json
import os
import re
import time
import requests

CURRENCY_PATTERN = re.compile(r"[\$\€\£\¥\₹]|(USD|AED|EUR|GBP|INR|PKR)", re.IGNORECASE)
MAGNITUDE_PATTERN = re.compile(r"\d+([.,]\d+)?\s*(m|b|k|million|billion|sqft|sq\s*ft|feet)", re.IGNORECASE)

def upload_to_gemini_files(file_path, api_key):
    mime_type = "video/mp4"
    file_size = os.path.getsize(file_path)

    # 1. Initial resumable upload request
    init_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }
    body = json.dumps({"file": {"display_name": os.path.basename(file_path)}})
    res = requests.post(init_url, headers=headers, data=body)
    res.raise_for_status()

    upload_url = res.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("No upload URL returned by Gemini Files API")

    # 2. Upload file content
    with open(file_path, "rb") as f:
        data = f.read()

    upload_headers = {
        "Content-Length": str(file_size),
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
    }
    res2 = requests.post(upload_url, headers=upload_headers, data=data)
    res2.raise_for_status()

    file_info = res2.json().get("file", {})
    file_name = file_info.get("name")
    file_uri = file_info.get("uri")

    # 3. Poll until ACTIVE
    poll_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}"
    for _ in range(60):
        r = requests.get(poll_url)
        if r.status_code == 200:
            state = r.json().get("state")
            if state == "ACTIVE":
                print(f"[meta] File is ACTIVE on Gemini Files API: {file_name}")
                return file_uri, file_name
            elif state == "FAILED":
                raise RuntimeError("File processing failed on Gemini Files API")
        time.sleep(5)

    raise TimeoutError("Timed out waiting for video file to become ACTIVE")

def clean_text_field(text):
    # Remove currency and magnitude claims
    t = CURRENCY_PATTERN.sub("", text)
    t = MAGNITUDE_PATTERN.sub("", t)
    return re.sub(r"\s+", " ", t).strip()

def sanitize_metadata(meta, duration):
    # Title < 60 chars
    title = clean_text_field(meta.get("title", "Inspiring Moment"))
    if len(title) > 59:
        title = title[:59].rsplit(" ", 1)[0]
    if not title:
        title = "Inspiring Moment"

    # Tags: 10-15 tags, total string < 460
    raw_tags = meta.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

    clean_tags = []
    for tag in raw_tags:
        ct = clean_text_field(tag)
        if ct and len(ct) > 1 and ct not in clean_tags:
            clean_tags.append(ct)

    # Ensure 10-15 tags
    fallback_tags = ["lifestyle", "shorts", "daily", "inspiration", "trends", "video", "moments", "viral", "creative", "explore", "focus", "mindset"]
    for ft in fallback_tags:
        if len(clean_tags) >= 12:
            break
        if ft not in clean_tags:
            clean_tags.append(ft)

    clean_tags = clean_tags[:15]
    while len(",".join(clean_tags)) > 450 and len(clean_tags) > 10:
        clean_tags.pop()

    # Hashtags: exactly 3 hashtags, #shorts when duration <= 60
    raw_hashtags = meta.get("hashtags", [])
    if isinstance(raw_hashtags, str):
        raw_hashtags = [h.strip() for h in raw_hashtags.split() if h.strip()]

    clean_ht = []
    for h in raw_hashtags:
        h = h.strip()
        if not h.startswith("#"):
            h = f"#{h}"
        h = re.sub(r"[^#a-zA-Z0-9]", "", h)
        if len(h) > 1 and h.lower() not in [x.lower() for x in clean_ht]:
            clean_ht.append(h)

    if duration <= 60 and not any(h.lower() == "#shorts" for h in clean_ht):
        clean_ht.insert(0, "#shorts")

    default_hts = ["#shorts", "#lifestyle", "#viral", "#inspire"]
    for dh in default_hts:
        if len(clean_ht) >= 3:
            break
        if not any(h.lower() == dh.lower() for h in clean_ht):
            clean_ht.append(dh)

    clean_ht = clean_ht[:3]

    # Description = body + blank line + 3 hashtags + blank line + "Elisa White"
    body = clean_text_field(meta.get("description", ""))
    if not body:
        body = f"{title}. Watch till the end for something special!"

    ht_str = " ".join(clean_ht)
    description = f"{body}\n\n{ht_str}\n\nElisa White"

    meta["title"] = title
    meta["description"] = description
    meta["tags"] = clean_tags
    meta["hashtags"] = clean_ht
    meta["path"] = "video_ai_gemini"
    return meta

def generate_video_metadata(file_path, duration, api_key, model="gemini-flash-lite-latest"):
    file_uri, file_name = upload_to_gemini_files(file_path, api_key)

    prompt = (
        "Watch this video carefully and generate YouTube metadata in strict JSON format.\n"
        "Required JSON keys:\n"
        "- title: engaging YouTube title, strictly under 60 characters, no currency, no magnitude/number claims.\n"
        "- description: 2-3 engaging sentences describing what is happening in the video.\n"
        "- tags: array of 10 to 15 relevant keyword tags (single words or short phrases).\n"
        "- hashtags: array of exactly 3 relevant hashtags starting with '#' (include #shorts if short/vertical).\n"
        "- topic: a specific 1 to 4 word thematic niche/topic of this video (e.g. 'luxury interior design', 'fitness motivation').\n"
        "CRITICAL RULES: Do NOT include currency symbols ($ € £ ¥ ₹) or words (USD, EUR, etc). "
        "Do NOT mention sizes or magnitude claims (sqft, million, etc). "
        "The 'topic' field is strictly MANDATORY."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}},
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }

    res = requests.post(url, json=payload)
    res.raise_for_status()

    result_json = res.json()
    raw_text = result_json["candidates"][0]["content"]["parts"][0]["text"]
    meta = json.loads(raw_text)

    # Verify topic is present and non-empty
    topic = meta.get("topic", "").strip()
    if not topic:
        print("[meta] Missing topic. Retrying generateContent call with explicit reminder...")
        retry_prompt = prompt + "\n\nREMINDER: You forgot 'topic' previously. You MUST provide the 'topic' key in the JSON output!"
        payload["contents"][0]["parts"][1]["text"] = retry_prompt
        r_retry = requests.post(url, json=payload)
        if r_retry.status_code == 200:
            retry_raw = r_retry.json()["candidates"][0]["content"]["parts"][0]["text"]
            try:
                meta = json.loads(retry_raw)
                topic = meta.get("topic", "").strip()
            except Exception:
                pass

        if not topic:
            topic = "Lifestyle & Aesthetics"
            print("[meta] topic fallback used")
            meta["topic"] = topic

    meta = sanitize_metadata(meta, duration)
    meta["topic"] = topic
    return meta
