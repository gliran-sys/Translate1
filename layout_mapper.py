"""
layout_mapper.py
Handles keyboard layout translation between English QWERTY and Hebrew.

The mapping reflects the standard Israeli Hebrew keyboard layout:
physical key positions on a QWERTY keyboard map to their Hebrew equivalents.
"""

# Standard Hebrew keyboard layout mapping (English physical key → Hebrew character)
EN_TO_HE: dict[str, str] = {
    'q': '/',  'w': "'", 'e': 'ק', 'r': 'ר', 't': 'א',
    'y': 'ט',  'u': 'ו', 'i': 'ן', 'o': 'ם', 'p': 'פ',
    'a': 'ש',  's': 'ד', 'd': 'ג', 'f': 'כ', 'g': 'ע',
    'h': 'י',  'j': 'ח', 'k': 'ל', 'l': 'ך',
    'z': 'ז',  'x': 'ס', 'c': 'ב', 'v': 'ה', 'b': 'נ',
    'n': 'מ',  'm': 'צ',
}

# Reverse mapping: Hebrew character → English physical key
HE_TO_EN: dict[str, str] = {v: k for k, v in EN_TO_HE.items()}

# All Hebrew characters we can recognise
_HE_CHARS = set(EN_TO_HE.values())
# All Latin letters we map from
_EN_CHARS = set(EN_TO_HE.keys())


def _is_latin_alpha(text: str) -> bool:
    """Return True if every character is an ASCII letter (a-z / A-Z)."""
    return bool(text) and all(c.lower() in _EN_CHARS for c in text)


def _is_hebrew(text: str) -> bool:
    """Return True if every character is a mapped Hebrew letter."""
    return bool(text) and all(c in _HE_CHARS for c in text)


def translate(text: str) -> str | None:
    """
    Auto-detect the script of *text* and translate to the other layout.

    Returns:
        The translated string, or None if:
        - The text is empty or contains unmapped characters.
        - The translation would be identical to the input.
    """
    if not text:
        return None

    lower = text.lower()

    if _is_latin_alpha(lower):
        # English physical keys → Hebrew characters
        result = ''.join(EN_TO_HE[c] for c in lower)
        return result if result != text else None

    if _is_hebrew(text):
        # Hebrew characters → English physical keys
        result = ''.join(HE_TO_EN[c] for c in text)
        return result if result != text else None

    return None


def get_supported_layouts() -> list[str]:
    """Return the list of supported layout-pair names."""
    return ['English ↔ Hebrew']
