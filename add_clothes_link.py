"""Start one or more generated-image pins from the terminal, all sharing one title.

    python add_clothes_link.py "Autumn Look" <amazon-url> [url2] [url3] ...

Resolves each link to its product photo and saves that to incoming-clothes/ as
the reference image. From there, for each one:

  1. Generate a pin image from it yourself (the ChatGPT step — outside this app).
  2. Save the result as generated-images/<slug>.png — the slug is printed below.
  3. Run `python sync_generated.py`, or leave `python app.py` running (it polls
     for this automatically), and the pin becomes postable.

The web app (python app.py) does the same thing from the dashboard's template
dropdown, including pasting several links at once — this stays for a quick
terminal drop.
"""

import sys

import pins


def main():
    if len(sys.argv) < 3:
        print(__doc__.strip())
        sys.exit(1)

    title = sys.argv[1]
    urls = sys.argv[2:]

    if len(urls) == 1:
        try:
            pin = pins.create_generated_pin(urls[0], title=title, progress=lambda message: print(f"  {message}"))
        except Exception as e:
            print(f"Failed: {e}")
            sys.exit(1)
        print(f"\nSlug: {pin['slug']}")
        print(f"When it's ready, save the generated image as: generated-images/{pin['slug']}.png")
        return

    result = pins.create_generated_pins_batch(title, urls, progress=lambda message: print(f"  {message}"))
    print(f"\nSaved {len(result['pins'])} of {len(urls)} reference photo(s):")
    for pin in result["pins"]:
        print(f"  {pin['slug']} -> generated-images/{pin['slug']}.png")
    if result["errors"]:
        print("\nFailed:")
        for error in result["errors"]:
            print(f"  {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
