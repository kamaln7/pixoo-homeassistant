#!/usr/bin/env python3
"""Convert a bitmap font into the JSON format the integration loads.

    python3 scripts/font_to_json.py scientifica-11.bdf --out pixoo_fonts/scientifica.json
    python3 scripts/font_to_json.py some.ttf --size 8 --out pixoo_fonts/some.json

BDF is exact: it already *is* a bitmap, so the glyphs come out pixel for pixel.
TTF has to be rasterised, which needs Pillow and a size that suits the font --
pixel fonts are drawn for one specific size and turn to mush at any other.

Every glyph is normalised onto one cell whose height is the font's ascent plus
descent, because draw_text takes its line height from the '0' glyph and applies
it to every glyph in the font.

The integration advances the pen by ``width + 1`` between characters, so the
emitted width is the font's own advance minus that one column of spacing --
otherwise every font would come out a pixel loose.
"""

import argparse
import json
import os
import re
import sys

PRINTABLE = [chr(c) for c in range(32, 127)]


def _blank(width, height):
    return [["."] * width for _ in range(height)]


def _rows_to_strings(cell):
    return ["".join(row) for row in cell]


def parse_bdf(path, chars):
    """Return (glyphs, height) with every glyph normalised to one cell."""
    text = open(path, encoding="latin-1").read()

    def header(name, default=None):
        match = re.search(rf"^{name}\s+(-?\d+)", text, re.M)
        return int(match.group(1)) if match else default

    ascent = header("FONT_ASCENT")
    descent = header("FONT_DESCENT")
    if ascent is None or descent is None:
        # Fall back to the bounding box when the properties are absent.
        box = re.search(r"^FONTBOUNDINGBOX\s+(\d+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)", text, re.M)
        if not box:
            sys.exit(f"{path}: no FONT_ASCENT/FONT_DESCENT and no FONTBOUNDINGBOX")
        _, box_h, _, box_y = (int(v) for v in box.groups())
        ascent, descent = box_h + box_y, -box_y

    height = ascent + descent
    wanted = set(chars)
    glyphs = {}

    for block in text.split("STARTCHAR")[1:]:
        encoding = re.search(r"^ENCODING\s+(-?\d+)", block, re.M)
        bbx = re.search(r"^BBX\s+(\d+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)", block, re.M)
        bitmap = re.search(r"^BITMAP\s*\n(.*?)^ENDCHAR", block, re.M | re.S)
        if not (encoding and bbx and bitmap):
            continue

        codepoint = int(encoding.group(1))
        if codepoint < 0 or chr(codepoint) not in wanted:
            continue

        glyph_w, glyph_h, x_off, y_off = (int(v) for v in bbx.groups())
        dwidth = re.search(r"^DWIDTH\s+(-?\d+)", block, re.M)
        advance = int(dwidth.group(1)) if dwidth else glyph_w + 1

        # draw_text adds one column of spacing itself.
        cell_w = max(advance - 1, x_off + glyph_w, 1)
        cell = _blank(cell_w, height)

        rows = [r.strip() for r in bitmap.group(1).strip().splitlines() if r.strip()]
        top = ascent - (y_off + glyph_h)
        for row_index, row_hex in enumerate(rows[:glyph_h]):
            value = int(row_hex, 16)
            bit_count = len(row_hex) * 4
            y = top + row_index
            if not 0 <= y < height:
                continue
            for column in range(glyph_w):
                x = x_off + column
                if 0 <= x < cell_w and (value >> (bit_count - 1 - column)) & 1:
                    cell[y][x] = "#"

        glyphs[chr(codepoint)] = _rows_to_strings(cell)

    return glyphs, height


def parse_ttf(path, size, chars):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        sys.exit("Rasterising a TTF needs Pillow: pip install pillow")

    font = ImageFont.truetype(path, size)
    ascent, descent = font.getmetrics()
    height = ascent + descent

    def raster(character):
        try:
            advance = int(round(font.getlength(character)))
        except AttributeError:  # very old Pillow
            advance = font.getsize(character)[0]
        image = Image.new("1", (advance + 8, height), 0)
        ImageDraw.Draw(image).text((0, 0), character, font=font, fill=1)
        return advance, image

    # A character the font has no glyph for renders as .notdef, usually a
    # hollow box. Emitting those would put boxes on the panel; leaving them out
    # lets the integration fall back to '?' instead. U+FFFF is not assigned, so
    # whatever it draws IS this font's .notdef.
    notdef_image = raster("\uffff")[1]
    # Some fonts draw .notdef as nothing at all. Then it is indistinguishable
    # from a space and the check has to be switched off, or every space in
    # every string would be dropped.
    notdef = notdef_image.tobytes() if notdef_image.getbbox() else None

    glyphs = {}
    skipped = []
    for character in chars:
        advance, probe = raster(character)
        if notdef is not None and probe.tobytes() == notdef:
            skipped.append(character)
            continue

        # Rendered into a generous canvas: a glyph's ink can extend past its
        # advance, and sizing the cell from the advance alone clips it.
        image = probe
        box = image.getbbox()
        ink_right = box[2] if box else 0
        cell_w = max(advance - 1, ink_right, 1)

        cell = _blank(cell_w, height)
        pixels = image.load()
        for y in range(height):
            for x in range(cell_w):
                if pixels[x, y]:
                    cell[y][x] = "#"
        glyphs[character] = _rows_to_strings(cell)

    return glyphs, height


def tighten_columns(glyphs):
    """Strip blank columns from each side of every glyph.

    A face's own left side bearing lands here as leading blank columns, and
    draw_text already adds a column of spacing between characters, so carrying
    the bearing too spaces the text a pixel wider than the font intends.
    Glyphs that are entirely blank -- space, most obviously -- are left alone,
    since their width is the whole point of them.
    """
    out = {}
    for character, rows in glyphs.items():
        cols = len(rows[0])
        inked = [x for x in range(cols) if any(row[x] != "." for row in rows)]
        if not inked:
            out[character] = rows
            continue
        first, last = inked[0], inked[-1]
        out[character] = [row[first:last + 1] for row in rows]
    return out


def trim_blank_rows(glyphs):
    """Drop rows that are blank in every glyph, top and bottom.

    A font's cell is ascent+descent tall, which usually leaves a couple of empty
    rows above and below the ink. On a 64px panel those rows are worth
    reclaiming, and dropping them uniformly keeps every glyph aligned.
    """
    if not glyphs:
        return glyphs, 0

    height = len(next(iter(glyphs.values())))
    blank = lambda index: all(set(rows[index]) <= {"."} for rows in glyphs.values())

    top = 0
    while top < height and blank(top):
        top += 1
    if top == height:  # every glyph is empty; leave it alone
        return glyphs, height

    bottom = height
    while bottom > top and blank(bottom - 1):
        bottom -= 1

    return {c: rows[top:bottom] for c, rows in glyphs.items()}, bottom - top


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("font", help="path to a .bdf or .ttf")
    parser.add_argument("--out", help="output JSON path (default: stdout)")
    parser.add_argument("--size", type=int, help="pixel size, TTF only")
    parser.add_argument("--force-uppercase", action="store_true",
                        help="set when the font has no lower-case glyphs")
    parser.add_argument("--chars", help="characters to include (default: printable ASCII)")
    parser.add_argument("--preview", action="store_true", help="print a few glyphs as ASCII and exit")
    parser.add_argument("--trim", action="store_true",
                        help="drop rows blank across every glyph, reclaiming the cell's padding")
    parser.add_argument("--tight", action="store_true",
                        help="strip each glyph's blank side columns, so only draw_text's own "
                             "one column of spacing separates characters")
    args = parser.parse_args()

    chars = list(args.chars) if args.chars else PRINTABLE

    if args.font.lower().endswith(".bdf"):
        glyphs, height = parse_bdf(args.font, chars)
    else:
        if not args.size:
            sys.exit("--size is required for a TTF")
        glyphs, height = parse_ttf(args.font, args.size, chars)

    if not glyphs:
        sys.exit("no glyphs were produced")

    if args.trim:
        glyphs, height = trim_blank_rows(glyphs)
    if args.tight:
        glyphs = tighten_columns(glyphs)
    for required in ("0", "?"):
        if required not in glyphs:
            print(f"warning: no '{required}' glyph; the integration needs it", file=sys.stderr)

    if args.preview:
        for character in "A0?:W a":
            if character in glyphs:
                print(f"{character!r} w={len(glyphs[character][0])}")
                for row in glyphs[character]:
                    print("   " + row)
        return

    payload = {
        "height": height,
        "force_uppercase": args.force_uppercase,
        "glyphs": {c: glyphs[c] for c in chars if c in glyphs},
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        open(args.out, "w", encoding="utf-8").write(text)
        widths = {len(rows[0]) for rows in glyphs.values()}
        print(f"{args.out}: {len(glyphs)} glyphs, {height}px tall, widths {min(widths)}-{max(widths)}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
