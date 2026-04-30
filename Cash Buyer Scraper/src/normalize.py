"""
Shared name-normalization helpers.

Used by:
  - buyer_history.py  (buyer_name_key exact-match dedup for times_bought_90d)
  - remarks.py         (Assessor-owner vs deed-buyer mismatch detection)
  - assessor.py        (multi-match candidate disambiguation)
"""

import re

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")
_TOKEN_SPLIT_RE = re.compile(r"[;&,]|\bAND\b")
_TOKEN_STRIP_RE = re.compile(r"[.,;:]+$")


def normalize_name(name: str) -> str:
    """
    Canonical dedup key for a single buyer/entity name.

    Deliberately aggressive: uppercase, then strip every character that
    isn't A-Z0-9 (including internal whitespace). This collapses formatting
    variants of the SAME name that the deed scraper has been observed to
    render inconsistently across records -- e.g. one real LLC ("901-2.0
    LLC") appearing as "901 2 0 LLC" in one record and "901 20 LLC" in
    another (the hyphen/period gets turned into a space in one HTML render
    and dropped entirely in another).

    Used ONLY as an internal DB dedup key for repeat-purchase counting --
    never for display (buyer_name in the output stays the original string).
    Two different real names built from the same letters in a different
    grouping could theoretically collide onto the same key; acceptable
    since the only consequence is a slightly inflated repeat-purchase count,
    not wrong displayed data.
    """
    return _NON_ALNUM_RE.sub("", name.upper())


def name_tokens(name: str) -> set[str]:
    """
    Split a (possibly multi-party) name string into a set of normalized
    individual name-word tokens.

    Handles the joiners observed across sources for the SAME people:
      - deed scraper output: "FEHNEL HUNTER P; FEHNEL KATHRYN S"  (semicolon)
      - Assessor portal:     "FEHNEL HUNTER P & KATHRYN S"         (ampersand)
    Splits on [;&,] and the word "AND", uppercases, strips trailing
    punctuation per token, and returns the flattened token set.
    """
    parts = _TOKEN_SPLIT_RE.split(name.upper())
    tokens: set[str] = set()
    for part in parts:
        for word in part.split():
            word = _TOKEN_STRIP_RE.sub("", word)
            if word:
                tokens.add(word)
    return tokens


def names_share_tokens(name_a: str, name_b: str, threshold: float = 0.5) -> bool:
    """
    True if the token sets of name_a/name_b overlap by >= threshold of the
    SMALLER set's size -- dividing by the smaller side (not max()) so that a
    subset match, e.g. buyer "FEHNEL HUNTER P" fully contained in Assessor's
    "FEHNEL HUNTER P & KATHRYN S", counts as a match.
    """
    tokens_a = name_tokens(name_a)
    tokens_b = name_tokens(name_b)
    if not tokens_a or not tokens_b:
        return False
    overlap = tokens_a & tokens_b
    smaller = min(len(tokens_a), len(tokens_b))
    return len(overlap) / smaller >= threshold
