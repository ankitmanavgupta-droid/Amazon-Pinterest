"""Pin state model and build pipeline.

Each pin lives in posts/<slug>/ with pin.json as its single source of truth:

    posts/<slug>/
      pin.json          titles, products (+ affiliate links), layout, SEO, status
      raw/productN.jpg  original product photo
      cutout/productN.png  background removed, cropped
      pin.png           the rendered pin image (after the editor saves)

Status is derived from what's on disk plus the timestamps in pin.json, so
there's no separate state to keep in sync.
"""

import html
import json
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from amazon.affiliate import build_affiliate_link, extract_asin, region_from_url, resolve_url
from amazon.canopy import fetch_product
from amazon.images import download_image, download_remote_image
from config import (
    AFFILIATE_TAGS_BY_REGION, GITHUB_PAGES_BASE_URL, INCOMING_CLOTHES_DIR,
    MAX_PRODUCTS, POSTS_DIR, SHOP_DIR, WARDROBE_DIR,
)
from pinterest.seo import SEOGenerationError, generate_seo_content

# The look the pin builder opens with; the editor can override any of it.
DEFAULT_LAYOUT = {
    "background": "#f6d9df",
    "brand": "Home Studio",
    "titleFont1": "Playfair Display",
    "titleFont2": "Alex Brush",
    "titleSize1": 55,
    "titleSize2": 81,
    "caption": "",
    "captionSize": 13,
}

DEFAULT_OUTFIT_SECTIONS = (
    {"id": "tops", "label": "Tops"},
    {"id": "jackets", "label": "Jackets"},
    {"id": "jeans", "label": "Jeans"},
    {"id": "shoes", "label": "Shoes"},
    {"id": "accessories", "label": "Accessories"},
)
MAX_OUTFIT_ASSETS = 40
MAX_OUTFIT_SECTIONS = 12


class PinNotFoundError(KeyError):
    pass


class ProductFetchError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "pin"


def next_slug(title1: str, title2: str = "") -> str:
    """'<slugified titles>-N', continuing from the highest N already in posts/
    so re-running the same titles never overwrites an earlier pin."""
    base = slugify(f"{title1} {title2}")
    highest = 0
    if POSTS_DIR.exists():
        pattern = re.compile(rf"^{re.escape(base)}-(\d+)$")
        for entry in POSTS_DIR.iterdir():
            match = pattern.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"{base}-{highest + 1}"


def pin_dir(slug: str) -> Path:
    return POSTS_DIR / slug


def load_pin(slug: str) -> dict:
    path = pin_dir(slug) / "pin.json"
    if not path.exists():
        raise PinNotFoundError(slug)
    return json.loads(path.read_text())


def save_pin(pin: dict) -> dict:
    directory = pin_dir(pin["slug"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pin.json").write_text(json.dumps(pin, indent=2))
    return pin


def pin_status(pin: dict) -> str:
    if pin.get("posted_at"):
        return "posted"
    if pin.get("scheduled_for"):
        return "scheduled"
    if pin.get("published_at"):
        return "published"
    if (pin_dir(pin["slug"]) / "pin.png").exists():
        return "rendered"
    # A generated-template pin has no editor step, so 'draft' (which implies
    # "open it and build something") would be misleading — it's just waiting
    # on the ChatGPT step that happens outside this app.
    if pin.get("template") == "generated":
        return "awaiting_image"
    return "draft"


def has_landing_page(pin: dict) -> bool:
    """Only the product collage gets a page of its own on GitHub Pages. Its
    whole point is several products being separately clickable inside one
    image, which needs somewhere to host the hotspots. An outfit or a
    generated image is a single picture — it links straight at a product, so a
    page in between would just be a redirect."""
    return pin.get("template", "product") == "product"


def destination_link(pin: dict):
    """Where this pin's Pinterest post should send people, or None if nowhere.

    A collage points at its landing page. Anything else points at the product
    it came from, or — for an outfit whose garments carry no links — nothing at
    all, and the Pin goes out without one.
    """
    if has_landing_page(pin):
        return f"{GITHUB_PAGES_BASE_URL}/{pin['slug']}.html"
    if pin.get("source_link"):
        return pin["source_link"]
    layers = (pin.get("layout") or {}).get("layers") or []
    for entry in [*layers, *(pin.get("products") or [])]:
        if entry.get("url"):
            return entry["url"]
    return None


def pin_summary(pin: dict) -> dict:
    """The shape the dashboard needs — status plus the handful of fields it shows."""
    slug = pin["slug"]
    template = pin.get("template", "product")

    live_url = destination_link(pin) if pin.get("published_at") else None

    return {
        "slug": slug,
        "template": template,
        "title1": pin.get("title1", ""),
        "title2": pin.get("title2", ""),
        "status": pin_status(pin),
        "productCount": len(pin.get("products", [])),
        "seoTitle": (pin.get("seo") or {}).get("title", ""),
        "pinUrl": pin.get("pin_url"),
        "scheduledFor": pin.get("scheduled_for"),
        "scheduledTimezone": pin.get("scheduled_timezone"),
        "liveUrl": live_url,
        "sourceLink": pin.get("source_link"),
        "createdAt": pin.get("created_at"),
        "hasImage": (pin_dir(slug) / "pin.png").exists(),
    }


def list_pins() -> list:
    if not POSTS_DIR.exists():
        return []
    pins = []
    for directory in POSTS_DIR.iterdir():
        if (directory / "pin.json").exists():
            pins.append(load_pin(directory.name))
    pins.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return pins


def delete_pin(slug: str):
    """Removes the local working files. Anything already pushed to GitHub Pages
    or posted to Pinterest stays live — those can't be undone from here."""
    import shutil

    directory = pin_dir(slug)
    if directory.exists():
        shutil.rmtree(directory)


# ---------- Build pipeline ----------

def build_regional_urls(resolved_url: str) -> tuple:
    """Affiliate link per region we hold a tag for. Returns (urls_by_region,
    original_region, other_region_to_check) — the caller does the actual
    cross-marketplace lookup so it can be folded into one Canopy request."""
    original_region = region_from_url(resolved_url)
    tag = AFFILIATE_TAGS_BY_REGION.get(original_region)

    other_region = next(
        (region for region, other_tag in AFFILIATE_TAGS_BY_REGION.items() if region != original_region and other_tag),
        None,
    )
    urls = {original_region: build_affiliate_link(resolved_url, tag)} if tag else {}
    return urls, original_region, other_region


def resolve_product(url: str) -> dict:
    """Resolves one Amazon URL to its title, region-aware affiliate links, and
    the raw product photo bytes. Shared by the product-collage build pipeline
    (process_product, below) and the reference-image path for the
    generated-image template (create_generated_pin)."""
    resolved_url = resolve_url(url)
    asin = extract_asin(resolved_url)
    regional_urls, original_region, other_region = build_regional_urls(resolved_url)

    details = fetch_product(resolved_url, asin=asin, other_region=other_region)

    if details["regional_url"] and other_region:
        other_tag = AFFILIATE_TAGS_BY_REGION.get(other_region)
        if other_tag:
            regional_urls[other_region] = build_affiliate_link(details["regional_url"], other_tag)

    if not regional_urls:
        raise ProductFetchError(f"No affiliate tag configured for region '{original_region}'")

    return {
        "title": details["title"],
        "url": regional_urls.get(original_region) or next(iter(regional_urls.values())),
        "regionalUrls": regional_urls,
        "image_bytes": download_image(details["image_url"]),
    }


def process_product(url: str, index: int, directory: Path) -> dict:
    """Resolves one product URL into affiliate links + a background-removed cutout."""
    from amazon.background_removal import remove_background  # lazy: rembg is slow to import

    resolved = resolve_product(url)

    (directory / "raw").mkdir(parents=True, exist_ok=True)
    (directory / "raw" / f"product{index}.jpg").write_bytes(resolved["image_bytes"])

    cutout_bytes = remove_background(resolved["image_bytes"])
    (directory / "cutout").mkdir(parents=True, exist_ok=True)
    cutout_name = f"product{index}.png"
    (directory / "cutout" / cutout_name).write_bytes(cutout_bytes)

    return {
        "title": resolved["title"],
        "url": resolved["url"],
        "regionalUrls": resolved["regionalUrls"],
        "cutout": cutout_name,
    }


def build_pin(title1: str, title2: str = "", urls: list = None, board_names: list = None, progress=None) -> dict:
    """Runs the full build for one pin: products -> cutouts -> SEO -> pin.json.

    progress(message) is called as each step completes so callers (the web UI's
    job runner, or the CLI) can show what's happening.
    """
    def report(message):
        if progress:
            progress(message)

    urls = (urls or [])[:MAX_PRODUCTS]
    slug = next_slug(title1, title2)
    directory = pin_dir(slug)
    directory.mkdir(parents=True, exist_ok=True)

    products = []
    errors = []
    for index, url in enumerate(urls, start=1):
        report(f"Fetching product {index} of {len(urls)}...")
        try:
            products.append(process_product(url, index, directory))
        except Exception as e:
            errors.append(f"Product {index}: {e}")
            report(f"  skipped product {index}: {e}")

    if not products:
        delete_pin(slug)
        raise ProductFetchError("; ".join(errors) or "No products could be fetched")

    seo = {}
    try:
        report("Writing SEO title, description and hashtags...")
        seo = generate_seo_content(
            [p["title"] for p in products if p["title"]],
            niche_hint=f"{title1} {title2}".strip(),
            board_names=board_names,
        )
    except SEOGenerationError as e:
        errors.append(f"SEO: {e}")
        report(f"  SEO generation skipped: {e}")

    layout = dict(DEFAULT_LAYOUT)
    layout["caption"] = seo.get("description", "")

    pin = {
        "slug": slug,
        "title1": title1,
        "title2": title2,
        "created_at": _now(),
        "products": products,
        "seo": seo,
        "layout": layout,
        "warnings": errors,
        "published_at": None,
        "posted_at": None,
        "pin_url": None,
    }
    save_pin(pin)
    report(f"Built {slug} with {len(products)} product(s).")
    return pin


def build_pins_batch(title1: str, title2: str = "", url_groups: list = None,
                      board_names: list = None, progress=None) -> dict:
    """Builds several product-collage pins in one run, all sharing title1/title2 —
    each group in url_groups becomes its own pin (its own slug, via next_slug's
    usual -N suffix). One bad group is reported and skipped rather than losing
    the rest of the batch, same as the CLI's file-batch mode in build_pin.py.

    Returns {"pins": [...], "errors": [...]}."""
    def report(message):
        if progress:
            progress(message)

    url_groups = [g for g in (url_groups or []) if g]
    built = []
    errors = []
    for index, urls in enumerate(url_groups, start=1):
        report(f"--- Pin {index} of {len(url_groups)} ---")
        try:
            built.append(build_pin(title1, title2, urls, board_names=board_names, progress=report))
        except Exception as e:
            errors.append(f"Pin {index}: {e}")
            report(f"  pin {index} failed: {e}")

    return {"pins": built, "errors": errors}


# ---------- Outfit builder (uploaded wardrobe cutouts) ----------

def create_outfit_pin(title1: str, title2: str = "") -> dict:
    """Creates an empty outfit workspace. Garment photos are uploaded later
    and converted to transparent cutouts by :func:`add_outfit_asset`."""
    title1 = (title1 or "Outfit").strip()
    title2 = (title2 or "Edit").strip()
    slug = next_slug(title1, title2)
    layout = {
        "backgroundChoice": "white",
        "title1": title1,
        "title2": title2,
        "layers": [],
    }
    pin = {
        "slug": slug,
        "template": "outfit",
        "title1": title1,
        "title2": title2,
        "created_at": _now(),
        "outfit_sections": [dict(section) for section in DEFAULT_OUTFIT_SECTIONS],
        "products": [],
        "seo": {},
        "layout": layout,
        "warnings": [],
        "published_at": None,
        "posted_at": None,
        "pin_url": None,
    }
    return save_pin(pin)


def outfit_sections(pin: dict) -> list:
    """Returns this outfit's sections, including defaults for older saved pins."""
    sections = pin.get("outfit_sections")
    if not sections:
        return [dict(section) for section in DEFAULT_OUTFIT_SECTIONS]
    return [
        {"id": str(section.get("id") or "").strip(), "label": str(section.get("label") or "").strip()}
        for section in sections
        if section.get("id") and section.get("label")
    ]


def add_outfit_section(slug: str, label: str) -> dict:
    pin = load_pin(slug)
    if pin.get("template") != "outfit":
        raise ValueError("Sections can only be added to an outfit pin.")
    label = (label or "").strip()[:40]
    if not label:
        raise ValueError("Give the new section a name.")
    sections = outfit_sections(pin)
    if len(sections) >= MAX_OUTFIT_SECTIONS:
        raise ValueError(f"An outfit can have at most {MAX_OUTFIT_SECTIONS} sections.")
    base = slugify(label)
    existing_ids = {section["id"] for section in sections}
    section_id = base
    suffix = 2
    while section_id in existing_ids:
        section_id = f"{base}-{suffix}"
        suffix += 1
    section = {"id": section_id, "label": label}
    sections.append(section)
    pin["outfit_sections"] = sections
    save_pin(pin)
    return section


def _next_product_index(products: list) -> int:
    used_indexes = []
    for existing in products:
        match = re.fullmatch(r"product(\d+)\.png", existing.get("cutout") or "")
        if match:
            used_indexes.append(int(match.group(1)))
    return max(used_indexes, default=0) + 1


def add_outfit_asset(slug: str, category: str, image_bytes: bytes, filename: str = "garment") -> dict:
    """Background-removes one uploaded wardrobe image and adds it to a pin.

    Product-style storage is deliberate: it lets the existing cutout endpoint,
    landing-page hotspot format and publisher keep working for outfit pins.
    """
    category = (category or "").strip().lower()
    if not image_bytes:
        raise ValueError("The uploaded image is empty.")

    pin = load_pin(slug)
    if pin.get("template") != "outfit":
        raise ValueError("Garments can only be uploaded to an outfit pin.")
    if category not in {section["id"] for section in outfit_sections(pin)}:
        raise ValueError(f"Unknown outfit category: {category}")
    products = pin.setdefault("products", [])
    if len(products) >= MAX_OUTFIT_ASSETS:
        raise ValueError(f"An outfit workspace can hold at most {MAX_OUTFIT_ASSETS} uploaded items.")

    from amazon.background_removal import remove_background  # rembg is expensive to import

    directory = pin_dir(slug)
    raw_dir = directory / "raw"
    cutout_dir = directory / "cutout"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cutout_dir.mkdir(parents=True, exist_ok=True)
    index = _next_product_index(products)
    (raw_dir / f"product{index}.upload").write_bytes(image_bytes)

    cutout_name = f"product{index}.png"
    (cutout_dir / cutout_name).write_bytes(remove_background(image_bytes))
    display_name = Path(filename or "garment").stem[:100] or "garment"
    product = {
        "id": uuid.uuid4().hex,
        "title": display_name,
        "category": category,
        "url": "",
        "regionalUrls": None,
        "cutout": cutout_name,
    }
    products.append(product)
    save_pin(pin)
    return product


def add_outfit_remote_asset(slug: str, category: str, image_url: str) -> dict:
    """Downloads a web-dragged image and runs the normal outfit cutout pipeline."""
    image_bytes = download_remote_image(image_url)
    filename = Path(urlparse(image_url).path).name or "web-garment.jpg"
    return add_outfit_asset(slug, category, image_bytes, filename)


# The flat-lay these pins are modelled on: the garments own the left column at
# a bit over half the frame — they're the subject, so they're by far the
# largest things in it — and the accessories run down the right in a fixed
# order, headphones highest and shoes last, with fragrance and watch paired on
# one row. Positions stay fully draggable/resizable in the editor afterwards.
# Mirrored in templates/outfit_builder.html's computeArrangement, for the
# "Arrange outfit" button and for items added by hand.
# The canvas is 9:16 — TikTok's full-screen carousel shape — so a slideshow
# fills the phone edge to edge with nothing added around it. 540x960 doubles
# to exactly TikTok's 1080x1920, so the export never resamples.
CANVAS_W, CANVAS_H = 540, 960
LAYOUT_TOP_MARGIN, LAYOUT_BOTTOM_MARGIN = 38, 938

# The garments run nearly the full height of the frame — top and jeans stacked
# with barely a gap — which is what makes the flat-lay read as an outfit rather
# than a product grid.
LEFT_ZONE_X, LEFT_ZONE_W = 11, 324          # 60% of the frame
LEFT_TOP_SLOT = {"x": LEFT_ZONE_X, "y": 38, "w": LEFT_ZONE_W, "h": 345}
LEFT_BOTTOM_SLOT = {"x": 24, "y": 390, "w": 294, "h": 548}

# The accessories are deliberately small beside them, and spaced rather than
# stretched to fill the column.
RIGHT_ZONE_X, RIGHT_ZONE_W = 340, 173
MAX_RIGHT_ROW_GAP = 70
PAIR_GAP = 17

# Which wardrobe categories count as the outfit's top and bottom garment. Used
# for the layout, and by slideshows.py to keep a TikTok batch from repeating
# the same top (or, where it can, the same bottom).
TOP_CATEGORIES = {"tops", "jumpers", "jackets"}
BOTTOM_CATEGORIES = {"jeans"}

# Down the right column: (categories, height as a fraction of the frame, width
# as a fraction of the column). A tuple shares one row side by side. These are
# absolute sizes rather than shares of the space, so an accessory stays small
# whether there are three of them or six.
RIGHT_COLUMN = (
    ("headphones", 0.202, 1.0),
    ("glasses", 0.072, 0.95),
    ("belts", 0.072, 0.80),
    (("fragrance", "watches"), 0.144, 1.0),
    ("rings", 0.05, 0.9),
    ("accessories", 0.05, 0.9),
    ("shoes", 0.20, 1.0),
)

# Sections get named freely, and "+ Section" suffixes an id it has seen before
# (headphones-2), so slots are matched on a normalised name rather than an
# exact id — otherwise a renamed section silently loses its place.
_SLOT_ALIASES = {
    "top": "tops", "tshirts": "tops", "tees": "tops", "shirts": "tops",
    "jumper": "jumpers", "knitwear": "jumpers", "jackets": "jackets", "jacket": "jackets",
    "jean": "jeans", "denim": "jeans", "trousers": "jeans", "bottoms": "jeans", "pants": "jeans",
    "shoe": "shoes", "trainers": "shoes", "sneakers": "shoes", "boots": "shoes",
    "belt": "belts", "watch": "watches",
    "perfume": "fragrance", "fragrances": "fragrance", "aftershave": "fragrance", "scent": "fragrance",
    "glass": "glasses", "sunglasses": "glasses", "eyewear": "glasses", "specs": "glasses",
    "headphone": "headphones", "earphones": "headphones", "earbuds": "headphones", "airpods": "headphones",
}


def slot_key(category: str) -> str:
    """The layout slot a section maps to, ignoring the '-2' the app appends to
    a duplicate section name and the obvious synonyms."""
    key = re.sub(r"-\d+$", "", str(category or "").strip().lower())
    return _SLOT_ALIASES.get(key, key)


def _stack(indexes: list, slot: dict) -> dict:
    """Several items in one slot fan out slightly rather than stacking exactly."""
    return {
        index: {
            "x": slot["x"] + position * 14,
            "y": slot["y"] + position * 10,
            "w": slot["w"], "h": slot["h"],
        }
        for position, index in enumerate(indexes)
    }


def _arrange_wardrobe_layout(categories: list) -> list:
    """categories: one entry per product, in the pin's product order. Returns
    a same-length list of {x, y, w, h} slots for that arrangement."""
    keys = [slot_key(category) for category in categories]
    by_key = {}
    for index, key in enumerate(keys):
        by_key.setdefault(key, []).append(index)

    rects = {}
    for key, indexes in by_key.items():
        if key in TOP_CATEGORIES:
            rects.update(_stack(indexes, LEFT_TOP_SLOT))
        elif key in BOTTOM_CATEGORIES:
            rects.update(_stack(indexes, LEFT_BOTTOM_SLOT))

    # Right column: only rows that are actually present take up space.
    rows = []
    for entry, height_fraction, width_fraction in RIGHT_COLUMN:
        members = (entry,) if isinstance(entry, str) else entry
        present = [member for member in members if member in by_key]
        if present:
            rows.append((present, height_fraction, width_fraction))
    leftover = sorted(
        key for key in by_key
        if key not in TOP_CATEGORIES and key not in BOTTOM_CATEGORIES
        and not any(key in members for members, _, _ in rows)
    )
    # Anything unrecognised goes above the shoes, which stay the last row.
    for key in leftover:
        rows.insert(max(0, len(rows) - 1), ([key], 0.05, 0.9))

    if rows:
        available = LAYOUT_BOTTOM_MARGIN - LAYOUT_TOP_MARGIN
        heights = [height_fraction * CANVAS_H for _, height_fraction, _ in rows]
        total = sum(heights)
        if total > available:  # more categories than the column can hold at size
            heights = [height * available / total for height in heights]
            gap = 0.0
        else:
            gap = min((available - total) / max(1, len(rows) - 1), MAX_RIGHT_ROW_GAP)

        cursor = LAYOUT_TOP_MARGIN
        for (members, _, width_fraction), height in zip(rows, heights):
            row_width = RIGHT_ZONE_W * width_fraction
            row_x = RIGHT_ZONE_X + (RIGHT_ZONE_W - row_width) / 2
            width = (row_width - PAIR_GAP * (len(members) - 1)) / len(members)
            for position, key in enumerate(members):
                slot = {
                    "x": row_x + position * (width + PAIR_GAP),
                    "y": cursor, "w": width, "h": height,
                }
                rects.update(_stack(by_key[key], slot))
            cursor += height + gap

    fallback = {"x": RIGHT_ZONE_X, "y": LAYOUT_TOP_MARGIN, "w": 120, "h": 120}
    return [rects.get(index, dict(fallback)) for index in range(len(categories))]


# TikTok's carousel is full-screen 9:16; a pin is 2:3, which TikTok would pad
# with blurred or solid fill. Re-framing it ourselves keeps control of that.
TIKTOK_FRAME = (1080, 1920)


def write_tiktok_frame(slug: str, size: tuple = TIKTOK_FRAME) -> Path:
    """Writes the copy of a pin's image that gets uploaded to TikTok.

    The editor already composes at 9:16 and exports at exactly 1080x1920, so
    this is normally a straight copy — the image goes up at native resolution
    with nothing resampled and nothing added around it. It only has work to do
    for a pin rendered at some other shape, which it fits to 9:16 by continuing
    the top and bottom rows outwards rather than banding.
    """
    import io

    from PIL import Image

    source_path = pin_dir(slug) / "pin.png"
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    width, height = size
    image = Image.open(io.BytesIO(source_path.read_bytes())).convert("RGB")
    if image.size == (width, height):
        destination = pin_dir(slug) / "tiktok.png"
        destination.write_bytes(source_path.read_bytes())
        return destination

    scaled = image.resize((width, max(1, round(image.height * width / image.width))), Image.LANCZOS)

    if scaled.height >= height:  # already at least as tall as 9:16 — trim evenly
        top = (scaled.height - height) // 2
        canvas = scaled.crop((0, top, width, top + height))
    else:
        canvas = Image.new("RGB", (width, height))
        top = (height - scaled.height) // 2
        canvas.paste(scaled, (0, top))
        if top:
            edge = scaled.crop((0, 0, width, 1)).resize((width, top), Image.NEAREST)
            canvas.paste(edge, (0, 0))
        bottom = height - (top + scaled.height)
        if bottom:
            edge = scaled.crop((0, scaled.height - 1, width, scaled.height)).resize((width, bottom), Image.NEAREST)
            canvas.paste(edge, (0, top + scaled.height))

    destination = pin_dir(slug) / "tiktok.png"
    canvas.save(destination, "PNG")
    return destination


def _fit_rect_to_image(rect: dict, image_path: Path) -> dict:
    """Tightens a slot's box around the cutout that goes in it.

    Slots are a fixed size per category, but a cutout has its own proportions —
    a tall perfume bottle in a squarish slot draws small with dead space either
    side, and the editor's resize handle then sits out in that dead space
    rather than on the picture. Matching the box to the image's own ratio
    leaves it drawn identically and puts the handle back on its corner.
    """
    from PIL import Image

    try:
        with Image.open(image_path) as image:
            width, height = image.size
    except (OSError, ValueError):
        return rect
    if not width or not height:
        return rect

    scale = min(rect["w"] / width, rect["h"] / height)
    fitted_width, fitted_height = width * scale, height * scale
    return {
        "x": round(rect["x"] + (rect["w"] - fitted_width) / 2, 2),
        "y": round(rect["y"] + (rect["h"] - fitted_height) / 2, 2),
        "w": round(fitted_width, 2),
        "h": round(fitted_height, 2),
    }


def create_outfit_pin_from_items(
    title1: str, title2: str, items: list, sections: list, generated_from: dict = None,
) -> dict:
    """Creates an outfit pin pre-populated from wardrobe items (the random /
    combination generator's output), rendered at fixed per-category positions.
    Cutouts are *copied* — the same wardrobe item can appear on many generated
    pins (e.g. one top paired with every pair of jeans in a combination run)."""
    title1 = (title1 or "Outfit").strip() or "Outfit"
    title2 = (title2 or "").strip()
    slug = next_slug(title1, title2)
    directory = pin_dir(slug)
    raw_dir, cutout_dir = directory / "raw", directory / "cutout"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cutout_dir.mkdir(parents=True, exist_ok=True)

    products = []
    for item in items:
        index = _next_product_index(products)
        cutout_name = f"product{index}.png"
        source = (WARDROBE_DIR / "cutout" / item["cutout"])
        (cutout_dir / cutout_name).write_bytes(source.read_bytes())

        products.append({
            "id": uuid.uuid4().hex,
            "title": item.get("title", ""),
            "category": item["category"],
            "url": "",
            "regionalUrls": None,
            "cutout": cutout_name,
            "wardrobeItemId": item["id"],
        })

    slots = _arrange_wardrobe_layout([product["category"] for product in products])
    layers = []
    for index, (product, slot) in enumerate(zip(products, slots)):
        rect = _fit_rect_to_image(slot, cutout_dir / product["cutout"])
        layers.append({
            "productId": product["id"], "x": rect["x"], "y": rect["y"],
            "w": rect["w"], "h": rect["h"], "rotation": 0,
            "zIndex": 10 + index, "url": "", "regionalUrls": None,
        })

    pin = {
        "slug": slug,
        "template": "outfit",
        "title1": title1,
        "title2": title2,
        "created_at": _now(),
        "outfit_sections": [dict(section) for section in sections],
        "products": products,
        "seo": {},
        "layout": {
            "backgroundChoice": "blue",
            "title1": "",
            "title2": "",
            "layers": layers,
        },
        "generated_from": generated_from or None,
        "warnings": [],
        "published_at": None,
        "posted_at": None,
        "pin_url": None,
    }
    return save_pin(pin)


def reroll_generated_categories(slug: str, categories: list = None) -> dict:
    """Re-picks a fresh wardrobe item for each of a generated pin's "random"
    categories, in place — keeps the same product id/cutout filename (only a
    cache-busting version bumps) so any manual position tweak on that layer
    in the editor survives the reroll."""
    import wardrobe as wardrobe_module

    pin = load_pin(slug)
    generated_from = pin.get("generated_from") or {}
    rerollable = set(generated_from.get("random") or [])
    if not rerollable:
        raise ValueError("This pin has no randomly-picked categories to reroll.")
    explicit = bool(categories)
    categories = set(categories) if categories else rerollable
    invalid = categories - rerollable
    if invalid:
        raise ValueError(f"Not reroll-eligible: {', '.join(sorted(invalid))}")

    data = wardrobe_module.load_wardrobe()
    products = pin.get("products", [])
    used_ids = {product.get("wardrobeItemId") for product in products}

    rerolled = []
    for category in sorted(categories):
        category_products = [product for product in products if product.get("category") == category]
        pool = [
            item for item in wardrobe_module.active_items(data, category)
            if item["id"] not in used_ids
        ]
        if len(pool) < len(category_products):
            # Asking for one specific category and it can't move is an error;
            # a blanket "reroll this pin" just skips whatever has no spare.
            if explicit:
                raise ValueError(f"Not enough other available items in '{category}' to reroll to.")
            continue
        rerolled.append(category)
        choices = random.sample(pool, len(category_products))
        for product, choice in zip(category_products, choices):
            used_ids.discard(product.get("wardrobeItemId"))
            cutout_path = pin_dir(slug) / "cutout" / product["cutout"]
            cutout_path.write_bytes((WARDROBE_DIR / "cutout" / choice["cutout"]).read_bytes())
            product["title"] = choice.get("title", "")
            product["wardrobeItemId"] = choice["id"]
            product["v"] = product.get("v", 1) + 1
            used_ids.add(choice["id"])

    if not rerolled:
        raise ValueError("No spare wardrobe items to reroll to — add more, or restore some archived ones.")

    save_pin(pin)
    return pin


def move_outfit_asset(slug: str, asset_id: str, category: str) -> dict:
    pin = load_pin(slug)
    valid_categories = {section["id"] for section in outfit_sections(pin)}
    if category not in valid_categories:
        raise ValueError(f"Unknown outfit category: {category}")
    product = next((item for item in pin.get("products", []) if item.get("id") == asset_id), None)
    if not product:
        raise ValueError("That wardrobe item no longer exists.")
    product["category"] = category
    save_pin(pin)
    return product


def delete_outfit_asset(slug: str, asset_id: str):
    pin = load_pin(slug)
    products = pin.get("products", [])
    product = next((item for item in products if item.get("id") == asset_id), None)
    if not product:
        raise ValueError("That wardrobe item no longer exists.")

    cutout_name = Path(product.get("cutout") or "").name
    if cutout_name:
        (pin_dir(slug) / "cutout" / cutout_name).unlink(missing_ok=True)
        raw_stem = Path(cutout_name).stem
        for raw_path in (pin_dir(slug) / "raw").glob(f"{raw_stem}.*"):
            raw_path.unlink(missing_ok=True)

    pin["products"] = [item for item in products if item.get("id") != asset_id]
    layout = pin.get("layout") or {}
    layout["layers"] = [layer for layer in layout.get("layers", []) if layer.get("productId") != asset_id]
    pin["layout"] = layout
    save_pin(pin)


# ---------- Generated-image pin (a second template) ----------
#
# This template skips products/cutouts/the editor entirely. A link is resolved
# to its product photo the same way the collage template does, but the photo
# becomes a *reference image* for an external, manual ChatGPT step instead of
# a cutout: it's dropped in incoming-clothes/, you generate a pin-ready image
# from it yourself, and generated_pins.sync_incoming_images() picks up the
# result once you save it to generated-images/<slug>.png and posts it as-is.

def create_generated_pin(url: str, title: str = "", progress=None) -> dict:
    """Starts a generated-image pin: resolves url to its product photo, saves
    that as the reference image, and writes the pin.json stub. Returns the
    pin, in "awaiting_image" status until the matching ChatGPT output shows up."""
    def report(message):
        if progress:
            progress(message)

    report("Fetching product photo...")
    resolved = resolve_product(url)
    slug = next_slug(title or resolved["title"] or "generated")

    INCOMING_CLOTHES_DIR.mkdir(parents=True, exist_ok=True)
    (INCOMING_CLOTHES_DIR / f"{slug}.jpg").write_bytes(resolved["image_bytes"])

    pin = {
        "slug": slug,
        "template": "generated",
        "title1": title or resolved["title"],
        "title2": "",
        # Kept separately from title1 (which may be a custom batch title
        # shared across several pins) so SEO generation always has the real
        # product name to draw keywords from — see generated_pins.sync_incoming_images.
        "product_title": resolved["title"],
        "created_at": _now(),
        "source_link": resolved["url"],
        "products": [],
        "seo": {},
        "layout": {},
        "warnings": [],
        "published_at": None,
        "posted_at": None,
        "pin_url": None,
    }
    save_pin(pin)
    report(f"Saved reference image to incoming-clothes/{slug}.jpg — generate its "
           f"pin image and save it as generated-images/{slug}.png when it's ready.")
    return pin


def create_generated_pins_batch(title: str, urls: list, progress=None) -> dict:
    """Starts several generated-image pins in one run, all sharing `title` —
    one per link in urls. One bad link is reported and skipped rather than
    losing the rest of the batch.

    Returns {"pins": [...], "errors": [...]}."""
    def report(message):
        if progress:
            progress(message)

    urls = [u for u in (urls or []) if u]
    built = []
    errors = []
    for index, url in enumerate(urls, start=1):
        report(f"--- Link {index} of {len(urls)} ---")
        try:
            built.append(create_generated_pin(url, title=title, progress=report))
        except Exception as e:
            errors.append(f"Link {index}: {e}")
            report(f"  link {index} failed: {e}")

    return {"pins": built, "errors": errors}


# ---------- Landing page ----------

LANDING_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #fafafa; font-family: -apple-system, BlinkMacSystemFont, sans-serif;
         display: flex; justify-content: center; padding: 20px; }}
  .frame {{ position: relative; width: 100%; max-width: 500px; }}
  .frame img {{ width: 100%; display: block; border-radius: 8px; }}
  .hotspot {{ position: absolute; display: block; }}
  .hotspot:hover {{ outline: 2px solid rgba(255,255,255,0.6); outline-offset: -2px; border-radius: 8px; }}
  .disclosure {{ max-width: 500px; margin: 12px auto 0; text-align: center; font-size: 11px; color: #888; }}
</style>
</head>
<body>
  <div>
    <div class="frame">
      <img src="{image_name}" alt="{title}">
{hotspots}
    </div>
    <p class="disclosure">As an Amazon Associate I earn from qualifying purchases.</p>
  </div>
<script>
  // Swaps each hotspot's link to the visitor's regional store where we have one.
  // Falls back silently to the default href (already correct) on any failure.
  (function () {{
    var regionalUrlsByIndex = {regional_urls};
    var countryCodeToRegion = {{ GB: 'UK', US: 'US' }};
    if (!regionalUrlsByIndex.some(Boolean)) return;

    fetch('https://ipwho.is/')
      .then(function (r) {{ return r.json(); }})
      .then(function (geo) {{
        var region = countryCodeToRegion[geo.country_code];
        if (!region) return;
        document.querySelectorAll('.hotspot[data-hotspot-index]').forEach(function (el) {{
          var urls = regionalUrlsByIndex[el.getAttribute('data-hotspot-index')];
          if (urls && urls[region]) el.href = urls[region];
        }});
      }})
      .catch(function () {{}});
  }})();
</script>
</body>
</html>
"""


def write_landing_page(slug: str, hotspots: list) -> Path:
    """Writes docs/shop/<slug>.html next to its image.

    The image is a sibling file rather than an inlined base64 data URL, which
    keeps the page ~2KB instead of ~1MB and lets browsers cache the image.
    """
    SHOP_DIR.mkdir(parents=True, exist_ok=True)

    hotspot_html = "\n".join(
        '      <a class="hotspot" data-hotspot-index="{i}" href="{url}" target="_blank" rel="noopener sponsored"'
        ' style="left:{left:.2f}%; top:{top:.2f}%; width:{width:.2f}%; height:{height:.2f}%;"></a>'.format(
            i=i,
            url=html.escape(spot["url"], quote=True),
            left=spot["leftPct"],
            top=spot["topPct"],
            width=spot["widthPct"],
            height=spot["heightPct"],
        )
        for i, spot in enumerate(hotspots)
    )

    page = LANDING_PAGE_TEMPLATE.format(
        title=html.escape(slug),
        image_name=f"{slug}.png",
        hotspots=hotspot_html,
        regional_urls=json.dumps([spot.get("regionalUrls") for spot in hotspots]),
    )

    path = SHOP_DIR / f"{slug}.html"
    path.write_text(page)
    return path
