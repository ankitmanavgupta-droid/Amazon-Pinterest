from datetime import datetime, timedelta, timezone

import pytest

import publishing
from pinterest import zernio_client


# ---------- Reading the requested time ----------

def soon(**kwargs) -> datetime:
    return datetime.now() + timedelta(**kwargs)


def test_parse_schedule_time_keeps_wall_clock_and_zone():
    """What the dashboard's datetime-local picker sends: no offset, plus the
    browser's zone — both go to Zernio as-is so 09:30 means 09:30 there."""
    when = soon(days=1).strftime("%Y-%m-%dT%H:%M")

    stamp, tz_name = publishing.parse_schedule_time(when, "Europe/London")

    assert stamp.startswith(when)
    assert tz_name == "Europe/London"


def test_parse_schedule_time_drops_the_zone_when_the_stamp_has_an_offset():
    when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")

    stamp, tz_name = publishing.parse_schedule_time(when, "Europe/London")

    assert stamp == when
    assert tz_name is None  # an explicit offset already pins the moment down


def test_parse_schedule_time_rejects_the_past():
    with pytest.raises(publishing.PublishError, match="in the past"):
        publishing.parse_schedule_time("2020-01-01T09:00", "Europe/London")


def test_parse_schedule_time_rejects_a_past_moment_with_an_offset():
    when = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")

    with pytest.raises(publishing.PublishError, match="in the past"):
        publishing.parse_schedule_time(when)


def test_parse_schedule_time_rejects_nonsense():
    with pytest.raises(publishing.PublishError, match="Couldn't read"):
        publishing.parse_schedule_time("next tuesday-ish")


def test_parse_schedule_time_rejects_an_unknown_zone():
    with pytest.raises(publishing.PublishError, match="Unknown timezone"):
        publishing.parse_schedule_time(soon(days=1).strftime("%Y-%m-%dT%H:%M"), "Mars/Olympus_Mons")


# ---------- What gets sent to Zernio ----------

@pytest.fixture
def captured_payload(monkeypatch):
    """Captures the request body instead of calling Zernio."""
    sent = {}

    class FakeResponse:
        ok = True
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"post": {"_id": "post123"}}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(json)
        return FakeResponse()

    monkeypatch.setattr(zernio_client.requests, "post", fake_post)
    monkeypatch.setattr(zernio_client, "_headers", lambda: {})
    return sent


def pin_args(**overrides):
    return {
        "account_id": "acc1",
        "board_id": "board1",
        "image_url": "https://cdn.test/pin.png",
        "link": "https://example.test/shop/demo-1.html",
        "description": "Shop the look",
        **overrides,
    }


def test_create_pin_publishes_now_by_default(captured_payload):
    zernio_client.create_pin(**pin_args())

    assert captured_payload["publishNow"] is True
    assert "scheduledFor" not in captured_payload


def test_create_pin_schedules_instead_of_publishing_now(captured_payload):
    zernio_client.create_pin(**pin_args(scheduled_for="2026-09-01T09:30:00", tz_name="Europe/London"))

    assert captured_payload["scheduledFor"] == "2026-09-01T09:30:00"
    assert captured_payload["timezone"] == "Europe/London"
    assert "publishNow" not in captured_payload  # sending both would be ambiguous
