"""
Resolve the free-text names and course references department forms are full of.

The same person appears as "Prof. N Suresh Kumar Reddy", "NSK Reddy", "Prof.
NSK Reddy" and "NSK"; the same course as "ME F219 - MANUFACTURING PROCESSES",
"Manufacturing Processes", "MP" and "Mp lab". Matching is conservative: an
ambiguous or weak match returns None and the caller records a note, because a
wrong match silently gives someone else's preference or section away.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_TITLES = re.compile(r"^(PROF|DR|MR|MS|MRS|SHRI|SMT)\.?\s+", re.I)
_CODE = re.compile(
    r"\b([A-Z]{2,5})(?:\s*/\s*[A-Z]{2,5})?\s*([FGHEKU])\s*(\d{3}[A-Z]?)\b"
)
_BARE_CODE = re.compile(r"\b([FGHEKU]\d{3}[A-Z]?)\b")


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", text.upper())).strip()


# ---- course codes ------------------------------------------------------------


def extract_code(text: str) -> str | None:
    """
    Pull a canonical course code out of text.

    'ME F221 - MECHANISMS' -> 'ME F221'; 'MF/ITEB F421' -> 'MF F421' (a
    cross-listed code is filed under its first department); 'BITSF241' ->
    'BITS F241'.
    """
    if not text:
        return None
    match = _CODE.search(text.upper())
    if not match:
        return None
    return f"{match.group(1)} {match.group(2)}{match.group(3)}"


class CourseResolver:
    """Map free text to a known course code."""

    def __init__(self, titles: dict[str, str], aliases: dict[str, str] | None = None):
        #: code -> title
        self.titles = {code: title for code, title in titles.items()}
        self.aliases = {normalise(k): v for k, v in (aliases or {}).items()}

    def add(self, code: str, title: str = "") -> None:
        if title and not self.titles.get(code):
            self.titles[code] = title
        else:
            self.titles.setdefault(code, title)

    def resolve(self, text: str | None) -> str | None:
        if not text or len(text.strip()) < 2:
            return None
        code = extract_code(text)
        if code:
            return code

        norm = normalise(text)
        if norm in self.aliases:
            return self.aliases[norm]

        bare = _BARE_CODE.search(text.upper())
        if bare:
            hits = [c for c in self.titles if c.endswith(" " + bare.group(1))]
            if len(hits) == 1:
                return hits[0]

        by_title = self._by_title(norm)
        if by_title:
            return by_title

        for token in norm.split():
            if token in self.aliases:
                return self.aliases[token]
        # "EDP-P" style: the alias is the part before a dash.
        head = normalise(re.split(r"[-(,/]", text)[0])
        if head in self.aliases:
            return self.aliases[head]
        return None

    def _by_title(self, norm: str) -> str | None:
        if len(norm) < 6:
            return None
        hits = []
        for code, title in self.titles.items():
            t = normalise(title)
            if not t:
                continue
            if norm == t or (len(t) >= 6 and (norm in t or t in norm)):
                hits.append(code)
        return hits[0] if len(hits) == 1 else None


# ---- people ------------------------------------------------------------------


def name_tokens(name: str) -> list[str]:
    cleaned = re.sub(r"\(.*?\)", " ", name)
    cleaned = re.sub(r"-\s*IC\b", " ", cleaned, flags=re.I)
    cleaned = _TITLES.sub("", cleaned.strip())
    cleaned = _TITLES.sub("", cleaned.strip())
    return normalise(cleaned).split()


def _token_score(q: str, roster_tokens: list[str], used: set[int]) -> tuple[float, int] | None:
    """Best (score, index) of an unused roster token matching query token q."""
    best: tuple[float, int] | None = None
    for i, r in enumerate(roster_tokens):
        if i in used:
            continue
        if q == r:
            score = 2.0
        elif len(q) == 1 and r.startswith(q):
            score = 0.8
        elif len(r) == 1 and q.startswith(r):
            score = 0.8
        elif len(q) >= 4 and len(r) >= 4 and SequenceMatcher(None, q, r).ratio() >= 0.84:
            score = 1.6  # spelling variant: ABHISEK / ABHISHEK
        else:
            continue
        if best is None or score > best[0]:
            best = (score, i)
    return best


def match_score(query: str, candidate: str) -> float:
    """
    How well `query` names the person `candidate`; 0 for no match.

    Every query token must be accounted for. Short all-caps tokens of two or
    three letters may be initials ("GV", "KS", "NSK"). At least one whole
    word must match unless the query is nothing but initials.
    """
    q_tokens, c_tokens = name_tokens(query), name_tokens(candidate)
    if not q_tokens or not c_tokens:
        return 0.0
    used: set[int] = set()
    total, whole = 0.0, 0
    for q in q_tokens:
        hit = _token_score(q, c_tokens, used)
        if hit is not None:
            used.add(hit[1])
            total += hit[0]
            # A shared initial ("K" = "K") is not a whole word: without this,
            # scholar "K Suhasini" matched faculty "Jayaprakash K S".
            whole += hit[0] >= 1.6 and len(q) > 1
            continue
        if 2 <= len(q) <= 3:
            letters = list(q)
            matched = []
            for letter in letters:
                h = _token_score(letter, c_tokens, used | set(matched))
                if h is None:
                    return 0.0
                matched.append(h[1])
            used.update(matched)
            total += 0.8 * len(letters)
            continue
        return 0.0
    if whole == 0 and not (len(q_tokens) == 1 and 2 <= len(q_tokens[0]) <= 4):
        return 0.0
    # Prefer candidates whose words are mostly covered.
    return total + 0.1 * len(used) / len(c_tokens)


class NameMatcher:
    """Match free-text names against a roster of (id, name, aliases)."""

    def __init__(self, roster: list[tuple[str, str, list[str]]], aliases: dict[str, str] | None = None):
        self.roster = roster
        self.by_exact: dict[str, str] = {}
        for pid, name, extra in roster:
            for n in [name, *extra]:
                self.by_exact.setdefault(" ".join(name_tokens(n)), pid)
        #: alias text -> canonical roster name
        self.aliases = {" ".join(name_tokens(k)): v for k, v in (aliases or {}).items()}

    def match(self, text: str | None) -> str | None:
        if not text or not text.strip():
            return None
        key = " ".join(name_tokens(text))
        if not key:
            return None
        if key in self.by_exact:
            return self.by_exact[key]
        if key in self.aliases:
            target = " ".join(name_tokens(self.aliases[key]))
            if target in self.by_exact:
                return self.by_exact[target]

        scored = sorted(
            ((match_score(text, name), pid) for pid, name, _ in self.roster),
            reverse=True,
        )
        if not scored or scored[0][0] <= 0:
            return None
        if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.3:
            return None  # ambiguous
        return scored[0][1]

    def best_score(self, text: str) -> float:
        return max((match_score(text, name) for _, name, _ in self.roster), default=0.0)
