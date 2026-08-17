"""Tests for salient phrase suggestion."""

from lumos.core import salient
from lumos.core.config import LlmConfig, SuggestConfig
from lumos.core.salient import (
    _allocate,
    _excerpt,
    _extract_json_array,
    _label_parts,
    _normalize,
    _prompt_text,
    _split_into_parts,
    verify_phrases,
)


ARTICLE = (
    "Ergonomic keyboards are a niche.\n\n"
    "The Glove80 costs $365 and ships with   Kailh Choc switches.\n"
    "Its columnar stagger is the reason typos drop after a week."
)

# 12 paragraphs of equal length — long enough for clustering to be visible
LONG = "\n\n".join(
    f"Paragraph {i} says something worth remembering about keyboards." for i in range(12)
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
    assert s.phrase_count(9000) == 4
    assert s.phrase_count(9000) > s.phrase_count(3000)


def test_phrase_count_keeps_scaling_on_long_pages():
    s = SuggestConfig()
    # No fixed ceiling in the range real pages live in: a 50k essay offers more
    # than a 25k one, instead of both landing on the same number
    assert s.phrase_count(50_000) > s.phrase_count(25_000) > s.phrase_count(12_000)
    assert s.phrase_count(50_000) == 20


def test_phrase_count_is_clamped():
    s = SuggestConfig()
    assert s.phrase_count(0) == s.min_phrases
    assert s.phrase_count(500) == s.min_phrases
    # The ceiling is a runaway guard, far above any real page
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


def test_verify_prefers_spread_over_model_order():
    # Model front-loaded its picks: the later ones win the slots anyway
    phrases = [
        f"Paragraph {i} says something worth remembering" for i in (0, 1, 2, 4, 8, 10)
    ]
    result = verify_phrases(phrases, LONG, max_phrases=4)
    assert result == [
        f"Paragraph {i} says something worth remembering" for i in (0, 4, 8, 10)
    ]


def test_verify_fills_empty_bands_with_leftovers():
    # Two bands claimed, so the runners-up top the list back up to max_phrases
    phrases = [
        "Paragraph 0 says something worth remembering",
        "Paragraph 1 says something worth remembering",
        "Paragraph 8 says something worth remembering",
    ]
    assert len(verify_phrases(phrases, LONG, max_phrases=3)) == 3


def test_verify_keeps_well_spread_phrases_intact():
    picks = [f"Paragraph {i} says something worth remembering" for i in (0, 4, 8)]
    assert verify_phrases(picks, LONG, max_phrases=3) == picks


def test_verify_returns_document_order():
    picks = [f"Paragraph {i} says something worth remembering" for i in (8, 0, 4)]
    result = verify_phrases(picks, LONG, max_phrases=3)
    assert result == [f"Paragraph {i} says something worth remembering" for i in (0, 4, 8)]


def test_verify_strips_part_markers_from_phrases():
    phrase = "=== PART 2/3 ===\nIts columnar stagger is the reason typos drop after a week."
    assert verify_phrases([phrase], ARTICLE) == [
        "Its columnar stagger is the reason typos drop after a week."
    ]


def test_split_into_parts_covers_whole_text():
    parts = _split_into_parts(LONG, 4)
    assert len(parts) == 4
    assert "".join(parts) == LONG
    # Roughly even: no part more than twice the ideal share
    assert max(len(p) for p in parts) < 2 * len(LONG) / 4


def test_split_into_parts_cuts_on_paragraph_boundaries():
    for part in _split_into_parts(LONG, 4):
        assert part.strip().startswith("Paragraph ")


def test_split_into_parts_degenerate_inputs():
    assert _split_into_parts(ARTICLE, 1) == [ARTICLE]
    assert _split_into_parts("", 4) == [""]
    # More parts than boundaries: falls back to whitespace, never crashes
    assert "".join(_split_into_parts("one two three", 8)) == "one two three"


def test_label_parts_marks_each_part():
    labeled = _label_parts(_split_into_parts(LONG, 3))
    assert labeled.count("=== PART") == 3
    assert "=== PART 3/3 ===" in labeled


def test_label_parts_leaves_single_part_unmarked():
    assert _label_parts([ARTICLE]) == ARTICLE


def test_prompt_text_under_budget_keeps_everything():
    prompt = _prompt_text(LONG, 4, max_chars=100_000)
    for i in range(12):
        assert f"Paragraph {i} says" in prompt


def test_prompt_text_over_budget_samples_the_whole_page():
    # Budget fits ~4 of 12 paragraphs: the tail must still be represented
    prompt = _prompt_text(LONG, 4, max_chars=280)
    assert "Paragraph 0 says" in prompt
    assert "Paragraph 9 says" in prompt  # last part, previously cut off
    assert len(prompt) < len(LONG)


def test_prompt_text_respects_the_budget():
    budget = 400
    prompt = _prompt_text(LONG, 4, max_chars=budget)
    body = "".join(p for p in prompt.split("\n") if not p.startswith("=== PART"))
    assert len(body) <= budget


def test_excerpt_cuts_on_a_boundary():
    text = "First sentence here. Second sentence here. Third sentence here."
    assert _excerpt(text, 50) == "First sentence here. Second sentence here. "
    assert _excerpt(text, len(text)) == text


def test_excerpt_keeps_most_of_the_slice():
    # No usable boundary late in the slice — take the hard cut over a stub
    text = "word " * 100
    assert len(_excerpt(text, 40)) > 20


def test_allocate_splits_slots_by_length():
    chunks = ["a" * 300, "b" * 100]
    assert _allocate(chunks, 8) == [6, 2]


def test_allocate_never_exceeds_the_total():
    chunks = ["a" * 100] * 5
    assert sum(_allocate(chunks, 3)) <= 5  # every chunk keeps at least one slot
    assert sum(_allocate(chunks, 10)) == 10


def test_allocate_gives_every_chunk_a_slot():
    assert all(n >= 1 for n in _allocate(["a" * 1000, "b" * 10], 4))


PAGE = "\n\n".join(
    f"Sentence {i:03d} carries the argument of this article forward." for i in range(300)
)


def _first_line_of_each_part(body):
    """Stand-in for the model: one verbatim passage per labeled part."""
    parts = [p.strip() for p in salient._MARKER_RE.split(body) if p.strip()]
    return [p.split("\n\n")[0].strip() for p in parts]


def _sentence_numbers(phrases):
    return [int(p.split()[1]) for p in phrases]


def test_suggest_reads_a_long_page_in_parallel_calls(monkeypatch):
    """A page 3x the per-call budget is read in several calls, not sampled down."""
    seen = []

    def fake_ask(client, config, body, count):
        seen.append(body)
        return _first_line_of_each_part(body)[:count]

    monkeypatch.setattr(salient, "_ask", fake_ask)
    monkeypatch.setattr(salient, "_client", lambda *a, **k: None)
    monkeypatch.setenv("TEST_KEY", "x")

    phrases, err = salient.suggest_phrases(
        PAGE, LlmConfig(api_key_env="TEST_KEY"), max_phrases=9, max_chars=len(PAGE) // 3
    )

    assert err is None
    assert len(seen) > 1  # split across calls instead of one truncated call
    assert "Sentence 299" in "".join(seen)  # the tail was actually read
    # Suggestions land throughout, including the final stretch of the page
    numbers = _sentence_numbers(phrases)
    assert numbers == sorted(numbers)
    assert numbers[0] < 50 and numbers[-1] > 250


def test_suggest_covers_a_page_past_the_call_limit(monkeypatch):
    """Beyond max_calls the chunks get excerpted — coverage still reaches the end."""
    monkeypatch.setattr(
        salient, "_ask", lambda c, cfg, body, n: _first_line_of_each_part(body)[:n]
    )
    monkeypatch.setattr(salient, "_client", lambda *a, **k: None)
    monkeypatch.setenv("TEST_KEY", "x")

    phrases, err = salient.suggest_phrases(
        PAGE,
        LlmConfig(api_key_env="TEST_KEY"),
        max_phrases=8,
        max_chars=len(PAGE) // 10,
        max_calls=2,
    )
    assert err is None
    assert _sentence_numbers(phrases)[-1] > 250


def test_suggest_survives_one_failing_call(monkeypatch):
    calls = {"n": 0}

    def flaky_ask(client, config, body, count):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("rate limited")
        return _first_line_of_each_part(body)[:count]

    monkeypatch.setattr(salient, "_ask", flaky_ask)
    monkeypatch.setattr(salient, "_client", lambda *a, **k: None)
    monkeypatch.setenv("TEST_KEY", "x")

    phrases, err = salient.suggest_phrases(
        PAGE, LlmConfig(api_key_env="TEST_KEY"), max_phrases=6, max_chars=len(PAGE) // 3
    )
    assert err is None and phrases  # the surviving chunks still contribute


def test_suggest_reports_failure_when_every_call_dies(monkeypatch):
    def dead_ask(client, config, body, count):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(salient, "_ask", dead_ask)
    monkeypatch.setattr(salient, "_client", lambda *a, **k: None)
    monkeypatch.setenv("TEST_KEY", "x")

    phrases, err = salient.suggest_phrases(PAGE, LlmConfig(api_key_env="TEST_KEY"))
    assert phrases == [] and "rate limited" in err


def test_fast_model_reads_short_pages_when_enabled():
    llm = LlmConfig(model="big", fast_model="small")
    assert llm.for_length(9_999, 10_000).model == "small"
    assert llm.for_length(10_000, 10_000).model == "big"
    # Everything but the model carries over
    assert llm.for_length(10, 10_000).api_key_env == llm.api_key_env


def test_fast_model_is_off_by_default():
    # The small model trades away the opening of the page for a second or two
    assert SuggestConfig().fast_below == 0
    llm = LlmConfig(model="big", fast_model="small")
    assert llm.for_length(10, SuggestConfig().fast_below).model == "big"


def test_fast_model_can_be_cleared():
    llm = LlmConfig(model="big", fast_model="")
    assert llm.for_length(10, 15000).model == "big"


def test_suggest_returns_fewer_than_asked_when_the_model_holds_back(monkeypatch):
    # A page of boilerplate: the model skips parts, and nothing pads the result
    monkeypatch.setattr(
        salient, "_ask", lambda c, cfg, body, n: _first_line_of_each_part(body)[:1]
    )
    monkeypatch.setattr(salient, "_client", lambda *a, **k: None)
    monkeypatch.setenv("TEST_KEY", "x")

    phrases, err = salient.suggest_phrases(
        PAGE, LlmConfig(api_key_env="TEST_KEY"), max_phrases=12, max_chars=len(PAGE)
    )
    assert err is None
    assert 0 < len(phrases) < 12
