"""
layout_mapper.py
Generic keyboard layout translation engine.

Each layout is a dict mapping physical QWERTY key labels (lowercase) to the
character that key produces in that language's keyboard layout.

A LayoutPair encapsulates two layouts and provides bidirectional translation:
  - If the typed text belongs to layout A's character set → translate to B
  - If the typed text belongs to layout B's character set → translate to A
  - If mixed or unmappable → return None (no suggestion)

Adding a new language: add an entry to LAYOUTS and it is automatically
available as part of any LayoutPair combination.
"""

from __future__ import annotations

import winreg

# ---------------------------------------------------------------------------
# Built-in layout definitions
# Physical QWERTY key label (lowercase) → character produced by that key
# ---------------------------------------------------------------------------

LAYOUTS: dict[str, dict[str, str]] = {
    "English": {
        'q': 'q', 'w': 'w', 'e': 'e', 'r': 'r', 't': 't',
        'y': 'y', 'u': 'u', 'i': 'i', 'o': 'o', 'p': 'p',
        'a': 'a', 's': 's', 'd': 'd', 'f': 'f', 'g': 'g',
        'h': 'h', 'j': 'j', 'k': 'k', 'l': 'l',
        'z': 'z', 'x': 'x', 'c': 'c', 'v': 'v', 'b': 'b',
        'n': 'n', 'm': 'm',
    },
    "Hebrew": {
        'q': '/', 'w': "'", 'e': 'ק', 'r': 'ר', 't': 'א',
        'y': 'ט', 'u': 'ו', 'i': 'ן', 'o': 'ם', 'p': 'פ',
        'a': 'ש', 's': 'ד', 'd': 'ג', 'f': 'כ', 'g': 'ע',
        'h': 'י', 'j': 'ח', 'k': 'ל', 'l': 'ך',
        'z': 'ז', 'x': 'ס', 'c': 'ב', 'v': 'ה', 'b': 'נ',
        'n': 'מ', 'm': 'צ',
    },
    "Russian": {
        'q': 'й', 'w': 'ц', 'e': 'у', 'r': 'к', 't': 'е',
        'y': 'н', 'u': 'г', 'i': 'ш', 'o': 'щ', 'p': 'з',
        'a': 'ф', 's': 'ы', 'd': 'в', 'f': 'а', 'g': 'п',
        'h': 'р', 'j': 'о', 'k': 'л', 'l': 'д',
        'z': 'я', 'x': 'ч', 'c': 'с', 'v': 'м', 'b': 'и',
        'n': 'т', 'm': 'ь',
    },
    "Arabic": {
        'q': 'ض', 'w': 'ص', 'e': 'ث', 'r': 'ق', 't': 'ف',
        'y': 'غ', 'u': 'ع', 'i': 'ه', 'o': 'خ', 'p': 'ح',
        'a': 'ش', 's': 'س', 'd': 'ي', 'f': 'ب', 'g': 'ل',
        'h': 'ا', 'j': 'ت', 'k': 'ن', 'l': 'م',
        'z': 'ظ', 'x': 'ط', 'c': 'ز', 'v': 'و', 'b': 'ر',
        'n': 'ل', 'm': 'ى',
    },
    "Greek": {
        'q': ';', 'w': 'ς', 'e': 'ε', 'r': 'ρ', 't': 'τ',
        'y': 'υ', 'u': 'θ', 'i': 'ι', 'o': 'ο', 'p': 'π',
        'a': 'α', 's': 'σ', 'd': 'δ', 'f': 'φ', 'g': 'γ',
        'h': 'η', 'j': 'ξ', 'k': 'κ', 'l': 'λ',
        'z': 'ζ', 'x': 'χ', 'c': 'ψ', 'v': 'ω', 'b': 'β',
        'n': 'ν', 'm': 'μ',
    },
}

# Minimum number of characters required before showing a suggestion.
# Prevents noisy single-character bubbles.
MIN_WORD_LENGTH = 2


# ---------------------------------------------------------------------------
# LayoutPair
# ---------------------------------------------------------------------------

class LayoutPair:
    """
    Encapsulates two keyboard layouts and provides bidirectional translation.

    Translation algorithm:
      A→B: for each char in input, find its physical key via the inverse of
           layout A, then look up that physical key in layout B.
      B→A: same in reverse.

    Auto-detection: if all (lowercased) input chars belong to layout A →
    try A→B; if all belong to layout B → try B→A.
    """

    def __init__(self, lang_a: str, lang_b: str) -> None:
        if lang_a not in LAYOUTS:
            raise ValueError(f"Unknown layout: {lang_a!r}.  "
                             f"Available: {list(LAYOUTS)}")
        if lang_b not in LAYOUTS:
            raise ValueError(f"Unknown layout: {lang_b!r}.  "
                             f"Available: {list(LAYOUTS)}")
        if lang_a == lang_b:
            raise ValueError("lang_a and lang_b must be different")

        self.lang_a = lang_a
        self.lang_b = lang_b

        self._map_a: dict[str, str] = LAYOUTS[lang_a]   # physical_key → char_a
        self._map_b: dict[str, str] = LAYOUTS[lang_b]   # physical_key → char_b

        # Inverse: char → physical_key
        self._inv_a: dict[str, str] = {v: k for k, v in self._map_a.items()}
        self._inv_b: dict[str, str] = {v: k for k, v in self._map_b.items()}

        # Frozensets of characters each layout produces
        self._chars_a: frozenset[str] = frozenset(self._map_a.values())
        self._chars_b: frozenset[str] = frozenset(self._map_b.values())

    @property
    def name(self) -> str:
        return f"{self.lang_a} ↔ {self.lang_b}"

    @property
    def tracked_chars(self) -> frozenset[str]:
        """
        Union of all characters from both layouts.
        The keyboard hook uses this to decide which keystrokes to accumulate
        (all others clear the buffer, signalling an untranslatable context).
        """
        return self._chars_a | self._chars_b

    def translate(self, text: str) -> str | None:
        """
        Auto-detect the source layout and translate to the other.

        Returns None if:
          - text has fewer than MIN_WORD_LENGTH characters
          - text contains characters not belonging exclusively to one layout
          - the translation would be identical to the input
        """
        if len(text) < MIN_WORD_LENGTH:
            return None

        # Normalise case so Latin-based layouts (like English) match lowercase
        lower = text.lower()

        # Try A → B
        if all(c in self._chars_a for c in lower):
            result = self._convert(lower, self._inv_a, self._map_b)
            if result and result != text:
                return result

        # Try B → A  (use original text — non-Latin scripts are case-neutral)
        if all(c in self._chars_b for c in text):
            result = self._convert(text, self._inv_b, self._map_a)
            if result and result != text:
                return result

        return None

    @staticmethod
    def _convert(
        text: str,
        inv_src: dict[str, str],
        dst: dict[str, str],
    ) -> str | None:
        """Translate *text* via inverse-source → destination lookup."""
        out: list[str] = []
        for ch in text:
            physical = inv_src.get(ch)
            if physical is None:
                return None
            target = dst.get(physical)
            if target is None:
                return None
            out.append(target)
        return ''.join(out)


# ---------------------------------------------------------------------------
# Convenience: pre-built pairs and defaults
# ---------------------------------------------------------------------------

# Maps the 4-hex-digit Windows LCID suffix to a LAYOUTS name.
# A keyboard layout code (KLID) looks like "0000040d"; the last 4 chars are
# the locale ID, so Hebrew "040d" → "Hebrew", Russian "0419" → "Russian", etc.
_LCID_TO_LANG: dict[str, str] = {
    # English variants
    '0409': 'English', '0809': 'English', '0c09': 'English',
    '1009': 'English', '1409': 'English', '1809': 'English',
    # Hebrew
    '040d': 'Hebrew',
    # Russian
    '0419': 'Russian',
    # Arabic variants
    '0401': 'Arabic', '0801': 'Arabic', '0c01': 'Arabic',
    '1001': 'Arabic', '1401': 'Arabic', '1801': 'Arabic',
    # Greek
    '0408': 'Greek',
}


def _installed_layout_names() -> list[str]:
    """
    Read HKCU\\Keyboard Layout\\Preload and return the LAYOUTS names that
    correspond to the user's installed Windows keyboard layouts.
    Falls back to all LAYOUTS names if the registry key is unavailable.
    """
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Keyboard Layout\Preload')
        found: set[str] = set()
        i = 0
        while True:
            try:
                _, klid, _ = winreg.EnumValue(key, i)
                lcid = str(klid).lower().zfill(8)[-4:]
                lang = _LCID_TO_LANG.get(lcid)
                if lang and lang in LAYOUTS:
                    found.add(lang)
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
        if found:
            return list(found)
    except OSError:
        pass
    return list(LAYOUTS)


def build_all_pairs() -> list[LayoutPair]:
    """Return one LayoutPair for every unique combination in LAYOUTS."""
    names = list(LAYOUTS)
    pairs: list[LayoutPair] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pairs.append(LayoutPair(a, b))
    return pairs


def build_installed_pairs() -> list[LayoutPair]:
    """
    Return LayoutPairs only for the keyboard layouts the user has installed
    in Windows.  Falls back to build_all_pairs() if detection fails.
    """
    names = _installed_layout_names()
    pairs: list[LayoutPair] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pairs.append(LayoutPair(a, b))
    return pairs or build_all_pairs()


DEFAULT_PAIR = LayoutPair("English", "Hebrew")
