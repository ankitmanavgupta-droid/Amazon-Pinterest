"""Post an already-published pin to Pinterest.

    python post_pin.py <slug>

Use this when the landing page is already live and only the Pinterest post
needs doing (or re-doing). `publish_pin.py` covers the normal path.
"""

import sys

from publishing import post_pin


def main():
    if len(sys.argv) != 2:
        print("Usage: python post_pin.py <slug>")
        sys.exit(1)

    try:
        print(f"Posted: {post_pin(sys.argv[1])}")
    except Exception as e:
        print(f"Failed to post '{sys.argv[1]}': {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
