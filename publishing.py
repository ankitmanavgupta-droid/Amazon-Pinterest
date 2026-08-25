"""Publishing a pin: pushing its landing page live, then posting it to
Pinterest — either straight away or at a time you pick."""

import shutil
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import GITHUB_PAGES_BASE_URL, PROJECT_ROOT, SHOP_DIR
from pinterest.seo import format_description, pick_best_board
from pinterest.zernio_client import (
    create_pin, create_tiktok_photo_post, get_connected_pinterest_accounts,
    get_connected_tiktok_accounts, list_boards, upload_media, zernio_failure,
)
from pins import (
    _now, destination_link, has_landing_page, load_pin, pin_dir, save_pin, write_tiktok_frame,
)


class PublishError(RuntimeError):
    pass


def _git(*args) -> str:
    result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise PublishError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def _git_ok(*args) -> bool:
    """Runs a git command for its exit status, without raising."""
    return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True).returncode == 0


def _push_current_branch():
    """Pushes, catching up with the remote first.

    Publishing from more than one place — or a push whose tracking ref didn't
    get updated — leaves the local idea of the remote stale, and git then
    rejects the push ('cannot lock ref … is at X but expected Y'). Fetching
    first makes that self-healing; only a genuine divergence needs a rebase,
    and landing pages are separate per-slug files so they rarely conflict.
    """
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    _git("fetch", "origin", branch)

    upstream = f"origin/{branch}"
    behind = _git("rev-list", "--count", f"HEAD..{upstream}").strip()
    if behind != "0":
        if not _git_ok("rebase", upstream):
            _git_ok("rebase", "--abort")
            raise PublishError(
                f"The remote {branch} has {behind} commit(s) this copy doesn't, and they don't "
                "rebase cleanly. Sort the repo out by hand (git pull --rebase), then publish again."
            )

    _git("push", "origin", branch)


def publish_pin(slug: str) -> str:
    """Makes a pin ready to post, and returns where its Pin will link to.

    For the product collage that means pushing its landing page live on GitHub
    Pages. Every other template is a single image with no page of its own, so
    there's nothing to push — publishing just marks it ready, and the Pin
    links straight at a product (or carries no link at all)."""
    pin = load_pin(slug)
    png_source = pin_dir(slug) / "pin.png"
    if not png_source.exists():
        raise PublishError(f"{slug} hasn't been rendered yet — open it in the editor and save first.")

    if has_landing_page(pin):
        page = SHOP_DIR / f"{slug}.html"
        if not page.exists():
            raise PublishError(f"{slug} has no landing page yet — open it in the editor and save first.")

        png_dest = SHOP_DIR / f"{slug}.png"
        shutil.copy(png_source, png_dest)

        for path in (page, png_dest):
            _git("add", str(path.relative_to(PROJECT_ROOT)))

        # Nothing staged means the files are byte-identical to what's already
        # committed — a re-publish with no changes, which shouldn't be an error.
        if _git("diff", "--cached", "--name-only").strip():
            _git("commit", "-m", f"Add pin: {slug}")
        _push_current_branch()

    pin["published_at"] = _now()
    save_pin(pin)

    return destination_link(pin)


def _send_to_pinterest(slug: str, scheduled_for: str = None, tz_name: str = None) -> tuple:
    """The shared path behind post_pin and schedule_pin — they differ only in
    whether a time is attached to the Zernio call. Returns (pin, result, board_id);
    recording the outcome on the pin is the caller's job."""
    pin = load_pin(slug)
    png_path = pin_dir(slug) / "pin.png"

    if not png_path.exists():
        raise PublishError(f"{slug} hasn't been rendered yet — open it in the editor and save first.")
    if not pin.get("published_at"):
        raise PublishError(f"{slug} isn't published yet — publish it first so the Pin has somewhere to link to.")
    if pin.get("scheduled_for"):
        raise PublishError(
            f"{slug} is already scheduled for {pin['scheduled_for']}. Zernio's API can't cancel a "
            "scheduled post, so change or delete it in their dashboard first."
        )

    seo = pin.get("seo") or {}
    description = format_description(seo) or f"{pin.get('title1', '')} {pin.get('title2', '')}".strip()

    accounts = get_connected_pinterest_accounts()
    if not accounts:
        raise PublishError("No Pinterest account connected in Zernio — connect one in their dashboard first.")
    account_id = accounts[0]["_id"]

    boards = list_boards(account_id)
    board_id = pick_best_board(f"{seo.get('title', '')} {description}", boards, preferred_name=seo.get("board"))
    if not board_id:
        raise PublishError("That Pinterest account has no boards to pin to.")

    # A collage links to its landing page; anything else links at the product
    # it came from, or goes out with no link when it has none.
    link = destination_link(pin)

    image_url = upload_media(str(png_path))
    result = create_pin(
        account_id=account_id,
        board_id=board_id,
        image_url=image_url,
        link=link,
        description=description,
        title=seo.get("title"),
        scheduled_for=scheduled_for,
        tz_name=tz_name,
    )
    return pin, result, board_id


def post_pin(slug: str) -> str:
    """Posts the rendered pin to Pinterest via Zernio, now. Returns the live Pin URL."""
    pin, result, board_id = _send_to_pinterest(slug)

    pin_url = result["post"]["platforms"][0]["platformPostUrl"]
    pin["posted_at"] = _now()
    pin["pin_url"] = pin_url
    pin["board_id"] = board_id
    save_pin(pin)

    return pin_url


def post_slideshow(
    slideshow_id: str, caption: str = None,
    privacy_level: str = "PUBLIC_TO_EVERYONE", draft: bool = False,
) -> str:
    """Posts one batch of outfit pins to TikTok as a photo carousel.

    Each slide is its own pin — already rendered, and posting to Pinterest
    separately. TikTok gets them as a single slideshow, so this uploads every
    slide's image and makes one post. Returns the live TikTok URL, or None when
    it went to the Creator Inbox as a draft for finishing in the app."""
    import slideshows

    show = slideshows.get_slideshow(slideshow_id)
    if show.get("posted_at"):
        raise PublishError("That slideshow is already on TikTok — posting again would duplicate it.")
    if show.get("drafted_at") and not draft:
        raise PublishError(
            "That slideshow is already waiting in your TikTok drafts — finish it in the app, "
            "or discard it there first."
        )

    summary = slideshows.slideshow_summary(show)
    if not summary["slides"]:
        raise PublishError("That slideshow has no slides left to post.")
    if summary["unrendered"]:
        missing = ", ".join(summary["unrendered"])
        raise PublishError(f"Save these outfits before posting the slideshow: {missing}.")

    accounts = get_connected_tiktok_accounts()
    if not accounts:
        raise PublishError("No TikTok account connected in Zernio — connect one in their dashboard first.")
    account_id = accounts[0]["_id"]

    caption = (caption if caption is not None else show.get("caption", "")).strip()
    if not caption:
        # Fall back to whatever SEO the first slide already carries, so a
        # slideshow is never posted with an empty caption.
        first = load_pin(summary["slides"][0]["slug"])
        seo = first.get("seo") or {}
        caption = format_description(seo) or "Outfit ideas"

    # Upload the 9:16 re-frame, not the 2:3 pin — TikTok pads anything squarer
    # with blurred fill, and the pin itself has to stay 2:3 for Pinterest.
    image_urls = [upload_media(str(write_tiktok_frame(slide["slug"]))) for slide in summary["slides"]]

    def send(as_draft):
        return create_tiktok_photo_post(
            account_id=account_id,
            image_urls=image_urls,
            title=caption.splitlines()[0] if caption else "Outfit ideas",
            description=caption,
            privacy_level=privacy_level,
            draft=as_draft,
        )

    result = send(draft)
    failure = zernio_failure(result)

    # TikTok rations direct posting. When that runs out the images are fine and
    # the request is fine — only the delivery route is unavailable — so send it
    # to the Creator Inbox rather than making the whole batch a dead end.
    if failure and not draft and "at capacity" in failure.lower():
        draft = True
        result = send(True)
        failure = zernio_failure(result)

    post = (result or {}).get("post") or {}
    platform = next(iter(post.get("platforms") or []), {})
    tiktok_url = platform.get("platformPostUrl") or platform.get("postUrl")
    status = str(platform.get("status") or post.get("status") or "").lower()

    # Keep whatever came back: without a post URL this is the only way to tell
    # a slideshow TikTok is still processing from one it quietly refused.
    slideshows.update_slideshow(slideshow_id, caption=caption, last_response=result)

    if failure or status in ("failed", "error", "rejected"):
        raise PublishError(f"TikTok rejected the slideshow: {failure or status}")

    if draft:
        # A draft isn't live — it's waiting in the TikTok app — so recording it
        # as posted would be a lie, and re-sending would pile up duplicates.
        slideshows.update_slideshow(slideshow_id, drafted_at=_now())
        return None

    if not tiktok_url:
        raise PublishError(
            "Zernio accepted the request but returned no TikTok post URL, so this can't be "
            f"confirmed as posted. Check your TikTok drafts and Zernio's dashboard. Response: {result}"
        )

    slideshows.update_slideshow(slideshow_id, posted_at=_now(), tiktok_url=tiktok_url)
    return tiktok_url


def parse_schedule_time(when: str, tz_name: str = None) -> tuple:
    """Turns a requested time into (timestamp_to_send, timezone_to_send).

    Accepts what a browser's datetime-local input gives ('2026-08-20T09:30',
    no offset — read in tz_name, or the machine's own zone) as well as a full
    ISO 8601 timestamp that already carries its own offset, in which case no
    separate timezone is sent."""
    try:
        moment = datetime.fromisoformat(when)
    except (TypeError, ValueError):
        raise PublishError(f"Couldn't read {when!r} as a date and time — use YYYY-MM-DDTHH:MM.")

    if moment.tzinfo is not None:
        if moment <= datetime.now(moment.tzinfo):
            raise PublishError(f"{when} is in the past — pick a time from now on.")
        return moment.isoformat(timespec="seconds"), None

    # Naive: compare against the same clock Zernio will schedule against.
    zone = None
    if tz_name:
        try:
            zone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise PublishError(f"Unknown timezone {tz_name!r}.")

    if moment <= (datetime.now(zone) if zone else datetime.now()).replace(tzinfo=None):
        raise PublishError(f"{when} is in the past — pick a time from now on.")

    return moment.isoformat(timespec="seconds"), tz_name


def schedule_pin(slug: str, when: str, tz_name: str = None) -> str:
    """Hands the pin to Zernio to publish at `when` instead of right away.

    Zernio holds it on their side, so the pin still goes out with this machine
    asleep. The landing page has to be live first — that part can't be deferred,
    since the scheduled Pin will link straight to it.

    Returns the scheduled timestamp as stored."""
    if load_pin(slug).get("posted_at"):
        raise PublishError(f"{slug} is already live on Pinterest — scheduling it would post it twice.")

    when_iso, timezone_sent = parse_schedule_time(when, tz_name)
    pin, result, board_id = _send_to_pinterest(slug, scheduled_for=when_iso, tz_name=timezone_sent)

    post = result.get("post") or {}
    pin["scheduled_for"] = when_iso
    pin["scheduled_timezone"] = timezone_sent
    pin["scheduled_post_id"] = post.get("_id") or post.get("id")
    pin["board_id"] = board_id
    save_pin(pin)

    return when_iso
