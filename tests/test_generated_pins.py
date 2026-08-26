from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import generated_pins
import pins
from pinterest.seo import SEOGenerationError


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Redirects every folder generated_pins.py touches into tmp_path."""
    posts = tmp_path / "posts"
    posts.mkdir()
    monkeypatch.setattr(pins, "POSTS_DIR", posts)

    layout = {}
    for name in ("INCOMING_CLOTHES_DIR", "GENERATED_IMAGES_DIR", "PROCESSED_INPUTS_DIR", "FAILED_INPUTS_DIR"):
        path = tmp_path / name
        monkeypatch.setattr(generated_pins, name, path)
        layout[name] = path

    monkeypatch.setattr(generated_pins, "IMAGEGEN_STATE_FILE", tmp_path / ".imagegen-state.json")
    monkeypatch.setattr(generated_pins, "DRIP_SCHEDULE_FILE", tmp_path / "drip_schedule.json")

    # Default to a no-op stub so no test hits the real Anthropic API by
    # accident — tests that care about SEO content override this themselves.
    monkeypatch.setattr(generated_pins, "generate_seo_content", lambda *a, **k: {})
    return layout


def awaiting_pin(slug: str, **overrides) -> dict:
    pin = {
        "slug": slug,
        "template": "generated",
        "title1": slug,
        "title2": "",
        "created_at": "2026-08-19T00:00:00+00:00",
        "source_link": f"https://www.amazon.co.uk/dp/{slug.upper()}",
        "products": [],
        "seo": {},
        "layout": {},
        "warnings": [],
        "published_at": None,
        "posted_at": None,
        "pin_url": None,
        **overrides,
    }
    pins.save_pin(pin)
    return pin


# ---------- Matching ----------

def test_sync_matches_a_generated_image_to_its_waiting_pin(dirs):
    awaiting_pin("autumn-cardigan")
    (dirs["GENERATED_IMAGES_DIR"]).mkdir()
    (dirs["GENERATED_IMAGES_DIR"] / "autumn-cardigan.png").write_bytes(b"fake png")

    matched = generated_pins.sync_incoming_images()

    assert matched == ["autumn-cardigan"]
    pin = pins.load_pin("autumn-cardigan")
    assert pin["published_at"]
    assert (pins.pin_dir("autumn-cardigan") / "pin.png").read_bytes() == b"fake png"
    assert pins.pin_status(pin) == "published"


def test_sync_moves_the_reference_image_to_processed_inputs(dirs):
    awaiting_pin("autumn-cardigan")
    dirs["INCOMING_CLOTHES_DIR"].mkdir()
    (dirs["INCOMING_CLOTHES_DIR"] / "autumn-cardigan.jpg").write_bytes(b"reference photo")
    dirs["GENERATED_IMAGES_DIR"].mkdir()
    (dirs["GENERATED_IMAGES_DIR"] / "autumn-cardigan.png").write_bytes(b"fake png")

    generated_pins.sync_incoming_images()

    assert not (dirs["INCOMING_CLOTHES_DIR"] / "autumn-cardigan.jpg").exists()
    assert (dirs["PROCESSED_INPUTS_DIR"] / "autumn-cardigan.jpg").read_bytes() == b"reference photo"


def test_sync_does_not_reprocess_a_generated_image_on_a_second_pass(dirs):
    """generated-images/ is a permanent archive, never emptied — the state
    file is what has to stop a re-scan from redoing the match."""
    awaiting_pin("autumn-cardigan")
    dirs["GENERATED_IMAGES_DIR"].mkdir()
    (dirs["GENERATED_IMAGES_DIR"] / "autumn-cardigan.png").write_bytes(b"fake png")

    first = generated_pins.sync_incoming_images()
    second = generated_pins.sync_incoming_images()

    assert first == ["autumn-cardigan"]
    assert second == []


def test_sync_ignores_images_with_no_matching_pin(dirs):
    dirs["GENERATED_IMAGES_DIR"].mkdir()
    (dirs["GENERATED_IMAGES_DIR"] / "mystery-file.png").write_bytes(b"???")

    assert generated_pins.sync_incoming_images() == []


def test_sync_ignores_product_template_pins(dirs):
    """A same-named product-collage pin must not have its image clobbered by
    an unrelated generated-images/ file."""
    pins.save_pin({"slug": "summer-tops-1", "template": "product", "products": [{"title": "x"}]})
    dirs["GENERATED_IMAGES_DIR"].mkdir()
    (dirs["GENERATED_IMAGES_DIR"] / "summer-tops-1.png").write_bytes(b"unrelated")

    assert generated_pins.sync_incoming_images() == []
    assert not (pins.pin_dir("summer-tops-1") / "pin.png").exists()


def test_sync_does_not_overwrite_a_pin_that_already_has_an_image(dirs):
    pin = awaiting_pin("autumn-cardigan")
    (pins.pin_dir("autumn-cardigan") / "pin.png").write_bytes(b"already rendered")
    dirs["GENERATED_IMAGES_DIR"].mkdir()
    (dirs["GENERATED_IMAGES_DIR"] / "autumn-cardigan.png").write_bytes(b"new attempt")

    generated_pins.sync_incoming_images()

    assert (pins.pin_dir("autumn-cardigan") / "pin.png").read_bytes() == b"already rendered"


def test_sync_generates_seo_using_the_product_title_not_a_custom_batch_title(dirs, monkeypatch):
    """title1 can be a common title shared across a batch — the real product
    name (product_title) is what has to reach SEO generation."""
    awaiting_pin("autumn-cardigan", title1="Autumn Look", product_title="Cosy Ribbed Cardigan")
    dirs["GENERATED_IMAGES_DIR"].mkdir()
    (dirs["GENERATED_IMAGES_DIR"] / "autumn-cardigan.png").write_bytes(b"fake png")

    captured = {}

    def fake_generate(product_titles, niche_hint="", board_names=None):
        captured.update(product_titles=product_titles, niche_hint=niche_hint, board_names=board_names)
        return {"title": "Cosy Ribbed Cardigan | Autumn Style", "description": "...", "hashtags": ["autumn"], "board": "Tops"}

    monkeypatch.setattr(generated_pins, "generate_seo_content", fake_generate)

    generated_pins.sync_incoming_images(board_names=["Tops", "Home Decor"])

    assert captured["product_titles"] == ["Cosy Ribbed Cardigan"]
    assert captured["niche_hint"] == "Autumn Look"
    assert captured["board_names"] == ["Tops", "Home Decor"]

    pin = pins.load_pin("autumn-cardigan")
    assert pin["seo"]["title"] == "Cosy Ribbed Cardigan | Autumn Style"
    assert pin["seo"]["board"] == "Tops"


def test_sync_still_publishes_when_seo_generation_fails(dirs, monkeypatch):
    """A pin with no generated-image folder to wait on shouldn't get stuck
    just because the SEO call failed — same tolerance build_pin has."""
    awaiting_pin("autumn-cardigan")
    dirs["GENERATED_IMAGES_DIR"].mkdir()
    (dirs["GENERATED_IMAGES_DIR"] / "autumn-cardigan.png").write_bytes(b"fake png")

    def failing_generate(*a, **k):
        raise SEOGenerationError("Missing ANTHROPIC_API_KEY.")

    monkeypatch.setattr(generated_pins, "generate_seo_content", failing_generate)

    matched = generated_pins.sync_incoming_images()

    assert matched == ["autumn-cardigan"]
    pin = pins.load_pin("autumn-cardigan")
    assert pin["published_at"]
    assert pin["seo"] == {}
    assert pins.pin_status(pin) == "published"


def test_mark_failed_moves_the_reference_image_out(dirs):
    awaiting_pin("autumn-cardigan")
    dirs["INCOMING_CLOTHES_DIR"].mkdir()
    (dirs["INCOMING_CLOTHES_DIR"] / "autumn-cardigan.jpg").write_bytes(b"abandoned")

    destination = generated_pins.mark_failed("autumn-cardigan")

    assert destination == dirs["FAILED_INPUTS_DIR"] / "autumn-cardigan.jpg"
    assert destination.read_bytes() == b"abandoned"
    assert not (dirs["INCOMING_CLOTHES_DIR"] / "autumn-cardigan.jpg").exists()


def test_mark_failed_raises_when_nothing_is_pending(dirs):
    with pytest.raises(generated_pins.ImageGenError, match="No pending"):
        generated_pins.mark_failed("nonexistent-slug")


# ---------- Drip scheduling ----------

def published_pin(slug: str, created_at: str, **overrides) -> dict:
    pin = {
        "slug": slug, "template": "product", "created_at": created_at,
        "published_at": "2026-08-19T00:00:00+00:00", "products": [{"title": "x"}],
        **overrides,
    }
    pins.save_pin(pin)
    (pins.pin_dir(slug)).mkdir(exist_ok=True)
    (pins.pin_dir(slug) / "pin.png").write_bytes(b"x")
    return pin


def test_ready_queue_is_oldest_first_and_excludes_scheduled_or_posted(dirs):
    published_pin("newer", created_at="2026-08-19T00:00:00+00:00")
    published_pin("older", created_at="2026-08-01T00:00:00+00:00")
    published_pin("already-scheduled", created_at="2026-08-10T00:00:00+00:00", scheduled_for="2026-09-01T00:00:00")
    published_pin("already-posted", created_at="2026-08-05T00:00:00+00:00", posted_at="2026-08-06T00:00:00+00:00")

    assert [p["slug"] for p in generated_pins.ready_queue()] == ["older", "newer"]


def test_enable_drip_defaults_the_first_slot_to_one_interval_from_now(dirs):
    config = generated_pins.enable_drip(3, tz_name="Europe/London")

    assert config["enabled"] is True
    assert config["interval_hours"] == 3
    when = datetime.fromisoformat(config["next_slot"])
    now = datetime.now(ZoneInfo("Europe/London"))
    assert timedelta(hours=2, minutes=55) < (when - now) < timedelta(hours=3, minutes=5)


def test_enable_drip_keeps_the_next_slot_when_only_the_interval_changes(dirs):
    first = generated_pins.enable_drip(3, tz_name="Europe/London")
    second = generated_pins.enable_drip(6, tz_name="Europe/London")

    assert second["next_slot"] == first["next_slot"]
    assert second["interval_hours"] == 6


def test_disable_drip_turns_it_off_without_losing_the_pointer(dirs):
    generated_pins.enable_drip(3)
    config = generated_pins.disable_drip()

    assert config["enabled"] is False
    assert config["next_slot"] is not None


def test_run_drip_schedule_does_nothing_when_disabled(dirs):
    published_pin("waiting", created_at="2026-08-01T00:00:00+00:00")

    assert generated_pins.run_drip_schedule() == []


def test_run_drip_schedule_assigns_increasing_slots_in_queue_order(dirs, monkeypatch):
    published_pin("first", created_at="2026-08-01T00:00:00+00:00")
    published_pin("second", created_at="2026-08-02T00:00:00+00:00")
    generated_pins.enable_drip(3, tz_name="Europe/London")

    sent = []
    monkeypatch.setattr(generated_pins, "schedule_pin", lambda slug, when, tz_name=None: sent.append((slug, when)) or when)

    scheduled = generated_pins.run_drip_schedule()

    assert [s for s, _ in scheduled] == ["first", "second"]
    first_time = datetime.fromisoformat(scheduled[0][1])
    second_time = datetime.fromisoformat(scheduled[1][1])
    assert second_time - first_time == timedelta(hours=3)


def test_run_drip_schedule_is_safe_to_call_again_once_caught_up(dirs, monkeypatch):
    """A pin that's already been scheduled must not be handed a second slot."""
    published_pin("only-one", created_at="2026-08-01T00:00:00+00:00")
    generated_pins.enable_drip(3)

    calls = []

    def fake_schedule(slug, when, tz_name=None):
        calls.append(slug)
        pin = pins.load_pin(slug)
        pin["scheduled_for"] = when
        pins.save_pin(pin)
        return when

    monkeypatch.setattr(generated_pins, "schedule_pin", fake_schedule)

    generated_pins.run_drip_schedule()
    generated_pins.run_drip_schedule()

    assert calls == ["only-one"]


def test_run_drip_schedule_catches_up_a_stale_pointer_instead_of_scheduling_in_the_past(dirs, monkeypatch):
    """If nothing was ready for a while (or the machine was asleep), the
    pointer can drift into the past — the next real slot must still be in
    the future, not silently rejected by Zernio for being backdated."""
    generated_pins.enable_drip(3, tz_name="Europe/London")
    config = generated_pins._load_drip()
    config["next_slot"] = (datetime.now() - timedelta(hours=10)).isoformat(timespec="seconds")
    generated_pins._save_drip(config)

    published_pin("late-arrival", created_at="2026-08-01T00:00:00+00:00")

    sent = {}
    monkeypatch.setattr(generated_pins, "schedule_pin", lambda slug, when, tz_name=None: sent.update(when=when) or when)

    generated_pins.run_drip_schedule()

    scheduled_dt = datetime.fromisoformat(sent["when"])
    assert scheduled_dt > datetime.now()


# ---------- A slot that has already gone by ----------

def _drip(dirs, next_slot, interval_hours=0.5, enabled=True):
    generated_pins._save_drip({
        "enabled": enabled, "interval_hours": interval_hours,
        "next_slot": next_slot, "timezone": "Europe/London",
    })


def test_a_lapsed_slot_rolls_forward_to_the_next_one_still_coming(dirs):
    """Slots tick by unused whenever nothing is waiting, so the stored time
    drifts into the past — the panel was reporting a slot from that morning."""
    london = ZoneInfo("Europe/London")
    now = datetime.now(london)
    ten_hours_ago = (now - timedelta(hours=10)).replace(microsecond=0)
    _drip(dirs, ten_hours_ago.isoformat(timespec="seconds"))

    slot = datetime.fromisoformat(generated_pins.drip_status()["next_slot"])

    assert slot > now
    assert slot - now <= timedelta(hours=0.5)
    # and stays on the cadence it was set to, rather than resetting to now+interval
    assert (slot - ten_hours_ago) % timedelta(hours=0.5) == timedelta(0)


def test_rolling_forward_is_written_back_not_just_reported(dirs):
    past = (datetime.now(ZoneInfo("Europe/London")) - timedelta(hours=3)).replace(microsecond=0)
    _drip(dirs, past.isoformat(timespec="seconds"))

    reported = generated_pins.drip_status()["next_slot"]

    assert generated_pins._load_drip()["next_slot"] == reported


def test_a_slot_still_in_the_future_is_left_alone(dirs):
    ahead = (datetime.now(ZoneInfo("Europe/London")) + timedelta(hours=2)).replace(microsecond=0)
    _drip(dirs, ahead.isoformat(timespec="seconds"))

    assert generated_pins.drip_status()["next_slot"] == ahead.isoformat(timespec="seconds")


def test_an_empty_queue_still_moves_the_slot_on(dirs):
    """It used to return before the catch-up, so with nothing published the
    stored slot fell further behind on every poll."""
    past = (datetime.now(ZoneInfo("Europe/London")) - timedelta(hours=6)).replace(microsecond=0)
    _drip(dirs, past.isoformat(timespec="seconds"))

    assert generated_pins.run_drip_schedule() == []
    assert datetime.fromisoformat(generated_pins._load_drip()["next_slot"]) > datetime.now(ZoneInfo("Europe/London"))


def test_a_disabled_feed_is_not_rolled_forward(dirs):
    past = (datetime.now(ZoneInfo("Europe/London")) - timedelta(hours=6)).replace(microsecond=0)
    _drip(dirs, past.isoformat(timespec="seconds"), enabled=False)

    assert generated_pins.drip_status()["next_slot"] == past.isoformat(timespec="seconds")
