"""TikTok slideshow batches — groups generated outfit pins into carousels.

An outfit pin posts to Pinterest on its own, but TikTok takes them three at a
time as a photo carousel. Batching isn't arbitrary: within one slideshow the
same top must never appear twice (three slides of the same t-shirt isn't a
outfit reel), and the same bottom shouldn't either — though that one gives way
when the recipe makes it impossible, e.g. combining tops against a single pair
of jeans. A batch where that happened is flagged so it's visible.

Batches live in .slideshows.json as lists of pin slugs; the pins themselves
stay the source of truth for their own images and state.
"""

import json
import uuid
from collections import Counter
from datetime import datetime, timezone

from config import SLIDESHOWS_FILE
from pins import BOTTOM_CATEGORIES, TOP_CATEGORIES, PinNotFoundError, load_pin, pin_dir

SLIDESHOW_SIZE = 3
MAX_SLIDESHOW_SIZE = 35  # TikTok's cap on photos per carousel

# What a slideshow goes out with unless the caption box is edited.
DEFAULT_TIKTOK_CAPTION = (
    "outfit inspiration for you "
    "#outfitinspo #outfitsideas #boysfashion #mensoutfits #mystyle"
)


class SlideshowError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def garment_id(pin: dict, categories: set):
    """The wardrobe item filling one slot of an outfit — what two pins have to
    differ on to belong in the same slideshow."""
    for product in pin.get("products", []):
        if product.get("category") in categories:
            return product.get("wardrobeItemId") or product.get("id")
    return None


def plan_batches(pins: list, size: int = SLIDESHOW_SIZE) -> list:
    """Groups pins into batches of `size`, never repeating a top within a batch
    and avoiding a repeated bottom where it can. Returns a list of pin lists.

    Each slot goes to the eligible outfit whose top is the most plentiful in
    what's left. Taking the scarce ones first is what strands them: four tops
    over two pairs of jeans packs as 3+3+2 this way, but 3+3+1+1 if the batches
    are just filled in order.
    """
    remaining = list(pins)
    batches = []
    while remaining:
        batch, used_tops, used_bottoms = [], set(), set()
        while len(batch) < size:
            top_counts = Counter(garment_id(pin, TOP_CATEGORIES) for pin in remaining)
            bottom_counts = Counter(garment_id(pin, BOTTOM_CATEGORIES) for pin in remaining)

            # First pass holds both rules; the second gives up on bottoms only,
            # which is what lets a one-pair-of-jeans recipe batch at all.
            pick = None
            for allow_repeat_bottom in (False, True):
                eligible = [
                    pin for pin in remaining
                    if garment_id(pin, TOP_CATEGORIES) not in used_tops
                    and (allow_repeat_bottom or garment_id(pin, BOTTOM_CATEGORIES) not in used_bottoms)
                ]
                if eligible:
                    pick = max(eligible, key=lambda pin: (
                        top_counts[garment_id(pin, TOP_CATEGORIES)],
                        bottom_counts[garment_id(pin, BOTTOM_CATEGORIES)],
                    ))
                    break
            if pick is None:
                break

            remaining.remove(pick)
            batch.append(pick)
            top = garment_id(pick, TOP_CATEGORIES)
            bottom = garment_id(pick, BOTTOM_CATEGORIES)
            if top is not None:
                used_tops.add(top)
            if bottom is not None:
                used_bottoms.add(bottom)

        # The first pin of a batch always qualifies (both sets start empty), so
        # `remaining` always shrinks and this terminates.
        batches.append(batch)
    return batches


def batch_warnings(pins: list) -> list:
    """What a batch had to compromise on, for the UI to surface."""
    warnings = []
    bottoms = [garment_id(pin, BOTTOM_CATEGORIES) for pin in pins]
    bottoms = [bottom for bottom in bottoms if bottom is not None]
    if len(bottoms) != len(set(bottoms)):
        warnings.append("Repeats a bottom — not enough different ones to go round.")
    if len(pins) < SLIDESHOW_SIZE:
        warnings.append(f"Only {len(pins)} slide{'s' if len(pins) != 1 else ''} — a full batch is {SLIDESHOW_SIZE}.")
    return warnings


# ---------- Storage ----------

def load_slideshows() -> dict:
    if not SLIDESHOWS_FILE.exists():
        return {"version": 1, "slideshows": []}
    data = json.loads(SLIDESHOWS_FILE.read_text())
    data.setdefault("slideshows", [])
    return data


def save_slideshows(data: dict) -> dict:
    SLIDESHOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SLIDESHOWS_FILE.write_text(json.dumps(data, indent=2))
    return data


def _new_slideshow(slugs: list) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "slugs": list(slugs),
        "caption": "",
        "created_at": _now(),
        "posted_at": None,
        "drafted_at": None,
        "done_at": None,
        "tiktok_url": None,
    }


def create_batches_for(slugs: list, size: int = SLIDESHOW_SIZE) -> list:
    """Plans slideshows over the given pins and stores them. Any pin already in
    a slideshow is left where it is rather than being duplicated into a second."""
    data = load_slideshows()
    already_batched = {slug for show in data["slideshows"] for slug in show["slugs"]}

    pins = []
    for slug in slugs:
        if slug in already_batched:
            continue
        try:
            pins.append(load_pin(slug))
        except PinNotFoundError:
            continue

    created = [_new_slideshow([pin["slug"] for pin in batch]) for batch in plan_batches(pins, size)]
    data["slideshows"].extend(created)
    save_slideshows(data)
    return created


def set_arrangement(arrangement: list) -> list:
    """Replaces the stored grouping wholesale — what the UI sends after a pin
    is dragged between batches. arrangement is [{"id": ..., "slugs": [...]}];
    unknown ids become new slideshows and emptied ones are dropped. Posted
    slideshows are immutable: their grouping is already out in the world."""
    data = load_slideshows()
    by_id = {show["id"]: show for show in data["slideshows"]}
    posted = {show["id"]: show for show in data["slideshows"] if show.get("posted_at")}

    seen = set()
    rebuilt = []
    for entry in arrangement:
        slugs = [slug for slug in (entry.get("slugs") or []) if slug not in seen]
        seen.update(slugs)
        existing = by_id.get(entry.get("id"))
        if existing and existing.get("posted_at"):
            rebuilt.append(existing)  # keep exactly as posted
            continue
        if not slugs:
            continue
        if len(slugs) > MAX_SLIDESHOW_SIZE:
            raise SlideshowError(f"A slideshow can hold at most {MAX_SLIDESHOW_SIZE} slides.")
        if existing:
            rebuilt.append({**existing, "slugs": slugs})
        else:
            rebuilt.append(_new_slideshow(slugs))

    for show_id, show in posted.items():
        if not any(entry["id"] == show_id for entry in rebuilt):
            rebuilt.append(show)

    data["slideshows"] = rebuilt
    save_slideshows(data)
    return rebuilt


def get_slideshow(slideshow_id: str) -> dict:
    show = next((s for s in load_slideshows()["slideshows"] if s["id"] == slideshow_id), None)
    if not show:
        raise SlideshowError("That slideshow no longer exists.")
    return show


def update_slideshow(slideshow_id: str, **fields) -> dict:
    data = load_slideshows()
    show = next((s for s in data["slideshows"] if s["id"] == slideshow_id), None)
    if not show:
        raise SlideshowError("That slideshow no longer exists.")
    show.update(fields)
    save_slideshows(data)
    return show


def delete_slideshow(slideshow_id: str):
    data = load_slideshows()
    data["slideshows"] = [s for s in data["slideshows"] if s["id"] != slideshow_id]
    save_slideshows(data)


def prune_missing_pins() -> dict:
    """Drops slugs whose pin has been deleted, and any slideshow left empty."""
    data = load_slideshows()
    kept = []
    for show in data["slideshows"]:
        slugs = [slug for slug in show["slugs"] if (pin_dir(slug) / "pin.json").exists()]
        if slugs or show.get("posted_at"):
            kept.append({**show, "slugs": slugs})
    data["slideshows"] = kept
    return save_slideshows(data)


def slideshow_summary(show: dict) -> dict:
    """The shape the UI needs: resolved slides, warnings and readiness."""
    slides = []
    for slug in show["slugs"]:
        try:
            pin = load_pin(slug)
        except PinNotFoundError:
            continue
        slides.append({
            "slug": slug,
            "titles": [product.get("title", "") for product in pin.get("products", [])],
            "hasImage": (pin_dir(slug) / "pin.png").exists(),
        })

    pins = [load_pin(slide["slug"]) for slide in slides]
    unrendered = [slide["slug"] for slide in slides if not slide["hasImage"]]
    return {
        "id": show["id"],
        "slides": slides,
        # The default is served rather than hardcoded in the page, so the box
        # shows exactly what would go out if it isn't touched.
        "caption": show.get("caption") or DEFAULT_TIKTOK_CAPTION,
        "postedAt": show.get("posted_at"),
        "draftedAt": show.get("drafted_at"),
        "doneAt": show.get("done_at"),
        "tiktokUrl": show.get("tiktok_url"),
        "warnings": batch_warnings(pins),
        "unrendered": unrendered,
        "ready": bool(slides) and not unrendered and not show.get("drafted_at") and not show.get("done_at"),
    }


def list_slideshows(include_done: bool = False) -> list:
    """The dashboard only wants outstanding work, so a slideshow that's been
    posted from the TikTok app is kept on record — which stops its outfits
    being batched again — but dropped from the list."""
    shows = prune_missing_pins()["slideshows"]
    if not include_done:
        shows = [show for show in shows if not show.get("done_at")]
    return [slideshow_summary(show) for show in shows]
