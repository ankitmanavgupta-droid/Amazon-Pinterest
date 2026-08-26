import pytest

import pins
import wardrobe


@pytest.fixture
def wardrobe_dir(tmp_path, monkeypatch):
    directory = tmp_path / "wardrobe-items"
    monkeypatch.setattr(wardrobe, "WARDROBE_DIR", directory)
    monkeypatch.setattr(pins, "WARDROBE_DIR", directory)
    return directory


@pytest.fixture
def posts_dir(tmp_path, monkeypatch):
    directory = tmp_path / "posts"
    directory.mkdir()
    monkeypatch.setattr(pins, "POSTS_DIR", directory)
    return directory


def test_load_wardrobe_defaults_when_missing(wardrobe_dir):
    data = wardrobe.load_wardrobe()

    assert [s["id"] for s in data["sections"]] == ["tops", "jumpers", "jeans", "shoes", "belts", "watches", "fragrance"]
    assert data["items"] == []
    assert data["recipes"] == []


def test_add_wardrobe_item_removes_background_and_categorises(wardrobe_dir, monkeypatch):
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: b"transparent-" + data)

    item = wardrobe.add_wardrobe_item("tops", b"photo", "linen-shirt.jpg")
    saved = wardrobe.load_wardrobe()

    assert item["category"] == "tops"
    assert item["title"] == "linen-shirt"
    assert item["archived"] is False
    assert saved["items"] == [item]
    assert (wardrobe_dir / "cutout" / "item1.png").read_bytes() == b"transparent-photo"


def test_add_wardrobe_item_rejects_unknown_category(wardrobe_dir):
    with pytest.raises(ValueError, match="Unknown wardrobe category"):
        wardrobe.add_wardrobe_item("hats-and-magic", b"photo", "hat.jpg")


def test_move_archive_restore_and_delete_item(wardrobe_dir, monkeypatch):
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: b"transparent")
    item = wardrobe.add_wardrobe_item("tops", b"photo", "shirt.jpg")

    moved = wardrobe.move_wardrobe_item(item["id"], "jumpers")
    assert moved["category"] == "jumpers"

    wardrobe.archive_wardrobe_items([item["id"]])
    data = wardrobe.load_wardrobe()
    assert data["items"][0]["archived"] is True
    assert wardrobe.active_items(data, "jumpers") == []

    wardrobe.restore_wardrobe_items([item["id"]])
    data = wardrobe.load_wardrobe()
    assert data["items"][0]["archived"] is False
    assert len(wardrobe.active_items(data, "jumpers")) == 1

    wardrobe.delete_wardrobe_item(item["id"])
    assert wardrobe.load_wardrobe()["items"] == []
    assert not (wardrobe_dir / "cutout" / "item1.png").exists()


def _add(wardrobe_dir_unused, category, label):
    return wardrobe.add_wardrobe_item(category, label.encode(), f"{label}.jpg")


def test_generate_outfit_batch_combines_exhaustively_and_rolls_random_independently(
    wardrobe_dir, posts_dir, monkeypatch,
):
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: data)
    _add(wardrobe_dir, "jeans", "blue-jeans")
    _add(wardrobe_dir, "jeans", "black-jeans")
    _add(wardrobe_dir, "tops", "white-tee")
    _add(wardrobe_dir, "tops", "striped-tee")
    _add(wardrobe_dir, "watches", "gold-watch")
    _add(wardrobe_dir, "watches", "silver-watch")

    created = wardrobe.generate_outfit_batch("Outfit", "Edit", combine=["jeans", "tops"], random_spec={"watches": 1})

    assert len(created) == 4  # 2 jeans x 2 tops
    combos = {
        tuple(sorted(product["title"] for product in pin["products"] if product["category"] in ("jeans", "tops")))
        for pin in created
    }
    assert len(combos) == 4  # every jeans/tops pair is represented exactly once

    watch_titles = [next(p["title"] for p in pin["products"] if p["category"] == "watches") for pin in created]
    assert set(watch_titles) <= {"gold-watch", "silver-watch"}
    for pin in created:
        assert pin["layout"]["backgroundChoice"] == "blue"
        assert pin["layout"]["title1"] == "" and pin["layout"]["title2"] == ""
        assert pin["generated_from"] == {"combine": ["jeans", "tops"], "random": ["watches"]}


def test_layout_gives_the_garments_the_left_column_at_over_half_the_frame():
    """The garments are the subject — they were coming out at 40% of the width
    and reading as an afterthought beside the accessories."""
    rects = pins._arrange_wardrobe_layout(["tops", "jeans", "belts", "watches", "shoes"])
    top, jeans = rects[0], rects[1]

    assert top["w"] / pins.CANVAS_W > 0.5
    assert jeans["w"] / pins.CANVAS_W > 0.45
    assert top["x"] == pins.LEFT_ZONE_X
    assert top["y"] + top["h"] <= jeans["y"]
    # and comfortably larger than anything in the accessory column
    assert top["w"] > max(rect["w"] for rect in rects[2:])


def test_layout_orders_the_right_column_headphones_first_and_shoes_last():
    categories = ["tops", "jeans", "shoes", "belts", "glasses", "headphones-2", "fragrance", "watches"]
    rects = dict(zip(categories, pins._arrange_wardrobe_layout(categories)))

    column = ["headphones-2", "glasses", "belts", "fragrance", "shoes"]
    tops = [rects[category]["y"] for category in column]
    assert tops == sorted(tops), "the accessory column runs headphones -> glasses -> belt -> fragrance -> shoes"


def test_layout_pairs_the_fragrance_and_watch_on_one_row():
    categories = ["fragrance", "watches"]
    fragrance, watch = pins._arrange_wardrobe_layout(categories)

    assert fragrance["y"] == watch["y"]
    assert fragrance["h"] == watch["h"]
    assert watch["x"] >= fragrance["x"] + fragrance["w"], "they sit side by side, not overlapping"


def test_layout_gives_the_shoes_the_biggest_accessory_slot():
    categories = ["shoes", "belts", "watches", "glasses"]
    rects = dict(zip(categories, pins._arrange_wardrobe_layout(categories)))

    assert rects["shoes"]["h"] > rects["belts"]["h"]
    assert rects["shoes"]["h"] > rects["glasses"]["h"]


def test_layout_matches_a_renamed_or_duplicated_section_to_its_slot():
    """'+ Section' suffixes a duplicate name, so headphones-2 has to land in
    the headphones slot rather than being treated as something unknown."""
    assert pins.slot_key("headphones-2") == "headphones"
    assert pins.slot_key("Sunglasses") == "glasses"
    assert pins.slot_key("Perfume") == "fragrance"
    assert pins.slot_key("trainers") == "shoes"

    suffixed = pins._arrange_wardrobe_layout(["headphones-2"])[0]
    plain = pins._arrange_wardrobe_layout(["headphones"])[0]
    assert suffixed == plain


def test_accessories_keep_their_size_however_many_there_are():
    """They used to share out the column, so a look with fewer accessories blew
    them up to fill it. Sizes are absolute now and the slack becomes spacing."""
    many = dict(zip(
        ["glasses", "belts", "shoes"], pins._arrange_wardrobe_layout(["glasses", "belts", "shoes"])))
    few = dict(zip(["belts", "shoes"], pins._arrange_wardrobe_layout(["belts", "shoes"])))

    assert few["belts"]["h"] == pytest.approx(many["belts"]["h"], abs=0.01)
    assert few["shoes"]["h"] == pytest.approx(many["shoes"]["h"], abs=0.01)
    assert few["shoes"]["y"] + few["shoes"]["h"] <= pins.LAYOUT_BOTTOM_MARGIN + 0.01


def test_the_garments_fill_almost_the_whole_height():
    top, jeans = pins._arrange_wardrobe_layout(["tops", "jeans"])
    span = (jeans["y"] + jeans["h"]) - top["y"]

    assert span / pins.CANVAS_H > 0.9, "top and jeans should run nearly the full frame"
    assert jeans["y"] - (top["y"] + top["h"]) < 20, "and sit close together, not spread apart"


def test_accessories_stay_much_smaller_than_the_garments():
    categories = ["tops", "jeans", "headphones", "belts", "shoes"]
    rects = dict(zip(categories, pins._arrange_wardrobe_layout(categories)))

    biggest_accessory = max(rects[c]["w"] for c in ("headphones", "belts", "shoes"))
    assert biggest_accessory < rects["tops"]["w"] * 0.6
    assert rects["belts"]["h"] < rects["headphones"]["h"], "the belt is the slimmest row"


def test_layout_keeps_everything_inside_the_canvas():
    categories = ["tops", "jumpers", "jeans", "shoes", "belts", "watches", "fragrance", "glasses", "headphones-2"]

    for rect in pins._arrange_wardrobe_layout(categories):
        assert 0 <= rect["x"] and rect["x"] + rect["w"] <= pins.CANVAS_W + 0.01
        assert 0 <= rect["y"] and rect["y"] + rect["h"] <= pins.CANVAS_H + 0.01


def test_generate_outfit_batch_rejects_empty_combine_category(wardrobe_dir, posts_dir, monkeypatch):
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: data)
    _add(wardrobe_dir, "tops", "white-tee")

    with pytest.raises(ValueError, match="No available jeans"):
        wardrobe.generate_outfit_batch("Outfit", "Edit", combine=["jeans", "tops"], random_spec={})


def test_generate_outfit_batch_rejects_when_over_combination_cap(wardrobe_dir, posts_dir, monkeypatch):
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: data)
    monkeypatch.setattr(wardrobe, "MAX_GENERATED_COMBINATIONS", 3)
    for index in range(4):
        _add(wardrobe_dir, "tops", f"tee-{index}")

    with pytest.raises(ValueError, match="narrow the combined"):
        wardrobe.generate_outfit_batch("Outfit", "Edit", combine=["tops"], random_spec={})


def test_reroll_only_changes_random_categories_and_keeps_layer_position(wardrobe_dir, posts_dir, monkeypatch):
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: data)
    _add(wardrobe_dir, "jeans", "blue-jeans")
    _add(wardrobe_dir, "watches", "gold-watch")
    _add(wardrobe_dir, "watches", "silver-watch")

    [pin] = wardrobe.generate_outfit_batch("Outfit", "Edit", combine=["jeans"], random_spec={"watches": 1})
    jeans_before = next(p for p in pin["products"] if p["category"] == "jeans")
    watch_before = next(p for p in pin["products"] if p["category"] == "watches")
    layer_before = next(layer for layer in pin["layout"]["layers"] if layer["productId"] == watch_before["id"])

    updated = pins.reroll_generated_categories(pin["slug"])

    jeans_after = next(p for p in updated["products"] if p["category"] == "jeans")
    watch_after = next(p for p in updated["products"] if p["category"] == "watches")
    layer_after = next(layer for layer in updated["layout"]["layers"] if layer["productId"] == layer_before["productId"])

    assert jeans_after["wardrobeItemId"] == jeans_before["wardrobeItemId"]  # combine category untouched
    assert watch_after["wardrobeItemId"] != watch_before["wardrobeItemId"]  # random category rerolled
    assert watch_after["title"] != watch_before["title"]
    assert {watch_before["title"], watch_after["title"]} == {"gold-watch", "silver-watch"}
    assert watch_after["id"] == watch_before["id"]  # same product/layer slot, just a new image
    assert watch_after["v"] == 2
    assert layer_after["x"] == layer_before["x"] and layer_after["y"] == layer_before["y"]


def test_reroll_rejects_non_generated_pin(posts_dir):
    pin = pins.create_outfit_pin("Autumn", "Outfit")

    with pytest.raises(ValueError, match="no randomly-picked categories"):
        pins.reroll_generated_categories(pin["slug"])


def test_add_and_delete_recipe(wardrobe_dir):
    recipe = wardrobe.add_recipe("Weekend casual", ["jeans", "tops"], {"watches": 1})
    assert wardrobe.load_wardrobe()["recipes"] == [recipe]

    wardrobe.delete_recipe(recipe["id"])
    assert wardrobe.load_wardrobe()["recipes"] == []


def test_layer_box_is_tightened_around_its_cutout(wardrobe_dir, posts_dir, monkeypatch):
    """A tall bottle dropped into a squarish slot used to keep the slot's box,
    leaving the picture floating in dead space with the resize handle out in
    the empty part rather than on the image."""
    import io

    from PIL import Image

    def png(width, height):
        buffer = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 255)).save(buffer, "PNG")
        return buffer.getvalue()

    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: data)
    wardrobe.add_wardrobe_item("fragrance", png(300, 1000), "bottle.png")

    [pin] = wardrobe.generate_outfit_batch("Outfit", "", combine=[], random_spec={"fragrance": 1})
    layer = pin["layout"]["layers"][0]
    slot = pins._arrange_wardrobe_layout(["fragrance"])[0]

    assert layer["w"] / layer["h"] == pytest.approx(300 / 1000, abs=0.001)
    # Tight on at least one axis, and never spilling out of the slot.
    assert layer["w"] <= slot["w"] + 0.01 and layer["h"] <= slot["h"] + 0.01
    assert layer["w"] == pytest.approx(slot["w"], abs=0.01) or layer["h"] == pytest.approx(slot["h"], abs=0.01)
    # Still centred in the slot it was given, so it renders where it did before.
    assert layer["x"] + layer["w"] / 2 == pytest.approx(slot["x"] + slot["w"] / 2, abs=0.01)
    assert layer["y"] + layer["h"] / 2 == pytest.approx(slot["y"] + slot["h"] / 2, abs=0.01)


def test_fitting_leaves_an_already_matching_box_alone(tmp_path):
    import io

    from PIL import Image

    path = tmp_path / "square.png"
    Image.new("RGBA", (200, 200), (0, 0, 0, 255)).save(path, "PNG")

    rect = {"x": 10.0, "y": 20.0, "w": 100.0, "h": 100.0}
    assert pins._fit_rect_to_image(rect, path) == rect


def test_fitting_survives_an_unreadable_cutout(tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not actually a png")
    rect = {"x": 1.0, "y": 2.0, "w": 50.0, "h": 60.0}

    assert pins._fit_rect_to_image(rect, broken) == rect


def test_deleting_a_section_can_move_its_items_elsewhere(wardrobe_dir, monkeypatch):
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: data)
    item = wardrobe.add_wardrobe_item("belts", b"belt", "belt.jpg")

    wardrobe.delete_wardrobe_section("belts", move_items_to="tops")
    data = wardrobe.load_wardrobe()

    assert "belts" not in [section["id"] for section in data["sections"]]
    assert data["items"][0]["id"] == item["id"]
    assert data["items"][0]["category"] == "tops"


def test_deleting_a_section_without_a_target_removes_its_items(wardrobe_dir, monkeypatch):
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: data)
    wardrobe.add_wardrobe_item("belts", b"belt", "belt.jpg")
    kept = wardrobe.add_wardrobe_item("tops", b"tee", "tee.jpg")

    wardrobe.delete_wardrobe_section("belts")
    data = wardrobe.load_wardrobe()

    assert [item["id"] for item in data["items"]] == [kept["id"]]
    assert not (wardrobe_dir / "cutout" / "item1.png").exists()


def test_deleting_a_section_prunes_it_out_of_saved_recipes(wardrobe_dir):
    wardrobe.add_recipe("Weekend", ["tops", "belts"], {"belts": 1, "watches": 1})

    wardrobe.delete_wardrobe_section("belts")
    recipe = wardrobe.load_wardrobe()["recipes"][0]

    assert recipe["combine"] == ["tops"]
    assert recipe["random"] == {"watches": 1}


def test_the_last_section_cannot_be_deleted(wardrobe_dir):
    data = wardrobe.load_wardrobe()
    data["sections"] = [{"id": "tops", "label": "Tops"}]
    wardrobe.save_wardrobe(data)

    with pytest.raises(ValueError, match="at least one section"):
        wardrobe.delete_wardrobe_section("tops")


def test_deleting_a_section_into_itself_is_rejected(wardrobe_dir):
    with pytest.raises(ValueError, match="can't be moved into"):
        wardrobe.delete_wardrobe_section("belts", move_items_to="belts")


def test_accessory_row_heights_match_what_was_asked_for():
    """These are tuned by eye against the reference flat-lays, so pin them —
    a stray edit to one row silently reshapes every generated outfit."""
    categories = ["tops", "jeans", "headphones", "glasses", "belts", "fragrance", "watches", "accessories", "shoes"]
    rects = dict(zip(categories, pins._arrange_wardrobe_layout(categories)))

    def percent(category):
        return rects[category]["h"] / pins.CANVAS_H * 100

    assert percent("glasses") == pytest.approx(7.2, abs=0.05)
    assert percent("belts") == pytest.approx(7.2, abs=0.05)
    assert percent("accessories") == pytest.approx(5.0, abs=0.05)
    assert percent("shoes") == pytest.approx(20.0, abs=0.05)


def test_an_unnamed_section_gets_the_small_accessory_height():
    rects = dict(zip(["tops", "cufflinks"], pins._arrange_wardrobe_layout(["tops", "cufflinks"])))

    assert rects["cufflinks"]["h"] / pins.CANVAS_H * 100 == pytest.approx(5.0, abs=0.05)


def _overlaps(a, b):
    return not (a["x"] + a["w"] <= b["x"] + 0.01 or b["x"] + b["w"] <= a["x"] + 0.01
                or a["y"] + a["h"] <= b["y"] + 0.01 or b["y"] + b["h"] <= a["y"] + 0.01)


def test_several_items_of_one_category_sit_side_by_side():
    """Three accessories used to be offset 14px from each other, which just
    piled them up. They share the slot's width instead."""
    categories = ["tops", "jeans", "accessories", "accessories", "accessories"]
    rects = pins._arrange_wardrobe_layout(categories)
    first, second, third = rects[2], rects[3], rects[4]

    assert first["y"] == second["y"] == third["y"]
    assert first["x"] + first["w"] <= second["x"] + 0.01
    assert second["x"] + second["w"] <= third["x"] + 0.01
    for left, right in ((first, second), (second, third), (first, third)):
        assert not _overlaps(left, right)


def test_categories_sharing_a_slot_do_not_land_on_each_other():
    """A top and a jumper are both the upper garment — placed independently
    they came out at identical coordinates."""
    categories = ["tops", "jumpers", "jeans"]
    top, jumper, _ = pins._arrange_wardrobe_layout(categories)

    assert not _overlaps(top, jumper)


def test_no_two_pieces_overlap_in_a_full_wardrobe():
    categories = [
        "tops", "jumpers", "jeans", "headphones-2", "glasses", "belts",
        "fragrance", "watches", "accessories", "accessories", "shoes",
    ]
    rects = pins._arrange_wardrobe_layout(categories)

    clashes = [
        (categories[i], categories[j])
        for i in range(len(rects)) for j in range(i + 1, len(rects))
        if _overlaps(rects[i], rects[j])
    ]
    assert clashes == []
