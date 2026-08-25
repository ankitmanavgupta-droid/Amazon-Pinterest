"""Publish pins' landing pages and post them to Pinterest, from the terminal.

    python publish_pin.py <slug>    publish + post one pin
    python publish_pin.py           publish + post every pin that's ready

"Ready" means the pin has been laid out and saved in the editor. The web app
(python app.py) does the same thing with a UI.
"""

import sys

import pins
from publishing import post_pin, publish_pin


def ready_slugs() -> list:
    """Pins that have been rendered but not yet posted."""
    return [
        pin["slug"]
        for pin in pins.list_pins()
        if pins.pin_status(pin) in ("rendered", "published")
    ]


def publish_and_post(slug: str) -> bool:
    pin = pins.load_pin(slug)

    try:
        if not pin.get("published_at"):
            live_url = publish_pin(slug)
            print(f"  [{slug}] published: {live_url}")
        pin_url = post_pin(slug)
        print(f"  [{slug}] posted: {pin_url}")
        return True
    except Exception as e:
        print(f"  [{slug}] failed: {e}")
        return False


def main():
    if len(sys.argv) == 2:
        slugs = [sys.argv[1]]
    else:
        slugs = ready_slugs()
        if not slugs:
            print("Nothing is ready to publish. Lay a pin out in the editor and save it first.")
            return
        print(f"Found {len(slugs)} pin(s) ready: {', '.join(slugs)}")

    succeeded = sum(publish_and_post(slug) for slug in slugs)
    print(f"\n{succeeded} of {len(slugs)} pin(s) published and posted.")
    if succeeded < len(slugs):
        sys.exit(1)


if __name__ == "__main__":
    main()
