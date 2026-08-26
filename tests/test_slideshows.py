import pytest

import pins
import slideshows


@pytest.fixture
def slideshow_file(tmp_path, monkeypatch):
    path = tmp_path / ".slideshows.json"
    monkeypatch.setattr(slideshows, "SLIDESHOWS_FILE", path)
    return path


@pytest.fixture
def posts_dir(tmp_path, monkeypatch):
    directory = tmp_path / "posts"
    directory.mkdir()
    monkeypatch.setattr(pins, "POSTS_DIR", directory)
    return directory


def outfit(slug, top, bottom, extra=None):
    """A pin shaped the way the wardrobe generator makes them."""
    products = [
        {"id": f"{slug}-top", "category": "tops", "wardrobeItemId": top, "title": top},
        {"id": f"{slug}-bottom", "category": "jeans", "wardrobeItemId": bottom, "title": bottom},
    ]
    if extra:
        products.append({"id": f"{slug}-x", "category": "watches", "wardrobeItemId": extra, "title": extra})
    return {"slug": slug, "products": products}


def tops_in(batch):
    return [slideshows.garment_id(pin, pins.TOP_CATEGORIES) for pin in batch]


def bottoms_in(batch):
    return [slideshows.garment_id(pin, pins.BOTTOM_CATEGORIES) for pin in batch]


def test_batches_are_size_three_with_no_repeated_top_or_bottom():
    outfits = [
        outfit("a", "tee", "blue"), outfit("b", "shirt", "black"), outfit("c", "polo", "grey"),
        outfit("d", "tee", "black"), outfit("e", "shirt", "grey"), outfit("f", "polo", "blue"),
    ]

    batches = slideshows.plan_batches(outfits)

    assert [len(batch) for batch in batches] == [3, 3]
    for batch in batches:
        assert len(set(tops_in(batch))) == 3, "a top was repeated inside a batch"
        assert len(set(bottoms_in(batch))) == 3, "a bottom was repeated inside a batch"


def test_every_outfit_lands_in_exactly_one_batch():
    outfits = [outfit(chr(97 + i), f"top{i}", f"bottom{i}") for i in range(7)]

    batches = slideshows.plan_batches(outfits)

    placed = [pin["slug"] for batch in batches for pin in batch]
    assert sorted(placed) == sorted(pin["slug"] for pin in outfits)
    assert len(placed) == len(set(placed))


def test_bottom_repeats_are_allowed_when_there_is_only_one_bottom():
    """Combining tops against a single pair of jeans: the top rule still has to
    hold, so the bottom rule is the one that gives way."""
    outfits = [outfit(s, top, "the-only-jeans") for s, top in
               [("a", "tee"), ("b", "shirt"), ("c", "polo"), ("d", "vest")]]

    batches = slideshows.plan_batches(outfits)

    assert [len(batch) for batch in batches] == [3, 1]
    for batch in batches:
        assert len(set(tops_in(batch))) == len(batch), "tops must stay unique even when bottoms repeat"
    assert bottoms_in(batches[0]) == ["the-only-jeans"] * 3


def test_packing_does_not_strand_duplicate_tops_in_single_slide_batches():
    """4 tops x 2 bottoms. Filling batches in order burns through the tops and
    leaves the last two outfits sharing a top, so they can't sit together —
    3+3+1+1. Spending the most plentiful tops first packs it as 3+3+2."""
    outfits = [
        outfit(f"{top}-{bottom}", top, bottom)
        for top in ("tee", "shirt", "polo", "vest")
        for bottom in ("blue", "black")
    ]

    batches = slideshows.plan_batches(outfits)

    assert [len(batch) for batch in batches] == [3, 3, 2]
    for batch in batches:
        assert len(set(tops_in(batch))) == len(batch)


def test_a_repeated_top_forces_a_new_batch_rather_than_being_dropped():
    outfits = [outfit("a", "tee", "blue"), outfit("b", "tee", "black"), outfit("c", "tee", "grey")]

    batches = slideshows.plan_batches(outfits)

    assert [len(batch) for batch in batches] == [1, 1, 1]


def test_warnings_flag_a_relaxed_batch_and_a_short_one():
    relaxed = [outfit("a", "tee", "jeans"), outfit("b", "shirt", "jeans"), outfit("c", "polo", "jeans")]
    assert any("Repeats a bottom" in warning for warning in slideshows.batch_warnings(relaxed))

    clean = [outfit("a", "tee", "blue"), outfit("b", "shirt", "black"), outfit("c", "polo", "grey")]
    assert slideshows.batch_warnings(clean) == []

    short = [outfit("a", "tee", "blue")]
    assert any("Only 1 slide" in warning for warning in slideshows.batch_warnings(short))


def test_outfits_without_a_bottom_still_batch():
    outfits = [
        {"slug": "a", "products": [{"id": "1", "category": "tops", "wardrobeItemId": "tee"}]},
        {"slug": "b", "products": [{"id": "2", "category": "tops", "wardrobeItemId": "shirt"}]},
    ]

    batches = slideshows.plan_batches(outfits)

    assert [len(batch) for batch in batches] == [2]


# ---------- Storage ----------

def test_create_batches_persists_and_skips_already_batched_pins(slideshow_file, posts_dir):
    for slug, top, bottom in [("a", "tee", "blue"), ("b", "shirt", "black"), ("c", "polo", "grey")]:
        pins.save_pin(outfit(slug, top, bottom))

    created = slideshows.create_batches_for(["a", "b", "c"])
    assert len(created) == 1
    assert sorted(created[0]["slugs"]) == ["a", "b", "c"]

    # Re-running doesn't duplicate pins into a second slideshow.
    again = slideshows.create_batches_for(["a", "b", "c"])
    assert again == []
    assert len(slideshows.load_slideshows()["slideshows"]) == 1


def test_set_arrangement_moves_a_slide_between_batches(slideshow_file, posts_dir):
    for slug, top in [("a", "tee"), ("b", "shirt"), ("c", "polo"), ("d", "vest")]:
        pins.save_pin(outfit(slug, top, "jeans"))
    slideshows.create_batches_for(["a", "b", "c", "d"])
    first, second = slideshows.load_slideshows()["slideshows"]

    rebuilt = slideshows.set_arrangement([
        {"id": first["id"], "slugs": ["a", "b"]},
        {"id": second["id"], "slugs": ["c", "d"]},
    ])

    assert [show["slugs"] for show in rebuilt] == [["a", "b"], ["c", "d"]]


def test_set_arrangement_drops_emptied_batches_and_deduplicates(slideshow_file, posts_dir):
    for slug, top in [("a", "tee"), ("b", "shirt")]:
        pins.save_pin(outfit(slug, top, "jeans"))
    slideshows.create_batches_for(["a", "b"])
    show = slideshows.load_slideshows()["slideshows"][0]

    rebuilt = slideshows.set_arrangement([
        {"id": show["id"], "slugs": ["a", "b"]},
        {"id": "new-one", "slugs": ["a"]},  # 'a' already claimed above
        {"id": "another", "slugs": []},
    ])

    assert [s["slugs"] for s in rebuilt] == [["a", "b"]]


def test_a_posted_slideshow_is_not_regrouped(slideshow_file, posts_dir):
    for slug, top in [("a", "tee"), ("b", "shirt")]:
        pins.save_pin(outfit(slug, top, "jeans"))
    slideshows.create_batches_for(["a", "b"])
    show = slideshows.load_slideshows()["slideshows"][0]
    slideshows.update_slideshow(show["id"], posted_at="2026-08-25T10:00:00+00:00")

    rebuilt = slideshows.set_arrangement([{"id": show["id"], "slugs": ["a"]}])

    assert rebuilt[0]["slugs"] == ["a", "b"], "a posted slideshow keeps the grouping that went out"


def test_prune_drops_slugs_whose_pin_was_deleted(slideshow_file, posts_dir):
    for slug, top in [("a", "tee"), ("b", "shirt")]:
        pins.save_pin(outfit(slug, top, "jeans"))
    slideshows.create_batches_for(["a", "b"])

    pins.delete_pin("a")
    data = slideshows.prune_missing_pins()

    assert data["slideshows"][0]["slugs"] == ["b"]


def test_summary_reports_unrendered_slides_as_not_ready(slideshow_file, posts_dir):
    for slug, top in [("a", "tee"), ("b", "shirt")]:
        pins.save_pin(outfit(slug, top, "jeans"))
    slideshows.create_batches_for(["a", "b"])

    summary = slideshows.list_slideshows()[0]
    assert summary["unrendered"] == ["a", "b"]
    assert summary["ready"] is False

    for slug in ("a", "b"):
        (posts_dir / slug / "pin.png").write_bytes(b"rendered")

    summary = slideshows.list_slideshows()[0]
    assert summary["unrendered"] == []
    assert summary["ready"] is True


# ---------- Reporting what Zernio actually said ----------

def test_failure_reason_comes_from_error_message():
    """status: "failed" on its own told us nothing — the sentence a person can
    act on is in errorMessage, which the first version of this missed."""
    from zernio import zernio_failure

    result = {
        "post": {"platforms": [{
            "platform": "tiktok", "status": "failed",
            "errorMessage": "TikTok direct posting is at capacity right now.",
        }]},
        "error": "All platforms failed",
    }

    assert zernio_failure(result) == "TikTok direct posting is at capacity right now."


def test_failure_reason_falls_back_through_the_other_shapes():
    from zernio import zernio_failure

    assert zernio_failure({"platformResults": [{"error": "nope"}]}) == "nope"
    assert zernio_failure({"error": "All platforms failed"}) == "All platforms failed"
    assert zernio_failure({"post": {"platforms": [{"status": "success"}]}}) == ""


def test_a_slideshow_finished_in_the_tiktok_app_leaves_the_dashboard(slideshow_file, posts_dir):
    """Marked done, it stays on record so its outfits aren't batched again,
    but the dashboard only lists what's still outstanding."""
    for slug, top in [("a", "tee"), ("b", "shirt")]:
        pins.save_pin(outfit(slug, top, "jeans"))
        (posts_dir / slug / "pin.png").write_bytes(b"rendered")
    slideshows.create_batches_for(["a", "b"])
    show = slideshows.load_slideshows()["slideshows"][0]

    assert len(slideshows.list_slideshows()) == 1
    slideshows.update_slideshow(show["id"], done_at="2026-08-26T10:00:00+00:00")

    assert slideshows.list_slideshows() == []
    assert len(slideshows.list_slideshows(include_done=True)) == 1
    # still on record, so re-batching those pins is a no-op
    assert slideshows.create_batches_for(["a", "b"]) == []


def test_a_drafted_slideshow_is_not_offered_for_sending_again(slideshow_file, posts_dir):
    for slug, top in [("a", "tee"), ("b", "shirt")]:
        pins.save_pin(outfit(slug, top, "jeans"))
        (posts_dir / slug / "pin.png").write_bytes(b"rendered")
    slideshows.create_batches_for(["a", "b"])
    show = slideshows.load_slideshows()["slideshows"][0]

    assert slideshows.list_slideshows()[0]["ready"] is True

    slideshows.update_slideshow(show["id"], drafted_at="2026-08-26T10:00:00+00:00")
    summary = slideshows.list_slideshows()[0]

    assert summary["ready"] is False
    assert summary["draftedAt"]


def test_a_slideshow_offers_the_default_caption_until_one_is_written(slideshow_file, posts_dir):
    for slug, top in [("a", "tee"), ("b", "shirt")]:
        pins.save_pin(outfit(slug, top, "jeans"))
    slideshows.create_batches_for(["a", "b"])
    show = slideshows.load_slideshows()["slideshows"][0]

    assert slideshows.list_slideshows()[0]["caption"] == slideshows.DEFAULT_TIKTOK_CAPTION
    assert "#outfitinspo" in slideshows.DEFAULT_TIKTOK_CAPTION

    slideshows.update_slideshow(show["id"], caption="my own words")
    assert slideshows.list_slideshows()[0]["caption"] == "my own words"
