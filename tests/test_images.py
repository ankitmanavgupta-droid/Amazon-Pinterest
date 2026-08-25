import pytest

from amazon import images


class FakeImageResponse:
    def __init__(self, chunks=(b"image",), headers=None, status_code=200):
        self._chunks = chunks
        self.headers = headers or {"Content-Type": "image/jpeg"}
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def iter_content(self, _chunk_size):
        return iter(self._chunks)


def public_dns(monkeypatch):
    monkeypatch.setattr(
        images.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(images.socket.AF_INET, images.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )


def test_download_remote_image_accepts_any_public_image_host(monkeypatch):
    public_dns(monkeypatch)
    seen = {}

    def fake_get(url, **kwargs):
        seen.update({"url": url, **kwargs})
        return FakeImageResponse((b"pin", b"image"))

    monkeypatch.setattr(images.requests, "get", fake_get)

    result = images.download_remote_image("https://files.oaiusercontent.com/generated/item.png")

    assert result == b"pinimage"
    assert seen["stream"] is True
    assert seen["allow_redirects"] is False


def test_download_remote_image_rejects_private_urls(monkeypatch):
    monkeypatch.setattr(images.requests, "get", lambda *args, **kwargs: pytest.fail("should not download"))

    with pytest.raises(ValueError, match="private image"):
        images.download_remote_image("https://127.0.0.1/private.png")


def test_download_remote_image_requires_image_content_type(monkeypatch):
    public_dns(monkeypatch)
    monkeypatch.setattr(
        images.requests,
        "get",
        lambda *args, **kwargs: FakeImageResponse(headers={"Content-Type": "text/html"}),
    )

    with pytest.raises(ValueError, match="did not return an image"):
        images.download_remote_image("https://images.example.com/item")
