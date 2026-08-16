"""Tests for salient phrase suggestion."""

from lumos.core.config import SuggestConfig
from lumos.core.salient import _extract_json_array, _normalize, verify_phrases


ARTICLE = (
    "Ergonomic keyboards are a niche.\n\n"
    "The Glove80 costs $365 and ships with   Kailh Choc switches.\n"
    "Its columnar stagger is the reason typos drop after a week."
)


def test_normalize_collapses_whitespace():
    assert _normalize("a  b\n\nc\t d ") == "a b c d"


def test_extract_json_array_ignores_surrounding_prose():
    raw = 'Sure! Here you go:\n["first phrase", "second phrase"]\nHope that helps.'
    assert _extract_json_array(raw) == ["first phrase", "second phrase"]


def test_extract_json_array_on_garbage():
    assert _extract_json_array("no array here") == []
    assert _extract_json_array("[not json]") == []


def test_extract_json_array_unwraps_object_response():
    # Model wrapped the list in an object — still recoverable
    assert _extract_json_array('{"phrases": ["a", "b"]}') == ["a", "b"]


def test_verify_keeps_verbatim_phrases():
    phrases = ["The Glove80 costs $365 and ships with Kailh Choc switches."]
    result = verify_phrases(phrases, ARTICLE)
    # Whitespace-insensitive match; returned in normalized form
    assert result == ["The Glove80 costs $365 and ships with Kailh Choc switches."]


def test_verify_drops_hallucinated_phrases():
    phrases = [
        "The Glove80 costs $500 and is the best keyboard ever made.",
        "Its columnar stagger is the reason typos drop after a week.",
    ]
    assert verify_phrases(phrases, ARTICLE) == [
        "Its columnar stagger is the reason typos drop after a week."
    ]


def test_verify_drops_too_short_phrases():
    assert verify_phrases(["a niche."], ARTICLE) == []


def test_verify_drops_overlapping_phrases():
    phrases = [
        "The Glove80 costs $365 and ships with Kailh Choc switches.",
        "costs $365 and ships with Kailh Choc",
    ]
    assert len(verify_phrases(phrases, ARTICLE)) == 1


def test_phrase_count_scales_with_length():
    s = SuggestConfig()
    # Density stays roughly constant instead of a fixed count per page
    assert s.phrase_count(3000) == 2
    assert s.phrase_count(9000) == 5
    assert s.phrase_count(9000) > s.phrase_count(3000)


def test_phrase_count_is_clamped():
    s = SuggestConfig()
    assert s.phrase_count(0) == s.min_phrases
    assert s.phrase_count(500) == s.min_phrases
    assert s.phrase_count(10_000_000) == s.max_phrases


def test_phrase_count_follows_ratio():
    dense = SuggestConfig(ratio=0.16)
    sparse = SuggestConfig(ratio=0.04)
    assert dense.phrase_count(9000) > sparse.phrase_count(9000)


def test_verify_respects_max_phrases():
    phrases = [
        "Ergonomic keyboards are a niche.",
        "The Glove80 costs $365 and ships with Kailh Choc switches.",
        "Its columnar stagger is the reason typos drop after a week.",
    ]
    assert len(verify_phrases(phrases, ARTICLE, max_phrases=2)) == 2
