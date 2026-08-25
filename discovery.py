"""Finding products worth pinning, from a vibe rather than a list of links.

    vibe -> search terms -> Amazon search -> filter -> aesthetic ranking -> links

The filter in the middle is mechanical (ratings, review counts, sponsored slots,
duplicates); the ranking either side of it is Claude's, and lives in
pinterest/curation.py. Splitting them that way keeps the cheap rejections cheap:
no image is downloaded or judged for a product a rule would have thrown out.

Searches are cached under .cache/searches/ because Canopy's free tier is 100
requests a month and building pins spends from the same pot.
"""

import hashlib
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from amazon.canopy import search_products
from config import DISCOVERY_CACHE_DIR
from pinterest.curation import rank_by_aesthetic, suggest_search_terms

# A product needs enough of a track record to be worth pinning, but the bar has
# to stay low enough to let genuinely new finds through.
MIN_RATING = 4.0
MIN_REVIEWS = 20

# How many survivors get their photo judged. Every one costs an image download
# and its tokens, and past ~24 the tail is filler anyway.
MAX_TO_JUDGE = 24

# Ranked results below this are dropped rather than shown — a low score means
# Claude found something concrete wrong with the photo.
MIN_SCORE = 5.0

# One Amazon search will happily return fifteen listings from the same seller,
# and a pin of six near-identical tops from one brand looks like a catalogue
# page. The shortlist gets a loose cap so the ranker still has a choice within a
# brand; the results get a tight one.
MAX_PER_BRAND = 2
SHORTLIST_PER_BRAND = 6

# Colour variants are listed as separate ASINs under near-identical titles, so
# ASINs alone don't dedupe them. Comparing the opening words does.
TITLE_PREFIX_WORDS = 8

CACHE_SECONDS = 7 * 24 * 60 * 60


class DiscoveryError(RuntimeError):
    pass


def _cache_path(term: str, domain: str) -> Path:
    key = hashlib.sha256(f"{domain}:{term.lower()}".encode()).hexdigest()[:16]
    return DISCOVERY_CACHE_DIR / f"{key}.json"


def cached_search(term: str, domain: str = "UK", max_age: int = CACHE_SECONDS) -> list:
    """search_products, but a repeated term inside max_age is free."""
    path = _cache_path(term, domain)
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        return json.loads(path.read_text())

    results = search_products(term, domain=domain)

    DISCOVERY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results))
    return results


def is_usable(row: dict) -> bool:
    """Whether a search result is worth spending an image download on."""
    if row.get("sponsored"):
        return False  # a paid slot, not a search ranking — different signal entirely
    if not row.get("mainImageUrl") or not row.get("url"):
        return False

    rating = row.get("rating")
    reviews = row.get("ratingsTotal")
    if rating is None or reviews is None:
        return False
    return rating >= MIN_RATING and reviews >= MIN_REVIEWS


def popularity(row: dict) -> float:
    """Ranks the mechanical survivors before the expensive step.

    Rating alone puts a 5.0-from-3-reviews above a 4.6-from-4000, so reviews are
    folded in on a log scale: they should count, but not linearly.
    """
    rating = row.get("rating") or 0
    reviews = row.get("ratingsTotal") or 0
    return rating * math.log10(reviews + 10)


def canonical_url(row: dict) -> str:
    """The plain /dp/<asin> form of a search result's link.

    Search URLs carry a long ref/dib tracking query that expires, and every
    colour variant of a product has its own. Keeping the marketplace host means
    region detection downstream still works.
    """
    url = row.get("url") or ""
    asin = row.get("asin")
    host = urlparse(url).netloc
    if not asin or not host:
        return url
    return f"https://{host}/dp/{asin}"


def variant_key(row: dict) -> tuple:
    """Groups the colour variants of one product together."""
    words = re.findall(r"[a-z0-9]+", (row.get("title") or "").lower())
    return (row.get("brand") or "", tuple(words[:TITLE_PREFIX_WORDS]))


def cap_per_brand(products: list, limit: int) -> list:
    """Keeps at most `limit` products per brand, preserving the order given —
    so whatever ranked them decides which of a brand's products survive."""
    seen = {}
    kept = []
    for product in products:
        brand = (product.get("brand") or "").strip().lower()
        if brand:
            if seen.get(brand, 0) >= limit:
                continue
            seen[brand] = seen.get(brand, 0) + 1
        kept.append(product)
    return kept


def to_candidate(row: dict, term: str) -> dict:
    return {
        "asin": row.get("asin"),
        "title": row.get("title") or "",
        "url": canonical_url(row),
        "image_url": row.get("mainImageUrl"),
        "brand": row.get("brand") or "",
        "rating": row.get("rating"),
        "reviews": row.get("ratingsTotal"),
        "searchTerm": term,
    }


def gather_candidates(terms: list, domain: str = "UK", progress=None) -> list:
    """Searches every term, keeps what passes the filter, dedupes by ASIN."""
    def report(message):
        if progress:
            progress(message)

    by_asin = {}
    seen_variants = set()
    failures = []
    for index, term in enumerate(terms, start=1):
        report(f"Searching Amazon {domain} for '{term}' ({index} of {len(terms)}, ~1 min each)...")
        try:
            rows = cached_search(term, domain=domain)
        except Exception as e:  # a dead term shouldn't lose the other searches
            failures.append(f"{term}: {e}")
            report(f"  search failed: {e}")
            continue

        kept = 0
        for row in rows:
            if not is_usable(row):
                continue
            asin = row.get("asin")
            # First term to find a product keeps it; the same item often shows
            # up under several searches, and in several colours.
            key = variant_key(row)
            if asin and asin not in by_asin and key not in seen_variants:
                seen_variants.add(key)
                by_asin[asin] = to_candidate(row, term)
                kept += 1
        report(f"  {kept} of {len(rows)} results passed the rating/review filter")

    if not by_asin and failures:
        raise DiscoveryError("; ".join(failures))

    return sorted(by_asin.values(), key=popularity, reverse=True)


def discover(vibe: str, domain: str = "UK", term_count: int = 3, board_names: list = None,
             min_score: float = MIN_SCORE, progress=None) -> dict:
    """The whole pipeline. Returns {"vibe", "terms", "products", "considered"}.

    products are ranked best first, each with a score and the reason for it.
    """
    def report(message):
        if progress:
            progress(message)

    report("Working out what to search for...")
    terms = suggest_search_terms(vibe, count=term_count, board_names=board_names)
    report(f"Searching: {', '.join(terms)}")

    candidates = gather_candidates(terms, domain=domain, progress=report)
    if not candidates:
        raise DiscoveryError(
            f"Nothing matched for '{vibe}' — every result was sponsored, unrated, or below "
            f"{MIN_RATING}★ with {MIN_REVIEWS}+ reviews. Try a broader vibe."
        )

    shortlist = cap_per_brand(candidates, SHORTLIST_PER_BRAND)[:MAX_TO_JUDGE]
    report(f"Looking at {len(shortlist)} product photos...")
    ranked = rank_by_aesthetic(shortlist, vibe=vibe)

    good = cap_per_brand([p for p in ranked if p["score"] >= min_score], MAX_PER_BRAND)
    report(f"{len(good)} would make a good pin, from {len(ranked)} judged.")

    return {
        "vibe": vibe,
        "terms": terms,
        "domain": domain,
        "products": good,
        "considered": len(candidates),
    }
