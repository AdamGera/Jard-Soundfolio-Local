"""Draw the Jaro meeting animation from fictional data with Pillow.

Run with: uv run --no-project --with pillow==13.0.0 python tools/render_readme_demo.py
"""

from functools import lru_cache
from math import cos, pi, sin
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SIZE = (960, 576)
SCALE = 2
FRAME_MS = 80

BG = "#101014"
WINDOW = "#202027"
SIDEBAR = "#17171c"
PANEL = "#25252d"
SOFT = "#2b2b35"
EDGE = "#393944"
INK = "#f1f0f5"
QUIET = "#aeadbc"
MUTED = "#898896"
LAVENDER = "#c9c3f6"
PURPLE_BG = "#403b5d"
BLUE = "#82d5c9"

LEFT, RIGHT = 246, 892
DURATION = 204
TABS = (
    ("Overview", "overview", 105),
    ("My Notes", "notes", 104),
    ("Transcript", "transcript", 112),
    ("Timeline", "timeline", 98),
    ("Speaker analytics", "analytics", 159),
)
DESTINATIONS = {
    "Home": (109, 105),
    "Overview": (113, 175),
    "Timeline": (614, 151),
    "Transcript": (507, 151),
    "Speaker analytics": (739, 151),
}
CHAPTERS = (
    (0, "Pilot rollout", "Start with a small group and send the invites Tuesday."),
    (72, "What to test", "Check the recording flow and whether playback is easy to find."),
    (158, "Friday review", "Bring the pilot feedback and decide what to change."),
)
TURNS = (
    (0, "Speaker A", "Let’s start with a small pilot group."),
    (29, "Speaker B", "I’ll send the invites on Tuesday and collect feedback."),
    (72, "Speaker A", "Let’s focus on recording and playback."),
    (158, "Speaker B", "I’ll bring the pilot feedback to Friday’s review."),
)
SPEAKERS = (
    ("Speaker A", LAVENDER, ((0, 25), (41, 54), (72, 108), (124, 155))),
    ("Speaker B", BLUE, ((28, 41), (55, 70), (108, 125), (158, 190))),
)
WAVE_HEIGHTS = [
    2 + 15 * abs(sin(i * 0.87) * cos(i * 0.43)) + 6 * abs(sin(i * 0.21)) for i in range(75)
]


@lru_cache
def font(size, weight="regular"):
    names = {
        "regular": ("/System/Library/Fonts/HelveticaNeue.ttc", "DejaVuSans.ttf"),
        "bold": ("/System/Library/Fonts/HelveticaNeue.ttc", "DejaVuSans-Bold.ttf"),
        "mono": ("/System/Library/Fonts/Menlo.ttc", "DejaVuSansMono.ttf"),
        "serif": ("/System/Library/Fonts/Supplemental/Georgia.ttf", "DejaVuSerif.ttf"),
    }
    for name in names[weight]:
        try:
            index = 1 if weight == "bold" and name.endswith(".ttc") else 0
            return ImageFont.truetype(name, size * SCALE, index=index)
        except OSError:
            continue
    raise RuntimeError("Install Helvetica Neue or the DejaVu fonts.")


def px(items):
    return tuple(round(value * SCALE) for value in items)


def clock(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes}:{seconds:02}"


class Canvas:
    def __init__(self):
        self.image = Image.new("RGB", px(SIZE), BG)
        self.draw = ImageDraw.Draw(self.image)

    def box(self, bounds, fill, radius=0, outline=None):
        self.draw.rounded_rectangle(px(bounds), radius=px((radius,))[0], fill=fill,
                                    outline=outline, width=SCALE)

    def dot(self, x, y, radius, fill, outline=None):
        self.draw.ellipse(px((x-radius, y-radius, x+radius, y+radius)),
                          fill=fill, outline=outline, width=SCALE)

    def line(self, points, color, width=1.4):
        self.draw.line([px(point) for point in points], fill=color, width=round(width * SCALE), joint="curve")

    def poly(self, points, fill):
        self.draw.polygon([px(point) for point in points], fill=fill)

    def text(self, x, y, value, size=16, color=INK, weight="regular", anchor="lm"):
        self.draw.text(px((x, y)), value, font=font(size, weight), fill=color, anchor=anchor)

    def width(self, value, size=16, weight="regular"):
        return font(size, weight).getlength(value) / SCALE

    def icon(self, name, x, y, color=QUIET):
        def path(*points):
            self.line([(x + a, y + b) for a, b in points], color)

        def box(a, b, c, d, radius=1):
            self.box((x+a, y+b, x+c, y+d), None, radius, color)

        if name == "home":
            path((1, 7), (8, 1), (15, 7), (14, 7), (14, 15), (10, 15), (10, 10),
                 (6, 10), (6, 15), (2, 15), (2, 7))
        elif name == "overview":
            for bounds in ((1, 1, 7, 9), (9, 1, 15, 6), (1, 11, 7, 15), (9, 8, 15, 15)):
                box(*bounds)
        elif name in {"notes", "transcript"}:
            path((3, 1), (11, 1), (14, 4), (14, 14), (3, 14), (3, 1))
            path((11, 1), (11, 4), (14, 4))
            path((5, 7), (11, 7))
            if name == "transcript":
                path((5, 10), (11, 10))
        elif name == "timeline":
            path((3, 2), (3, 13), (8, 13))
            path((3, 4), (12, 4))
            path((3, 9), (10, 9))
        elif name == "analytics":
            for a, height in ((2, 5), (7, 9), (12, 13)):
                path((a, 15), (a, 15-height))
        elif name == "folder":
            path((1, 4), (6, 4), (8, 6), (15, 6), (15, 13), (1, 13), (1, 4))
        elif name == "mic":
            box(6, 1, 10, 9, 2)
            path((3, 7), (3, 9), (5, 12), (11, 12), (13, 9), (13, 7))
            path((8, 12), (8, 15))
        elif name == "play":
            self.poly(((x+5, y+2), (x+13, y+8), (x+5, y+14)), color)
        elif name == "pause":
            path((5, 2), (5, 14))
            path((10, 2), (10, 14))
        elif name == "voice":
            for a, height in ((1, 5), (4, 10), (7, 14), (10, 7), (13, 10)):
                path((a, 8-height/2), (a, 8+height/2))
        elif name == "chat":
            path((2, 3), (12, 3), (15, 6), (15, 11), (11, 13), (6, 13),
                 (2, 16), (3, 12), (1, 10), (1, 5), (2, 3))
        elif name == "plus":
            path((8, 3), (8, 13))
            path((3, 8), (13, 8))

    def finish(self):
        return self.image.resize(SIZE, Image.Resampling.LANCZOS)


def window(view):
    canvas = Canvas()
    canvas.box((13, 15, 947, 562), WINDOW, 18, EDGE)
    canvas.box((14, 16, 186, 561), SIDEBAR, 17)
    canvas.box((156, 17, 186, 560), SIDEBAR)
    canvas.line(((186, 16), (186, 560)), EDGE, 1)
    for x, color in ((31, "#ff6059"), (49, "#ffbd43"), (67, "#36c85e")):
        canvas.dot(x, 37, 5, color)
    canvas.dot(212, 37, 9, None, MUTED)
    canvas.dot(215, 34, 1.2, INK)
    canvas.text(229, 37, "Jaro", 14, INK, "bold")
    canvas.icon("plus", 810, 29)
    canvas.text(831, 37, "New session", 13, QUIET)

    if view == "Home":
        canvas.box((27, 87, 174, 123), SOFT, 8)
    canvas.icon("home", 38, 97)
    canvas.text(62, 105, "Home", 14, INK if view == "Home" else QUIET)
    canvas.text(38, 146, "Sessions", 13, QUIET, "bold")
    for index, label in enumerate(("Launch planning", "Design review", "Research notes")):
        middle = 176 + 33 * index
        selected = index == 0 and view != "Home"
        if selected:
            canvas.box((27, middle-14, 174, middle+14), SOFT, 7)
        canvas.icon("transcript", 38, middle-8, INK if selected else MUTED)
        canvas.text(61, middle, label, 13, INK if selected else QUIET)
    return canvas


def home(canvas):
    canvas.box((LEFT, 104, RIGHT, 411), "#22222a", 22, EDGE)
    canvas.dot(569, 227, 30, SOFT, EDGE)
    canvas.icon("voice", 562, 219, LAVENDER)
    canvas.text(569, 286, "Record or open audio.", 18, INK, "bold", "mm")
    canvas.box((378, 432, 758, 476), PANEL, 22, EDGE)
    canvas.box((385, 438, 488, 470), "#eeedf4", 16)
    canvas.icon("mic", 400, 446, "#23232c")
    canvas.text(421, 454, "Record", 14, "#23232c", "bold")
    canvas.icon("folder", 508, 446)
    canvas.text(531, 454, "Open audio", 14)
    canvas.dot(672, 454, 3, LAVENDER)
    canvas.text(685, 454, "Live", 14, LAVENDER)


def session_header(canvas, view):
    canvas.text(LEFT, 94, "Launch planning", 32, INK, "serif")
    canvas.text(RIGHT, 95, "12 Nov 2026   ·   3:24   ·   Auto · English", 13, QUIET, anchor="rm")
    width = sum(item[2] for item in TABS)
    canvas.box((LEFT, 132, LEFT+width, 170), PANEL, 19, EDGE)
    x = LEFT
    for label, icon, size in TABS:
        active = label == view
        if active:
            canvas.box((x+4, 136, x+size-3, 166), "#17171d", 15)
        color = INK if active else QUIET
        canvas.icon(icon, x+12, 143, color)
        canvas.text(x+33, 151, label, 13, color, "bold" if active else "regular")
        x += size


def timestamp(canvas, x, y, time):
    canvas.box((x, y-11, x+44, y+11), PURPLE_BG, 5)
    canvas.text(x+22, y, clock(time), 12, LAVENDER, "bold", "mm")


def overview(canvas, local):
    canvas.text(LEFT, 213, "Start with a small pilot and review the feedback on Friday.", 20)
    for index, (time, title, description) in enumerate(CHAPTERS[1:], 1):
        y = 215 + 80 * index
        canvas.text(LEFT, y-22, title, 18, INK, "bold")
        canvas.text(LEFT, y+5, description, 16, QUIET)
        x = LEFT + canvas.width(description) + 10
        timestamp(canvas, x, y+5, time)
        if index == 1 and 0.95 <= local < 1.23:
            canvas.box((x-2, y-8, x+46, y+18), None, 6, LAVENDER)


def timeline(canvas, local):
    for index, (time, title, description) in enumerate(CHAPTERS):
        y = 212 + index * 78
        selected = index == 1 and local >= 0.95
        canvas.box((LEFT, y-16, RIGHT, y+46), SOFT if selected else WINDOW, 10,
                   EDGE if selected else None)
        timestamp(canvas, LEFT+12, y+8, time)
        canvas.text(LEFT+74, y+3, title, 17, INK, "bold")
        canvas.text(LEFT+74, y+25, description, 15, QUIET)
        if not selected:
            canvas.line(((LEFT+12, y+47), (RIGHT-12, y+47)), EDGE, 1)
        canvas.icon("play", RIGHT-34, y+6, LAVENDER if selected else MUTED)


def transcript(canvas, local):
    for index, (time, speaker, sentence) in enumerate(TURNS):
        y = 204 + index * 66
        color = LAVENDER if speaker == "Speaker A" else BLUE
        canvas.text(LEFT, y, clock(time), 13, color, "bold")
        canvas.text(LEFT+53, y, speaker, 13, color, "bold")
        if index == 3 and local > 0.95:
            words = sentence.split()
            current = min(int((local-0.95) * 5), len(words)-1)
            x = LEFT+53
            for i, word in enumerate(words):
                if i == current:
                    canvas.box((x-2, y+11, x+canvas.width(word)+2, y+33), PURPLE_BG, 4)
                canvas.text(x, y+22, word, 16)
                x += canvas.width(word + " ")
        else:
            canvas.text(LEFT+53, y+22, sentence, 16)
        canvas.line(((LEFT, y+46), (RIGHT, y+46)), EDGE, 1)


def analytics(canvas, local):
    start, end = LEFT+111, RIGHT-13
    for index, (speaker, color, intervals) in enumerate(SPEAKERS):
        y = 248 + index * 29
        canvas.text(LEFT+12, y, speaker, 13, QUIET)
        canvas.box((start, y-5, end, y+5), SOFT, 5)
        for begin, finish in intervals:
            canvas.box((start + (end-start)*begin/DURATION, y-4,
                        start + (end-start)*finish/DURATION, y+4), color, 3)
    current = 72 if local < 0.95 else min(174, 138 + 19 * (local-0.95))
    cursor = start + (end-start)*current/DURATION
    canvas.line(((cursor, 234), (cursor, 291)), INK, 1.4)
    for x, value, anchor in ((start, "0:00", "lm"), ((start+end)/2, "1:42", "mm"), (end, "3:24", "rm")):
        canvas.text(x, 308, value, 12, MUTED, anchor=anchor)
    at_cursor = next((speaker for speaker, _, intervals in SPEAKERS
                      if any(begin <= current < finish for begin, finish in intervals)), "Silence")
    canvas.text(LEFT, 330, f"{clock(current)} · {at_cursor}", 13, QUIET)
    canvas.text(LEFT, 212, "Speaker timeline", 18, INK, "bold")
    canvas.text(RIGHT, 212, "3:24 total", 13, QUIET, anchor="rm")
    canvas.text(LEFT, 370, "Speaking time", 18, INK, "bold")
    canvas.text(RIGHT, 370, "Share of speaker time", 13, QUIET, anchor="rm")
    totals = [sum(end - start for start, end in intervals) for _, _, intervals in SPEAKERS]
    for index, ((speaker, color, _), total) in enumerate(zip(SPEAKERS, totals)):
        y = 410 + index*35
        fraction = total / sum(totals)
        canvas.box((LEFT, y-15, RIGHT, y+15), PANEL, 7)
        canvas.dot(LEFT+14, y, 3.5, color)
        canvas.text(LEFT+26, y, speaker, 13, INK)
        canvas.box((LEFT+120, y-4, RIGHT-107, y+4), EDGE, 4)
        canvas.box((LEFT+120, y-4, LEFT+120+(RIGHT-LEFT-227)*fraction, y+4), color, 4)
        canvas.text(RIGHT-63, y, clock(total), 13, QUIET, anchor="rm")
        canvas.text(RIGHT-13, y, f"{fraction:.0%}", 14, color, "bold", "rm")


def dock(canvas, view, local):
    playing = view in {"Overview", "Timeline", "Transcript"} and local >= 1.02
    if playing and view != "Transcript":
        canvas.box((LEFT, 445, RIGHT, 499), PANEL, 12, EDGE)
        canvas.icon("pause", LEFT+15, 464, INK)
        playhead = (72 + local-1.02) * len(WAVE_HEIGHTS) / DURATION
        for i, height in enumerate(WAVE_HEIGHTS):
            x = LEFT + 51 + i*6.85
            canvas.line(((x, 472-height/2), (x, 472+height/2)), LAVENDER if i < playhead else MUTED, 2)
        canvas.text(RIGHT-14, 472, clock(72 + local-1.02), 12, QUIET, anchor="rm")
    canvas.box((LEFT, 516, LEFT+52, 547), SOFT, 15, EDGE)
    canvas.icon("voice", LEFT+18, 524, LAVENDER if playing else QUIET)
    canvas.box((LEFT+60, 516, RIGHT, 547), SOFT, 15, EDGE)
    canvas.icon("chat", LEFT+74, 523)
    canvas.text(LEFT+97, 532, "Ask about this recording", 13, QUIET)


def cursor(canvas, point, click_phase=None):
    x, y = point
    if click_phase is not None and 0 <= click_phase < 0.27:
        canvas.dot(x, y, 7 + click_phase * 34, None, LAVENDER)
    shape = ((x, y), (x, y+19), (x+4, y+14), (x+8, y+21),
             (x+11, y+19), (x+7, y+12), (x+14, y+12))
    canvas.poly([(a+1.5, b+1.5) for a, b in shape], BG)
    canvas.poly(shape, INK)
    canvas.line((*shape, shape[0]), "#15151a", 1.1)


VIEWS = {"Home": home, "Overview": overview, "Timeline": timeline,
         "Transcript": transcript, "Speaker analytics": analytics}

# A scene starts with the tab or sidebar click, then shows one interaction before the next view.
STORY = (
    ("Home", 2.08, (436, 454)),
    ("Overview", 2.56, (LEFT + font(16).getlength(CHAPTERS[1][2]) / SCALE + 32, 300)),
    ("Timeline", 2.56, (285, 298)),
    ("Transcript", 2.64, (301, 424)),
    ("Speaker analytics", 2.96, (710, 274)),
)


def blend_points(a, b, ratio):
    eased = (1 - cos(pi * min(1, max(0, ratio)))) / 2
    return tuple(p + (q-p)*eased for p, q in zip(a, b))


def draw_frame(view, local, duration, action, next_view):
    canvas = window(view)
    if view == "Home":
        home(canvas)
        start = DESTINATIONS[view]
    else:
        session_header(canvas, view)
        VIEWS[view](canvas, local)
        dock(canvas, view, local)
        start = DESTINATIONS[view]
    point, phase = start, None
    if view != "Home" and local < 0.27:
        phase = local
    if local >= 0.43:
        point = blend_points(start, action, (local-0.43)/0.48)
        phase = None if view == "Home" else local-0.95
    if local >= duration-0.62:
        point = blend_points(action, DESTINATIONS[next_view], (local-duration+0.62)/0.57)
        phase = None
    cursor(canvas, point, phase)
    return canvas.finish()


def render(output):
    # Use one palette across cuts to keep text and background colors stable while the cursor moves.
    sheet = Image.new("RGB", (SIZE[0], len(STORY)*SIZE[1]))
    for i, (view, duration, action) in enumerate(STORY):
        next_view = STORY[(i+1) % len(STORY)][0]
        sheet.paste(draw_frame(view, 1.5, duration, action, next_view), (0, i*SIZE[1]))
    palette = sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames = []
    for i, (view, duration, action) in enumerate(STORY):
        next_view = STORY[(i+1) % len(STORY)][0]
        for frame in range(round(duration * 1000 / FRAME_MS)):
            image = draw_frame(view, frame * FRAME_MS / 1000, duration, action, next_view)
            frames.append(image.quantize(palette=palette, dither=Image.Dither.NONE))
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:], loop=0,
                   duration=FRAME_MS, optimize=True)


if __name__ == "__main__":
    render(ROOT / "docs/media/soundfolio-demo.gif")
