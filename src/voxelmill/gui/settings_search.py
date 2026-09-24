"""Fuzzy, mode-aware ranking for the Setup settings search box.

A pure, dependency-free matcher (stdlib ``re`` and ``difflib`` only) so it can
be unit tested without Qt. It scores a :class:`~.settings_table.SettingDescriptor`
against a query on four surfaces -- its dotted path, its label, its help text
and its CLI flag -- and returns a deterministic, ranked list.

Ranking, from most to least confident:

1. the query equals one of those surfaces exactly (normalised to lowercase
   whitespace-separated words);
2. the query equals a single whole word of one of them (``"spacing"`` finds
   ``support.spacing_mm`` as readily as it finds ``support.brace_spacing_mm``,
   both by their ``spacing`` word);
3. one of the surfaces starts with the query;
4. the query appears anywhere in one of the surfaces;
5. every word of a multi-word query matches some word of the surface exactly,
   in any order (``"spacing brace"`` finds ``brace_spacing_mm``);
6. the query's letters appear as a subsequence of the surface, in order but
   with gaps allowed (a loose "did you mean roughly this" match);
7. typo tolerance: each query word is matched against its closest surface
   word by :class:`difflib.SequenceMatcher` ratio, and the average ratio
   scales a lower base score (``"brase spacing"`` still finds
   ``support.brace_spacing_mm``).

Ties within a tier, and the final ordering, break on the path string so the
result is the same every run for the same inputs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from ..settings_schema import FIELDS
from .helptext import help_for
from .settings_table import SettingDescriptor

_TOKEN_RE = re.compile(r'[a-z0-9]+')

#: Confidence tiers, highest first. Multiplied by a per-surface weight below.
_EXACT = 1000.0
_WHOLE_WORD = 950.0
_PREFIX = 900.0
_SUBSTRING = 800.0
_ALL_WORDS = 700.0
_SUBSEQUENCE = 600.0
_TYPO_BASE = 500.0
_TYPO_MIN_RATIO = 0.6

#: How much each surface counts, when several all match the same query.
_SURFACE_WEIGHTS = {
    'path': 1.0,
    'label': 0.95,
    'flag': 0.9,
    'help': 0.6,
}


def _words(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(text.lower()))


def _is_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return False
    pos = 0
    for char in haystack:
        if pos < len(needle) and char == needle[pos]:
            pos += 1
            if pos == len(needle):
                return True
    return pos == len(needle)


def _surface_score(query: str, query_words: tuple[str, ...], text: str | None,
                    *, allow_loose: bool = True) -> float:
    """Best tier this one surface reaches for ``query``, or 0 for no match.

    ``allow_loose`` gates the subsequence and typo-tolerance tiers. Help text
    is a paragraph, not a keyword; letting a short query subsequence-match or
    ratio-match against a paragraph finds almost anything, so the free-text
    help surface only takes part in the exact/word/prefix/substring tiers.
    """
    if not text:
        return 0.0
    words = _words(text)
    if not words:
        return 0.0
    normalised = ' '.join(words)
    if query == normalised:
        return _EXACT
    if len(query_words) == 1 and query_words[0] in words:
        return _WHOLE_WORD
    if normalised.startswith(query):
        return _PREFIX
    if query in normalised:
        return _SUBSTRING
    if len(query_words) > 1 and all(word in words for word in query_words):
        return _ALL_WORDS
    if not allow_loose:
        return 0.0
    if _is_subsequence(query.replace(' ', ''), ''.join(words)):
        return _SUBSEQUENCE
    ratios = [max((SequenceMatcher(None, qword, word).ratio() for word in words), default=0.0)
              for qword in query_words]
    average = sum(ratios) / len(ratios) if ratios else 0.0
    if average >= _TYPO_MIN_RATIO:
        return _TYPO_BASE * average
    return 0.0


def score_descriptor(query: str, descriptor: SettingDescriptor) -> float:
    """0 (no match) or a positive score; higher is a better match."""
    query = query.strip().lower()
    if not query:
        return 0.0
    query_words = _words(query)
    if not query_words:
        return 0.0
    field = FIELDS.get(descriptor.path)
    flag = field.flag if field is not None else None
    surfaces = {
        'path': descriptor.path,
        'label': descriptor.label,
        'flag': flag,
        'help': help_for(descriptor.path),
    }
    best = 0.0
    for name, text in surfaces.items():
        tier = _surface_score(query, query_words, text, allow_loose=(name != 'help'))
        if tier <= 0.0:
            continue
        best = max(best, tier * _SURFACE_WEIGHTS[name])
    return best


@dataclass(frozen=True)
class SearchMatch:
    descriptor: SettingDescriptor
    score: float


def rank_settings(query: str, descriptors: Iterable[SettingDescriptor]) -> list[SearchMatch]:
    """Every descriptor that matches ``query``, best first, ties by path.

    An empty or whitespace-only query matches nothing: the caller's job is to
    show everything unfiltered in that case, not to ask this function to rank
    an unranked list.
    """
    query = (query or '').strip()
    if not query:
        return []
    scored = ((descriptor, score_descriptor(query, descriptor)) for descriptor in descriptors)
    matches = [SearchMatch(descriptor, score) for descriptor, score in scored if score > 0.0]
    matches.sort(key=lambda match: (-match.score, match.descriptor.path))
    return matches
