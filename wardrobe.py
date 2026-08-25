"""The shared wardrobe behind Outfit Studio's random/combination generator.

Unlike an outfit pin's own uploaded cutouts (posts/<slug>/products, scoped to
that one pin — see pins.py), this wardrobe is one persistent closet:

    wardrobe-items/
      wardrobe.json     sections, items (incl. archived flag), saved recipes
      raw/itemN.<ext>   original upload
      cutout/itemN.png  background-removed cutout

Items are sorted into category folders, then repeatedly drawn from by the
combination generator (see generate_outfit_batch) — archiving an item takes it
out of that draw without deleting it.
"""

import itertools
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from config import WARDROBE_DIR

DEFAULT_WARDROBE_SECTIONS = (
    {"id": "tops", "label": "Tops"},
    {"id": "jumpers", "label": "Jumpers"},
    {"id": "jeans", "label": "Jeans"},
    {"id": "shoes", "label": "Shoes"},
    {"id": "belts", "label": "Belts"},
    {"id": "watches", "label": "Watches"},
    {"id": "fragrance", "label": "Fragrance"},
)
MAX_WARDROBE_SECTIONS = 12
MAX_WARDROBE_ITEMS = 500
# generate_outfit_batch runs synchronously (file copies + JSON, no network or
# ML calls), so the combination count it's asked to produce has to stay small
# enough to not block a request.
MAX_GENERATED_COMBINATIONS = 24


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _raw_dir() -> Path:
    return WARDROBE_DIR / "raw"


def _cutout_dir() -> Path:
    return WARDROBE_DIR / "cutout"


def load_wardrobe() -> dict:
    path = WARDROBE_DIR / "wardrobe.json"
    if not path.exists():
        return {
            "sections": [dict(section) for section in DEFAULT_WARDROBE_SECTIONS],
            "items": [],
            "recipes": [],
        }
    import json
    data = json.loads(path.read_text())
    data.setdefault("sections", [dict(section) for section in DEFAULT_WARDROBE_SECTIONS])
    data.setdefault("items", [])
    data.setdefault("recipes", [])
    return data


def save_wardrobe(data: dict) -> dict:
    import json
    WARDROBE_DIR.mkdir(parents=True, exist_ok=True)
    (WARDROBE_DIR / "wardrobe.json").write_text(json.dumps(data, indent=2))
    return data


def wardrobe_sections(data: dict) -> list:
    sections = data.get("sections")
    if not sections:
        return [dict(section) for section in DEFAULT_WARDROBE_SECTIONS]
    return [
        {"id": str(section.get("id") or "").strip(), "label": str(section.get("label") or "").strip()}
        for section in sections
        if section.get("id") and section.get("label")
    ]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "section"


def add_wardrobe_section(label: str) -> dict:
    data = load_wardrobe()
    label = (label or "").strip()[:40]
    if not label:
        raise ValueError("Give the new section a name.")
    sections = wardrobe_sections(data)
    if len(sections) >= MAX_WARDROBE_SECTIONS:
        raise ValueError(f"The wardrobe can have at most {MAX_WARDROBE_SECTIONS} sections.")
    base = _slugify(label)
    existing_ids = {section["id"] for section in sections}
    section_id = base
    suffix = 2
    while section_id in existing_ids:
        section_id = f"{base}-{suffix}"
        suffix += 1
    section = {"id": section_id, "label": label}
    sections.append(section)
    data["sections"] = sections
    save_wardrobe(data)
    return section


def delete_wardrobe_section(section_id: str, move_items_to: str = None):
    """Removes a section. Items filed under it move to `move_items_to`, or are
    deleted along with it when that's None — a section is a folder, so emptying
    it silently into nowhere would lose the cutouts without saying so."""
    data = load_wardrobe()
    sections = wardrobe_sections(data)
    if not any(section["id"] == section_id for section in sections):
        raise ValueError("That section no longer exists.")
    if len(sections) <= 1:
        raise ValueError("The wardrobe needs at least one section.")
    if move_items_to == section_id:
        raise ValueError("Items can't be moved into the section being deleted.")
    if move_items_to and not any(section["id"] == move_items_to for section in sections):
        raise ValueError("Unknown section to move items into.")

    doomed = [item for item in data.get("items", []) if item.get("category") == section_id]
    if move_items_to:
        for item in doomed:
            item["category"] = move_items_to
        data["sections"] = [section for section in sections if section["id"] != section_id]
        save_wardrobe(data)
    else:
        data["sections"] = [section for section in sections if section["id"] != section_id]
        save_wardrobe(data)
        for item in doomed:
            delete_wardrobe_item(item["id"])

    # A recipe naming a section that no longer exists would fail at generate
    # time with a confusing "unknown category", so prune them here instead.
    data = load_wardrobe()
    for recipe in data.get("recipes", []):
        recipe["combine"] = [category for category in recipe.get("combine", []) if category != section_id]
        recipe["random"] = {
            category: count for category, count in (recipe.get("random") or {}).items()
            if category != section_id
        }
    save_wardrobe(data)


def _next_item_index(items: list) -> int:
    used = []
    for item in items:
        match = re.fullmatch(r"item(\d+)\.png", item.get("cutout") or "")
        if match:
            used.append(int(match.group(1)))
    return max(used, default=0) + 1


def add_wardrobe_item(category: str, image_bytes: bytes, filename: str = "garment") -> dict:
    category = (category or "").strip().lower()
    if not image_bytes:
        raise ValueError("The uploaded image is empty.")

    data = load_wardrobe()
    if category not in {section["id"] for section in wardrobe_sections(data)}:
        raise ValueError(f"Unknown wardrobe category: {category}")
    items = data.setdefault("items", [])
    if len(items) >= MAX_WARDROBE_ITEMS:
        raise ValueError(f"The wardrobe can hold at most {MAX_WARDROBE_ITEMS} items.")

    from amazon.background_removal import remove_background  # rembg is expensive to import

    raw_dir, cutout_dir = _raw_dir(), _cutout_dir()
    raw_dir.mkdir(parents=True, exist_ok=True)
    cutout_dir.mkdir(parents=True, exist_ok=True)

    index = _next_item_index(items)
    (raw_dir / f"item{index}.upload").write_bytes(image_bytes)
    cutout_name = f"item{index}.png"
    (cutout_dir / cutout_name).write_bytes(remove_background(image_bytes))

    display_name = Path(filename or "garment").stem[:100] or "garment"
    item = {
        "id": uuid.uuid4().hex,
        "title": display_name,
        "category": category,
        "cutout": cutout_name,
        "archived": False,
        "v": 1,
        "created_at": _now(),
    }
    items.append(item)
    save_wardrobe(data)
    return item


def add_wardrobe_remote_item(category: str, image_url: str) -> dict:
    from amazon.images import download_remote_image

    image_bytes = download_remote_image(image_url)
    filename = Path(urlparse(image_url).path).name or "web-garment.jpg"
    return add_wardrobe_item(category, image_bytes, filename)


def _find_item(data: dict, item_id: str) -> dict:
    item = next((entry for entry in data.get("items", []) if entry.get("id") == item_id), None)
    if not item:
        raise ValueError("That wardrobe item no longer exists.")
    return item


def move_wardrobe_item(item_id: str, category: str) -> dict:
    data = load_wardrobe()
    valid_categories = {section["id"] for section in wardrobe_sections(data)}
    if category not in valid_categories:
        raise ValueError(f"Unknown wardrobe category: {category}")
    item = _find_item(data, item_id)
    item["category"] = category
    save_wardrobe(data)
    return item


def delete_wardrobe_item(item_id: str):
    data = load_wardrobe()
    items = data.get("items", [])
    item = _find_item(data, item_id)

    cutout_name = Path(item.get("cutout") or "").name
    if cutout_name:
        (_cutout_dir() / cutout_name).unlink(missing_ok=True)
        raw_stem = Path(cutout_name).stem
        for raw_path in _raw_dir().glob(f"{raw_stem}.*"):
            raw_path.unlink(missing_ok=True)

    data["items"] = [entry for entry in items if entry.get("id") != item_id]
    save_wardrobe(data)


def archive_wardrobe_items(item_ids: list) -> list:
    data = load_wardrobe()
    ids = set(item_ids or [])
    changed = []
    for item in data.get("items", []):
        if item.get("id") in ids and not item.get("archived"):
            item["archived"] = True
            changed.append(item)
    save_wardrobe(data)
    return changed


def restore_wardrobe_items(item_ids: list) -> list:
    data = load_wardrobe()
    ids = set(item_ids or [])
    changed = []
    for item in data.get("items", []):
        if item.get("id") in ids and item.get("archived"):
            item["archived"] = False
            changed.append(item)
    save_wardrobe(data)
    return changed


def active_items(data: dict, category: str) -> list:
    """Non-archived items in a category — the only pool the generator draws from."""
    return [item for item in data.get("items", []) if item.get("category") == category and not item.get("archived")]


def add_recipe(name: str, combine: list, random_spec: dict) -> dict:
    data = load_wardrobe()
    name = (name or "").strip()[:60]
    if not name:
        raise ValueError("Give the recipe a name.")
    recipe = {
        "id": uuid.uuid4().hex,
        "name": name,
        "combine": list(combine or []),
        "random": {str(category): int(count) for category, count in (random_spec or {}).items()},
    }
    data.setdefault("recipes", []).append(recipe)
    save_wardrobe(data)
    return recipe


def delete_recipe(recipe_id: str):
    data = load_wardrobe()
    data["recipes"] = [recipe for recipe in data.get("recipes", []) if recipe.get("id") != recipe_id]
    save_wardrobe(data)


# ---------- Combination generator ----------

def generate_outfit_batch(title1: str, title2: str, combine: list, random_spec: dict) -> list:
    """Builds one outfit pin per combination of the `combine` categories,
    filling every other named category (`random_spec`: {category: count}) with
    an independent random pick per pin. Returns the created pins."""
    import pins as pins_module

    data = load_wardrobe()
    sections = wardrobe_sections(data)
    section_labels = {section["id"]: section["label"] for section in sections}
    combine = [str(category) for category in (combine or [])]
    random_spec = {str(category): int(count) for category, count in (random_spec or {}).items() if int(count) > 0}

    for category in combine:
        if category not in section_labels:
            raise ValueError(f"Unknown wardrobe category: {category}")
    for category in random_spec:
        if category not in section_labels:
            raise ValueError(f"Unknown wardrobe category: {category}")

    combine_pools = []
    for category in combine:
        pool = active_items(data, category)
        if not pool:
            raise ValueError(f"No available {section_labels[category].lower()} to combine.")
        combine_pools.append(pool)

    for category, count in random_spec.items():
        if len(active_items(data, category)) < count:
            raise ValueError(f"Not enough available {section_labels[category].lower()} for that recipe.")

    combinations = list(itertools.product(*combine_pools)) if combine_pools else [()]
    if len(combinations) > MAX_GENERATED_COMBINATIONS:
        raise ValueError(
            f"That would generate {len(combinations)} outfits — narrow the combined "
            f"categories or archive some items first (max {MAX_GENERATED_COMBINATIONS} per batch)."
        )

    created = []
    for combo in combinations:
        items = list(combo)
        for category, count in random_spec.items():
            items.extend(random.sample(active_items(data, category), count))
        pin = pins_module.create_outfit_pin_from_items(
            title1, title2, items, sections,
            generated_from={"combine": combine, "random": list(random_spec)},
        )
        created.append(pin)
    return created
