import json

import pytest

import pins


@pytest.fixture
def posts_dir(tmp_path, monkeypatch):
    directory = tmp_path / "posts"
    directory.mkdir()
    monkeypatch.setattr(pins, "POSTS_DIR", directory)
    return directory


def test_slugify_strips_punctuation_and_case():
    assert pins.slugify("Summer Tops!") == "summer-tops"
    assert pins.slugify("  Y2K / Baby-doll  ") == "y2k-baby-doll"


def test_slugify_falls_back_when_nothing_usable():
    assert pins.slugify("!!!") == "pin"


def test_next_slug_starts_at_one(posts_dir):
    assert pins.next_slug("Summer Tops", "From Amazon") == "summer-tops-from-amazon-1"


def test_next_slug_continues_past_existing_pins(posts_dir):
    """The bug that overwrote a pin: numbering restarted at 1 on a later run
    instead of continuing from what was already on disk."""
    (posts_dir / "summer-tops-from-amazon-1").mkdir()
    (posts_dir / "summer-tops-from-amazon-2").mkdir()

    assert pins.next_slug("Summer Tops", "From Amazon") == "summer-tops-from-amazon-3"


def test_next_slug_ignores_unrelated_and_malformed_names(posts_dir):
    (posts_dir / "summer-tops-from-amazon-1").mkdir()
    (posts_dir / "gym-essentials-9").mkdir()
    (posts_dir / "summer-tops-from-amazon-draft").mkdir()

    assert pins.next_slug("Summer Tops", "From Amazon") == "summer-tops-from-amazon-2"


def test_save_and_load_round_trip(posts_dir):
    pins.save_pin({"slug": "demo-1", "title1": "Demo"})
    assert pins.load_pin("demo-1")["title1"] == "Demo"


def test_load_missing_pin_raises(posts_dir):
    with pytest.raises(pins.PinNotFoundError):
        pins.load_pin("nope-1")


def test_pin_status_progresses_with_state(posts_dir):
    pin = {"slug": "demo-1"}
    assert pins.pin_status(pin) == "draft"

    (posts_dir / "demo-1").mkdir()
    (posts_dir / "demo-1" / "pin.png").write_bytes(b"fake")
    assert pins.pin_status(pin) == "rendered"

    pin["published_at"] = "2026-08-18T00:00:00+00:00"
    assert pins.pin_status(pin) == "published"

    pin["posted_at"] = "2026-08-18T00:01:00+00:00"
    assert pins.pin_status(pin) == "posted"


def test_list_pins_is_newest_first(posts_dir):
    pins.save_pin({"slug": "old-1", "created_at": "2026-08-01T00:00:00+00:00"})
    pins.save_pin({"slug": "new-1", "created_at": "2026-08-18T00:00:00+00:00"})

    assert [p["slug"] for p in pins.list_pins()] == ["new-1", "old-1"]


def test_list_pins_ignores_directories_without_pin_json(posts_dir):
    (posts_dir / "leftovers").mkdir()
    pins.save_pin({"slug": "real-1", "created_at": "2026-08-18T00:00:00+00:00"})

    assert [p["slug"] for p in pins.list_pins()] == ["real-1"]


def test_write_landing_page_places_hotspots(tmp_path, monkeypatch):
    monkeypatch.setattr(pins, "SHOP_DIR", tmp_path)
    hotspots = [
        {
            "leftPct": 10.0, "topPct": 20.0, "widthPct": 30.0, "heightPct": 40.0,
            "url": "https://www.amazon.com/dp/B01?tag=t-20",
            "regionalUrls": {"US": "https://www.amazon.com/dp/B01?tag=t-20"},
        }
    ]

    page = pins.write_landing_page("demo-1", hotspots).read_text()

    assert 'src="demo-1.png"' in page  # image is a sibling file, not inlined base64
    assert 'left:10.00%; top:20.00%; width:30.00%; height:40.00%' in page
    assert 'href="https://www.amazon.com/dp/B01?tag=t-20"' in page
    assert 'rel="noopener sponsored"' in page


def test_write_landing_page_escapes_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(pins, "SHOP_DIR", tmp_path)
    hotspots = [{
        "leftPct": 0, "topPct": 0, "widthPct": 1, "heightPct": 1,
        "url": 'https://evil.test/"><script>alert(1)</script>',
        "regionalUrls": None,
    }]

    page = pins.write_landing_page("demo-1", hotspots).read_text()

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_write_landing_page_without_regional_urls_skips_swap(tmp_path, monkeypatch):
    monkeypatch.setattr(pins, "SHOP_DIR", tmp_path)
    hotspots = [{"leftPct": 0, "topPct": 0, "widthPct": 1, "heightPct": 1, "url": "https://x.test", "regionalUrls": None}]

    page = pins.write_landing_page("demo-1", hotspots).read_text()

    assert "[null]" in page  # the geo script short-circuits on this


def test_pin_status_reports_scheduled_before_it_goes_out(posts_dir):
    (posts_dir / "demo-1").mkdir()
    (posts_dir / "demo-1" / "pin.png").write_bytes(b"fake")
    pin = {
        "slug": "demo-1",
        "published_at": "2026-08-18T00:00:00+00:00",
        "scheduled_for": "2026-09-01T09:30:00",
    }

    assert pins.pin_status(pin) == "scheduled"

    # Once Zernio actually posts it, 'posted' wins.
    pin["posted_at"] = "2026-09-01T09:30:05+00:00"
    assert pins.pin_status(pin) == "posted"


def test_create_outfit_pin_starts_empty_workspace(posts_dir):
    pin = pins.create_outfit_pin("Autumn", "Outfit")

    assert pin["template"] == "outfit"
    assert pin["products"] == []
    assert pin["layout"]["backgroundChoice"] == "white"
    assert pins.pin_status(pin) == "draft"


def test_add_outfit_asset_removes_background_and_categorises(posts_dir, monkeypatch):
    pin = pins.create_outfit_pin("Autumn", "Outfit")
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: b"transparent-png")

    product = pins.add_outfit_asset(pin["slug"], "jackets", b"photo", "denim-jacket.jpg")
    saved = pins.load_pin(pin["slug"])

    assert product["category"] == "jackets"
    assert product["title"] == "denim-jacket"
    assert saved["products"] == [product]
    assert (posts_dir / pin["slug"] / "cutout" / "product1.png").read_bytes() == b"transparent-png"


def test_add_outfit_asset_rejects_unknown_category(posts_dir):
    pin = pins.create_outfit_pin("Autumn", "Outfit")

    with pytest.raises(ValueError, match="Unknown outfit category"):
        pins.add_outfit_asset(pin["slug"], "hats-and-magic", b"photo", "hat.jpg")


def test_custom_outfit_section_can_receive_uploaded_assets(posts_dir, monkeypatch):
    pin = pins.create_outfit_pin("Autumn", "Outfit")
    section = pins.add_outfit_section(pin["slug"], "Hats & Hair")
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: b"transparent")

    product = pins.add_outfit_asset(pin["slug"], section["id"], b"photo", "beret.jpg")
    saved = pins.load_pin(pin["slug"])

    assert section == {"id": "hats-hair", "label": "Hats & Hair"}
    assert saved["outfit_sections"][-1] == section
    assert product["category"] == "hats-hair"


def test_outfit_asset_can_be_moved_and_deleted(posts_dir, monkeypatch):
    pin = pins.create_outfit_pin("Autumn", "Outfit")
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: b"transparent")
    product = pins.add_outfit_asset(pin["slug"], "tops", b"photo", "shirt.jpg")
    saved = pins.load_pin(pin["slug"])
    saved["layout"]["layers"] = [{"productId": product["id"], "x": 10}]
    pins.save_pin(saved)

    moved = pins.move_outfit_asset(pin["slug"], product["id"], "jackets")
    assert moved["category"] == "jackets"

    pins.delete_outfit_asset(pin["slug"], product["id"])
    deleted = pins.load_pin(pin["slug"])
    assert deleted["products"] == []
    assert deleted["layout"]["layers"] == []
    assert not (posts_dir / pin["slug"] / "cutout" / "product1.png").exists()
    assert not (posts_dir / pin["slug"] / "raw" / "product1.upload").exists()


def test_new_outfit_asset_does_not_overwrite_after_deleting_middle_item(posts_dir, monkeypatch):
    pin = pins.create_outfit_pin("Autumn", "Outfit")
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: b"cutout-" + data)
    first = pins.add_outfit_asset(pin["slug"], "tops", b"one", "one.jpg")
    second = pins.add_outfit_asset(pin["slug"], "tops", b"two", "two.jpg")
    third = pins.add_outfit_asset(pin["slug"], "tops", b"three", "three.jpg")

    pins.delete_outfit_asset(pin["slug"], second["id"])
    fourth = pins.add_outfit_asset(pin["slug"], "tops", b"four", "four.jpg")

    assert first["cutout"] == "product1.png"
    assert third["cutout"] == "product3.png"
    assert fourth["cutout"] == "product4.png"
    assert (posts_dir / pin["slug"] / "cutout" / "product3.png").read_bytes() == b"cutout-three"


def test_pin_summary_carries_the_scheduled_time(posts_dir):
    pins.save_pin({
        "slug": "demo-1",
        "published_at": "2026-08-18T00:00:00+00:00",
        "scheduled_for": "2026-09-01T09:30:00",
        "scheduled_timezone": "Europe/London",
    })

    summary = pins.pin_summary(pins.load_pin("demo-1"))

    assert summary["status"] == "scheduled"
    assert summary["scheduledFor"] == "2026-09-01T09:30:00"
    assert summary["scheduledTimezone"] == "Europe/London"


# ---------- Batch creation ----------

def fake_resolve_product(url, monkeypatch, fail_on=()):
    """Stubs pins.resolve_product / pins.process_product so batch tests never
    touch the network. Raises for urls whose slug is in fail_on."""
    def resolve(u):
        if u in fail_on:
            raise pins.ProductFetchError(f"no affiliate tag for {u}")
        return {"title": f"Product for {u}", "url": u, "regionalUrls": {"UK": u}, "image_bytes": b"fake"}

    def process(u, index, directory):
        resolved = resolve(u)
        return {"title": resolved["title"], "url": resolved["url"], "regionalUrls": resolved["regionalUrls"], "cutout": f"product{index}.png"}

    monkeypatch.setattr(pins, "resolve_product", resolve)
    monkeypatch.setattr(pins, "process_product", process)
    monkeypatch.setattr(pins, "generate_seo_content", lambda *a, **k: {})


def test_build_pins_batch_creates_one_pin_per_group(posts_dir, monkeypatch):
    fake_resolve_product("", monkeypatch)

    result = pins.build_pins_batch("Autumn Look", "", url_groups=[
        ["https://www.amazon.co.uk/dp/B01"],
        ["https://www.amazon.co.uk/dp/B02", "https://www.amazon.co.uk/dp/B03"],
    ])

    assert result["errors"] == []
    assert len(result["pins"]) == 2
    # Same title for every pin, disambiguated by next_slug's usual -N suffix.
    assert [p["slug"] for p in result["pins"]] == ["autumn-look-1", "autumn-look-2"]
    assert all(p["title1"] == "Autumn Look" for p in result["pins"])
    assert len(result["pins"][1]["products"]) == 2


def test_build_pins_batch_skips_a_failing_group_without_losing_the_rest(posts_dir, monkeypatch):
    fake_resolve_product("", monkeypatch, fail_on={"https://www.amazon.co.uk/dp/BAD"})

    result = pins.build_pins_batch("Autumn Look", "", url_groups=[
        ["https://www.amazon.co.uk/dp/BAD"],
        ["https://www.amazon.co.uk/dp/B02"],
    ])

    assert len(result["pins"]) == 1
    # The failed group's directory is cleaned up (build_pin's usual behaviour
    # for a pin with zero products), which frees its slug for reuse.
    assert result["pins"][0]["slug"] == "autumn-look-1"
    assert len(result["errors"]) == 1
    assert "Pin 1" in result["errors"][0]


def test_build_pins_batch_ignores_empty_groups(posts_dir, monkeypatch):
    fake_resolve_product("", monkeypatch)

    result = pins.build_pins_batch("Autumn Look", "", url_groups=[[], ["https://www.amazon.co.uk/dp/B01"], []])

    assert len(result["pins"]) == 1


def test_create_generated_pins_batch_creates_one_pin_per_link(posts_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(pins, "INCOMING_CLOTHES_DIR", tmp_path)
    fake_resolve_product("", monkeypatch)

    result = pins.create_generated_pins_batch("Autumn Look", urls=[
        "https://www.amazon.co.uk/dp/B01", "https://www.amazon.co.uk/dp/B02",
    ])

    assert result["errors"] == []
    assert [p["slug"] for p in result["pins"]] == ["autumn-look-1", "autumn-look-2"]
    assert all(p["template"] == "generated" for p in result["pins"])
    assert all(p["title1"] == "Autumn Look" for p in result["pins"])
    assert (tmp_path / "autumn-look-1.jpg").exists()
    assert (tmp_path / "autumn-look-2.jpg").exists()


def test_create_generated_pins_batch_skips_a_failing_link(posts_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(pins, "INCOMING_CLOTHES_DIR", tmp_path)
    fake_resolve_product("", monkeypatch, fail_on={"https://www.amazon.co.uk/dp/BAD"})

    result = pins.create_generated_pins_batch("Autumn Look", urls=[
        "https://www.amazon.co.uk/dp/BAD", "https://www.amazon.co.uk/dp/B02",
    ])

    assert len(result["pins"]) == 1
    assert len(result["errors"]) == 1
    assert "Link 1" in result["errors"][0]


def test_only_the_collage_template_gets_a_landing_page(posts_dir, monkeypatch):
    monkeypatch.setattr(pins, "GITHUB_PAGES_BASE_URL", "https://example.test/shop")

    collage = {"slug": "collage-1", "template": "product", "products": [{"url": "https://amazon.test/a"}]}
    assert pins.has_landing_page(collage)
    assert pins.destination_link(collage) == "https://example.test/shop/collage-1.html"

    for template in ("outfit", "generated"):
        assert not pins.has_landing_page({"slug": "x-1", "template": template})


def test_an_outfit_links_to_a_garment_url_when_it_has_one(posts_dir):
    with_link = {
        "slug": "outfit-1", "template": "outfit",
        "layout": {"layers": [{"url": ""}, {"url": "https://amazon.test/top"}]},
    }
    assert pins.destination_link(with_link) == "https://amazon.test/top"

    from_products = {
        "slug": "outfit-2", "template": "outfit",
        "layout": {"layers": []}, "products": [{"url": "https://amazon.test/belt"}],
    }
    assert pins.destination_link(from_products) == "https://amazon.test/belt"


def test_an_outfit_with_no_garment_links_has_no_destination(posts_dir):
    pin = {
        "slug": "outfit-3", "template": "outfit",
        "layout": {"layers": [{"url": ""}, {"url": ""}]}, "products": [{"url": ""}],
    }

    assert pins.destination_link(pin) is None


def test_a_generated_pin_still_links_to_its_source_product(posts_dir):
    pin = {"slug": "gen-1", "template": "generated", "source_link": "https://amazon.test/gen"}

    assert pins.destination_link(pin) == "https://amazon.test/gen"


def _solid_pin(posts_dir, slug, size, colour):
    from PIL import Image

    (posts_dir / slug).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(posts_dir / slug / "pin.png")


def test_tiktok_frame_is_nine_by_sixteen(posts_dir):
    _solid_pin(posts_dir, "demo-1", (1000, 1500), (220, 231, 236))

    from PIL import Image

    frame = Image.open(pins.write_tiktok_frame("demo-1"))

    assert frame.size == (1080, 1920)
    assert (posts_dir / "demo-1" / "pin.png").exists(), "the 2:3 pin Pinterest uses is left alone"


def test_tiktok_frame_extends_the_background_rather_than_bar_it(posts_dir):
    """A flat background has to carry on into the added height — letterbox
    bars would be obvious against these flat-lays."""
    background = (220, 231, 236)
    _solid_pin(posts_dir, "demo-1", (1000, 1500), background)

    from PIL import Image

    frame = Image.open(pins.write_tiktok_frame("demo-1"))

    assert frame.getpixel((5, 2)) == background        # top padding
    assert frame.getpixel((5, 1917)) == background     # bottom padding
    assert frame.getpixel((5, 960)) == background      # the composition itself


def test_tiktok_frame_crops_a_source_already_taller_than_nine_by_sixteen(posts_dir):
    _solid_pin(posts_dir, "tall-1", (1000, 2400), (10, 20, 30))

    from PIL import Image

    frame = Image.open(pins.write_tiktok_frame("tall-1"))

    assert frame.size == (1080, 1920)


def test_tiktok_frame_needs_a_rendered_pin(posts_dir):
    (posts_dir / "empty-1").mkdir()

    with pytest.raises(FileNotFoundError):
        pins.write_tiktok_frame("empty-1")
