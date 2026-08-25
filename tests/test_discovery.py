import pytest

import discovery


def row(**overrides) -> dict:
    """A Canopy search result that passes the filter unless overridden.

    Title and brand default to something unique per ASIN, the way real listings
    are — the tests that want a duplicate or a shared brand say so explicitly.
    """
    asin = overrides.pop("asin", "B01")
    return {
        "asin": asin,
        "title": f"{asin} Linen Button Down Shirt Long Sleeve Women Cotton Casual",
        "url": f"https://www.amazon.co.uk/Some-Long-Slug/dp/{asin}/ref=sr_1_4?dib=xyz&keywords=linen",
        "mainImageUrl": f"https://images.test/{asin}.jpg",
        "brand": f"Brand{asin}",
        "rating": 4.5,
        "ratingsTotal": 300,
        "isPrime": True,
        "sponsored": False,
        **overrides,
    }


# ---------- The mechanical filter ----------

def test_keeps_a_well_reviewed_organic_result():
    assert discovery.is_usable(row())


def test_drops_sponsored_slots():
    """A paid placement says nothing about whether the product is any good."""
    assert not discovery.is_usable(row(sponsored=True))


def test_drops_poorly_rated_and_barely_reviewed_products():
    assert not discovery.is_usable(row(rating=3.2))
    assert not discovery.is_usable(row(ratingsTotal=4))


def test_drops_results_missing_an_image_or_link():
    assert not discovery.is_usable(row(mainImageUrl=None))
    assert not discovery.is_usable(row(url=None))


def test_drops_unrated_results_rather_than_assuming_the_best():
    assert not discovery.is_usable(row(rating=None))
    assert not discovery.is_usable(row(ratingsTotal=None))


# ---------- Ordering before the expensive step ----------

def test_popularity_does_not_let_a_handful_of_reviews_win():
    barely_reviewed = row(rating=5.0, ratingsTotal=3)
    well_reviewed = row(rating=4.6, ratingsTotal=4000)

    assert discovery.popularity(well_reviewed) > discovery.popularity(barely_reviewed)


def test_popularity_prefers_the_better_rating_at_equal_volume():
    assert discovery.popularity(row(rating=4.8)) > discovery.popularity(row(rating=4.1))


# ---------- Gathering ----------

@pytest.fixture
def searches(monkeypatch):
    """Serves canned search results and records the terms asked for."""
    calls = []
    results = {}

    def fake_search(term, domain="UK", max_age=None):
        calls.append(term)
        return results.get(term, [])

    monkeypatch.setattr(discovery, "cached_search", fake_search)
    return calls, results


def test_gather_dedupes_products_found_by_more_than_one_term(searches):
    calls, results = searches
    results["linen shirt"] = [row(asin="B01"), row(asin="B02")]
    results["cotton blouse"] = [row(asin="B02"), row(asin="B03")]

    candidates = discovery.gather_candidates(["linen shirt", "cotton blouse"])

    assert calls == ["linen shirt", "cotton blouse"]
    assert sorted(c["asin"] for c in candidates) == ["B01", "B02", "B03"]


def test_gather_records_which_term_found_each_product(searches):
    _, results = searches
    results["cotton blouse"] = [row(asin="B09")]

    assert discovery.gather_candidates(["cotton blouse"])[0]["searchTerm"] == "cotton blouse"


def test_gather_returns_best_first(searches):
    _, results = searches
    results["tops"] = [
        row(asin="MEH", rating=4.1, ratingsTotal=40),
        row(asin="GREAT", rating=4.8, ratingsTotal=2000),
    ]

    assert [c["asin"] for c in discovery.gather_candidates(["tops"])] == ["GREAT", "MEH"]


def test_gather_survives_one_failing_search(searches, monkeypatch):
    """One dead search term shouldn't lose the results from the others."""
    _, results = searches
    results["good term"] = [row(asin="B01")]

    def flaky(term, domain="UK", max_age=None):
        if term == "bad term":
            raise RuntimeError("Canopy timed out")
        return results.get(term, [])

    monkeypatch.setattr(discovery, "cached_search", flaky)

    candidates = discovery.gather_candidates(["bad term", "good term"])

    assert [c["asin"] for c in candidates] == ["B01"]


def test_gather_raises_when_every_search_fails(searches, monkeypatch):
    def always_fails(term, domain="UK", max_age=None):
        raise RuntimeError("Canopy is down")

    monkeypatch.setattr(discovery, "cached_search", always_fails)

    with pytest.raises(discovery.DiscoveryError, match="Canopy is down"):
        discovery.gather_candidates(["a", "b"])


# ---------- Caching ----------

def test_cached_search_only_hits_the_api_once(tmp_path, monkeypatch):
    """Canopy's free tier is 100 requests a month — a repeated term must be free."""
    monkeypatch.setattr(discovery, "DISCOVERY_CACHE_DIR", tmp_path)
    calls = []

    def fake_api(term, domain="UK"):
        calls.append(term)
        return [row()]

    monkeypatch.setattr(discovery, "search_products", fake_api)

    first = discovery.cached_search("linen shirt")
    second = discovery.cached_search("linen shirt")

    assert calls == ["linen shirt"]
    assert first == second


def test_cached_search_separates_marketplaces(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "DISCOVERY_CACHE_DIR", tmp_path)
    monkeypatch.setattr(discovery, "search_products", lambda term, domain="UK": [row(asin=domain)])

    assert discovery.cached_search("tops", domain="UK")[0]["asin"] == "UK"
    assert discovery.cached_search("tops", domain="US")[0]["asin"] == "US"


def test_cached_search_refetches_once_the_entry_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "DISCOVERY_CACHE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(discovery, "search_products", lambda term, domain="UK": calls.append(term) or [row()])

    discovery.cached_search("tops")
    discovery.cached_search("tops", max_age=0)

    assert len(calls) == 2


# ---------- The whole pipeline ----------

def test_discover_drops_products_the_ranking_scored_low(monkeypatch):
    monkeypatch.setattr(discovery, "suggest_search_terms", lambda *a, **k: ["linen shirt"])
    monkeypatch.setattr(discovery, "cached_search", lambda term, domain="UK", max_age=None: [
        row(asin="PRETTY"), row(asin="UGLY"),
    ])


    def fake_ranking(products, vibe=""):
        scores = {"PRETTY": 9.0, "UGLY": 2.0}
        return sorted(
            ({**p, "score": scores[p["asin"]], "reason": "..."} for p in products),
            key=lambda p: p["score"], reverse=True,
        )

    monkeypatch.setattr(discovery, "rank_by_aesthetic", fake_ranking)

    result = discovery.discover("cottagecore tops")

    assert [p["asin"] for p in result["products"]] == ["PRETTY"]
    assert result["considered"] == 2  # both were looked at, one didn't make the cut
    assert result["terms"] == ["linen shirt"]


def test_discover_explains_itself_when_the_filter_leaves_nothing(monkeypatch):
    monkeypatch.setattr(discovery, "suggest_search_terms", lambda *a, **k: ["obscure thing"])
    monkeypatch.setattr(discovery, "cached_search", lambda term, domain="UK", max_age=None: [row(sponsored=True)])

    with pytest.raises(discovery.DiscoveryError, match="sponsored"):
        discovery.discover("something nobody sells")


# ---------- Keeping the results varied ----------

def test_canonical_url_strips_amazon_search_tracking():
    """Search URLs carry a ref/dib query that expires and differs per variant."""
    assert discovery.canonical_url(row(asin="B07")) == "https://www.amazon.co.uk/dp/B07"


def test_canonical_url_keeps_the_marketplace_host():
    """region_from_url reads the host downstream, so it has to survive."""
    us = row(asin="B07", url="https://www.amazon.com/Thing/dp/B07/ref=sr_1_1?x=y")

    assert discovery.canonical_url(us) == "https://www.amazon.com/dp/B07"


def test_canonical_url_left_alone_when_there_is_no_asin():
    messy = row(asin=None, url="https://www.amazon.co.uk/weird/link")

    assert discovery.canonical_url(messy) == "https://www.amazon.co.uk/weird/link"


def test_gather_drops_colour_variants_of_the_same_product(searches):
    """Each colour is its own ASIN under a near-identical title — six of one
    blouse is not six products."""
    _, results = searches
    shared = "Renaissance Puff Sleeve Corset Blouse Square Neck Cottagecore Top"
    results["blouse"] = [
        {**row(asin="B01"), "brand": "Scarlet", "title": f"{shared} in Cream"},
        {**row(asin="B02"), "brand": "Scarlet", "title": f"{shared} in Sage"},
        {**row(asin="B03"), "brand": "Scarlet", "title": "Corduroy Pinafore Dungaree Dress Adjustable Strap Women Vintage"},
    ]

    candidates = discovery.gather_candidates(["blouse"])

    assert sorted(c["asin"] for c in candidates) == ["B01", "B03"]


def test_cap_per_brand_keeps_the_best_of_each_brand():
    """Applied after ranking, so the order it's given decides who survives."""
    ranked = [
        {"asin": "A1", "brand": "Scarlet"},
        {"asin": "A2", "brand": "Scarlet"},
        {"asin": "A3", "brand": "Scarlet"},
        {"asin": "B1", "brand": "Allegra"},
    ]

    assert [p["asin"] for p in discovery.cap_per_brand(ranked, 2)] == ["A1", "A2", "B1"]


def test_cap_per_brand_does_not_group_unbranded_products_together():
    """A missing brand is unknown, not a brand they all share."""
    unbranded = [{"asin": f"A{i}", "brand": ""} for i in range(5)]

    assert len(discovery.cap_per_brand(unbranded, 2)) == 5


def test_discover_limits_how_many_of_one_brand_come_back(monkeypatch):
    monkeypatch.setattr(discovery, "suggest_search_terms", lambda *a, **k: ["blouse"])
    monkeypatch.setattr(discovery, "cached_search", lambda term, domain="UK", max_age=None: [
        {**row(asin=f"B0{i}"), "brand": "Scarlet"} for i in range(6)
    ])
    monkeypatch.setattr(discovery, "rank_by_aesthetic",
                        lambda products, vibe="": [{**p, "score": 9.0, "reason": "..."} for p in products])

    result = discovery.discover("cottagecore tops")

    assert len(result["products"]) == discovery.MAX_PER_BRAND
