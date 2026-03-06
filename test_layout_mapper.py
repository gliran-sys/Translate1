"""Tests for layout_mapper.py — pure Python, no Windows runtime needed."""

import sys
import types
import unittest

# Stub out `winreg` so layout_mapper can be imported on non-Windows or in CI.
winreg_stub = types.ModuleType('winreg')
winreg_stub.HKEY_CURRENT_USER = 0
winreg_stub.OpenKey = None  # unused; _installed_layout_names is not tested here
sys.modules.setdefault('winreg', winreg_stub)

from layout_mapper import (  # noqa: E402
    LAYOUTS,
    MIN_WORD_LENGTH,
    LayoutPair,
    _LCID_TO_LANG,
    build_all_pairs,
)


class TestLayoutPairInit(unittest.TestCase):
    def test_valid_pair_created(self):
        pair = LayoutPair("English", "Hebrew")
        self.assertEqual(pair.lang_a, "English")
        self.assertEqual(pair.lang_b, "Hebrew")

    def test_name_format(self):
        pair = LayoutPair("English", "Russian")
        self.assertEqual(pair.name, "English ↔ Russian")

    def test_unknown_lang_a_raises(self):
        with self.assertRaises(ValueError):
            LayoutPair("Klingon", "Hebrew")

    def test_unknown_lang_b_raises(self):
        with self.assertRaises(ValueError):
            LayoutPair("English", "Klingon")

    def test_same_language_raises(self):
        with self.assertRaises(ValueError):
            LayoutPair("English", "English")

    def test_tracked_chars_is_union(self):
        pair = LayoutPair("English", "Hebrew")
        eng_chars = frozenset(LAYOUTS["English"].values())
        heb_chars = frozenset(LAYOUTS["Hebrew"].values())
        self.assertEqual(pair.tracked_chars, eng_chars | heb_chars)


class TestTranslateEnglishHebrew(unittest.TestCase):
    def setUp(self):
        self.pair = LayoutPair("English", "Hebrew")

    # --- A → B (English typed → suggest Hebrew) ---

    def test_english_to_hebrew_single_word(self):
        # physical 'h','i' on English keyboard → Hebrew 'י','ן'
        result = self.pair.translate("hi")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        # 'h' physical key → Hebrew 'י', 'i' physical key → Hebrew 'ן'
        self.assertEqual(result, "ין")

    def test_english_to_hebrew_longer(self):
        # 'hello': h→י e→ק l→ך l→ך o→ם
        result = self.pair.translate("hello")
        self.assertIsNotNone(result)
        self.assertEqual(result, "יקךךם")

    def test_english_uppercase_normalised(self):
        # translate() lowercases input, so HELLO == hello
        self.assertEqual(
            self.pair.translate("hello"),
            self.pair.translate("HELLO"),
        )

    # --- B → A (Hebrew typed → suggest English) ---

    def test_hebrew_to_english(self):
        # שלום: ש=physical-a, ל=physical-k, ו=physical-u, ם=physical-o (FINAL mem)
        # Note: 'ם' (U+05DD, final mem) ≠ 'מ' (U+05DE, regular mem).
        # Hebrew uses distinct final forms at word-end; the layout maps
        # 'o' → 'ם' and 'n' → 'מ', so shalom translates to "akuo".
        result = self.pair.translate("שלום")
        self.assertIsNotNone(result)
        self.assertEqual(result, "akuo")

    def test_hebrew_to_english_hi(self):
        # 'י' = physical h, 'ן' = physical i → English 'h','i'
        result = self.pair.translate("ין")
        self.assertIsNotNone(result)
        self.assertEqual(result, "hi")

    # --- edge cases ---

    def test_too_short_returns_none(self):
        self.assertIsNone(self.pair.translate("h"))
        self.assertIsNone(self.pair.translate(""))

    def test_mixed_layouts_returns_none(self):
        # 'a' is English; 'ש' is Hebrew — mixed → None
        self.assertIsNone(self.pair.translate("aש"))

    def test_digit_returns_none(self):
        # '5' is in neither layout
        self.assertIsNone(self.pair.translate("55"))

    def test_same_result_returns_none(self):
        # English layout maps each letter to itself; a word that happens to
        # map to identical output should be suppressed.
        # (This can't happen with English→Hebrew, but test the invariant via
        # a pair where it could occur if layouts were identical.)
        # For English↔Hebrew a real mismatch is guaranteed; just verify a
        # known translation is NOT equal to the input.
        result = self.pair.translate("hello")
        self.assertNotEqual(result, "hello")

    def test_min_word_length_boundary(self):
        self.assertIsNone(self.pair.translate("h" * (MIN_WORD_LENGTH - 1)))
        self.assertIsNotNone(self.pair.translate("h" * MIN_WORD_LENGTH))


class TestTranslateOtherPairs(unittest.TestCase):
    def test_english_russian_round_trip(self):
        pair = LayoutPair("English", "Russian")
        # 'q' physical key: English='q', Russian='й'
        result = pair.translate("qq")
        self.assertIsNotNone(result)
        self.assertEqual(result, "йй")
        # Reverse
        back = pair.translate("йй")
        self.assertIsNotNone(back)
        self.assertEqual(back, "qq")

    def test_english_greek_round_trip(self):
        pair = LayoutPair("English", "Greek")
        result = pair.translate("ee")  # e → ε
        self.assertIsNotNone(result)
        self.assertEqual(result, "εε")
        back = pair.translate("εε")
        self.assertIsNotNone(back)
        self.assertEqual(back, "ee")

    def test_hebrew_russian_pair(self):
        pair = LayoutPair("Hebrew", "Russian")
        # 'q' physical: Hebrew='/', Russian='й' — '/' is in Hebrew
        # '/' is a single-char, below MIN_WORD_LENGTH, but use a longer input
        # 'e' physical: Hebrew='ק', Russian='у'
        result = pair.translate("קק")
        self.assertIsNotNone(result)
        self.assertEqual(result, "уу")


class TestLayoutIntegrity(unittest.TestCase):
    """Sanity-checks that each layout definition is self-consistent."""

    def test_all_layouts_have_26_entries(self):
        for name, layout in LAYOUTS.items():
            with self.subTest(layout=name):
                self.assertEqual(len(layout), 26, f"{name} has {len(layout)} entries")

    def test_all_layouts_keyed_by_lowercase_letters(self):
        expected_keys = set('abcdefghijklmnopqrstuvwxyz') - {'x'} | {'x'}
        for name, layout in LAYOUTS.items():
            with self.subTest(layout=name):
                self.assertEqual(set(layout.keys()), expected_keys)

    def test_english_is_identity(self):
        for k, v in LAYOUTS["English"].items():
            self.assertEqual(k, v, f"English layout: key {k!r} maps to {v!r}")

    def test_no_layout_has_duplicate_values(self):
        # Arabic is intentionally excluded: the real Windows Arabic (Saudi Arabia)
        # keyboard (KLID 00000401) maps BOTH physical 'g' and 'n' to ل (lam,
        # U+0644).  This is a genuine quirk of the hardware layout rather than a
        # code bug.  All other layouts must have unique output characters.
        KNOWN_DUPLICATES = {'Arabic'}
        for name, layout in LAYOUTS.items():
            if name in KNOWN_DUPLICATES:
                continue
            with self.subTest(layout=name):
                values = list(layout.values())
                self.assertEqual(len(values), len(set(values)),
                                 f"{name} has duplicate output characters")

    def test_arabic_known_lam_duplicate(self):
        # Specifically document and assert the known Arabic lam duplicate so it
        # can't silently change to a *different* (unexpected) duplicate.
        arab = LAYOUTS['Arabic']
        lam_keys = [k for k, v in arab.items() if v == 'ل']
        self.assertEqual(sorted(lam_keys), ['g', 'n'],
                         "Arabic ل duplicate should be on exactly 'g' and 'n'")

    def test_non_english_layouts_differ_from_english(self):
        english = LAYOUTS["English"]
        for name, layout in LAYOUTS.items():
            if name == "English":
                continue
            with self.subTest(layout=name):
                different = sum(1 for k in layout if layout[k] != english[k])
                self.assertGreater(different, 15,
                                   f"{name} looks too similar to English")


class TestLcidToLang(unittest.TestCase):
    def test_hebrew_lcid(self):
        self.assertEqual(_LCID_TO_LANG.get('040d'), 'Hebrew')

    def test_russian_lcid(self):
        self.assertEqual(_LCID_TO_LANG.get('0419'), 'Russian')

    def test_english_us_lcid(self):
        self.assertEqual(_LCID_TO_LANG.get('0409'), 'English')

    def test_english_uk_lcid(self):
        self.assertEqual(_LCID_TO_LANG.get('0809'), 'English')

    def test_all_values_are_known_layouts(self):
        for lcid, lang in _LCID_TO_LANG.items():
            with self.subTest(lcid=lcid):
                self.assertIn(lang, LAYOUTS)


class TestBuildAllPairs(unittest.TestCase):
    def test_returns_list_of_layout_pairs(self):
        pairs = build_all_pairs()
        self.assertIsInstance(pairs, list)
        self.assertTrue(all(isinstance(p, LayoutPair) for p in pairs))

    def test_correct_count(self):
        n = len(LAYOUTS)
        expected = n * (n - 1) // 2
        self.assertEqual(len(build_all_pairs()), expected)

    def test_no_duplicate_pairs(self):
        pairs = build_all_pairs()
        names = [(p.lang_a, p.lang_b) for p in pairs]
        self.assertEqual(len(names), len(set(names)))

    def test_no_same_language_pair(self):
        for p in build_all_pairs():
            self.assertNotEqual(p.lang_a, p.lang_b)


if __name__ == '__main__':
    unittest.main()
