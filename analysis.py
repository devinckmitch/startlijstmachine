from __future__ import annotations

from difflib import get_close_matches

import db
from excel_parser import display_name, normalise_name


def find_player_candidates(search_term: str, all_players: list[str]) -> list[str]:
    """Return players whose normalised name contains or closely matches the search term."""
    norm = normalise_name(search_term)
    if not norm:
        return []

    # Substring matches first
    substring_hits = [p for p in all_players if norm in p]

    # Fuzzy match: compare against individual name tokens so short search
    # terms (e.g. "adems" → "adams") still hit full names like "adams karl"
    all_tokens: dict[str, str] = {}  # token → full player name
    for p in all_players:
        for token in p.split():
            all_tokens[token] = p

    fuzzy_tokens = get_close_matches(norm, all_tokens.keys(), n=10, cutoff=0.6)
    fuzzy_hits = [all_tokens[t] for t in fuzzy_tokens]

    # Combine, deduplicate, preserve order
    seen: set[str] = set()
    result = []
    for p in substring_hits + fuzzy_hits:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def get_partner_table(player: str) -> list[dict]:
    """Returns list of {"partner": display_name, "keer samen": count}."""
    rows = db.get_partner_frequency(player)
    return [
        {"Speelpartner": display_name(r["partner"]), "Keer samen": r["times_together"]}
        for r in rows
    ]


def get_tee_time_table(player: str) -> list[dict]:
    """Returns list of {"Tijd": tee_time, "Keer gespeeld": count}."""
    rows = db.get_tee_time_distribution(player)
    return [
        {"Tijd": r["tee_time"], "Keer gespeeld": r["appearances"]}
        for r in rows
    ]
