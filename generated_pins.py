"""Draining the generated-image pin queue.

Two independent jobs live here, both meant to be called repeatedly (a poll
loop, or a cron entry) rather than once:

sync_incoming_images()
    Matches a ChatGPT-finished PNG in generated-images/ back to the pin that's
    waiting for it, by filename stem — see config.py for the full folder
    layout. generated-images/ is a permanent archive that's never emptied, so
    .imagegen-state.json is what stops a repeat scan from redoing the match.

run_drip_schedule()
    Once turned on, hands the oldest not-yet-scheduled pin to Zernio every
    `interval_hours`, whatever template it is. This is on top of, not instead
    of, scheduling a pin for one specific time — that path (publishing.py's
    schedule_pin) is unchanged.
"""

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import (
    DRIP_SCHEDULE_FILE, FAILED_INPUTS_DIR, GENERATED_IMAGES_DIR,
    IMAGEGEN_STATE_FILE, INCOMING_CLOTHES_DIR, PROCESSED_INPUTS_DIR,
)
from pins import PinNotFoundError, _now, list_pins, load_pin, pin_dir, pin_status, save_pin
from pinterest.seo import SEOGenerationError, generate_seo_content
from publishing import PublishError, parse_schedule_time, schedule_pin

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")


class ImageGenError(RuntimeError):
    pass


# ---------- Matching generated images back to their pin ----------

def _load_state() -> dict:
    if IMAGEGEN_STATE_FILE.exists():
        return json.loads(IMAGEGEN_STATE_FILE.read_text())
    return {}


def _save_state(state: dict):
    IMAGEGEN_STATE_FILE.write_text(json.dumps(state, indent=2))


def _find_incoming(slug: str) -> Path:
    for ext in IMAGE_EXTENSIONS:
        candidate = INCOMING_CLOTHES_DIR / f"{slug}{ext}"
        if candidate.exists():
            return candidate
    return None


def sync_incoming_images(progress=None, board_names: list = None) -> list:
    """Matches every unconsumed file in generated-images/ to its pin. Returns
    the slugs newly matched.

    Generating SEO here rather than at link-submission time means it's only
    ever spent on pins that actually get a generated image, not every
    reference photo that gets abandoned along the way.
    """
    def report(message):
        if progress:
            progress(message)

    if not GENERATED_IMAGES_DIR.exists():
        return []

    state = _load_state()
    matched = []

    for image_path in sorted(GENERATED_IMAGES_DIR.iterdir()):
        if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        slug = image_path.stem
        if slug in state:
            continue  # already consumed on an earlier pass

        try:
            pin = load_pin(slug)
        except PinNotFoundError:
            continue  # not a slug we recognise — leave it rather than guess

        if pin.get("template") != "generated":
            continue
        if pin_status(pin) != "awaiting_image":
            continue  # already has a pin.png — don't clobber hand-placed work

        shutil.copy(image_path, pin_dir(slug) / "pin.png")

        # Same pipeline the product-collage template uses: a title/description/
        # hashtags/board pick from Claude, feeding the same board-selection and
        # posting code in publishing.py — nothing there needs to know which
        # template a pin came from.
        try:
            report("  writing SEO title, description and hashtags...")
            pin["seo"] = generate_seo_content(
                [pin.get("product_title") or pin.get("title1", "")],
                niche_hint=pin.get("title1", ""),
                board_names=board_names,
            )
        except SEOGenerationError as e:
            report(f"  SEO generation skipped: {e}")

        pin["published_at"] = _now()
        save_pin(pin)

        incoming = _find_incoming(slug)
        if incoming:
            PROCESSED_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.move(str(incoming), str(PROCESSED_INPUTS_DIR / incoming.name))

        state[slug] = {"status": "processed", "processedAt": _now()}
        matched.append(slug)
        report(f"Matched generated-images/{image_path.name} to '{slug}'.")

    if matched:
        _save_state(state)
    return matched


def mark_failed(slug: str) -> Path:
    """Moves a pending reference image out of the active queue by hand — for
    when the ChatGPT step was abandoned rather than just not done yet."""
    incoming = _find_incoming(slug)
    if not incoming:
        raise ImageGenError(f"No pending reference image for '{slug}' in incoming-clothes/.")

    FAILED_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    destination = FAILED_INPUTS_DIR / incoming.name
    shutil.move(str(incoming), str(destination))

    state = _load_state()
    state[slug] = {"status": "failed", "failedAt": _now()}
    _save_state(state)
    return destination


# ---------- Drip scheduling ----------

def _load_drip() -> dict:
    if DRIP_SCHEDULE_FILE.exists():
        return json.loads(DRIP_SCHEDULE_FILE.read_text())
    return {"enabled": False, "interval_hours": None, "next_slot": None, "timezone": None}


def _save_drip(config: dict):
    DRIP_SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DRIP_SCHEDULE_FILE.write_text(json.dumps(config, indent=2))


def drip_status() -> dict:
    return _load_drip()


def enable_drip(interval_hours: float, start_at: str = None, tz_name: str = None) -> dict:
    """Turns on interval posting: every interval_hours, the oldest
    published-but-unscheduled pin gets handed to Zernio for that slot.

    start_at sets the first slot explicitly; left out, the first slot is one
    interval from now. Changing the interval while already running keeps the
    next slot where it is — restarting from "now" would jump the queue.
    """
    if interval_hours <= 0:
        raise ImageGenError("interval_hours must be positive.")

    config = _load_drip()
    if start_at:
        next_slot, tz_name = parse_schedule_time(start_at, tz_name)
    elif not config.get("next_slot"):
        zone = ZoneInfo(tz_name) if tz_name else None
        next_slot = (datetime.now(zone) + timedelta(hours=interval_hours)).isoformat(timespec="seconds")
    else:
        next_slot = config["next_slot"]
        tz_name = config.get("timezone")

    config.update({"enabled": True, "interval_hours": interval_hours, "next_slot": next_slot, "timezone": tz_name})
    _save_drip(config)
    return config


def disable_drip() -> dict:
    config = _load_drip()
    config["enabled"] = False
    _save_drip(config)
    return config


def _advance(next_slot: str, interval_hours: float) -> str:
    return (datetime.fromisoformat(next_slot) + timedelta(hours=interval_hours)).isoformat(timespec="seconds")


def ready_queue() -> list:
    """Pins published but not yet scheduled or posted, oldest first — the
    drip feed's source, whatever template they are."""
    return sorted(
        (pin for pin in list_pins() if pin_status(pin) == "published"),
        key=lambda p: p.get("created_at") or "",
    )


def run_drip_schedule(progress=None) -> list:
    """Assigns the next slot(s) to whatever's waiting. Safe to call repeatedly
    (e.g. every poll): a pin that's already scheduled drops out of
    ready_queue(), so it's never double-booked.

    Returns [(slug, scheduled_for), ...] for whatever it just scheduled.
    """
    def report(message):
        if progress:
            progress(message)

    config = _load_drip()
    if not config.get("enabled"):
        return []

    ready = ready_queue()
    if not ready:
        return []

    next_slot_dt = datetime.fromisoformat(config["next_slot"])
    now = datetime.now(next_slot_dt.tzinfo)
    if next_slot_dt <= now:
        # Nothing was ready to fill the earlier slots (or the machine was
        # asleep) — catch up from now rather than handing Zernio a past time.
        next_slot_dt = now + timedelta(hours=config["interval_hours"])
        config["next_slot"] = next_slot_dt.isoformat(timespec="seconds")

    scheduled = []
    for pin in ready:
        try:
            when = schedule_pin(pin["slug"], config["next_slot"], tz_name=config.get("timezone"))
        except PublishError as e:
            report(f"  couldn't schedule '{pin['slug']}': {e}")
            continue

        report(f"Scheduled '{pin['slug']}' for {when}.")
        scheduled.append((pin["slug"], when))
        config["next_slot"] = _advance(config["next_slot"], config["interval_hours"])
        _save_drip(config)

    return scheduled
