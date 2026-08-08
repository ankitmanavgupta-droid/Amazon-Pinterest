import sys

from amazon.affiliate import build_affiliate_link, resolve_url
from config import AMAZON_AFFILIATE_TAG


def main():
    if len(sys.argv) != 2:
        print("Usage: python main.py <amazon_product_url>")
        sys.exit(1)

    if not AMAZON_AFFILIATE_TAG:
        print("Missing AMAZON_AFFILIATE_TAG. Copy .env.example to .env and fill it in.")
        sys.exit(1)

    url = resolve_url(sys.argv[1])
    print(build_affiliate_link(url, AMAZON_AFFILIATE_TAG))


if __name__ == "__main__":
    main()
