"""Find Amazon products worth pinning, from a vibe instead of a link list.

    python find_products.py "early autumn cottagecore tops"
    python find_products.py "coquette bedroom decor" --terms 4 --domain US
    python find_products.py "y2k going out tops" --append   # add to links.txt

Prints the products ranked best-first with the reason each scored as it did.
Searches are cached for a week, so re-running the same vibe costs nothing.

The web app (python app.py) does the same thing with pictures, which is easier
to judge — this stays for a terminal.
"""

import argparse

import discovery
from config import DISCOVERY_DOMAIN, MAX_PRODUCTS, PROJECT_ROOT


def append_to_links_file(vibe: str, products: list, limit: int):
    """Adds the picks to links.txt in the format build_pin.py reads."""
    path = PROJECT_ROOT / "links.txt"
    words = vibe.split()
    block = [f"#{' '.join(words[:3]).title()}", "#From Amazon"]
    block += [p["url"] for p in products[:limit]]

    existing = path.read_text().rstrip() + "\n\n" if path.exists() else ""
    path.write_text(existing + "\n".join(block) + "\n")
    print(f"\nAppended {min(len(products), limit)} link(s) to {path.name} — build them with:")
    print("  python build_pin.py links.txt")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vibe", help="the look to find, e.g. 'early autumn cottagecore tops'")
    parser.add_argument("--terms", type=int, default=3, help="Amazon searches to run (default 3, one Canopy request each)")
    parser.add_argument("--domain", default=DISCOVERY_DOMAIN, help=f"Amazon marketplace (default {DISCOVERY_DOMAIN})")
    parser.add_argument("--min-score", type=float, default=discovery.MIN_SCORE, help="drop anything scoring below this")
    parser.add_argument("--append", action="store_true", help=f"append the top {MAX_PRODUCTS} links to links.txt")
    args = parser.parse_args()

    try:
        result = discovery.discover(
            args.vibe,
            domain=args.domain,
            term_count=args.terms,
            min_score=args.min_score,
            progress=lambda message: print(f"  {message}"),
        )
    except Exception as e:
        print(f"Failed: {e}")
        raise SystemExit(1)

    products = result["products"]
    if not products:
        print(f"\nNothing scored {args.min_score}+ out of {result['considered']} candidates. "
              "Try a different vibe, or --min-score to see the near misses.")
        raise SystemExit(1)

    print(f"\n{len(products)} product(s) worth pinning, from {result['considered']} candidates:\n")
    for rank, product in enumerate(products, start=1):
        print(f"{rank}. {product['score']:.0f}/10  {product['title'][:70]}")
        print(f"   {product['reason']}")
        print(f"   {product['rating']}* ({product['reviews']} reviews) · found via '{product['searchTerm']}'")
        print(f"   {product['url']}\n")

    if args.append:
        append_to_links_file(args.vibe, products, MAX_PRODUCTS)


if __name__ == "__main__":
    main()
