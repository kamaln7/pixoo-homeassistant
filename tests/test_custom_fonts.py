import unittest

from custom_components.divoom_pixoo.pixoo64._custom_fonts import compile_font
from custom_components.divoom_pixoo.pixoo64._font import FONT_PICO_8, retrieve_glyph_width


def font(**glyphs):
    """A payload with the two glyphs the loader insists on, plus any extras."""
    base = {"0": ["###", "#.#", "###"], "?": ["##.", ".#.", ".#."]}
    base.update(glyphs)
    return {"glyphs": base}


class TestCompileFont(unittest.TestCase):
    def test_compiles_rows_to_bits_with_width_appended(self):
        glyphs, force_uppercase = compile_font("t", font(A=["#.#", ".#.", "#.#"]))

        # row-major bits, then the cell width
        self.assertEqual([1, 0, 1, 0, 1, 0, 1, 0, 1, 3], glyphs["A"])
        self.assertFalse(force_uppercase)

    def test_width_comes_from_row_length_so_fonts_can_be_proportional(self):
        glyphs, _ = compile_font("t", font(I=["#", "#", "#"], M=["#.#.#", "#.#.#", "#.#.#"]))

        self.assertEqual(1, glyphs["I"][-1])
        self.assertEqual(5, glyphs["M"][-1])

    def test_accepts_alternative_ink_characters(self):
        glyphs, _ = compile_font("t", font(A=["1 1", "0X0", "*.*"]))

        self.assertEqual([1, 0, 1, 0, 1, 0, 1, 0, 1, 3], glyphs["A"])

    def test_force_uppercase_is_read_from_the_payload(self):
        payload = font()
        payload["force_uppercase"] = True

        self.assertTrue(compile_font("t", payload)[1])

    def test_rejects_glyphs_of_differing_heights(self):
        # draw_text measures line height from '0' alone, so mixed heights would
        # silently misplace text rather than fail.
        self.assertIsNone(compile_font("t", font(A=["#.#", ".#."]))[0])

    def test_rejects_ragged_rows(self):
        self.assertIsNone(compile_font("t", font(A=["#.#", ".#", "#.#"]))[0])

    def test_rejects_multi_character_keys(self):
        self.assertIsNone(compile_font("t", font(**{"AB": ["#.#", ".#.", "#.#"]}))[0])

    def test_rejects_a_font_without_a_zero_glyph(self):
        self.assertIsNone(compile_font("t", {"glyphs": {"?": ["#", "#", "#"]}})[0])

    def test_rejects_a_declared_height_that_contradicts_the_rows(self):
        payload = font()
        payload["height"] = 9

        self.assertIsNone(compile_font("t", payload)[0])

    def test_rejects_empty_and_malformed_payloads(self):
        self.assertIsNone(compile_font("t", {})[0])
        self.assertIsNone(compile_font("t", {"glyphs": {}})[0])
        self.assertIsNone(compile_font("t", "nope")[0])
        self.assertIsNone(compile_font("t", {"glyphs": {"0": "###"}})[0])


class TestRetrieveGlyphWidth(unittest.TestCase):
    def test_missing_glyph_measures_as_the_fallback_that_gets_drawn(self):
        # draw_text substitutes '?' and advances by its width, so measuring has
        # to agree or centred text drifts by half the difference.
        self.assertEqual(FONT_PICO_8["?"][-1], retrieve_glyph_width("ø", FONT_PICO_8))

    def test_present_glyph_measures_itself(self):
        self.assertEqual(FONT_PICO_8["W"][-1], retrieve_glyph_width("W", FONT_PICO_8))

    def test_font_without_a_fallback_still_returns_zero(self):
        self.assertEqual(0, retrieve_glyph_width("x", {"0": [1, 1]}))


if __name__ == '__main__':
    unittest.main()
