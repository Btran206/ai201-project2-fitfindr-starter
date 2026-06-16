"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Lowercase `text` and split it into alphanumeric word tokens.

    Splitting on word boundaries (not raw substrings) is deliberate: it keeps
    the color "teal" from matching the keyword "tee", and lets a multi-word
    style tag like "graphic tee" contribute both "graphic" and "tee".
    """
    return re.findall(r"[a-z0-9]+", text.lower())


def _size_matches(query_size: str, listing_size: str) -> bool:
    """Return True if `query_size` matches `listing_size`, case-insensitively.

    Matches the full size string OR any one of its tokens, so "M" matches
    "S/M" and "M/L", and "8" matches "US 8". Token matching (rather than a raw
    substring test) avoids false positives like "S" matching "US 8".
    """
    q = query_size.strip().lower()
    if not q:
        return True
    full = listing_size.lower()
    return q == full or q in _tokenize(listing_size)


def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive and token-based
                     (e.g., "M" matches "S/M"; "8" matches "US 8").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform
    """
    listings = load_listings()
    keywords = _tokenize(description or "")

    scored: list[tuple[int, int, dict]] = []
    for index, item in enumerate(listings):
        # Hard filters: anything over budget or the wrong size is excluded.
        if max_price is not None and item["price"] > max_price:
            continue
        if size is not None and not _size_matches(size, item["size"]):
            continue

        # Relevance: count how often the query keywords appear across the
        # listing's searchable text fields.
        haystack = " ".join(
            [
                item["title"],
                item["description"],
                " ".join(item["style_tags"]),
                item["category"],
                " ".join(item["colors"]),
            ]
        )
        item_tokens = _tokenize(haystack)
        score = sum(item_tokens.count(kw) for kw in keywords)

        if score > 0:
            # `index` is a stable tiebreaker so equal-score items keep dataset order.
            scored.append((score, index, item))

    scored.sort(key=lambda triple: (-triple[0], triple[1]))
    return [item for _score, _index, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offers general styling advice for the item
        rather than raising an exception or returning an empty string.
        If the LLM call fails, returns a graceful fallback string built from
        the item's own fields — never raises, never returns "".
    """
    item_summary = (
        f"Name: {new_item['title']}\n"
        f"Category: {new_item['category']}\n"
        f"Colors: {', '.join(new_item['colors'])}\n"
        f"Style: {', '.join(new_item['style_tags'])}"
    )

    wardrobe_items = wardrobe.get("items", [])

    if wardrobe_items:
        wardrobe_lines = "\n".join(
            f"- {w['name']} ({w['category']}, "
            f"colors: {', '.join(w['colors'])}, "
            f"style: {', '.join(w['style_tags'])})"
            for w in wardrobe_items
        )
        user_msg = (
            f"I'm considering buying this secondhand item:\n{item_summary}\n\n"
            f"My current wardrobe includes:\n{wardrobe_lines}\n\n"
            f"Suggest 1-2 complete outfits that pair this new item with specific "
            f"pieces from my wardrobe. Name each wardrobe piece by name. "
            f"Keep the suggestions practical and wearable."
        )
    else:
        user_msg = (
            f"I'm considering buying this secondhand item:\n{item_summary}\n\n"
            f"I don't have a wardrobe to pull from yet. Give me general styling "
            f"advice: what types of pieces pair well with this item, what vibe it "
            f"suits, and how I could build outfits around it."
        )

    try:
        client = _get_groq_client()
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            temperature=0.7,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a secondhand fashion stylist. "
                        "Give practical, specific outfit suggestions."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
        )
        result = resp.choices[0].message.content.strip()
        if result:
            return result
    except Exception:
        pass

    description = new_item.get("description") or ""
    return (
        f"Couldn't generate outfit suggestions right now. "
        f"Here's what we found: {new_item['title']} "
        f"(${new_item['price']} on {new_item['platform']}) — "
        f"{description}"
    ).strip()


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or whitespace-only, returns a descriptive error
        string without calling the LLM — never raises.
        If the LLM call fails, returns a short fallback caption built from
        the item fields so the user still gets something usable.
    """
    if not outfit or not outfit.strip():
        return "No outfit was provided, so there's nothing to caption."

    user_msg = (
        f"Item: {new_item['title']} — ${new_item['price']} on {new_item['platform']}\n"
        f"Outfit: {outfit}\n\n"
        f"Write a 2-4 sentence Instagram caption for this thrifted outfit. "
        f"Sound casual and authentic, like a real OOTD post — not a product description. "
        f"Mention the item name, its price, and the platform exactly once each. "
        f"Capture the outfit vibe in specific terms. No hashtags."
    )

    try:
        client = _get_groq_client()
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            temperature=1.1,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a fashion influencer writing short, authentic outfit captions. "
                        "Be specific, casual, and creative — no generic phrases."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
        )
        result = resp.choices[0].message.content.strip()
        if result:
            return result
    except Exception:
        pass

    return (
        f"Found this {new_item['title']} for ${new_item['price']} on "
        f"{new_item['platform']} and it's going straight into the rotation."
    )
