import requests

from config import CANOPY_API_KEY

API_URL = "https://graphql.canopyapi.co/"

# One aliased query fetches the product's own details *and* checks whether the
# same ASIN also exists on the other marketplace — one HTTP round-trip instead
# of two. The regional half may come back null (many sellers use different
# ASINs per marketplace); that's expected, not an error.
PRODUCT_QUERY = """
query($url: String!, $asin: String!, $domain: AmazonDomain) {
  primary: amazonProduct(input: { urlLookup: { url: $url } }) {
    title
    mainImageUrl
  }
  regional: amazonProduct(input: { asinLookup: { asin: $asin, domain: $domain } }) {
    url
  }
}
"""

PRODUCT_ONLY_QUERY = """
query($url: String!) {
  primary: amazonProduct(input: { urlLookup: { url: $url } }) {
    title
    mainImageUrl
  }
}
"""


# Search runs against Amazon live, so it's slow (~60s) and returns ~48 results.
# Everything the discovery filter needs comes back in this one request.
SEARCH_QUERY = """
query($term: String!, $domain: AmazonDomain!) {
  amazonProductSearchResults(input: { searchTerm: $term, domain: $domain }) {
    productResults {
      results {
        title
        url
        asin
        mainImageUrl
        brand
        rating
        ratingsTotal
        isPrime
        sponsored
      }
    }
  }
}
"""


class CanopyAPIError(ValueError):
    pass


def _post(query: str, variables: dict, timeout: int = 60) -> dict:
    if not CANOPY_API_KEY:
        raise CanopyAPIError("Missing CANOPY_API_KEY. Sign up at canopyapi.co and add it to .env.")

    response = requests.post(
        API_URL,
        json={"query": query, "variables": variables},
        headers={"API-KEY": CANOPY_API_KEY},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def fetch_product(product_url: str, asin: str = None, other_region: str = None) -> dict:
    """Looks up an Amazon product by URL, optionally also checking whether the
    same ASIN resolves on another marketplace.

    Returns {"title", "image_url", "regional_url"}, where regional_url is None
    if that ASIN isn't listed on the other region's domain (or wasn't checked).
    """
    if asin and other_region:
        payload = _post(PRODUCT_QUERY, {"url": product_url, "asin": asin, "domain": other_region})
    else:
        payload = _post(PRODUCT_ONLY_QUERY, {"url": product_url})

    data = payload.get("data") or {}
    primary = data.get("primary") or {}
    image_url = primary.get("mainImageUrl")

    if not image_url:
        # Only now do the errors matter — a failed regional half is normal and
        # must not mask an otherwise-successful primary lookup.
        errors = payload.get("errors")
        detail = f": {errors}" if errors else ""
        raise CanopyAPIError(f"Canopy API returned no image for {product_url}{detail}")

    regional = data.get("regional") or {}
    return {
        "title": primary.get("title") or "",
        "image_url": image_url,
        "regional_url": regional.get("url"),
    }


def search_products(search_term: str, domain: str = "UK") -> list:
    """Runs one Amazon search and returns the raw result rows.

    One search costs one Canopy request whatever it returns, so callers should
    cache the results rather than re-running the same term (the free tier is
    100 requests a month, and building a pin spends them too).
    """
    payload = _post(SEARCH_QUERY, {"term": search_term, "domain": domain}, timeout=240)

    data = payload.get("data") or {}
    search = data.get("amazonProductSearchResults") or {}
    page = search.get("productResults") or {}
    results = page.get("results")

    if results is None:
        errors = payload.get("errors")
        raise CanopyAPIError(f"Canopy returned no results for {search_term!r}" + (f": {errors}" if errors else ""))

    return results
