"""Build pins from the command line.

    python build_pin.py links.txt     build every pin group in the file
    python build_pin.py               build one pin, prompting for details

The web app (python app.py) does the same thing with a UI; this stays for
bulk runs from a terminal.
"""

import sys
from pathlib import Path

import pins
from config import MAX_PRODUCTS


def parse_batch_file(path: Path) -> list:
    """Parses a text file where each pin starts with two '#' lines — title line
    1, then title line 2 — followed by that pin's product URLs:

        #Beach Outfits
        #From Amazon
        https://www.amazon.co.uk/dp/...
        https://www.amazon.co.uk/dp/...

        #Gym Essentials
        #Get Fit
        https://www.amazon.com/dp/...
    """
    lines = [line.strip() for line in path.read_text().splitlines()]

    groups = []
    i = 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue
        if not lines[i].startswith("#"):
            raise ValueError(f"Expected a '#' title line, got: {lines[i]!r}")

        title1 = lines[i].lstrip("#").strip()
        i += 1

        title2 = ""
        if i < len(lines) and lines[i].startswith("#"):
            title2 = lines[i].lstrip("#").strip()
            i += 1

        urls = []
        while i < len(lines) and lines[i] and not lines[i].startswith("#"):
            urls.append(lines[i])
            i += 1

        groups.append({"title1": title1, "title2": title2, "urls": urls})

    return groups


def prompt_for_pin() -> dict:
    title1 = input("Title line 1 (e.g. Summer Tops): ").strip()
    title2 = input("Title line 2 (e.g. From Amazon): ").strip()
    print(f"Paste Amazon product URLs, one per line (max {MAX_PRODUCTS}). Blank line to finish:")

    urls = []
    while len(urls) < MAX_PRODUCTS:
        line = input("> ").strip()
        if not line:
            break
        urls.append(line)

    return {"title1": title1, "title2": title2, "urls": urls}


def main():
    if len(sys.argv) == 2:
        groups = parse_batch_file(Path(sys.argv[1]))
    else:
        groups = [prompt_for_pin()]

    groups = [g for g in groups if g["title1"] and g["urls"]]
    if not groups:
        print("Nothing to build.")
        sys.exit(1)

    built = []
    for group in groups:
        print(f"\n=== {group['title1']} {group['title2']} ===")
        try:
            pin = pins.build_pin(
                group["title1"], group["title2"], group["urls"],
                progress=lambda message: print(f"  {message}"),
            )
            built.append(pin["slug"])
        except Exception as e:
            print(f"  failed: {e}")

    if not built:
        sys.exit(1)

    print(f"\nBuilt {len(built)} pin(s): {', '.join(built)}")
    print("Run `python app.py` and open http://localhost:5000 to lay them out, publish and post.")


if __name__ == "__main__":
    main()
