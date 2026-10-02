"""Team-name resolution from config/aliases.yaml. Exact alias matches only — no fuzzy guessing (CLAUDE.md §10)."""

from __future__ import annotations

import re

from src.config import load_yaml


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


def player_key(name: str) -> str:
    """Deterministic player id from a display name: 'D.J. Moore Jr.' -> 'DJ_MOORE'.

    Removes punctuation and generational suffixes so books that differ only in those still match.
    It does NOT guess nicknames or abbreviations ('Josh' vs 'Joshua'); those need an explicit alias.
    """
    words = re.sub(r"[^A-Za-z0-9 ]", "", name.replace("-", " ")).upper().split()
    while len(words) > 2 and words[-1] in _SUFFIXES:
        words.pop()
    return "_".join(words)


class TeamResolver:
    def __init__(self, aliases: dict | None = None):
        aliases = aliases if aliases is not None else load_yaml("aliases.yaml")
        self._lookup: dict[tuple[str, str], str] = {}
        self._display: dict[tuple[str, str], str] = {}
        for league, spec in aliases.get("leagues", {}).items():
            for code, names in spec.get("teams", {}).items():
                if not isinstance(code, str):
                    raise ValueError(f"aliases.yaml: team code {code!r} in {league} is not a string; quote it")
                self._display[(league, code)] = names[0] if names else code
                for n in [code, *names]:
                    self._lookup[(league, _key(n))] = code

    def resolve(self, league: str, name: str) -> str | None:
        return self._lookup.get((league, _key(name)))

    def display_name(self, league: str, code: str) -> str:
        return self._display.get((league, code), code)
