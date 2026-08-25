from pinterest.seo import format_description, pick_best_board

BOARDS = [
    {"id": "1", "name": "clothes"},
    {"id": "2", "name": "Home Decor"},
    {"id": "3", "name": "outfit ideas"},
    {"id": "4", "name": "Shoes"},
]


def test_prefers_the_board_the_model_picked():
    assert pick_best_board("anything at all", BOARDS, preferred_name="Shoes") == "4"


def test_preferred_name_match_ignores_case_and_padding():
    assert pick_best_board("anything", BOARDS, preferred_name="  home decor  ") == "2"


def test_falls_back_to_keyword_overlap_when_preferred_name_is_unknown():
    assert pick_best_board("summer outfit ideas for women", BOARDS, preferred_name="Nonexistent Board") == "3"


def test_falls_back_to_keyword_overlap_when_no_preference_given():
    assert pick_best_board("new shoes for summer", BOARDS) == "4"


def test_falls_back_to_first_board_when_nothing_matches():
    assert pick_best_board("completely unrelated text", BOARDS) == "1"


def test_returns_none_when_there_are_no_boards():
    assert pick_best_board("anything", []) is None


def test_format_description_appends_hashtags():
    seo = {"description": "Lovely tops.", "hashtags": ["summertops", "y2kfashion"]}

    assert format_description(seo) == "Lovely tops. #summertops #y2kfashion"


def test_format_description_does_not_double_the_hash():
    seo = {"description": "Lovely tops.", "hashtags": ["#summertops"]}

    assert format_description(seo) == "Lovely tops. #summertops"


def test_format_description_without_hashtags():
    assert format_description({"description": "Just text."}) == "Just text."


def test_format_description_of_empty_seo():
    assert format_description({}) == ""
