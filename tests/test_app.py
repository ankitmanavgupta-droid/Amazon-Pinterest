import base64

import pins
import slideshows
import wardrobe
from app import app


def test_wardrobe_offers_a_post_all_ready_tiktok_action():
    with app.test_client() as client:
        page = client.get("/wardrobe").get_data(as_text=True)

    assert 'id="postAllReady"' in page
    assert "Post all ready to TikTok" in page
    assert "for (const request of requests)" in page
    assert "await sendSlideshowToDraft(request.id, request.caption)" in page


def test_reused_outfit_position_gets_a_new_cutout_url(tmp_path, monkeypatch):
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()
    monkeypatch.setattr(pins, "POSTS_DIR", posts_dir)
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: b"cutout-" + data)
    pin = pins.create_outfit_pin("Autumn", "Outfit")
    first = pins.add_outfit_asset(pin["slug"], "tops", b"old", "old.png")

    with app.test_client() as client:
        old_url = client.get(f"/api/pins/{pin['slug']}").get_json()["products"][0]["cutoutUrl"]

        pins.delete_outfit_asset(pin["slug"], first["id"])
        second = pins.add_outfit_asset(pin["slug"], "tops", b"new", "new.png")
        new_url = client.get(f"/api/pins/{pin['slug']}").get_json()["products"][0]["cutoutUrl"]

    assert old_url.split("?", 1)[0] == new_url.split("?", 1)[0]
    assert old_url.endswith(first["id"])
    assert new_url.endswith(second["id"])
    assert new_url != old_url


def test_saving_a_pin_keeps_the_layout_it_was_given(tmp_path, monkeypatch):
    """The wardrobe preview grid renders a saved pin from whatever the server
    holds. It used to send its own generated-at copy instead, which quietly
    undid anything moved in the editor since."""
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()
    monkeypatch.setattr(pins, "POSTS_DIR", posts_dir)
    monkeypatch.setattr(wardrobe, "WARDROBE_DIR", tmp_path / "wardrobe-items")
    monkeypatch.setattr(pins, "WARDROBE_DIR", tmp_path / "wardrobe-items")
    monkeypatch.setattr(slideshows, "SLIDESHOWS_FILE", tmp_path / ".slideshows.json")
    monkeypatch.setattr("amazon.background_removal.remove_background", lambda data: data)

    wardrobe.add_wardrobe_item("tops", b"tee", "tee.jpg")
    [pin] = wardrobe.generate_outfit_batch("Outfit", "", combine=[], random_spec={"tops": 1})
    slug = pin["slug"]
    image = f"data:image/png;base64,{base64.b64encode(b'edited').decode()}"

    with app.test_client() as client:
        moved = {**pin["layout"], "layers": [{**layer, "x": 999} for layer in pin["layout"]["layers"]]}
        client.post(f"/api/pins/{slug}/save", json={"image": image, "layout": moved, "hotspots": []})

        stored = client.get(f"/api/pins/{slug}").get_json()

    assert [layer["x"] for layer in stored["layout"]["layers"]] == [999]
    assert (posts_dir / slug / "pin.png").read_bytes() == b"edited"
