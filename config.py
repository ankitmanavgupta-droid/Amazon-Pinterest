import os
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------- Paths ----------
PROJECT_ROOT = Path(__file__).parent
POSTS_DIR = PROJECT_ROOT / "posts"
DOCS_DIR = PROJECT_ROOT / "docs"
SHOP_DIR = DOCS_DIR / "shop"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

MAX_PRODUCTS = 6

# Amazon searches are cached here: Canopy's free tier is 100 requests a month
# and re-running the same search term shouldn't spend from it twice.
DISCOVERY_CACHE_DIR = PROJECT_ROOT / ".cache" / "searches"

# Which Amazon marketplace product discovery searches. Affiliate links are still
# built for every region with a tag, whichever store the search ran against.
DISCOVERY_DOMAIN = os.getenv("DISCOVERY_DOMAIN", "UK")

# ---------- Generated-image pin template ----------
# The handoff points for the manual ChatGPT step: a product photo goes in as a
# reference image, a finished pin image comes back out.
INCOMING_CLOTHES_DIR = PROJECT_ROOT / "incoming-clothes"    # reference photos awaiting a generated image
GENERATED_IMAGES_DIR = PROJECT_ROOT / "generated-images"    # finished PNGs, saved by hand — never emptied
PROCESSED_INPUTS_DIR = PROJECT_ROOT / "processed-inputs"    # reference photos successfully matched
FAILED_INPUTS_DIR = PROJECT_ROOT / "failed-inputs"          # reference photos abandoned by hand
IMAGEGEN_STATE_FILE = PROJECT_ROOT / ".imagegen-state.json"  # which generated-images/ files are already used

# ---------- Outfit Studio wardrobe (shared across many generated outfits) ----------
WARDROBE_DIR = PROJECT_ROOT / "wardrobe-items"

# Which generated outfits are grouped together as one TikTok photo carousel.
SLIDESHOWS_FILE = PROJECT_ROOT / ".slideshows.json"

# ---------- Interval ("drip") scheduling ----------
DRIP_SCHEDULE_FILE = PROJECT_ROOT / ".cache" / "drip_schedule.json"

# ---------- Amazon ----------
AMAZON_AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG")  # used for amazon.com (US) links
AMAZON_AFFILIATE_TAG_UK = os.getenv("AMAZON_AFFILIATE_TAG_UK")  # used for amazon.co.uk links — separate Associates account
CANOPY_API_KEY = os.getenv("CANOPY_API_KEY")

# Regions we build affiliate links for, and the tag each needs (skipped if unset).
AFFILIATE_TAGS_BY_REGION = {"US": AMAZON_AFFILIATE_TAG, "UK": AMAZON_AFFILIATE_TAG_UK}

# ---------- Pinterest (direct API — unused until trial access is approved) ----------
PINTEREST_APP_ID = os.getenv("PINTEREST_APP_ID")
PINTEREST_APP_SECRET = os.getenv("PINTEREST_APP_SECRET")
PINTEREST_REDIRECT_URI = os.getenv("PINTEREST_REDIRECT_URI")
PINTEREST_ACCESS_TOKEN = os.getenv("PINTEREST_ACCESS_TOKEN")
PINTEREST_API_BASE_URL = os.getenv("PINTEREST_API_BASE_URL", "https://api-sandbox.pinterest.com/v5")

# Third-party posting route (zernio.com) — used until PINTEREST_ACCESS_TOKEN above has
# real (non-trial) access, at which point pinterest/client.py can take over directly.
ZERNIO_API_KEY = os.getenv("ZERNIO_API_KEY")

# Used to auto-generate SEO pin titles/descriptions/hashtags (pinterest/seo.py)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def _github_pages_base_url() -> str:
    """Derives the live shop URL from the git remote, so this isn't hardcoded to
    one account. Override with GITHUB_PAGES_BASE_URL in .env if Pages is served
    from a custom domain."""
    override = os.getenv("GITHUB_PAGES_BASE_URL")
    if override:
        return override.rstrip("/")

    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""

    match = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$", remote)
    if not match:
        return ""
    owner, repo = match.groups()
    return f"https://{owner}.github.io/{repo}/shop"


GITHUB_PAGES_BASE_URL = _github_pages_base_url()
