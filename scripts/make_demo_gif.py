#!/usr/bin/env python3
"""
Generate docs/hotswap-demo.gif — the README hero animation.

Shows the full MCP Salad loop:
  salad suggest → pick a server → salad enable → tools live in session
Pure Pillow, no external GIF tooling needed.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent.parent / "docs" / "hotswap-demo.gif"

# ── Canvas / theme (GitHub dark) ─────────────────────────────────────────────
W, H   = 860, 560
PAD    = 28
LINE_H = 28
BG     = (13, 17, 23)
PANEL  = (22, 27, 34)
BORDER = (48, 54, 61)
FG     = (201, 209, 217)
MUTED  = (110, 118, 129)
GREEN  = (63, 185, 80)
CYAN   = (56, 189, 248)
YELLOW = (240, 200, 90)
PINK   = (255, 105, 180)
BLUE   = (88, 166, 255)
DIM    = (72, 79, 88)

FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
font      = ImageFont.truetype(FONT_PATH, 18)
font_b    = ImageFont.truetype(FONT_PATH, 18, index=1)
title_f   = ImageFont.truetype(FONT_PATH, 13)

Y0 = PAD + 50  # first text line


def draw_base():
    img = Image.new("RGB", (W, H), BG)
    d   = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [PAD - 12, PAD - 12, W - PAD + 12, H - PAD + 12],
        radius=12, fill=PANEL, outline=BORDER, width=1,
    )
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([PAD + i*22, PAD - 2, PAD + 12 + i*22, PAD + 10], fill=c)
    d.text((PAD + 80, PAD - 4), "mcp-salad  —  suggest → enable → use",
           font=title_f, fill=MUTED)
    return img, d


def render(lines, caption=""):
    """lines = list of [(text, color, bold?), ...]  per line."""
    img, d = draw_base()
    y = Y0
    for parts in lines:
        x = PAD + 8
        for part in parts:
            text  = part[0]
            color = part[1]
            bold  = len(part) > 2 and part[2]
            f     = font_b if bold else font
            d.text((x, y), text, font=f, fill=color)
            x += int(d.textlength(text, font=f))
        y += LINE_H
    if caption:
        cw = d.textlength(caption, font=font_b)
        d.text(((W - cw) / 2, H - PAD - 10), caption, font=font_b, fill=YELLOW)
    return img


# ── Script: (lines added this step, hold_ms) ─────────────────────────────────
# Each step appends to all previous lines (cumulative display).

def L(*parts):
    """Shorthand: one line = list of (text, color[, bold]) tuples."""
    return list(parts)

def BLANK():
    return [("", FG)]

# Each entry: (list-of-lines, hold_ms)
STEPS = [
    # ── salad suggest ──────────────────────────────────────────────────────
    ([L(("$ ", MUTED), ("salad suggest ", CYAN, True), ('"research patents"', FG))], 400),
    ([BLANK()], 80),
    ([L(("  🔍  Searching ", MUTED), ("14,373", YELLOW, True), (" servers for: ", MUTED), ("research, patents", CYAN))], 700),
    ([BLANK()], 80),
    ([L(("  1. ", DIM), ("lens-mcp          ", CYAN, True), ("[official] ", DIM), ("Patent search — USPTO, EPO, WIPO   ", MUTED), ("94%", GREEN))], 200),
    ([L(("  2. ", DIM), ("semantic-scholar  ", CYAN),  ("[official] ", DIM), ("200M+ papers, citations             ", MUTED), ("87%", GREEN))], 200),
    ([L(("  3. ", DIM), ("arxiv-mcp         ", CYAN),  ("[official] ", DIM), ("Preprint papers, full text access    ", MUTED), ("81%", GREEN))], 200),
    ([L(("  4. ", DIM), ("pubmed            ", CYAN),  ("[curated]  ", DIM), ("Biomedical literature (NIH)          ", MUTED), ("78%", GREEN))], 200),
    ([L(("  5. ", DIM), ("crossref-mcp      ", CYAN),  ("[official] ", DIM), ("DOI lookup, citation metadata        ", MUTED), ("71%", GREEN))], 300),
    ([BLANK()], 80),
    ([L(("  Install name (or ", MUTED), ("q", PINK), ("):  ", MUTED), ("lens-mcp", FG))], 800),

    # ── installed → enable ─────────────────────────────────────────────────
    ([BLANK()], 80),
    ([L(("  ✓ ", GREEN, True), ("lens-mcp", CYAN, True), (" installed", FG))], 600),
    ([BLANK()], 80),
    ([L(("$ ", MUTED), ("salad enable ", CYAN, True), ("lens-mcp", FG))], 400),
    ([L(("  ✓ ", GREEN, True), ("enabled", FG, True), (" — tools live in your session. No restart.", MUTED))], 500),
    ([BLANK()], 80),
    ([L(("  Claude now has: ", MUTED), ("search_patents  get_patent_details  check_prior_art  +4 more", YELLOW))], 800),

    # ── use in session ─────────────────────────────────────────────────────
    ([BLANK()], 80),
    ([L(("  you  › ", BLUE), ("  any prior art for mRNA lipid nanoparticle delivery?", FG))], 600),
    ([L(("  ✻    › ", CYAN), ("  Found 12 patents (US10,703,789 · EP3310359 · WO2018081480…)", FG))], 900),
    ([L(("         ", FG), ("↑ no restart. no new chat. it just appeared.", MUTED))], 1400),
]


def main():
    frames, durations = [], []
    shown = []

    for new_lines, hold_ms in STEPS:
        shown = shown + new_lines
        frames.append(render(shown))
        durations.append(hold_ms)

    # final frame with caption, held longer
    caption = "suggest → install → enable → use  |  no restart"
    for _ in range(3):
        frames.append(render(shown, caption=caption))
        durations.append(1000)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"wrote {OUT}  ({len(frames)} frames, {OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
