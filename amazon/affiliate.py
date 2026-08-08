import re
from urllib.parse import urlparse, urlunparse

import requests

ASIN_PATTERN = re.compile(r"/([A-Z0-9]{10})(?:[/?]|$)")
SHORT_LINK_HOSTS = ("amzn.to", "a.co")


class InvalidAmazonURLError(ValueError):
    pass


def resolve_url(url: str) -> str:
    """Follow redirects so short links (amzn.to/a.co) become full product URLs."""
    if any(host in url for host in SHORT_LINK_HOSTS):
        response = requests.head(url, allow_redirects=True, timeout=10)
        return response.url
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
