import io

from PIL import Image, ImageFilter
from rembg import remove


def image_pixels(image: Image.Image):
    get_flattened_data = getattr(image, "get_flattened_data", None)
    return get_flattened_data() if get_flattened_data else image.getdata()


def remove_background(image_bytes: bytes) -> bytes:
    """Returns PNG bytes with the background made transparent and cropped
    tightly to the product, so it doesn't drop into the builder surrounded
    by mostly-empty transparent space.

    Images that already contain transparent pixels do not need the rembg
    model; they only need to be normalized to PNG and cropped.
    """
    image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    alpha_min, _ = image.getchannel("A").getextrema()
    if alpha_min < 255:
        return crop_to_content(image_bytes)

    # Some generated images only *look* transparent: their checkerboard is
    # baked into an opaque RGB image. rembg tends to keep that pattern as a
    # large translucent blob, so key out this distinctive light-neutral
    # checkerboard directly instead.
    if has_baked_checkerboard(image):
        return remove_baked_checkerboard(image)

    cutout_bytes = remove(image_bytes)
    return crop_to_content(cutout_bytes)


def has_baked_checkerboard(image: Image.Image) -> bool:
    """Detect a light-grey checker pattern around the image boundary.

    A real white product-photo background has almost no brightness variation;
    the fake transparency pattern alternates between two light neutral tones.
    Sampling only the boundary keeps the product itself out of the decision.
    """
    image = image.convert("RGB")
    width, height = image.size
    band = max(4, min(width, height) // 20)
    boundary = (
        image.crop((0, 0, width, band)),
        image.crop((0, height - band, width, height)),
        image.crop((0, band, band, height - band)),
        image.crop((width - band, band, width, height - band)),
    )
    pixels = [pixel for region in boundary for pixel in image_pixels(region)]
    neutral_brightness = sorted(
        (red + green + blue) // 3
        for red, green, blue in pixels
        if min(red, green, blue) >= 220 and max(red, green, blue) - min(red, green, blue) <= 5
    )
    if len(neutral_brightness) < len(pixels) * 0.95:
        return False

    tenth = neutral_brightness[len(neutral_brightness) // 10]
    ninetieth = neutral_brightness[len(neutral_brightness) * 9 // 10]
    return ninetieth - tenth >= 8


def remove_baked_checkerboard(image: Image.Image) -> bytes:
    """Turns a baked light-neutral checkerboard into a real alpha channel."""
    image = image.convert("RGBA")
    mask = Image.new("L", image.size)
    mask.putdata([
        0 if min(red, green, blue) >= 220 and max(red, green, blue) - min(red, green, blue) <= 5 else 255
        for red, green, blue, _alpha in image_pixels(image)
    ])
    # Closing fills small bright gaps in pale products without restoring the
    # much larger checkerboard regions. A slight blur softens cutout edges.
    mask = mask.filter(ImageFilter.MaxFilter(11)).filter(ImageFilter.MinFilter(11))
    mask = mask.filter(ImageFilter.GaussianBlur(0.6))
    image.putalpha(mask)

    output = io.BytesIO()
    image.save(output, format="PNG")
    return crop_to_content(output.getvalue())


def crop_to_content(png_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    bbox = image.split()[-1].getbbox()  # bounding box of the alpha channel
    if bbox:
        image = image.crop(bbox)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
