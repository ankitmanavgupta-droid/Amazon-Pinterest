"""Schedule a pin to post to Pinterest later, from the terminal.

    python schedule_pin.py <slug> "2026-08-20T09:30"
    python schedule_pin.py <slug> "2026-08-20T09:30" Europe/London

The time is wall-clock time in the given timezone (this machine's own zone if
you leave it off). Zernio holds the pin and publishes it at that moment, so
nothing here has to still be running when it fires.

The landing page is published first — a scheduled pin links straight to it, so
that part can't wait. The web app (python app.py) does the same with a picker.
"""

import sys

import pins
from publishing import publish_pin, schedule_pin


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__.strip())
        sys.exit(1)

    slug, when = sys.argv[1], sys.argv[2]
    tz_name = sys.argv[3] if len(sys.argv) == 4 else None

    try:
        if not pins.load_pin(slug).get("published_at"):
            print(f"  [{slug}] publishing landing page...")
            print(f"  [{slug}] published: {publish_pin(slug)}")
        print(f"  [{slug}] scheduled for {schedule_pin(slug, when, tz_name=tz_name)}")
    except Exception as e:
        print(f"Failed to schedule '{slug}': {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
