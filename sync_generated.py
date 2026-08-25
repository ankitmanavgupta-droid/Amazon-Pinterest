"""Pick up finished ChatGPT images and keep the drip-scheduled queue fed.

    python sync_generated.py

Does one pass of what `python app.py` polls for automatically every minute
while it's running: matches generated-images/*.png back to the pin waiting
for it, and — if drip scheduling is on — hands Zernio the next slot for
whatever just became ready. Safe to run repeatedly, e.g. from cron/launchd if
you'd rather not keep the web app running all the time.

Zernio still fires each scheduled post from its own servers, so a post already
scheduled goes out whether or not anything here is running — this script only
matters for *picking up new work*.
"""

import generated_pins
from pinterest.zernio_client import get_connected_pinterest_accounts, list_boards


def _board_names() -> list:
    """Best-effort board list for SEO's board pre-pick — matching pins.build_pin's
    own best-effort fetch, this just proceeds without one on any failure."""
    try:
        accounts = get_connected_pinterest_accounts()
        if not accounts:
            return None
        return [b["name"] for b in list_boards(accounts[0]["_id"])]
    except Exception:
        return None


def main():
    matched = generated_pins.sync_incoming_images(
        progress=lambda message: print(f"  {message}"), board_names=_board_names(),
    )
    if matched:
        print(f"Matched {len(matched)} generated image(s): {', '.join(matched)}")
    else:
        print("Nothing new in generated-images/.")

    scheduled = generated_pins.run_drip_schedule(progress=lambda message: print(f"  {message}"))
    if scheduled:
        for slug, when in scheduled:
            print(f"Scheduled '{slug}' for {when}")
    elif generated_pins.drip_status().get("enabled"):
        print("Drip scheduling is on; nothing in the queue to fill a slot right now.")


if __name__ == "__main__":
    main()
