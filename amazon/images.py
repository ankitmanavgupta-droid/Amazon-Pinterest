import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

# Product images live on Amazon's public media CDN (m.media-amazon.com), which
# serves them to any client that looks like a browser — no auth, no scraping of
# product pages (Canopy handles the lookup that finds these URLs).
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def download_image(image_url: str) -> bytes:
    response = requests.get(image_url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.content


MAX_REMOTE_IMAGE_BYTES = 15 * 1024 * 1024


def _validate_public_image_url(image_url: str):
    parsed = urlparse(image_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname or parsed.port not in (None, 443):
        raise ValueError("Dropped web images must use a public HTTPS address.")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError):
            raise ValueError("The image host could not be reached.")
    if not addresses or any(
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_multicast or address.is_unspecified
        for address in addresses
    ):
        raise ValueError("Local or private image addresses are not allowed.")


def download_remote_image(image_url: str) -> bytes:
    """Downloads a public dragged image with SSRF, type and size checks."""
    current_url = image_url
    response = None
    for _ in range(4):
        _validate_public_image_url(current_url)
        parsed = urlparse(current_url)
        headers = {
            **HEADERS,
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        }
        response = requests.get(current_url, headers=headers, timeout=20, stream=True, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
            current_url = urljoin(current_url, response.headers["Location"])
            continue
        break
    else:
        raise ValueError("The image URL redirected too many times.")

    response.raise_for_status()
    content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
    if not content_type.startswith("image/"):
        raise ValueError("The dropped address did not return an image. Drag the image itself, not its page.")

    declared_size = int(response.headers.get("Content-Length") or 0)
    if declared_size > MAX_REMOTE_IMAGE_BYTES:
        raise ValueError("That web image is larger than 15 MB.")
    chunks = []
    size = 0
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > MAX_REMOTE_IMAGE_BYTES:
            raise ValueError("That web image is larger than 15 MB.")
        chunks.append(chunk)
    if not chunks:
        raise ValueError("The website returned an empty image.")
    return b"".join(chunks)


def download_pinterest_image(image_url: str) -> bytes:
    """Backward-compatible name for older callers."""
    return download_remote_image(image_url)
