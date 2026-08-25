"""Picking products worth pinning.

Two Claude calls sit either side of the Amazon search: one turns a vibe into the
search terms that actually surface that look on Amazon, and one looks at the
product photos and judges which of them will make a good pin.

The second is the part that matters. A product can be well-reviewed and still be
useless here, because what ends up on the pin isn't the product — it's the
product *photo*, with its background cut out. So the judgement is as much about
the photograph as the thing being sold.
"""

import base64
import io
import json

import anthropic
import requests

from config import ANTHROPIC_API_KEY
from pinterest.seo import parse_json_response

CURATION_MODEL = "claude-opus-5"

# Images are only being judged, not reproduced — small ones cost a fraction of
# the tokens and lose nothing that matters at this size.
THUMBNAIL_PX = 240

SEARCH_TERMS_PROMPT = """You write Amazon search queries for a Pinterest affiliate account.

Given a vibe or aesthetic, return the Amazon search terms most likely to surface products \
that genuinely fit it.

What makes a good term here:
- Write how a shopper types into Amazon, not how a brand writes copy. "linen button down \
shirt women" finds things; "effortless summer elegance" finds nothing.
- Aesthetic names only work when sellers actually use them in listings (cottagecore, y2k, \
coquette and balletcore do; most invented ones don't). When in doubt describe the garment \
and its material instead.
- Vary the terms — different garments, cuts or materials within the vibe, not one phrase \
reworded. Each term costs a separate search, so they shouldn't overlap.
- Include the category word (top, blouse, dress, lamp) so results aren't scattered.

Respond with ONLY a JSON object, no markdown fences, no other text:
{"terms": ["...", "..."]}
"""

AESTHETIC_PROMPT = """You curate products for a Pinterest affiliate account. Each product \
you approve has its photo's background removed and placed on a "shop the look" pin, so you \
are judging the photograph at least as much as the product.

Score each numbered image 0-10 on how well it would work.

Score high:
- One clear product (or one model wearing it), lit softly and evenly, easy to cut cleanly \
off its background.
- A muted, warm or neutral palette — the colours Pinterest fashion and interiors boards are \
built from.
- Looks styled and editorial, like something a person would save to a board.

Score low, however good the product itself is:
- Text, logos, badges, price stickers or watermarks burned into the image — these survive \
background removal and ruin the pin.
- Collages, split panels, or several angles of the product in one photo.
- Busy, cluttered or dark backgrounds, or a background the product blends into.
- Harsh flash, heavy shadow, obvious low resolution, or crude digital editing.
- Garish saturated colour that would fight whatever background the pin uses.

Judge only what you can see. Being on-theme is not enough to rescue a bad photo, and a \
beautiful photo of something off-theme is still off-theme.

For each image return its number, a score, and one short concrete reason naming what you \
actually saw ("clean cutout, soft neutral knit" / "price badge burned into corner").

Respond with ONLY a JSON object, no markdown fences, no other text:
{"rankings": [{"index": 1, "score": 8, "reason": "..."}, ...]}
Include every image you were shown, once.
"""


class CurationError(ValueError):
    pass


def _client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise CurationError("Missing ANTHROPIC_API_KEY. Add it to .env.")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _ask_for_json(system: str, content, max_tokens: int) -> dict:
    """One JSON answer from Claude.

    max_tokens has to cover the model's own reasoning as well as the answer —
    thinking is on by default on this model — so it's set well clear of the
    JSON's actual size. Truncation here would surface as a parse error.
    """
    response = _client().messages.create(
        model=CURATION_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return parse_json_response(text)
    except json.JSONDecodeError as e:
        raise CurationError(f"Claude didn't return valid JSON: {text!r}") from e


def suggest_search_terms(vibe: str, count: int = 3, board_names: list = None) -> list:
    """Turns a vibe ('early autumn cottagecore tops') into Amazon search terms.

    Each term costs a Canopy request, so count is deliberately small.
    """
    prompt = f"Vibe: {vibe}\n\nGive exactly {count} search terms."
    if board_names:
        prompt += "\n\nThe account's boards, for a sense of what it posts:\n"
        prompt += "\n".join(f"- {name}" for name in board_names)

    content = _ask_for_json(SEARCH_TERMS_PROMPT, prompt, max_tokens=4096)
    terms = [t.strip() for t in (content.get("terms") or []) if isinstance(t, str) and t.strip()]
    if not terms:
        raise CurationError(f"No search terms came back: {content!r}")
    return terms[:count]


def fetch_thumbnail(image_url: str) -> tuple:
    """Downloads a product image and shrinks it for judging.

    Returns (base64_data, media_type), or (None, None) if it couldn't be read —
    an unreadable image is a reason to drop a candidate, not to fail the run.
    """
    from PIL import Image  # local: Pillow is slow to import and only needed here

    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        image.thumbnail((THUMBNAIL_PX, THUMBNAIL_PX))

        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=80)
        return base64.standard_b64encode(buffer.getvalue()).decode(), "image/jpeg"
    except Exception:
        return None, None


def rank_by_aesthetic(products: list, vibe: str = "") -> list:
    """Scores products on how well their photo would work as part of a pin.

    Takes [{"title", "image_url", ...}] and returns the same dicts with "score"
    and "reason" added, best first. Products whose image couldn't be fetched are
    dropped — they can't be judged, and the pin builder couldn't use them either.
    """
    if not products:
        return []

    content = []
    judged = []
    for product in products:
        data, media_type = fetch_thumbnail(product.get("image_url") or "")
        if not data:
            continue
        judged.append(product)
        content.append({"type": "text", "text": f"Image {len(judged)}: {product.get('title', '')[:200]}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})

    if not judged:
        raise CurationError("None of the candidate images could be downloaded.")

    header = f"Vibe being curated for: {vibe}\n\n" if vibe else ""
    content.insert(0, {"type": "text", "text": f"{header}Score these {len(judged)} products."})

    rankings = _ask_for_json(AESTHETIC_PROMPT, content, max_tokens=16000).get("rankings") or []

    by_index = {}
    for entry in rankings:
        try:
            by_index[int(entry["index"])] = entry
        except (KeyError, TypeError, ValueError):
            continue  # a malformed row shouldn't sink the whole batch

    scored = []
    for position, product in enumerate(judged, start=1):
        entry = by_index.get(position) or {}
        try:
            score = float(entry.get("score"))
        except (TypeError, ValueError):
            score = 0.0
        scored.append({**product, "score": score, "reason": entry.get("reason", "")})

    scored.sort(key=lambda p: p["score"], reverse=True)
    return scored
