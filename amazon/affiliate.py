import re
from urllib.parse import parse_qs, urlparse, urlunparse

import requests

ASIN_PATTERN = re.compile(r"/([A-Z0-9]{10})(?:[/?]|$)")
SHORT_LINK_HOSTS = ("amzn.to", "a.co", "link.amazon")


class InvalidAmazonURLError(ValueError):
    pass


def resolve_url(url: str) -> str:
    """Follow redirects/unwrap tracking links so we end up at a real product URL.

    Handles short links (amzn.to/a.co) and sponsored-listing click-through links
    (amazon.<tld>/sspa/click?...&url=<encoded product path>), which is what you
    get when copying a link straight from Amazon search results.
    """
    if any(host in url for host in SHORT_LINK_HOSTS):
        # GET, not HEAD — some short-link domains (e.g. link.amazon) don't
        # follow redirects correctly on HEAD requests.
        response = requests.get(url, allow_redirects=True, timeout=10)
        return response.url

    parsed = urlparse(url)
    if parsed.path.startswith("/sspa/click"):
        target_path = parse_qs(parsed.query).get("url", [None])[0]
        if target_path:
            return urlunparse((parsed.scheme, parsed.netloc, target_path, "", "", ""))

    return url


def extract_asin(url: str) -> str:
    match = ASIN_PATTERN.search(urlparse(url).path)
    if not match:
        raise InvalidAmazonURLError(f"Could not find an ASIN in URL: {url}")
    return match.group(1)


def build_affiliate_link(url: str, affiliate_tag: str) -> str:
    if not affiliate_tag:
        raise ValueError("affiliate_tag is required")
    asin = extract_asin(url)
    domain = urlparse(url).netloc or "www.amazon.com"
    return urlunparse(("https", domain, f"/dp/{asin}", "", f"tag={affiliate_tag}", ""))


def region_from_url(url: str) -> str:
    """Guesses the Amazon marketplace region ('UK' or 'US') from a URL's domain.
    Defaults to 'US' for amazon.com and anything unrecognized."""
    netloc = urlparse(url).netloc.lower()
    if "co.uk" in netloc:
        return "UK"
    return "US"
