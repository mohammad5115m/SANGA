from io import BytesIO
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "static" / "icons"


def icon_content(maskable=False):
    if maskable:
        opening = '<rect width="96" height="96" fill="#23362d"/><g transform="translate(10 10) scale(.791667)">'
        closing = "</g>"
    else:
        opening = '<rect width="96" height="96" rx="21" fill="#23362d"/>'
        closing = ""
    return "\n".join(
        [
            opening,
            '<path d="M22 75V45C22 31 33 22 48 14C63 22 74 31 74 45V75" fill="none" stroke="#d3bd99" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>',
            '<path d="M39 37 57 38 68 50 63 66 48 73 31 64 28 51Z" fill="#fffdf9"/>',
            '<path d="M39 37 57 38 68 50 47 52Z" fill="#8b5e2b"/>',
            '<path d="M47 52 68 50 63 66 48 73Z" fill="#d3bd99"/>',
            closing,
        ]
    )


font = TTFont(ROOT / "static" / "fonts" / "Vazirmatn.woff2")
font.flavor = None
font = instantiateVariableFont(font, {"wght": 700}, inplace=False)
font_bytes = BytesIO()
font.save(font_bytes)
hb_font = hb.Font(hb.Face(font_bytes.getvalue()))
hb_font.scale = (font["head"].unitsPerEm,) * 2


def word_paths(value, color, origin):
    buffer = hb.Buffer()
    buffer.add_str(value)
    buffer.guess_segment_properties()
    hb.shape(hb_font, buffer)
    x = 0
    paths = []
    glyphset = font.getGlyphSet()
    for info, position in zip(buffer.glyph_infos, buffer.glyph_positions):
        glyph = glyphset[font.getGlyphName(info.codepoint)]
        pen = SVGPathPen(glyphset)
        glyph.draw(pen)
        d = pen.getCommands()
        if d:
            paths.append(
                f'<path transform="translate({x + position.x_offset} {position.y_offset})" d="{d}"/>'
            )
        x += position.x_advance
    return f'<g fill="{color}" transform="translate({origin} 53) scale(.022 -.022)">\n' + "\n".join(paths) + "\n</g>"


for name, primary, accent in (
    ("logo.svg", "#23362d", "#8b5e2b"),
    ("logo-inverse.svg", "#fffdf9", "#d3bd99"),
):
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="285" height="78" viewBox="0 0 285 78" role="img" aria-labelledby="logo-title">
  <title id="logo-title">Sang Esf</title>
  <g transform="translate(1 1) scale(.791667)">
    {icon_content()}
  </g>
  {word_paths("Sang", primary, 94)}
  {word_paths("Esf", accent, 208)}
</svg>
'''
    (ICONS / name).write_text(svg, encoding="utf-8")

for name, maskable in (("icon.svg", False), ("icon-maskable.svg", True)):
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96" role="img" aria-labelledby="logo-title">
  <title id="logo-title">Sang Esf</title>
  {icon_content(maskable)}
</svg>
'''
    (ICONS / name).write_text(svg, encoding="utf-8")
