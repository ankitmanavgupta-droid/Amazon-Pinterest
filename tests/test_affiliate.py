import pytest

from amazon.affiliate import InvalidAmazonURLError, build_affiliate_link, extract_asin


def test_extract_asin_from_dp_url():
    assert extract_asin("https://www.amazon.com/dp/B08N5WRWNW") == "B08N5WRWNW"


def test_extract_asin_from_product_title_url():
    url = "https://www.amazon.com/Echo-Dot-4th-Gen/dp/B08N5WRWNW/ref=sr_1_1"
    assert extract_asin(url) == "B08N5WRWNW"


def test_extract_asin_from_gp_product_url():
    assert extract_asin("https://www.amazon.com/gp/product/B08N5WRWNW") == "B08N5WRWNW"


def test_extract_asin_raises_on_invalid_url():
    with pytest.raises(InvalidAmazonURLError):
        extract_asin("https://www.amazon.com/s?k=headphones")


def test_build_affiliate_link():
    url = "https://www.amazon.com/dp/B08N5WRWNW"
    assert build_affiliate_link(url, "mytag-20") == "https://www.amazon.com/dp/B08N5WRWNW?tag=mytag-20"


def test_build_affiliate_link_preserves_domain():
    url = "https://www.amazon.co.uk/dp/B08N5WRWNW"
    result = build_affiliate_link(url, "mytag-21")
    assert result.startswith("https://www.amazon.co.uk/dp/B08N5WRWNW")


def test_build_affiliate_link_requires_tag():
    with pytest.raises(ValueError):
        build_affiliate_link("https://www.amazon.com/dp/B08N5WRWNW", "")
