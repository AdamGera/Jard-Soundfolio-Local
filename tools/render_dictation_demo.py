"""Draw Jaro's System Dictation walkthrough with fictional text.

Run with: uv run --no-project --with pillow==13.0.0 python tools/render_dictation_demo.py
"""

from functools import lru_cache
from math import cos, exp, sin

from PIL import Image

from render_readme_demo import (
    BG, BLUE, EDGE, FRAME_MS, INK, LAVENDER, MUTED, PANEL, PURPLE_BG, QUIET, ROOT,
    SIDEBAR, SIZE, WINDOW, Canvas, blend_points, cursor,
)

FOCUS = 0.96
HOLD = 1.76
RELEASE = 4.16
INSERT = 5.12
DURATION = 7.52
TRAFFIC_LIGHTS = ("#ee696c", "#e4b458", "#68bc80")
CHAT_ACCENT = "#7cdbaf"


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
    menu_x = 78 + canvas.width(app, 14, "bold")
    canvas.text(menu_x, 33, "File     Edit     View     Window", 13, QUIET)
    canvas.box((815, 28, 834, 38), None, 2, QUIET)
    canvas.box((818, 30, 830, 36), QUIET, 1)
    canvas.line(((837, 31), (837, 35)), QUIET)
    canvas.text(923, 33, "Fri  10:24", 13, QUIET, anchor="rm")


def window_frame(canvas, title, surface=WINDOW):
    canvas.box((101, 100, 859, 459), "#161620", 18)
    canvas.box((96, 93, 855, 450), surface, 16, EDGE)
    for x, color in zip((119, 138, 157), TRAFFIC_LIGHTS):
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
    window_frame(canvas, "weather-tool  /  main")
    canvas.box((97, 139, 245, 424), SIDEBAR, 4)
    canvas.line(((245, 138), (245, 424)), EDGE, 1)
    canvas.text(114, 161, "EXPLORER", 11, MUTED, "bold")
    canvas.icon("folder", 112, 178)
    canvas.text(134, 186, "weather-tool", 13, QUIET, "bold")
    canvas.icon("folder", 125, 204)
    canvas.text(147, 212, "src", 13, QUIET)
    canvas.box((105, 224, 238, 249), PURPLE_BG, 5)
    canvas.text(143, 237, "weather.py", 13, LAVENDER)
    for y, label in ((265, "client.py"), (291, "README.md")):
        canvas.text(143, y, label, 13, QUIET)

    canvas.box((246, 140, 667, 175), PANEL, 4)
    canvas.line(((246, 174), (666, 174)), EDGE, 1)
    canvas.line(((246, 141), (356, 141)), LAVENDER, 2)
    canvas.text(262, 157, "weather.py", 13, INK)
    canvas.text(372, 157, "client.py", 13, MUTED)
    canvas.text(261, 193, "src  /  weather.py", 12, QUIET)
    for number, y in enumerate((220, 249, 278, 307, 336, 365), 1):
        canvas.text(278, y, str(number), 12, MUTED, "mono", "rm")
    canvas.text(296, 220, "from .client import Client", 15, LAVENDER, "mono")
    canvas.text(296, 278, "def", 15, LAVENDER, "mono")
    canvas.text(332, 278, "report(city: str):", 15, INK, "mono")
    if time >= FOCUS:
        canvas.box((286, 293, 658, 321), PANEL, 4)
    canvas.text(332, 307, "#", 15, BLUE, "mono")
    insertion(canvas, 350, 307, phrase, time, 15, "mono", BLUE)
    canvas.text(332, 336, "client = Client()", 15, INK, "mono")
    canvas.text(332, 365, "return client.forecast(city)", 15, INK, "mono")

    canvas.line(((667, 138), (667, 424)), EDGE, 1)
    canvas.icon("chat", 681, 154, LAVENDER)
    canvas.text(704, 162, "Assistant", 13, INK, "bold")
    canvas.box((678, 188, 841, 269), PANEL, 7, EDGE)
    canvas.text(690, 209, "Context", 12, INK)
    canvas.icon("transcript", 689, 229)
    canvas.text(713, 237, "weather.py", 12, QUIET)
    canvas.line(((97, 424), (854, 424)), EDGE, 1)
    canvas.text(115, 437, "main", 11, QUIET, "mono")
    canvas.text(833, 437, "Python   UTF-8", 11, QUIET, "mono", "rm")


def send_icon(canvas, x, y, color):
    canvas.line(((x, y + 6), (x, y - 5)), color, 1.8)
    canvas.line(((x - 5, y), (x, y - 5), (x + 5, y)), color, 1.8)


def agent(canvas, phrase, time):
    window_frame(canvas, "Agent Workspace", "#1c202c")
    canvas.text(120, 161, "weather-tool", 14, INK, "bold")
    canvas.box((221, 150, 284, 172), "#2b3142", 6)
    canvas.text(252, 161, "main", 11, QUIET, "mono", "mm")
    canvas.line(((97, 181), (854, 181)), EDGE, 1)
    canvas.box((644, 182, 854, 449), "#232837", 11)
    canvas.line(((644, 182), (644, 448)), EDGE, 1)
    canvas.text(663, 205, "Project tasks", 13, INK, "bold")
    canvas.box((656, 226, 842, 259), "#333b50", 6)
    for y, name in ((243, "Forecast retries"), (281, "Check response cache"), (315, "Update example config")):
        canvas.dot(670, y, 3, LAVENDER if y == 243 else MUTED)
        canvas.text(682, y, name, 13, QUIET)

    canvas.text(122, 212, "Forecast retries", 22, INK, "bold")
    canvas.box((188, 239, 619, 274), "#303952", 8)
    canvas.text(201, 257, "Check what happens when a request times out.", 14)
    canvas.dot(133, 301, 8, "#645c89")
    canvas.text(150, 301, "The client currently attempts each request once.", 14, QUIET)
    canvas.box((120, 336, 619, 424), "#272d3e", 11, LAVENDER if time >= FOCUS else EDGE)
    if time < FOCUS:
        canvas.text(135, 358, "Add a follow-up…", 15, MUTED)
    insertion(canvas, 135, 358, phrase, time, 16, "regular", INK)
    canvas.icon("plus", 135, 392)
    canvas.text(160, 400, "Local workspace", 12, QUIET)
    canvas.dot(593, 400, 15, LAVENDER if time >= INSERT else "#404860")
    send_icon(canvas, 593, 400, BG if time >= INSERT else QUIET)


def contact(canvas, x, y, initials, fill):
    canvas.dot(x, y, 14, fill)
    canvas.text(x, y, initials, 10, INK, "bold", "mm")


def messages(canvas, phrase, time):
    window_frame(canvas, "Team Chat", "#1d2928")
    canvas.box((97, 139, 333, 449), "#202d2c", 11)
    canvas.line(((333, 138), (333, 448)), "#3b4c49", 1)
    canvas.text(116, 161, "Chats", 16, INK, "bold")
    canvas.icon("plus", 298, 153, CHAT_ACCENT)
    canvas.box((109, 183, 320, 229), "#324940", 8)
    for y, name, initials, color, excerpt in (
        (206, "Morgan", "M", "#4d6a70", "Can you join the review?"),
        (258, "Design team", "DT", "#5e5275", "Agenda is ready."),
        (310, "Sam", "S", "#6c544d", "Thanks for the update."),
    ):
        contact(canvas, 132, y, initials, color)
        canvas.text(156, y - 8, name, 14, INK, "bold")
        canvas.text(156, y + 9, excerpt, 12, QUIET)

    canvas.box((334, 138, 853, 194), "#293a37")
    contact(canvas, 358, 165, "M", "#4d6a70")
    canvas.text(384, 165, "Morgan", 15, INK, "bold")
    canvas.icon("chat", 809, 157, CHAT_ACCENT)
    canvas.text(593, 218, "Today", 12, QUIET, anchor="mm")
    canvas.box((550, 240, 829, 280), "#365447", 11)
    canvas.text(563, 259, "I saw the design review moved to Friday.", 14)
    canvas.box((355, 293, 582, 333), "#334341", 11)
    canvas.text(368, 312, "Can you join the review?", 14)
    canvas.text(568, 318, "10:23", 10, QUIET, anchor="rm")
    canvas.icon("plus", 348, 391, CHAT_ACCENT)
    canvas.box((374, 375, 805, 421), "#263733", 12, CHAT_ACCENT if time >= FOCUS else "#405650")
    if time < FOCUS:
        canvas.text(389, 398, "Write a reply…", 15, MUTED)
    insertion(canvas, 389, 398, phrase, time, 16, "regular", INK)
    canvas.dot(828, 398, 15, CHAT_ACCENT if time >= INSERT else "#50645c")
    send_icon(canvas, 828, 398, BG if time >= INSERT else QUIET)


SCENES = (
    ("Code Editor", editor, (350, 307), "Retry once if offline."),
    ("Agent Workspace", agent, (135, 358), "Add a test for a request timeout."),
    ("Team Chat", messages, (389, 398), "Yes, Friday works for me."),
)


def keyboard(canvas, held):
    # The highlighted key is physically to the right of the space bar and Command.
    canvas.box((95, 478, 343, 542), "#101116", 10, "#44454f")
    for x0, x1 in ((101, 160), (165, 217), (279, 337)):
        canvas.box((x0, 486, x1, 534), "#30313a", 6, "#4d4e57")
    canvas.text(191, 519, "command", 9, QUIET, anchor="mm")
    canvas.box((197, 497, 205, 505), None, 0, QUIET)
    for x, y in ((195, 495), (207, 495), (195, 507), (207, 507)):
        canvas.dot(x, y, 2, None, QUIET)
    canvas.line(((310, 515), (305, 519), (310, 523)), QUIET, 1.5)
    offset = 2 if held else 0
    face = "#75749a" if held else "#42434d"
    canvas.box((222, 489, 274, 536), "#181820", 6)
    canvas.box((222, 485 + offset, 274, 533 + offset), face, 6, LAVENDER if held else "#62646f")
    # Draw the legends directly so the keycaps render without platform glyph fallback.
    y = 494 + offset
    canvas.line(((253, y), (258, y), (264, y + 10), (269, y + 10)), INK, 1.7)
    canvas.line(((263, y), (269, y)), INK, 1.7)
    canvas.text(248, 519 + offset, "option", 11, INK, anchor="mm")


def draw_frame(scene, time):
    canvas = Canvas()
    app, draw_app, field, phrase = scene
    held = HOLD <= time < RELEASE
    descriptions = (
        (f"Click in the {app.lower()}", "Place the cursor where you want to dictate."),
        ("Hold Right Option and speak", f"“{phrase}”"),
        ("Release Right Option", "Your Mac turns speech into text."),
        ("Text inserted at the cursor", "Same shortcut across apps."),
    )
    step = sum(time >= boundary for boundary in (HOLD, RELEASE, INSERT))
    heading, detail = descriptions[step]
    desktop(canvas, app)
    draw_app(canvas, phrase, time)

    canvas.box((86, 470, 864, 550), "#1b1b25", 14, EDGE)
    keyboard(canvas, held)
    canvas.text(366, 495, heading, 19, INK, "bold")
    canvas.text(366, 521, detail, 15, QUIET)
    for index, item in enumerate(SCENES):
        canvas.dot(802 + index * 15, 510, 3, LAVENDER if item == scene else MUTED)

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
    palette_sheet = Image.new("RGB", (SIZE[0], len(samples) * SIZE[1] + 32))
    for index, (scene, time) in enumerate(samples):
        palette_sheet.paste(draw_frame(scene, time), (0, index * SIZE[1]))
    for index, color in enumerate((*TRAFFIC_LIGHTS, CHAT_ACCENT)):
        palette_sheet.paste(color, (index * 160, len(samples) * SIZE[1], (index + 1) * 160, palette_sheet.height))
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
