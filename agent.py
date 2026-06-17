"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import json
import os

from dotenv import load_dotenv
from groq import Groq

from tools import search_listings, suggest_outfit, create_fit_card

load_dotenv()

# ── query parser ──────────────────────────────────────────────────────────────

_MODEL = "llama-3.1-8b-instant"

_SYSTEM_PROMPT = """You are a parser for a secondhand clothing search agent. Extract structured \
search parameters from the user's request. Return ONLY a JSON object with exactly these keys:

- "description": string of garment keywords (type, style, color, era, brand). Remove any size or \
price wording from this string. Never empty.
- "size": the requested size as a short code, or null if none is mentioned. Normalize words to \
codes: small->"S", medium->"M", large->"L", extra large->"XL". Leave shoe/numeric/waist sizes as \
written ("8", "W30").
- "max_price": the maximum price as a number with no currency symbol, or null if no price is \
mentioned. Treat "under $30", "below 30", "max $30", "$30 or less", and "between $20 and $40" (->40) \
as the ceiling.

Never invent a size or price the user didn't state - use null.
Output valid JSON only. No prose, no markdown.

Examples:
"looking for a vintage graphic tee under $30" -> {"description":"vintage graphic tee","size":null,"max_price":30}
"90s track jacket in size M" -> {"description":"90s track jacket","size":"M","max_price":null}
"black combat boots size 8" -> {"description":"black combat boots","size":"8","max_price":null}
"designer ballgown size XXS under $5" -> {"description":"designer ballgown","size":"XXS","max_price":5}"""


def parse_query(query: str) -> dict:
    """Call the Groq LLM to extract description, size, and max_price from a natural-language query."""
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    resp = client.chat.completions.create(
        model=_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
    )
    content = resp.choices[0].message.content
    if not content:
        raise ValueError("Groq returned an empty response for query parsing.")
    return json.loads(content)


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1: init session
    session = _new_session(query, wardrobe)

    # Step 2: parse query — fall back to raw query on any failure
    try:
        session["parsed"] = parse_query(query)
    except Exception:
        session["parsed"] = {"description": query, "size": None, "max_price": None}

    parsed = session["parsed"]
    description = parsed.get("description", query)
    size = parsed.get("size")
    max_price = parsed.get("max_price")

    # Step 3: search listings
    session["search_results"] = search_listings(description, size, max_price)

    if not session["search_results"]:
        if size:
            # Retry without size filter before giving up
            session["search_results"] = search_listings(description, None, max_price)

        if not session["search_results"]:
            price_clause = f" under ${max_price}" if max_price is not None else ""
            if size:
                session["error"] = (
                    f"No items matched '{description}' in size {size}{price_clause}. "
                    f"We already tried broadening by removing the size filter and still found nothing — "
                    f"try raising your price or using different keywords."
                )
            else:
                session["error"] = (
                    f"No items matched '{description}'{price_clause}. "
                    f"Try raising your price or using different keywords."
                )
            return session

    # Step 4: select top result
    session["selected_item"] = session["search_results"][0]

    # Step 5: suggest outfit
    session["outfit_suggestion"] = suggest_outfit(session["selected_item"], wardrobe)

    # Step 6: create fit card (only if outfit string is non-empty)
    if session["outfit_suggestion"] and session["outfit_suggestion"].strip():
        session["fit_card"] = create_fit_card(session["outfit_suggestion"], session["selected_item"])

    # Step 7: return session
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n--- Session State ---")
    print(json.dumps(session, indent=2, default=str))

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
    print("\n--- Session State ---")
    print(json.dumps(session2, indent=2, default=str))
