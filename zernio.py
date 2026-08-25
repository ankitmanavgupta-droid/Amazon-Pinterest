from pathlib import Path

import requests

from config import ZERNIO_API_KEY

API_BASE_URL = "https://zernio.com/api/v1"

CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class ZernioAPIError(ValueError):
    pass


def _headers():
    if not ZERNIO_API_KEY:
        raise ZernioAPIError("Missing ZERNIO_API_KEY. Sign up at zernio.com and add it to .env.")
    return {"Authorization": f"Bearer {ZERNIO_API_KEY}", "Content-Type": "application/json"}


def _check(response, what: str):
    """raise_for_status() throws a bare HTTPError whose message is just the
    status code — the reason the call failed is in the body. Surface that
    instead, as the one exception type callers already handle."""
    if response.ok:
        return response

    detail = ""
    try:
        body = response.json()
        detail = body.get("error") or body.get("message") or body.get("detail") or ""
        if not detail and body:
            detail = str(body)
    except ValueError:
        detail = (response.text or "").strip()[:400]

    raise ZernioAPIError(f"{what} failed ({response.status_code}){f': {detail}' if detail else '.'}")


def upload_media(file_path: str) -> str:
    """Uploads a local image to Zernio's media storage and returns its public
    URL, for use as image_url in create_pin() — avoids needing to host the
    pin image publicly ourselves."""
    path = Path(file_path)
    content_type = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")

    presign_response = requests.post(
        f"{API_BASE_URL}/media/presign",
        json={"filename": path.name, "contentType": content_type},
        headers=_headers(),
        timeout=30,
    )
    _check(presign_response, "Preparing the image upload")
    presign_data = presign_response.json()

    upload_response = requests.put(
        presign_data["uploadUrl"],
        data=path.read_bytes(),
        headers={"Content-Type": content_type},
        timeout=60,
    )
    _check(upload_response, "Uploading the image")

    return presign_data["publicUrl"]


def get_connected_accounts(platform: str) -> list:
    """Lists connected accounts for a platform (each with an '_id' usable as
    account_id below). Requires having already connected that platform to your
    Zernio account via their dashboard's OAuth flow first."""
    response = requests.get(f"{API_BASE_URL}/accounts", params={"platform": platform}, headers=_headers(), timeout=30)
    _check(response, f"Listing connected {platform} accounts")
    return response.json().get("accounts", [])


def get_connected_pinterest_accounts() -> list:
    return get_connected_accounts("pinterest")


def get_connected_tiktok_accounts() -> list:
    return get_connected_accounts("tiktok")


def list_boards(account_id: str) -> list:
    """Lists the given Pinterest account's boards: [{id, name, description, privacy, ...}]."""
    response = requests.get(f"{API_BASE_URL}/accounts/{account_id}/pinterest-boards", headers=_headers(), timeout=30)
    _check(response, "Listing Pinterest boards")
    return response.json().get("boards", [])


def create_pin(
    account_id: str,
    board_id: str,
    image_url: str,
    link: str = None,
    description: str = "",
    title: str = None,
    scheduled_for: str = None,
    tz_name: str = None,
) -> dict:
    """Creates a Pin on the given board. image_url must be a publicly
    reachable URL — pass the result of upload_media() for a local file.
    title (max 100 chars) is Pinterest's searchable Pin title, separate from
    the description; Pinterest defaults it to the first line of content if omitted.
    link is optional — without one the Pin is just an image on the board.

    Without scheduled_for the Pin goes out immediately. With it (an ISO 8601
    timestamp, read in tz_name when it carries no UTC offset of its own) Zernio
    holds the Pin and publishes it at that time — from their servers, so nothing
    here needs to still be running when it fires."""
    # An outfit pin whose garments carry no product links has nowhere to send
    # people; Pinterest is fine with a Pin that just doesn't have a link.
    platform_specific_data = {"boardId": board_id}
    if link:
        platform_specific_data["link"] = link
    if title:
        platform_specific_data["title"] = title

    payload = {
        "content": description,
        "mediaItems": [{"type": "image", "url": image_url}],
        "platforms": [
            {
                "platform": "pinterest",
                "accountId": account_id,
                "platformSpecificData": platform_specific_data,
            }
        ],
    }
    if scheduled_for:
        payload["scheduledFor"] = scheduled_for
        if tz_name:
            payload["timezone"] = tz_name
    else:
        payload["publishNow"] = True

    response = requests.post(f"{API_BASE_URL}/posts", json=payload, headers=_headers(), timeout=30)
    _check(response, "Creating the Pin")
    return response.json()


TIKTOK_PRIVACY_LEVELS = (
    "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY",
)


def zernio_failure(result: dict) -> str:
    """The human-readable reason a post failed.

    Zernio reports it in three places depending on where it broke, and the
    per-platform `status: "failed"` on its own says nothing useful — the
    sentence a person can act on is in errorMessage.
    """
    post = (result or {}).get("post") or {}
    for entry in post.get("platforms") or []:
        message = entry.get("errorMessage") or entry.get("error") or entry.get("failureReason")
        if message:
            return message
    for entry in (result or {}).get("platformResults") or []:
        if entry.get("error"):
            return entry["error"]
    return (result or {}).get("error") or ""


def create_tiktok_photo_post(
    account_id: str,
    image_urls: list,
    title: str,
    description: str = "",
    privacy_level: str = "PUBLIC_TO_EVERYONE",
    allow_comment: bool = True,
    auto_add_music: bool = True,
    draft: bool = False,
    scheduled_for: str = None,
    tz_name: str = None,
) -> dict:
    """Posts several images as one TikTok photo carousel ("slideshow").

    Unlike every other platform, TikTok's options go in a top-level
    `tiktokSettings` block rather than the platform's platformSpecificData —
    that's a quirk of Zernio's API, not a mistake here. `content` becomes the
    ~90-character title (TikTok strips hashtags from it), while the longer
    caption belongs in `description`.

    content_preview_confirmed/express_consent_given are TikTok's mandatory
    declarations that a human approved this post before it went out.

    draft=True delivers to the account's Creator Inbox instead of publishing —
    the post is finished off in the TikTok app. TikTok rations direct posting,
    and this is the route Zernio points at when that capacity runs out.
    """
    if not image_urls:
        raise ZernioAPIError("A TikTok photo post needs at least one image.")
    if privacy_level not in TIKTOK_PRIVACY_LEVELS:
        raise ZernioAPIError(f"privacy_level must be one of {', '.join(TIKTOK_PRIVACY_LEVELS)}.")

    payload = {
        "content": title[:90],
        "mediaItems": [{"type": "image", "url": url} for url in image_urls],
        "platforms": [{"platform": "tiktok", "accountId": account_id}],
        "tiktokSettings": {
            "privacy_level": privacy_level,
            "allow_comment": allow_comment,
            "media_type": "photo",
            "photo_cover_index": 0,
            "description": description[:4000],
            "auto_add_music": auto_add_music,
            "content_preview_confirmed": True,
            "express_consent_given": True,
        },
    }
    if draft:
        payload["tiktokSettings"]["draft"] = True
    if scheduled_for:
        payload["scheduledFor"] = scheduled_for
        if tz_name:
            payload["timezone"] = tz_name
    else:
        payload["publishNow"] = True

    response = requests.post(f"{API_BASE_URL}/posts", json=payload, headers=_headers(), timeout=60)
    _check(response, "Creating the TikTok slideshow")
    return response.json()
