"""
Automated YouTube Comment Reply module powered by Gemini.
"""
import argparse
import datetime
import json
import os
import re
import requests
from src.auth import get_credentials, get_gemini_key
from src.yt_client import get_youtube_service, get_channel_info

EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff]|[‍⌀-⏿☀-➿⭐]",
    flags=re.UNICODE
)

def is_spam_or_ineligible(text):
    t = text.strip()
    if len(t) < 3:
        return True
    # Strip emojis to check if pure emoji
    non_emoji = EMOJI_PATTERN.sub("", t).strip()
    if len(non_emoji) == 0:
        return True
    # Spam keywords
    spam_phrases = ["sub4sub", "check my channel", "follow me", "free money", "whatsapp", "telegram", "crypto"]
    if any(p in t.lower() for p in spam_phrases):
        return True
    return False

def generate_reply_with_gemini(video_title, video_description, comment_text, api_key, model="gemini-flash-lite-latest"):
    prompt = (
        f"You are the creator of YouTube channel 'Elisa White'.\n"
        f"Video Title: {video_title}\n"
        f"Video Description: {video_description}\n"
        f"Viewer Comment: {comment_text}\n\n"
        f"Write ONE natural, friendly reply to this comment.\n"
        f"Rules:\n"
        f"- Exactly 1 to 2 short sentences.\n"
        f"- Friendly, authentic, creator tone.\n"
        f"- Do NOT include any links or URLs.\n"
        f"- Do NOT ask for subscribers, likes, or shares.\n"
        f"- Maximum 1 emoji total.\n"
        f"- Respond in the same language as the viewer's comment.\n"
        f"- Return ONLY the reply text, no quotes or explanations."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    res = requests.post(url, json=payload)
    res.raise_for_status()
    reply = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    # Strip enclosing quotes if any
    if reply.startswith('"') and reply.endswith('"'):
        reply = reply[1:-1].strip()
    return reply

def run_comment_replies(dry_run=False):
    creds = get_credentials()
    gemini_key = get_gemini_key()
    service = get_youtube_service(creds)
    channel_info = get_channel_info(service)
    channel_id = channel_info["id"]

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    config_path = os.path.join(base_dir, "config.json")
    with open(config_path, "r", encoding="utf-8-sig") as f:
        config = json.load(f)

    reply_cfg = config.get("comment_reply", {})
    if not reply_cfg.get("enabled", True):
        print("[reply] Comment reply module is disabled in config.json")
        return

    max_replies = reply_cfg.get("max_replies_per_run", 15)
    lookback_days = reply_cfg.get("lookback_days", 7)
    cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=lookback_days)

    state_path = os.path.join(base_dir, "state", "replied.json")
    with open(state_path, "r", encoding="utf-8-sig") as f:
        state = json.load(f)
    replied_ids = set(state.get("replied", []))

    # Fetch comment threads
    print(f"[reply] Fetching comment threads for channel {channel_id} (last {lookback_days} days)...")
    threads_to_process = []
    page_token = None

    while True:
        try:
            req = service.commentThreads().list(
                part="snippet,replies",
                allThreadsRelatedToChannelId=channel_id,
                order="time",
                maxResults=100,
                pageToken=page_token
            )
            res = req.execute()
        except Exception as e:
            print(f"[reply] Note: No comments found or error listing comments: {e}")
            break

        items = res.get("items", [])
        for item in items:
            thread_id = item["id"]
            if thread_id in replied_ids:
                continue

            top = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            published_str = top.get("publishedAt")
            if published_str:
                pub_time = datetime.datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                if pub_time < cutoff_date:
                    continue

            # Check if channel already replied
            author_channel = top.get("authorChannelId", {}).get("value")
            if author_channel == channel_id:
                continue

            replies = item.get("replies", {}).get("comments", [])
            already_replied = any(r.get("snippet", {}).get("authorChannelId", {}).get("value") == channel_id for r in replies)
            if already_replied:
                continue

            comment_text = top.get("textOriginal", "")
            if is_spam_or_ineligible(comment_text):
                continue

            video_id = top.get("videoId")
            threads_to_process.append({
                "thread_id": thread_id,
                "video_id": video_id,
                "comment_text": comment_text,
            })
            if len(threads_to_process) >= max_replies:
                break

        page_token = res.get("nextPageToken")
        if not page_token or len(threads_to_process) >= max_replies:
            break

    if not threads_to_process:
        print("[reply] No eligible comments found to reply to.")
        return

    print(f"[reply] Found {len(threads_to_process)} eligible comments to reply.")
    for item in threads_to_process:
        thread_id = item["thread_id"]
        video_id = item["video_id"]
        text = item["comment_text"]

        # Fetch video title and description
        v_title = "Elisa White Short"
        v_desc = ""
        if video_id:
            try:
                vres = service.videos().list(part="snippet", id=video_id).execute()
                v_items = vres.get("items", [])
                if v_items:
                    v_title = v_items[0]["snippet"]["title"]
                    v_desc = v_items[0]["snippet"]["description"]
            except Exception:
                pass

        reply_text = generate_reply_with_gemini(v_title, v_desc, text, gemini_key, config.get("model", "gemini-flash-lite-latest"))
        print(f'[reply] {video_id} {thread_id} -> "{reply_text}"')

        if not dry_run:
            service.comments().insert(
                part="snippet",
                body={
                    "snippet": {
                        "parentId": thread_id,
                        "textOriginal": reply_text
                    }
                }
            ).execute()
            state.setdefault("replied", []).append(thread_id)
            state.setdefault("history", []).append({
                "thread_id": thread_id,
                "video_id": video_id,
                "comment": text,
                "reply": reply_text,
                "time": datetime.datetime.now(datetime.timezone.utc).isoformat()
            })

    if not dry_run:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run without posting replies")
    args = parser.parse_args()
    run_comment_replies(dry_run=args.dry_run)
