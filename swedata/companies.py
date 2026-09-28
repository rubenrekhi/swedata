"""Company-name cleanup so "facebook", "Meta Platforms" and "META" land in one bucket."""

from __future__ import annotations

import re
from collections import Counter

UNKNOWN = "Unknown company"
GENERAL = "General advice"

# Lower-cased spelling -> canonical name. Anything not listed keeps the spelling
# the extractor used most often.
ALIASES = {
    "facebook": "Meta", "fb": "Meta", "meta platforms": "Meta",
    "alphabet": "Google",
    "aws": "Amazon", "amazon web services": "Amazon",
    "msft": "Microsoft",
    "jp morgan": "JPMorgan Chase", "jpmorgan": "JPMorgan Chase", "jpmc": "JPMorgan Chase",
    "j.p. morgan": "JPMorgan Chase", "jp morgan chase": "JPMorgan Chase",
    "goldman": "Goldman Sachs", "gs": "Goldman Sachs",
    "twitter": "X (Twitter)", "x": "X (Twitter)",
    "capital one": "Capital One", "capitalone": "Capital One",
    "bloomberg lp": "Bloomberg",
    "salesforce.com": "Salesforce",
}

_SUFFIX = re.compile(r"[,\s]+(inc|llc|ltd|corp|corporation|co|plc)\.?$", re.IGNORECASE)


def _clean(name: str) -> str:
    name = re.sub(r"\s+", " ", name or "").strip(" .,-")
    return _SUFFIX.sub("", name)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


class CompanyNormalizer:
    """Built from every raw spelling seen, so display names are consistent."""

    def __init__(self, raw_names: list[str]):
        variants: dict[str, Counter] = {}
        for raw in raw_names:
            cleaned = _clean(raw)
            if not cleaned:
                continue
            canonical = ALIASES.get(cleaned.casefold(), cleaned)
            variants.setdefault(_key(canonical), Counter())[canonical] += 1
        # Prefer the most common spelling; on ties, one with capitals ("OpenAI" over "openai").
        self._display = {
            key: max(counter.items(), key=lambda kv: (kv[1], kv[0] != kv[0].lower()))[0]
            for key, counter in variants.items()
        }

    def __call__(self, raw: str) -> str:
        cleaned = _clean(raw)
        if not cleaned or cleaned.casefold() in {"unknown", "n/a", "none"}:
            return UNKNOWN
        canonical = ALIASES.get(cleaned.casefold(), cleaned)
        return self._display.get(_key(canonical), canonical)
