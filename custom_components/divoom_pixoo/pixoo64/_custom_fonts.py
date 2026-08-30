"""Load user-supplied bitmap fonts.

Fonts live in a directory configured per device (``pixoo_fonts`` under the Home
Assistant config directory by default), one JSON file per font, named after the
font: ``scientifica.json`` is used from a page as ``font: scientifica``.

The file format is meant to be read and edited by a person, so a glyph is a list
of rows drawn with ``#`` for a lit pixel and ``.`` for an empty one::

    {
      "height": 7,
      "force_uppercase": false,
      "glyphs": {
        "A": [".##.",
              "#..#",
              "#..#",
              "####",
              "#..#",
              "#..#",
              "#..#"],
        "!": ["#", "#", "#", "#", "#", ".", "#"]
      }
    }

``1``/``X``/``x``/``*`` also count as lit and ``0``/space as empty, so output
pasted from other pixel-font tools usually works unchanged. A glyph's width is
however long its rows are, which is why proportional fonts need no width field:
``A`` above is 4px wide and ``!`` is 1px.

Rules, all of which are checked at load time so a bad file fails loudly instead
of drawing nonsense:

* every row within a glyph must be the same length,
* ``height`` is optional and only cross-checks the tallest glyph,
* a ``0`` glyph must exist, because ``draw_text`` takes the line spacing for
  every line from it.

Glyphs need not all be the same height. ``draw_character`` plots only lit pixels
and draws downward from the text position, so trailing blank rows render exactly
the same as no rows at all and a glyph without a descender need not carry the
padding. Leading blank rows are a different matter: every glyph is top-aligned
at the same y, so those are what put glyphs on a shared baseline and must be
kept.

Internally the integration wants a flat row-major list of bits with the cell
width appended, so this module compiles to that on load.
"""

import json
import logging
import os

_LOGGER = logging.getLogger(__name__)

FONTS_DIRNAME = "pixoo_fonts"

# Everything else is treated as an empty pixel.
INK_CHARACTERS = frozenset("#1Xx*")


def resolve_fonts_dir(hass, configured):
    """Absolute path of the font directory for a device."""
    configured = (configured or "").strip() or FONTS_DIRNAME
    if os.path.isabs(configured):
        return configured
    return hass.config.path(configured)


def _compile_glyph(rows):
    """Rows of '#'/'.' -> flat bit list with the width appended."""
    width = len(rows[0])
    bits = [1 if pixel in INK_CHARACTERS else 0 for row in rows for pixel in row]
    bits.append(width)
    return bits


def compile_font(font_name, payload):
    """Validate and compile one font payload.

    Returns (glyphs, force_uppercase), or (None, False) if the font is unusable.
    """
    if not isinstance(payload, dict):
        _LOGGER.error("Custom font '%s' must be a JSON object.", font_name)
        return None, False

    raw_glyphs = payload.get("glyphs")
    if not isinstance(raw_glyphs, dict) or not raw_glyphs:
        _LOGGER.error("Custom font '%s' has no glyphs.", font_name)
        return None, False

    glyphs = {}
    heights = set()
    for character, rows in raw_glyphs.items():
        if len(character) != 1:
            _LOGGER.error("Custom font '%s': %r is not a single character.", font_name, character)
            return None, False
        if not isinstance(rows, list) or not rows or not all(isinstance(row, str) for row in rows):
            _LOGGER.error("Custom font '%s': glyph %r must be a list of row strings.", font_name, character)
            return None, False

        widths = {len(row) for row in rows}
        if len(widths) > 1:
            _LOGGER.error("Custom font '%s': glyph %r has rows of differing lengths %s.",
                          font_name, character, sorted(widths))
            return None, False
        if widths == {0}:
            _LOGGER.error("Custom font '%s': glyph %r has empty rows.", font_name, character)
            return None, False

        heights.add(len(rows))
        glyphs[character] = _compile_glyph(rows)

    height = max(heights)
    declared = payload.get("height")
    if declared is not None and declared != height:
        _LOGGER.error("Custom font '%s': declares height %s but its tallest glyph is %d rows.",
                      font_name, declared, height)
        return None, False

    if "0" not in glyphs:
        _LOGGER.error("Custom font '%s': needs a '0' glyph, which draw_text uses to measure line height.",
                      font_name)
        return None, False

    # Glyphs may differ in height. draw_character plots only lit pixels and
    # draws downward from the text position, so trailing blank rows render
    # identically to no rows at all -- a descender-less glyph simply need not
    # carry the padding. What must be consistent is the TOP of the cell: every
    # glyph is top-aligned at the same y, so leading blank rows are what put
    # glyphs on a shared baseline and cannot be dropped.
    if len(heights) > 1:
        _LOGGER.debug("Custom font '%s': glyph heights vary (%s); assuming a shared top origin.",
                      font_name, sorted(heights))

    # draw_text takes the line spacing for EVERY line from the '0' glyph alone,
    # so a '0' shorter than the font's tallest glyph tightens multi-line text
    # and can overlap descenders from the line above.
    if len(glyphs["0"]) - 1 < height:
        _LOGGER.warning(
            "Custom font '%s': the '0' glyph is %d rows but the tallest is %d. draw_text derives line "
            "spacing from '0', so multi-line text will be spaced %d rows apart and may overlap.",
            font_name, len(glyphs["0"]) - 1, height, len(glyphs["0"]) - 1,
        )

    if "?" not in glyphs:
        # Not fatal, but draw_text falls back to '?' for anything missing.
        _LOGGER.warning("Custom font '%s': has no '?' glyph, so unsupported characters will not draw.",
                        font_name)

    return glyphs, bool(payload.get("force_uppercase", False))


def load_font_file(path):
    """Load and compile one font file. Returns (glyphs, force_uppercase)."""
    font_name = os.path.splitext(os.path.basename(path))[0].lower()
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as err:
        _LOGGER.error("Could not read custom font '%s' from %s: %s", font_name, path, err)
        return None, False

    return compile_font(font_name, payload)


def load_fonts(directory):
    """Load every ``*.json`` font in `directory`.

    Returns ``{name: {"glyphs": ..., "force_uppercase": ...}}``. A missing
    directory just means no custom fonts. Does blocking file I/O, so call it
    from an executor.
    """
    fonts = {}
    if not os.path.isdir(directory):
        _LOGGER.debug("No custom font directory at %s.", directory)
        return fonts

    for filename in sorted(os.listdir(directory)):
        if not filename.lower().endswith(".json"):
            continue
        glyphs, force_uppercase = load_font_file(os.path.join(directory, filename))
        if glyphs is None:
            continue
        name = os.path.splitext(filename)[0].lower()
        fonts[name] = {"glyphs": glyphs, "force_uppercase": force_uppercase}
        _LOGGER.info("Loaded custom font '%s' (%d glyphs) from %s", name, len(glyphs), directory)

    return fonts
