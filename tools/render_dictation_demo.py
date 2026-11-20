"""Draw Jaro's System Dictation walkthrough with fictional text.

Run with: uv run --no-project --with pillow==13.0.0 python tools/render_dictation_demo.py
"""

from math import cos, exp, sin

from PIL import Image

from render_readme_demo import (
    BG, EDGE, FRAME_MS, INK, LAVENDER, MUTED, PANEL, PURPLE_BG, QUIET, ROOT, SIZE,
    SOFT, WINDOW, Canvas, blend_points, cursor,
)

FOCUS = 1.20
HOLD = 2.32
RELEASE = 5.12
INSERT = 6.24
DURATION = 9.12
FIELD_POINT = (197, 288)
MESSAGE = "Could we move the design review to Thursday?"
STEPS = (
    ("Place the cursor", "Click where you want the text to go."),
    ("Hold Right Option and speak", "“Could we move the design review to Thursday?”"),
    ("Release Right Option", "Jaro turns your voice into text on your Mac."),
    ("Text inserted", "Keep typing in the same app."),
)


def indicator(canvas, time):
    # Illustrate the native indicator's warm recording and cool processing dot patterns.
    age = time - HOLD
    after_insert = max(0, time - INSERT)
    cool = min(1, max(0, (time - RELEASE) / 0.30))
    visibility = min(1, age / 0.32) * max(0, 1 - after_insert / 0.40)
    width = (112 - 36 * cool) * (0.38 + 0.62 * visibility)
    center_x, center_y = 480, 48
    canvas.box((center_x - width / 2, 35, center_x + width / 2, 61), BG, 13, EDGE)
    for column in range(-21, 22):
        x = column * 2.6
        if abs(x) > width / 2 - 5:
            continue
        phase = column * (0.28 + 0.15 * cool) + age * (7 + 3 * cool)
        amplitude = (2.2 + 5 * abs(sin(age * 6.2) * cos(age * 2.6))) * (1 - 0.60 * cool)
        strands = (amplitude * sin(phase), amplitude * cos(phase * 1.19))
        for row in range(-4, 5):
            y = row * 2.6
            if max(0, abs(x) - width / 2 + 13) ** 2 + y ** 2 > 11 ** 2:
                continue
            distance = min(abs(y - strand) for strand in strands)
            light = visibility * exp(-0.55 * distance) * (0.58 + 0.42 * abs(cos(phase + row)))
            pearl = (248 - 31 * cool, 244 - 10 * cool, 231 + 24 * cool)
            color = tuple(round(base + (tip - base) * light) for base, tip in zip((39, 39, 45), pearl))
            canvas.dot(center_x + x, center_y + y, 0.55 + 0.47 * light, color)


def draw_frame(time):
    canvas = Canvas()
    focused = time >= FOCUS
    held = HOLD <= time < RELEASE
    inserted = time >= INSERT
    step = sum(time >= boundary for boundary in (HOLD, RELEASE, INSERT))
    heading, detail = STEPS[step]

    canvas.box((13, 15, 947, 562), WINDOW, 18, EDGE)
    canvas.dot(39, 45, 9, None, MUTED)
    canvas.dot(42, 42, 1.2, INK)
    canvas.text(56, 45, "Jaro", 15, INK, "bold")
    canvas.text(922, 45, "System Dictation", 14, QUIET, anchor="rm")
    canvas.line(((14, 74), (946, 74)), EDGE, 1)

    # An illustrated destination app. The caret stays in the composer throughout the take.
    canvas.box((145, 109, 815, 427), PANEL, 18, EDGE)
    for x, color in ((169, "#ee696c"), (187, "#e4b458"), (205, "#68bc80")):
        canvas.dot(x, 132, 4, color)
    canvas.text(480, 133, "New message", 14, QUIET, "bold", anchor="mm")
    canvas.line(((146, 157), (814, 157)), EDGE, 1)
    canvas.text(180, 184, "To", 14, MUTED)
    canvas.text(221, 184, "Morgan", 15)
    canvas.box((173, 210, 787, 403), WINDOW, 11, LAVENDER if focused else EDGE)
    canvas.text(197, 241, "Hi Morgan,", 20)
    caret_x = 197
    if inserted:
        caret_x += canvas.width(MESSAGE, 20)
        if time < INSERT + 0.48:
            canvas.box((193, 272, caret_x + 4, 303), PURPLE_BG, 4)
        canvas.text(197, 288, MESSAGE, 20)
    if focused and (int((time - FOCUS) / 0.48) % 2 == 0 or INSERT <= time < INSERT + 0.48):
        canvas.line(((caret_x, 274), (caret_x, 301)), LAVENDER, 1.5)

    canvas.box((145, 452, 815, 532), PANEL, 14, EDGE)
    canvas.box((163, 468, 304, 519), BG, 9)
    offset = 2 if held else 0
    canvas.box((163, 465 + offset, 304, 514 + offset), LAVENDER if held else SOFT, 9, LAVENDER if held else EDGE)
    canvas.text(233, 490 + offset, "Right Option", 16, BG if held else INK, "bold", anchor="mm")
    canvas.text(329, 477, heading, 19, INK, "bold")
    canvas.text(329, 506, detail, 15, QUIET)

    if HOLD <= time < INSERT + 0.40:
        indicator(canvas, time)

    point = blend_points((363, 365), FIELD_POINT, (time - 0.24) / 0.96)
    if focused:
        point = blend_points(FIELD_POINT, (758, 366), (time - 1.52) / 0.62)
    cursor(canvas, point, time - FOCUS)
    return canvas.finish()


def render(output):
    samples = (0, FOCUS, HOLD + 0.80, RELEASE + 0.48, INSERT + 0.24)
    palette_sheet = Image.new("RGB", (SIZE[0], len(samples) * SIZE[1]))
    for index, time in enumerate(samples):
        palette_sheet.paste(draw_frame(time), (0, index * SIZE[1]))
    palette = palette_sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames = [
        draw_frame(frame * FRAME_MS / 1000).quantize(palette=palette, dither=Image.Dither.NONE)
        for frame in range(round(DURATION * 1000 / FRAME_MS))
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:], loop=0,
                   duration=FRAME_MS, optimize=True)


if __name__ == "__main__":
    render(ROOT / "docs/media/dictation-demo.gif")
