import json
import re

import anthropic

from config import ANTHROPIC_API_KEY

SEO_MODEL = "claude-haiku-4-5"

SEO_SYSTEM_PROMPT = """You are a Pinterest SEO copywriter. Given a list of product names \
that will appear together in one Pinterest Pin, write SEO-optimized Pinterest metadata for \
that Pin as a whole.

Pinterest SEO conventions to follow:
- Title: front-load the primary keyword/search term, keep it under 100 characters, no clickbait.
- Description: natural-language sentence(s) that work keywords in organically (not a keyword \
dump), 200-400 characters, end with a soft call to action (e.g. "Shop the look", "Tap to see more").
- Hashtags: 3-6 lowercase hashtags, specific to the niche (not generic ones like #shopping). \
Return them without the leading '#'.
- Board: if a list of board names is given, pick the single best fit for this Pin, copied \
exactly as written. If none fit well, use the closest one anyway.

Respond with ONLY a JSON object, no markdown fences, no other text:
{"title": "...", "description": "...", "hashtags": ["...", "..."], "board": "..."}
"""

STOPWORDS = {"the", "a", "an", "for", "with", "and", "or", "from", "of", "to", "in", "on"}


class SEOGenerationError(ValueError):
    pass


def parse_json_response(text: str) -> dict:
    """Claude sometimes wraps its JSON in a markdown fence despite being asked
    not to. Shared by every module here that asks for a JSON answer."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return json.loads(text)


def _keywords(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS}


def generate_seo_content(product_titles: list, niche_hint: str = "", board_names: list = None) -> dict:
    """Generates {"title", "description", "hashtags", "board"} for a Pinterest
    Pin covering the given products. niche_hint (e.g. the on-image title)
    steers tone/theme; board_names (if given) lets the model also pick the
    best-fitting board in the same call."""
    if not ANTHROPIC_API_KEY:
        raise SEOGenerationError("Missing ANTHROPIC_API_KEY. Add it to .env.")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    user_prompt = "Products in this Pin:\n" + "\n".join(f"- {title}" for title in product_titles)
    if niche_hint:
        user_prompt += f"\n\nTheme/niche: {niche_hint}"
    if board_names:
        user_prompt += "\n\nAvailable boards:\n" + "\n".join(f"- {name}" for name in board_names)

    response = client.messages.create(
        model=SEO_MODEL,
        max_tokens=512,
        system=SEO_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = next((b.text for b in response.content if b.type == "text"), "")

    try:
        content = parse_json_response(text)
    except json.JSONDecodeError as e:
        raise SEOGenerationError(f"Claude didn't return valid JSON: {text!r}") from e

    missing = [k for k in ("title", "description", "hashtags") if k not in content]
    if missing:
        raise SEOGenerationError(f"Response missing {missing}: {content!r}")

    content["hashtags"] = [h.lstrip("#") for h in content.get("hashtags", [])]
    return content


def pick_best_board(pin_text: str, boards: list, preferred_name: str = None) -> str:
    """Returns the id of the board to pin to.

    Prefers preferred_name (the board the SEO model chose) when it matches a
    real board; otherwise falls back to keyword overlap against board names,
    then to the first board.
    """
    if not boards:
        return None

    if preferred_name:
        wanted = preferred_name.strip().casefold()
        for board in boards:
            if board["name"].strip().casefold() == wanted:
                return board["id"]

    pin_keywords = _keywords(pin_text)
    best_board = None
    best_score = 0
    for board in boards:
        score = len(pin_keywords & _keywords(board["name"]))
        if score > best_score:
            best_score = score
            best_board = board

    return (best_board or boards[0])["id"]


def format_description(seo: dict) -> str:
    """Pin description with hashtags appended, as Pinterest expects them."""
    description = seo.get("description", "")
    hashtags = seo.get("hashtags") or []
    if hashtags:
        description += " " + " ".join(f"#{h.lstrip('#')}" for h in hashtags)
    return description
