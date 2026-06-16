"""
tests/test_tools.py — complete test suite for all three FitFindr tools.

Tool 1 — search_listings: keyword scoring + sort, price/size filtering,
  retry-path preconditions, and the "returns [] never raises" contract.
Tool 2 — suggest_outfit: populated wardrobe (names real pieces), empty wardrobe
  (general advice, no crash), and LLM-failure graceful fallback.
Tool 3 — create_fit_card: caption mentions item details, empty/whitespace outfit
  guard, LLM-failure fallback, and variety across different inputs.

Happy-path tests for Tools 2 and 3 make live Groq API calls.
All failure-mode tests patch the Groq client to force the error condition.

Run with:  pytest tests/test_tools.py -v
"""

from unittest.mock import patch

from tools import _tokenize, create_fit_card, search_listings, suggest_outfit
from utils.data_loader import get_empty_wardrobe, get_example_wardrobe


# ── shared fixtures ───────────────────────────────────────────────────────────

SAMPLE_ITEM = {
    "id": "lst_006",
    "title": "Vintage Graphic Tee",
    "description": "Faded band tee from the 90s. Slightly cropped.",
    "category": "tops",
    "style_tags": ["vintage", "graphic", "band tee"],
    "size": "M",
    "condition": "good",
    "price": 18.0,
    "colors": ["black", "white"],
    "brand": None,
    "platform": "depop",
}


# ══════════════════════════════════════════════════════════════════════════════
# Tool 1: search_listings
# ══════════════════════════════════════════════════════════════════════════════

EXPECTED_FIELDS = {
    "id", "title", "description", "category", "style_tags",
    "size", "condition", "price", "colors", "brand", "platform",
}


def _relevance(item, keywords):
    """Token-based keyword score — mirrors search_listings' own scoring."""
    haystack = " ".join(
        [item["title"], item["description"], " ".join(item["style_tags"]),
         item["category"], " ".join(item["colors"])]
    )
    item_tokens = _tokenize(haystack)
    return sum(item_tokens.count(kw) for kw in keywords)


# ── happy path ───────────────────────────────────────────────────────────────

def test_happy_path_returns_matches():
    """A normal query returns a non-empty list, every item is relevant."""
    keywords = ["vintage", "graphic", "tee"]
    results = search_listings("vintage graphic tee")
    assert len(results) > 0
    for r in results:
        assert _relevance(r, keywords) > 0
    assert _relevance(results[0], keywords) >= 3


def test_results_are_full_listing_dicts():
    """Each result is a complete listing dict, not a trimmed-down view."""
    results = search_listings("denim jacket")
    assert results
    for r in results:
        assert EXPECTED_FIELDS.issubset(r.keys())


def test_sorted_best_first():
    """Results come back sorted by relevance score, highest first."""
    keywords = ["vintage", "graphic", "tee"]
    results = search_listings("vintage graphic tee")
    assert len(results) >= 2
    scores = [_relevance(r, keywords) for r in results]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[-1]


# ── price filtering ──────────────────────────────────────────────────────────

def test_max_price_is_inclusive():
    """An item priced exactly at max_price is kept (slip dress is $30)."""
    results = search_listings("slip dress", max_price=30)
    assert any(r["id"] == "lst_013" for r in results)


def test_max_price_excludes_above():
    """Dropping the cap by a dollar excludes the boundary item."""
    results = search_listings("slip dress", max_price=29)
    assert all(r["id"] != "lst_013" for r in results)


def test_too_cheap_returns_empty():
    """A budget nothing can satisfy yields [] (a failure case the agent handles)."""
    assert search_listings("graphic tee", max_price=1.0) == []


# ── size filtering ───────────────────────────────────────────────────────────

def test_size_filter_exact():
    """size='M' returns the M track jacket and only M-compatible sizes."""
    results = search_listings("track jacket", size="M")
    assert any(r["id"] == "lst_004" for r in results)


def test_size_substring_and_case_insensitive():
    """Lowercase 'm' matches a listing sized 'S/M' (token + case-insensitive)."""
    results = search_listings("baby tee", size="m")
    assert any(r["id"] == "lst_002" for r in results)


def test_size_no_false_positive_on_shoes():
    """size='S' must NOT match shoes sized 'US 8'/'US 9' (the substring trap)."""
    results = search_listings("sneakers", size="S")
    assert all("us" not in r["size"].lower() for r in results)


def test_shoe_size_number_matches():
    """size='8' matches 'US 8'."""
    results = search_listings("sneakers", size="8")
    assert any(r["id"] == "lst_019" for r in results)


# ── retry-path preconditions ─────────────────────────────────────────────────
# The planning loop retries search_listings without size when the first call
# (with size) returns []. These two tests prove both branches of that flow
# work at the tool level — the agent's retry logic depends on this contract.

def test_size_causes_empty_but_unsized_finds_results():
    """First call (with size) → []; retry without size → non-empty.

    This is the 'retry succeeds' path in the planning loop:
      search_listings("vintage graphic tee", size="XS") → []
      search_listings("vintage graphic tee", size=None)  → results
    """
    narrowed = search_listings("vintage graphic tee", size="XS")
    assert narrowed == [], "Size 'XS' should filter out all graphic tees in the dataset"
    broadened = search_listings("vintage graphic tee", size=None)
    assert len(broadened) > 0, "Removing the size filter should surface matching tees"


def test_double_empty_triggers_error_state():
    """Both calls (with size, then without) → [].

    This is the 'still empty → error' path in the planning loop:
      search_listings("ballgown tuxedo wetsuit", size="XS") → []
      search_listings("ballgown tuxedo wetsuit", size=None)  → []
    The agent stops here and returns a specific error message.
    """
    with_size = search_listings("ballgown tuxedo wetsuit", size="XS")
    without_size = search_listings("ballgown tuxedo wetsuit", size=None)
    assert with_size == []
    assert without_size == []


# ── no-match / robustness ────────────────────────────────────────────────────

def test_no_keyword_match_returns_empty():
    """A description matching nothing returns [] rather than raising."""
    assert search_listings("ballgown tuxedo wetsuit") == []


def test_empty_description_returns_empty():
    """An empty description has no keywords -> [] (no crash)."""
    assert search_listings("") == []


def test_zero_score_items_dropped():
    """Only listings that actually match the keywords are returned."""
    results = search_listings("crochet halter")
    assert results
    for r in results:
        hay = (r["title"] + " " + " ".join(r["style_tags"]) + " " + r["description"]).lower()
        assert "crochet" in hay or "halter" in hay


# ══════════════════════════════════════════════════════════════════════════════
# Tool 2: suggest_outfit
# ══════════════════════════════════════════════════════════════════════════════

# ── populated wardrobe ───────────────────────────────────────────────────────

def test_suggest_outfit_populated_returns_nonempty():
    """With a real wardrobe, the tool returns a non-empty string."""
    result = suggest_outfit(SAMPLE_ITEM, get_example_wardrobe())
    assert isinstance(result, str)
    assert result.strip() != ""


def test_suggest_outfit_populated_names_wardrobe_pieces():
    """With a real wardrobe, the response references at least one wardrobe piece.

    The example wardrobe has jeans, trousers, a tank, crewneck, hoodie, jacket,
    sneakers, boots, belt, and a bag — common enough clothing words that the LLM
    will naturally use at least one when suggesting outfits.
    """
    wardrobe = get_example_wardrobe()
    result = suggest_outfit(SAMPLE_ITEM, wardrobe)
    piece_keywords = {
        word
        for item in wardrobe["items"]
        for word in item["name"].lower().split()
        if len(word) > 3
    }
    assert any(kw in result.lower() for kw in piece_keywords), (
        f"Expected at least one wardrobe piece keyword in the suggestion. "
        f"Got: {result[:200]}"
    )


# ── empty wardrobe ───────────────────────────────────────────────────────────

def test_suggest_outfit_empty_wardrobe_returns_nonempty():
    """Empty wardrobe is NOT an error — returns general styling advice."""
    result = suggest_outfit(SAMPLE_ITEM, get_empty_wardrobe())
    assert isinstance(result, str)
    assert result.strip() != ""


def test_suggest_outfit_empty_wardrobe_does_not_crash():
    """Empty wardrobe branch must never raise."""
    try:
        suggest_outfit(SAMPLE_ITEM, get_empty_wardrobe())
    except Exception as exc:
        raise AssertionError(f"suggest_outfit raised on empty wardrobe: {exc}")


# ── LLM failure / graceful fallback ─────────────────────────────────────────

def test_suggest_outfit_llm_failure_returns_fallback():
    """If the Groq API raises, the tool returns a non-empty fallback — never raises."""
    with patch("tools.Groq") as MockGroq:
        MockGroq.return_value.chat.completions.create.side_effect = Exception("API timeout")
        result = suggest_outfit(SAMPLE_ITEM, get_example_wardrobe())
    assert isinstance(result, str)
    assert result.strip() != ""
    assert "Vintage Graphic Tee" in result or "depop" in result.lower()


def test_suggest_outfit_llm_failure_never_returns_empty_string():
    """Fallback must not be "" or whitespace — the agent still needs to proceed."""
    with patch("tools.Groq") as MockGroq:
        MockGroq.return_value.chat.completions.create.side_effect = RuntimeError("network error")
        result = suggest_outfit(SAMPLE_ITEM, get_empty_wardrobe())
    assert result != ""
    assert result.strip() != ""


# ══════════════════════════════════════════════════════════════════════════════
# Tool 3: create_fit_card
# ══════════════════════════════════════════════════════════════════════════════

SAMPLE_OUTFIT = (
    "Outfit 1: Pair the Vintage Graphic Tee with Baggy straight-leg jeans and "
    "Chunky white sneakers for a classic 90s streetwear look. "
    "Outfit 2: Layer it under the Vintage black denim jacket with Black combat boots "
    "for a grungier edge."
)

DIFFERENT_ITEM = {
    "id": "lst_013",
    "title": "Flowy Satin Slip Dress",
    "description": "90s-inspired satin slip dress in dusty rose. Midi length.",
    "category": "tops",
    "style_tags": ["90s", "satin", "slip dress", "feminine"],
    "size": "S",
    "condition": "excellent",
    "price": 30.0,
    "colors": ["dusty rose", "pink"],
    "brand": None,
    "platform": "thredUp",
}

DIFFERENT_OUTFIT = (
    "Style the slip dress over a white ribbed tank top, add black combat boots "
    "and a black crossbody bag for a 90s grunge-meets-feminine vibe."
)


# ── happy path ───────────────────────────────────────────────────────────────

def test_create_fit_card_returns_nonempty():
    """Happy path returns a non-empty string caption."""
    result = create_fit_card(SAMPLE_OUTFIT, SAMPLE_ITEM)
    assert isinstance(result, str)
    assert result.strip() != ""


def test_create_fit_card_mentions_price_and_platform():
    """Caption must mention the item's price and platform (spec requirement)."""
    result = create_fit_card(SAMPLE_OUTFIT, SAMPLE_ITEM)
    assert "18" in result, f"Price '$18.0' not found in caption: {result}"
    assert "depop" in result.lower(), f"Platform 'depop' not found in caption: {result}"


def test_create_fit_card_different_inputs_produce_different_captions():
    """Two different items and outfits should produce noticeably different captions.

    At temperature=1.1, identical outputs from completely different inputs would be
    astronomically unlikely. This locks in the 'sounds different' spec requirement.
    """
    caption_a = create_fit_card(SAMPLE_OUTFIT, SAMPLE_ITEM)
    caption_b = create_fit_card(DIFFERENT_OUTFIT, DIFFERENT_ITEM)
    assert caption_a != caption_b, (
        "Two very different items/outfits produced identical captions — "
        "temperature may not be high enough or the prompt is too rigid."
    )


# ── empty / whitespace outfit guard ─────────────────────────────────────────

def test_create_fit_card_empty_outfit_returns_error_string():
    """Empty outfit string returns a descriptive error — the LLM is never called."""
    with patch("tools.Groq") as MockGroq:
        result = create_fit_card("", SAMPLE_ITEM)
        MockGroq.assert_not_called()
    assert isinstance(result, str)
    assert result.strip() != ""
    assert "No outfit" in result or "nothing to caption" in result.lower()


def test_create_fit_card_whitespace_outfit_returns_error_string():
    """Whitespace-only outfit is treated the same as empty — no LLM call."""
    with patch("tools.Groq") as MockGroq:
        result = create_fit_card("   \n\t  ", SAMPLE_ITEM)
        MockGroq.assert_not_called()
    assert result.strip() != ""
    assert "No outfit" in result or "nothing to caption" in result.lower()


# ── LLM failure / graceful fallback ─────────────────────────────────────────

def test_create_fit_card_llm_failure_returns_fallback():
    """If the Groq API raises, the tool returns a fallback string — never raises."""
    with patch("tools.Groq") as MockGroq:
        MockGroq.return_value.chat.completions.create.side_effect = Exception("API timeout")
        result = create_fit_card(SAMPLE_OUTFIT, SAMPLE_ITEM)
    assert isinstance(result, str)
    assert result.strip() != ""
    assert "Vintage Graphic Tee" in result or "depop" in result.lower()


def test_create_fit_card_llm_failure_never_returns_empty():
    """Fallback must not be '' or whitespace."""
    with patch("tools.Groq") as MockGroq:
        MockGroq.return_value.chat.completions.create.side_effect = RuntimeError("network error")
        result = create_fit_card(SAMPLE_OUTFIT, SAMPLE_ITEM)
    assert result != ""
    assert result.strip() != ""
