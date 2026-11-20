"""Draw Jaro's System Dictation walkthrough with fictional text.

Run with: uv run --no-project --with pillow==13.0.0 python tools/render_dictation_demo.py
"""

from functools import lru_cache
from math import cos, exp, sin

from PIL import Image

from render_readme_demo import (
    BG, BLUE, EDGE, FRAME_MS, INK, LAVENDER, MUTED, PANEL, PURPLE_BG, QUIET, ROOT,
    SIDEBAR, SIZE, SOFT, WINDOW, Canvas, blend_points, cursor,
)

FOCUS = 0.96
HOLD = 1.76
RELEASE = 4.16
INSERT = 5.12
DURATION = 7.52


def indicator(canvas, time):
    # Illustrate the native indicator's warm recording and cool processing dot patterns.
    age = time - HOLD
    after_insert = max(0, time - INSERT)
    cool = min(1, max(0, (time - RELEASE) / 0.30))
    visibility = min(1, age / 0.32) * max(0, 1 - after_insert / 0.40)
    width = (112 - 36 * cool) * (0.38 + 0.62 * visibility)
    center_x, center_y = 480, 66
    canvas.box((center_x - width / 2, 53, center_x + width / 2, 79), BG, 13, EDGE)
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


@lru_cache(maxsize=1)
def desktop_background():
    canvas = Canvas()
    canvas.box((13, 15, 947, 562), "#252536", 18, EDGE)
    canvas.box((15, 47, 945, 560), "#272738", 16)
    canvas.poly(((15, 335), (322, 49), (934, 49), (556, 560), (15, 560)), "#2c293d")
    canvas.poly(((270, 560), (648, 49), (936, 49), (945, 66), (945, 545), (929, 560)), "#2b3044")
    canvas.poly(((616, 560), (936, 175), (945, 167), (945, 545), (929, 560)), "#31384d")
    canvas.box((14, 16, 946, 49), "#1a1a26", 13)
    canvas.box((14, 36, 946, 49), "#1a1a26")
    canvas.line(((15, 49), (945, 49)), "#39384a", 1)
    return canvas.image


def desktop(canvas, app):
    canvas.image.paste(desktop_background())
    canvas.dot(36, 33, 5, LAVENDER)
    canvas.text(52, 33, app, 14, INK, "bold")
    canvas.text(167, 33, "File     Edit     View     Window", 13, QUIET)
    canvas.box((815, 28, 834, 38), None, 2, QUIET)
    canvas.box((818, 30, 830, 36), QUIET, 1)
    canvas.line(((837, 31), (837, 35)), QUIET)
    canvas.text(923, 33, "Fri  10:24", 13, QUIET, anchor="rm")


def window_frame(canvas, title):
    canvas.box((101, 100, 859, 459), "#161620", 18)
    canvas.box((96, 93, 855, 450), WINDOW, 16, EDGE)
    for x, color in ((119, "#ee696c"), (138, "#e4b458"), (157, "#68bc80")):
        canvas.dot(x, 115, 4.5, color)
    canvas.text(475, 116, title, 14, QUIET, "bold", anchor="mm")
    canvas.line(((97, 137), (854, 137)), EDGE, 1)


def insertion(canvas, x, y, phrase, time, size, weight, color):
    if time >= INSERT:
        end = x + canvas.width(phrase, size, weight)
        if time < INSERT + 0.48:
            canvas.box((x - 3, y - 13, end + 3, y + 13), PURPLE_BG, 4)
        canvas.text(x, y, phrase, size, color, weight)
        x = end
    if time >= FOCUS and (int((time - FOCUS) / 0.48) % 2 == 0 or INSERT <= time < INSERT + 0.48):
        canvas.line(((x, y - 12), (x, y + 12)), LAVENDER, 1.5)


def editor(canvas, phrase, time):
    window_frame(canvas, "weather.py  ·  Code Editor")
    canvas.box((97, 139, 269, 450), SIDEBAR, 12)
    canvas.box((250, 139, 269, 450), SIDEBAR)
    canvas.line(((269, 138), (269, 449)), EDGE, 1)
    canvas.text(115, 166, "PROJECT", 12, MUTED, "bold")
    canvas.icon("folder", 114, 188)
    canvas.text(137, 196, "weather-tool", 14, QUIET)
    canvas.box((108, 213, 257, 246), SOFT, 6)
    canvas.icon("transcript", 126, 222, LAVENDER)
    canvas.text(149, 230, "weather.py", 14)
    canvas.text(288, 162, "weather.py", 13, QUIET)
    canvas.line(((270, 181), (854, 181)), EDGE, 1)
    for number, y in enumerate((215, 249, 283), 1):
        canvas.text(306, y, str(number), 14, MUTED, "mono", "rm")
    canvas.text(324, 215, "def", 17, LAVENDER, "mono")
    canvas.text(365, 215, "fetch_report(client):", 17, INK, "mono")
    if time >= FOCUS:
        canvas.box((315, 231, 839, 267), PANEL, 4)
    canvas.text(365, 249, "#", 17, BLUE, "mono")
    canvas.text(365, 283, 'return client.get("/report")', 17, INK, "mono")
    insertion(canvas, 386, 249, phrase, time, 17, "mono", BLUE)


def browser(canvas, phrase, time):
    window_frame(canvas, "Project Handbook  ·  Browser")
    canvas.box((115, 149, 835, 182), SOFT, 9, EDGE)
    canvas.text(475, 166, "handbook.example", 13, QUIET, anchor="mm")
    canvas.text(475, 237, "Project Handbook", 28, INK, "bold", "mm")
    canvas.box((234, 273, 716, 328), SIDEBAR, 13, LAVENDER if time >= FOCUS else EDGE)
    canvas.dot(260, 299, 6, None, MUTED)
    canvas.line(((264, 303), (270, 309)), MUTED)
    if time < FOCUS:
        canvas.text(287, 300, "Search the handbook", 17, MUTED)
    insertion(canvas, 287, 300, phrase, time, 19, "regular", INK)
    canvas.text(475, 365, "Guides, references, and team conventions", 15, QUIET, anchor="mm")


SCENES = (
    ("Code Editor", editor, (386, 249), "Retry once if the connection drops."),
    ("Browser", browser, (287, 300), "Keyboard shortcuts"),
)


def draw_frame(scene, time):
    canvas = Canvas()
    app, draw_app, field, phrase = scene
    held = HOLD <= time < RELEASE
    descriptions = (
        (f"Click in the {app.lower()}", "Place the cursor where you want to dictate."),
        ("Hold Right Option and speak", f"“{phrase}”"),
        ("Release Right Option", "Your Mac turns speech into text."),
        (f"Text inserted in the {app.lower()}", "Use the same shortcut in another app."),
    )
    step = sum(time >= boundary for boundary in (HOLD, RELEASE, INSERT))
    heading, detail = descriptions[step]
    desktop(canvas, app)
    draw_app(canvas, phrase, time)

    canvas.box((152, 473, 807, 545), "#1b1b25", 14, EDGE)
    canvas.box((168, 486, 309, 535), BG, 9)
    offset = 2 if held else 0
    canvas.box((168, 482 + offset, 309, 531 + offset), LAVENDER if held else SOFT, 9, LAVENDER if held else EDGE)
    canvas.text(238, 507 + offset, "Right Option", 16, BG if held else INK, "bold", anchor="mm")
    canvas.text(332, 495, heading, 19, INK, "bold")
    canvas.text(332, 521, detail, 15, QUIET)

    if HOLD <= time < INSERT + 0.40:
        indicator(canvas, time)

    point = blend_points((599, 366), field, (time - 0.16) / (FOCUS - 0.16))
    if time >= FOCUS:
        point = blend_points(field, (750, 393), (time - FOCUS - 0.32) / 0.62)
    cursor(canvas, point, time - FOCUS)
    return canvas.finish()


def render(output):
    times = (0, HOLD + 0.80, RELEASE + 0.48, INSERT + 0.24)
    samples = [(scene, time) for scene in SCENES for time in times]
    palette_sheet = Image.new("RGB", (SIZE[0], len(samples) * SIZE[1]))
    for index, (scene, time) in enumerate(samples):
        palette_sheet.paste(draw_frame(scene, time), (0, index * SIZE[1]))
    palette = palette_sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames = [
        draw_frame(scene, frame * FRAME_MS / 1000).quantize(palette=palette, dither=Image.Dither.NONE)
        for scene in SCENES
        for frame in range(round(DURATION * 1000 / FRAME_MS))
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:], loop=0,
                   duration=FRAME_MS, optimize=True)


if __name__ == "__main__":
    render(ROOT / "docs/media/dictation-demo.gif")
