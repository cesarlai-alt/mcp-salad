#!/usr/bin/env python3
"""
Generate the README hero GIF: an animated terminal showing MCP Salad's
runtime hot-swap — a running Claude session gains a tool with no restart.

Pure Pillow (no external GIF tooling). Outputs docs/hotswap-demo.gif.
Content mirrors the real flow: Claude has no stock tool -> `salad enable
twstock` in another terminal -> tools/list_changed -> Claude answers.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent.parent / "docs" / "hotswap-demo.gif"

# ── canvas / theme (GitHub dark) ─────────────────────────────────────────────
W, H = 860, 520
PAD = 28
LINE_H = 30
BG = (13, 17, 23)
PANEL = (22, 27, 34)
BORDER = (48, 54, 61)
FG = (201, 209, 217)
MUTED = (110, 118, 129)
GREEN = (63, 185, 80)
CYAN = (56, 189, 248)
YELLOW = (240, 200, 90)
PINK = (255, 105, 180)
BLUE = (88, 166, 255)

FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
font = ImageFont.truetype(FONT_PATH, 20)
font_b = ImageFont.truetype(FONT_PATH, 20, index=1)  # bold face in Menlo.ttc
title_font = ImageFont.truetype(FONT_PATH, 15)

# Each step is a list of (text, color) lines shown cumulatively, + hold frames.
# (bold flag optional as 3rd tuple element)
Y0 = PAD + 46

SCRIPT = [
    # (lines_to_show_now, hold_frames)
    ([("caretaker@mac ~ $ claude", MUTED)], 8),
    ([("you  ›  what's TSMC trading at right now?", BLUE)], 10),
    ([("✻    ›  I don't have a stock-market tool for that.", FG)], 14),
    ([("", FG),
      ("── meanwhile, in another terminal ─────────────", MUTED)], 8),
    ([("$ salad enable twstock", FG, True)], 12),
    ([("✓ enabled twstock  (161 tools)", GREEN, True)], 10),
    ([("→ notifications/tools/list_changed  ->  session", PINK)], 14),
    ([("", FG),
      ("── back in the SAME running session ───────────", MUTED)], 8),
    ([("you  ›  try again", BLUE)], 10),
    ([("✻    ›  TSMC (2330) is trading at NT$1,085  +2.3%", FG)], 12),
    ([("       ↑ no restart. no new chat. it just appeared.", MUTED)], 20),
]

CAPTION = "No restart.  No polling.  Just MCP notifications."


def draw_base():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # window panel
    d.rounded_rectangle([PAD - 12, PAD - 12, W - PAD + 12, H - PAD + 12],
                        radius=12, fill=PANEL, outline=BORDER, width=1)
    # traffic lights
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([PAD + i * 22, PAD - 2, PAD + 12 + i * 22, PAD + 10], fill=c)
    d.text((PAD + 80, PAD - 4), "mcp-salad  —  runtime hot-swap", font=title_font, fill=MUTED)
    return img, d


def render(lines, caption_on=False):
    img, d = draw_base()
    y = Y0
    for item in lines:
        text = item[0]
        color = item[1]
        bold = len(item) > 2 and item[2]
        d.text((PAD + 6, y), text, font=(font_b if bold else font), fill=color)
        y += LINE_H
    if caption_on:
        cw = d.textlength(CAPTION, font=font_b)
        d.text(((W - cw) / 2, H - PAD - 6), CAPTION, font=font_b, fill=YELLOW)
    return img


def main():
    frames, durations = [], []
    shown = []
    for lines_now, hold in SCRIPT:
        shown = shown + lines_now
        frame = render(shown)
        frames.append(frame)
        durations.append(max(hold, 6) * 55)  # ms per held step
    # final caption hold
    final = render(shown, caption_on=True)
    for _ in range(3):
        frames.append(final)
        durations.append(1200)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT, save_all=True, append_images=frames[1:],
        duration=durations, loop=0, optimize=True, disposal=2,
    )
    print(f"wrote {OUT}  ({len(frames)} frames, {OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
