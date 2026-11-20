"""Draw Jaro's four System Dictation examples with original artwork and fictional text.

Run with: uv run --no-project --with pillow==13.0.0 python tools/render_dictation_demo.py
"""

from dataclasses import dataclass
from functools import lru_cache
from math import cos, exp, pi, sin
from random import Random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from render_readme_demo import FRAME_MS, ROOT, SCALE, SIZE, Canvas, blend_points, cursor, px

INK = "#242333"
WHITE = "#fffdfa"
PAPER = "#f8f5f0"
MUTED = "#6c6877"
EDGE = "#e4dfdf"
HUD = "#15151d"
FRAME = (12, 12, 948, 564)
WINDOW = (385, 96, 886, 456)
FOCUS, HOLD, RELEASE, INSERT = 0.80, 1.28, 3.68, 4.48
DURATION, FADE = 6.96, 0.40


@dataclass(frozen=True)
class Scene:
    app: str
    headline: tuple[str, str]
    colors: tuple[str, str, str, str]
    field: tuple[int, int]
    body: tuple[str, ...]


SCENES = (
    Scene("Email", ("Reply to", "an email."), ("#f2d8aa", "#df8d66", "#b65e53", "#f7e4c4"), (415, 237),
          ("Hi Mina,", "", "Thursday works for the review. I’ll send", "the updated slides by Wednesday.", "", "Thanks, Jules")),
    Scene("Slack", ("Share a", "team update."), ("#d7d0eb", "#a194c6", "#7463a5", "#ece4f5"), (430, 325),
          ("The preview is live. Please try the", "signup flow before standup.")),
    Scene("Claude", ("Talk through", "a prompt."), ("#edcec8", "#d08c87", "#ab6674", "#f7ded4"), (427, 298),
          ("Draft a workshop agenda for new designers.", "Include a hands-on exercise and a short", "discussion at the end.")),
    Scene("Google Docs", ("Get a draft", "started."), ("#c7dfe3", "#80aeb7", "#577d96", "#e3ecea"), (441, 291),
          ("We’ll test the new signup flow with five", "people this week. Watch where they pause", "and note what they expect to happen next.")),
)


@lru_cache(maxsize=4)
def artwork(index):
    """Make the four paper compositions once. All coordinates and grain are local."""
    base, middle, dark, light = SCENES[index].colors
    canvas = Canvas()
    canvas.image.paste(base, (0, 0, *canvas.image.size))
    if index == 0:
        # Offset circles cut from saffron and terracotta paper.
        canvas.dot(878, 537, 381, middle)
        canvas.dot(878, 537, 286, base)
        canvas.dot(878, 537, 190, dark)
        canvas.dot(878, 537, 118, light)
        canvas.dot(578, 44, 130, light)
    elif index == 1:
        # Overlapping diagonal strips on lavender stock.
        canvas.poly(((333, 576), (818, 0), (958, 0), (476, 576)), middle)
        canvas.poly(((525, 576), (960, 65), (960, 239), (669, 576)), dark)
        canvas.poly(((691, 576), (960, 267), (960, 345), (766, 576)), light)
        canvas.poly(((366, 0), (443, 0), (209, 265)), light)
    elif index == 2:
        # A folded fan in dusty rose.
        for x in range(350, 1470, 96):
            canvas.poly(((512, 576), (x, 0), (x + 37, 0)), middle)
        canvas.dot(917, 471, 194, dark)
        canvas.dot(917, 471, 147, light)
        canvas.dot(917, 471, 95, middle)
    else:
        # Blue-green contours, like a set of sweeping inked lines.
        canvas.dot(960, 576, 455, light)
        for radius in reversed(range(110, 449, 32)):
            canvas.dot(960, 576, radius, middle)
            canvas.dot(960, 576, radius - 5, light)
        canvas.poly(((376, 576), (960, 66), (960, 171), (505, 576)), middle)
        canvas.poly(((826, 576), (960, 447), (960, 576)), dark)

    random = Random(180 + index)
    grain = Image.frombytes("L", SIZE, bytes(random.randrange(118, 139) for _ in range(SIZE[0] * SIZE[1])))
    grain = grain.resize(canvas.image.size, Image.Resampling.BILINEAR).convert("RGB")
    paper = ImageChops.soft_light(canvas.image, grain)
    mask = Image.new("L", canvas.image.size)
    ImageDraw.Draw(mask).rounded_rectangle(px(FRAME), radius=28 * SCALE, fill=255)
    backdrop = Image.new("RGB", canvas.image.size, "#1a1921")
    backdrop.paste(paper, (0, 0), mask)
    return backdrop


@lru_cache(maxsize=1)
def shadow():
    mask = Image.new("L", (SIZE[0] * SCALE, SIZE[1] * SCALE))
    left, top, right, bottom = WINDOW
    bounds = (left + 2, top + 12, right + 2, bottom + 16)
    ImageDraw.Draw(mask).rounded_rectangle(px(bounds), radius=16 * SCALE, fill=90)
    return mask.filter(ImageFilter.GaussianBlur(18 * SCALE))


def window(canvas, title, header=PAPER):
    canvas.image.paste("#30233d", (0, 0, *canvas.image.size), shadow())
    canvas.box(WINDOW, WHITE, 17, "#e5dedf")
    canvas.box((386, 97, 885, 143), header, 16)
    canvas.box((386, 123, 885, 143), header)
    canvas.line(((386, 143), (885, 143)), EDGE, 1)
    for x, color in ((408, "#de8d8a"), (423, "#d8b376"), (438, "#8cb99a")):
        canvas.dot(x, 120, 4.25, color)
    canvas.text(635, 121, title, 14, INK, "bold", "mm")


def option_key(canvas, x, y, held):
    offset = 2 if held else 0
    canvas.box((x, y + 5, x + 62, y + 57), "#55516a", 9)
    canvas.box((x, y + offset, x + 62, y + 53 + offset), "#48416b" if held else "#2c2939", 9,
               "#ded9ff" if held else "#676077")
    symbol_y = y + 10 + offset
    canvas.line(((x + 33, symbol_y), (x + 38, symbol_y), (x + 44, symbol_y + 10),
                 (x + 50, symbol_y + 10)), WHITE, 1.7)
    canvas.line(((x + 44, symbol_y), (x + 50, symbol_y)), WHITE, 1.7)
    canvas.text(x + 31, y + 38 + offset, "option", 12, WHITE, anchor="mm")


def legend(canvas, index, scene, time):
    canvas.dot(65, 65, 11, None, INK)
    canvas.dot(69, 61, 1.5, INK)
    canvas.text(84, 65, "Jaro", 18, INK, "bold")
    canvas.text(137, 65, "System Dictation", 13, INK)

    width = canvas.width(scene.app, 15, "bold") + 28
    canvas.box((53, 151, 53 + width, 181), scene.colors[3], 15)
    canvas.text(67, 166, scene.app, 15, INK, "bold")
    canvas.text(51, 225, scene.headline[0], 45, INK, "serif")
    canvas.text(51, 279, scene.headline[1], 45, INK, "serif")

    held = HOLD <= time < RELEASE
    if time < FOCUS:
        action, detail = "Place the cursor", "Click where you want to type."
    elif time < HOLD:
        action, detail = "Hold Right Option", "Keep the key down."
    elif held:
        action, detail = "Speak naturally", "Hold Right Option while speaking."
    elif time < INSERT:
        action, detail = "Release to insert", "Jaro transcribes on your Mac."
    else:
        action, detail = "Text inserted", "Your draft stays editable."
    option_key(canvas, 53, 374, held)
    canvas.text(131, 390, action, 17, INK, "bold")
    canvas.text(131, 414, detail, 14, INK)
    for position in range(len(SCENES)):
        x = 53 + 38 * position
        canvas.box((x, 500, x + 30, 504), INK if position == index else scene.colors[1], 2)


def draft(canvas, scene, time, placeholder=None, size=18, line_height=24):
    x, y = scene.field
    if time >= INSERT:
        for line in scene.body:
            canvas.text(x, y, line, size, INK)
            y += line_height
        y -= line_height
        x += canvas.width(scene.body[-1], size)
    elif placeholder and time < FOCUS:
        canvas.text(x, y, placeholder, size, MUTED)
    if time >= FOCUS and (time >= INSERT or int((time - FOCUS) / 0.48) % 2 == 0):
        canvas.line(((x, y - 11), (x, y + 11)), "#605394", 1.6)


def button(canvas, x, y, label, fill="#4a416e"):
    width = canvas.width(label, 13, "bold") + 26
    canvas.box((x, y - 14, x + width, y + 14), fill, 7)
    canvas.text(x + width / 2, y, label, 13, WHITE, "bold", "mm")


def email(canvas, scene, time):
    window(canvas, "Reply")
    for y, label, value in ((169, "To", "Mina Patel"), (203, "Subject", "Re: Design review")):
        canvas.text(412, y, label, 13, MUTED)
        canvas.text(475, y, value, 15, INK, "bold" if y == 203 else "regular")
        canvas.line(((411, y + 17), (860, y + 17)), EDGE, 1)
    draft(canvas, scene, time, "Write a reply…", line_height=23)
    canvas.line(((411, 400), (860, 400)), EDGE, 1)
    button(canvas, 412, 424, "Send")
    canvas.icon("transcript", 490, 416, MUTED)


def slack(canvas, scene, time):
    window(canvas, "Studio workspace", "#ede8f5")
    canvas.text(411, 172, "#  launch", 19, INK, "bold")
    canvas.line(((386, 194), (885, 194)), EDGE, 1)
    canvas.box((411, 213, 446, 248), "#ded4ef", 8)
    canvas.text(429, 231, "EV", 12, "#4a3d6d", "bold", "mm")
    canvas.text(458, 222, "Evan", 15, INK, "bold")
    canvas.text(508, 222, "9:41 AM", 12, MUTED)
    canvas.text(458, 246, "Anything to test before standup?", 17, INK)
    canvas.box((412, 298, 859, 425), PAPER, 11, "#8874ac" if time >= FOCUS else EDGE)
    draft(canvas, scene, time, "Message #launch")
    canvas.icon("plus", 430, 392, MUTED)
    canvas.text(458, 400, "Aa", 14, MUTED)
    button(canvas, 789, 400, "Send", "#685185")


def claude(canvas, scene, time):
    window(canvas, "Planning session", "#f5ece5")
    canvas.text(414, 176, "Workshop planning", 24, INK, "serif")
    canvas.text(414, 212, "What should the first session cover?", 17, MUTED)
    canvas.box((411, 266, 860, 424), "#faf4ee", 13, "#a77377" if time >= FOCUS else EDGE)
    draft(canvas, scene, time, "Give Claude a task…")
    canvas.icon("plus", 428, 391, MUTED)
    button(canvas, 779, 399, "Submit", "#8c5355")


def document(canvas, scene, time):
    window(canvas, "Research notes", "#edf3f4")
    canvas.box((412, 156, 859, 195), "#eaf0f1", 8)
    canvas.text(431, 176, "Body text", 13, INK)
    canvas.line(((509, 164), (509, 187)), "#cdd9da", 1)
    canvas.text(526, 176, "18", 13, INK)
    canvas.text(573, 176, "B", 14, INK, "bold")
    canvas.text(600, 176, "I", 14, INK, "serif")
    canvas.box((420, 212, 852, 434), WHITE, 5, EDGE)
    canvas.text(441, 249, "Signup study", 24, INK, "serif")
    draft(canvas, scene, time, "Start writing…", size=17)


DRAW_APP = (email, slack, claude, document)


def indicator(canvas, time):
    # The warm recording trace, blue processing trace, and narrowing capsule follow Jaro’s HUD.
    age = time - HOLD
    finish = max(0, time - INSERT)
    visibility = min(1, age / 0.24) * max(0, 1 - finish / 0.36)
    cool = min(1, max(0, (time - RELEASE) / 0.28))
    width = (123 - 39 * cool) * (0.48 + 0.52 * visibility)
    center_x, center_y = 636, 504
    canvas.box((center_x - width / 2, 487, center_x + width / 2, 521), HUD, 17, "#4d4355")
    for column in range(-20, 21):
        x = column * 2.8
        if abs(x) > width / 2 - 7:
            continue
        phase = column * (0.30 + 0.15 * cool) + age * (7 + 3 * cool)
        strength = (2.4 + 5.6 * abs(sin(age * 6.2) * cos(age * 2.6))) * (1 - 0.63 * cool)
        strands = (strength * sin(phase), strength * cos(phase * 1.19))
        for row in range(-4, 5):
            y = row * 2.8
            distance = min(abs(y - strand) for strand in strands)
            light = visibility * exp(-0.54 * distance) * (0.58 + 0.42 * abs(cos(phase + row)))
            pearl = (248 - 31 * cool, 244 - 10 * cool, 231 + 24 * cool)
            color = tuple(round(base + (tip - base) * light) for base, tip in zip((37, 35, 47), pearl))
            canvas.dot(center_x + x, center_y + y, 0.55 + 0.52 * light, color)


def draw_frame(index, time):
    scene = SCENES[index]
    canvas = Canvas()
    canvas.image.paste(artwork(index))
    legend(canvas, index, scene, time)
    DRAW_APP[index](canvas, scene, time)
    if HOLD <= time < INSERT + 0.36:
        indicator(canvas, time)
    point = blend_points((769, 410), scene.field, (time - 0.08) / (FOCUS - 0.08))
    if time >= FOCUS:
        point = blend_points(scene.field, (881, 451), (time - FOCUS - 0.30) / 0.58)
    cursor(canvas, point, time - FOCUS)
    return canvas.finish()


def render(output):
    next_start = [draw_frame(index, 0) for index in range(len(SCENES))]
    samples = [draw_frame(index, t) for index in range(len(SCENES)) for t in (HOLD + 0.8, INSERT + 0.8)]
    sheet = Image.new("RGB", (SIZE[0], len(samples) * SIZE[1]))
    for index, frame in enumerate(samples):
        sheet.paste(frame, (0, index * SIZE[1]))
    palette = sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames = []
    for index in range(len(SCENES)):
        for frame in range(round(DURATION * 1000 / FRAME_MS)):
            time = frame * FRAME_MS / 1000
            image = draw_frame(index, time)
            if time >= DURATION - FADE:
                progress = (time - DURATION + FADE) / FADE
                eased = (1 - cos(pi * progress)) / 2
                image = Image.blend(image, next_start[(index + 1) % len(SCENES)], eased)
            frames.append(image.quantize(palette=palette, dither=Image.Dither.NONE))
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:], loop=0,
                   duration=FRAME_MS, optimize=True)


if __name__ == "__main__":
    render(ROOT / "docs/media/dictation-demo.gif")
