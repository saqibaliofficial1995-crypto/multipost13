"""
YouTube API client with safety gate and channel info inspection.
"""
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def get_youtube_service(creds):
    return build("youtube", "v3", credentials=creds)

def get_channel_info(service):
    resp = service.channels().list(
        part="snippet,status,statistics,contentDetails,brandingSettings",
        mine=True
    ).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("No YouTube channel found for the authenticated account")

    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    details = item.get("contentDetails", {})
    branding = item.get("brandingSettings", {})

    return {
        "id": item.get("id"),
        "title": snippet.get("title"),
        "uploads_playlist": details.get("relatedPlaylists", {}).get("uploads"),
        "video_count": int(stats.get("videoCount", 0)),
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "brandingSettings": branding,
    }

def upload_video(service, file_path, metadata, privacy_status="public", made_for_kids=False):
    # HARD SAFETY GATE
    if metadata.get("path") != "video_ai_gemini":
        raise ValueError("SAFETY GATE VIOLATION: metadata['path'] is not 'video_ai_gemini'")

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata.get("tags", []),
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        }
    }

    media = MediaFileUpload(
        file_path,
        mimetype="video/mp4",
        chunksize=1024 * 1024 * 4,
        resumable=True
    )

    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[upload] Upload progress: {int(status.progress() * 100)}%")

    video_id = response.get("id")
    print(f"[upload] Video published successfully: https://youtu.be/{video_id}")
    return video_id
