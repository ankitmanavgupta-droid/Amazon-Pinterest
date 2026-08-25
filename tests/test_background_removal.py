import io

from PIL import Image, ImageDraw

from amazon import background_removal


def png_bytes(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_transparent_image_skips_background_model_and_is_cropped(monkeypatch):
    image = Image.new("RGBA", (12, 10), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((3, 2, 8, 7), fill=(120, 80, 40, 255))
    source = png_bytes(image)
    monkeypatch.setattr(
        background_removal,
        "remove",
        lambda _data: (_ for _ in ()).throw(AssertionError("rembg should not run")),
    )

    result = background_removal.remove_background(source)

    cutout = Image.open(io.BytesIO(result))
    assert cutout.mode == "RGBA"
    assert cutout.size == (6, 6)


def test_opaque_alpha_channel_still_runs_background_model(monkeypatch):
    source = png_bytes(Image.new("RGBA", (12, 10), (120, 80, 40, 255)))
    model_result = Image.new("RGBA", (12, 10), (0, 0, 0, 0))
    ImageDraw.Draw(model_result).rectangle((4, 3, 7, 6), fill=(120, 80, 40, 255))
    calls = []

    def fake_remove(data):
        calls.append(data)
        return png_bytes(model_result)

    monkeypatch.setattr(background_removal, "remove", fake_remove)

    result = background_removal.remove_background(source)

    assert calls == [source]
    assert Image.open(io.BytesIO(result)).size == (4, 4)


def test_baked_checkerboard_skips_model_and_becomes_transparent(monkeypatch):
    image = Image.new("RGB", (80, 80))
    pixels = image.load()
    for y in range(80):
        for x in range(80):
            shade = 240 if (x // 8 + y // 8) % 2 else 254
            pixels[x, y] = (shade, shade, shade)
    ImageDraw.Draw(image).rectangle((25, 20, 54, 59), fill=(80, 110, 140))
    monkeypatch.setattr(
        background_removal,
        "remove",
        lambda _data: (_ for _ in ()).throw(AssertionError("rembg should not run")),
    )

    result = background_removal.remove_background(png_bytes(image))

    cutout = Image.open(io.BytesIO(result)).convert("RGBA")
    assert cutout.width < image.width
    assert cutout.height < image.height
    assert cutout.getchannel("A").getextrema() == (0, 255)


def test_flat_white_background_is_not_mistaken_for_checkerboard(monkeypatch):
    source = png_bytes(Image.new("RGB", (40, 40), "white"))
    calls = []

    def fake_remove(data):
        calls.append(data)
        return png_bytes(Image.new("RGBA", (4, 4), (20, 30, 40, 255)))

    monkeypatch.setattr(background_removal, "remove", fake_remove)

    background_removal.remove_background(source)

    assert calls == [source]
