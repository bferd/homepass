"""The admin's welcome and info text as guest HTML: a few formatting tags, links.

The text is stored exactly as typed and run through here on every render, so
nothing in storage is ever trusted. What it may hold is a small, fixed set of
formatting tags; everything else is text and is shown as typed.

- Allowed tags (any case): b, strong, i, em, u, s, small, mark, br, h1 to h6,
  ul, ol, li and font. Each is re-emitted lower-case and bare. The one
  exception is font's color, which is read, checked against a strict grammar
  and written back from the parsed value, never copied from the input.
- Any other tag, comment or declaration, a closing tag that closes nothing,
  and an allowed tag past the nesting cap, keeps its original text and is
  escaped, so a guest sees `<script>` rather than a script.
- Entities are not decoded: `&lt;b&gt;` is shown as typed, so an entity can
  never smuggle in a tag.
- Text between tags goes through app/linkify.py, which escapes it and turns
  https URLs, emails and phone numbers into the only anchors on the page. A
  tag's own text, attributes included, is never linked.

html.parser does the tokenising, but its callbacks hand back interpreted
pieces (lower-cased names, decoded attribute values) and it silently drops an
unterminated tag at the end. So each callback only records where its token
starts, and the output is cut from the raw text by position: every character
the admin typed lands in exactly one slice, and a slice that is not an
allowed tag is shown as text.
"""
import re
from functools import lru_cache
from html.parser import HTMLParser

from markupsafe import Markup, escape

from app.linkify import linkify

_INLINE = frozenset({"b", "strong", "i", "em", "u", "s", "small", "mark", "font"})
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_LISTS = frozenset({"ul", "ol"})
# Tags that start or end a line of their own. A newline typed right after one
# is dropped (the tag already breaks the line; the card keeps typed line
# breaks), and the collapsed card's first line ends at one.
_BLOCK = _HEADINGS | _LISTS | {"li"}
_ALLOWED = _INLINE | _BLOCK | {"br"}

# Deep enough for any honest list in a list; an opening tag past it is text.
MAX_DEPTH = 20

# The 148 CSS named colors (CSS Color Module Level 4), which are also the
# names the HTML legacy color parser accepts for <font color>. "transparent"
# and system colors are not in the list on purpose.
CSS_NAMED_COLORS = frozenset("""
aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond
blue blueviolet brown burlywood cadetblue chartreuse chocolate coral
cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray
darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid
darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey
darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue
firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod
gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki
lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon
lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue
lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue
mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen
mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod
palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum
powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon
sandybrown seagreen seashell sienna silver skyblue slateblue slategray
slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet
wheat white whitesmoke yellow yellowgreen
""".split())

_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
# Spaces only, never other whitespace, and exactly three plain integers.
_RGB = re.compile(r"rgb\( *(\d{1,3}) *, *(\d{1,3}) *, *(\d{1,3}) *\)", re.IGNORECASE)

_CACHE_SIZE = 256


def font_color(value: str | None) -> str | None:
    """The color to emit for a font tag's color attribute, or None to drop it.

    The result is always rebuilt from what was parsed: a name lower-cased, a
    hex value lower-cased, and rgb() turned into #rrggbb. The last is not just
    tidiness: <font color> goes through HTML's legacy color parser, not CSS,
    and that parser reads "rgb(255, 0, 0)" as a garbled hex value.
    """
    if not value:
        return None
    if value.lower() in CSS_NAMED_COLORS:
        return value.lower()
    if _HEX.fullmatch(value):
        return value.lower()
    m = _RGB.fullmatch(value)
    if m:
        channels = [int(c) for c in m.groups()]
        if all(c <= 255 for c in channels):
            return "#" + "".join(f"{c:02x}" for c in channels)
    return None


class _Tokens(HTMLParser):
    """Records where each token starts, and the extent of every tag.

    A tag is (start, end, kind, name, attrs). Everything else (text, entity
    references, comments, declarations) is only a start, (start, None, ...):
    its slice runs to the next token, and render() treats it as text or as
    literal text by kind.
    """

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self._text = text
        self._line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.tokens: list[tuple] = []

    def _start(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self._start()
        self.tokens.append((start, start + len(self.get_starttag_text()), "start", tag, attrs))

    def handle_startendtag(self, tag, attrs):
        start = self._start()
        self.tokens.append((start, start + len(self.get_starttag_text()), "startend", tag, attrs))

    def handle_endtag(self, tag):
        start = self._start()
        # html.parser ends a closing tag at its first ">".
        end = self._text.find(">", start)
        self.tokens.append((start, len(self._text) if end < 0 else end + 1, "end", tag, None))

    def handle_data(self, data):
        self.tokens.append((self._start(), None, "text", None, None))

    handle_entityref = handle_charref = lambda self, name: self.handle_data(name)

    def _literal(self, data):
        self.tokens.append((self._start(), None, "literal", None, None))

    handle_comment = handle_decl = handle_pi = unknown_decl = _literal


def _pieces(text: str) -> list[tuple[str, str]]:
    """The text cut into (kind, raw) pieces covering every character in order.

    kind is "text", "literal" (shown as typed, never linked), or a tag event:
    "start", "startend" or "end" followed by the lower-case name after a
    colon, with font's color carried in a third field.
    """
    parser = _Tokens(text)
    parser.feed(text)
    parser.close()
    pieces, pos = [], 0
    tokens = parser.tokens
    for n, (start, end, kind, name, attrs) in enumerate(tokens):
        if start > pos:
            # Something html.parser read without reporting, such as "</>".
            pieces.append(("text", text[pos:start], None))
        start = max(start, pos)
        if end is None:
            end = tokens[n + 1][0] if n + 1 < len(tokens) else len(text)
        if kind in ("text", "literal"):
            pieces.append((kind, text[start:end], None))
        else:
            color = None
            if name == "font" and attrs:
                color = font_color(next((v for k, v in attrs if k == "color"), None))
            pieces.append((f"{kind}:{name}", text[start:end], color))
        pos = max(pos, end)
    if pos < len(text):
        # An unterminated tag at the very end, which html.parser drops.
        pieces.append(("text", text[pos:], None))
    return pieces


def _events(text: str):
    """The pieces as a balanced stream: ("open", name, color), ("close", name),
    ("br",), ("text", raw) and ("literal", raw)."""
    stack: list[str] = []
    for kind, raw, color in _pieces(text.replace("\x00", "�")):
        if kind in ("text", "literal"):
            yield (kind, raw)
            continue
        event, name = kind.split(":", 1)
        if name not in _ALLOWED:
            yield ("literal", raw)
        elif name == "br":
            # "<br>" and "<br/>" are the one void tag; "</br>" closes nothing.
            if event == "end":
                yield ("literal", raw)
            else:
                yield ("br",)
        elif event == "startend":
            # "<b/>" is ambiguous (HTML reads it as an opening tag); show it.
            yield ("literal", raw)
        elif event == "start":
            # Do what a browser would do anyway, and keep the stack in step
            # with it: a list item closes the open item of its own list, and
            # a heading directly inside a heading closes that one.
            if name == "li":
                if not any(t in _LISTS for t in stack):
                    yield ("literal", raw)
                    continue
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i] in _LISTS:
                        break
                    if stack[i] == "li":
                        while len(stack) > i:
                            yield ("close", stack.pop())
                        break
            elif name in _HEADINGS and stack and stack[-1] in _HEADINGS:
                yield ("close", stack.pop())
            if len(stack) >= MAX_DEPTH:
                yield ("literal", raw)
                continue
            stack.append(name)
            yield ("open", name, color)
        elif name in stack:
            # Close everything opened after it first, innermost first.
            while stack:
                top = stack.pop()
                yield ("close", top)
                if top == name:
                    break
        else:
            yield ("literal", raw)
    while stack:
        yield ("close", stack.pop())


def _drop_newline(raw: str) -> str:
    """A text run without the one line break typed right after a block tag."""
    return re.sub(r"\A[ \t]*\n", "", raw)


def _emit(events, region: str | None) -> Markup:
    """HTML for a balanced event stream: allowed tags rebuilt, the rest escaped,
    links made in the text runs. render() and first_line() both end here."""
    out: list[str] = []
    run: list[str] = []
    after_block = False

    def flush():
        if run:
            out.append(str(linkify("".join(run), region)))
            run.clear()

    for event in events:
        kind = event[0]
        if kind == "text":
            raw = _drop_newline(event[1]) if after_block and not run else event[1]
            run.append(raw)
            if raw:
                after_block = False
            continue
        flush()
        if kind == "literal":
            out.append(str(escape(event[1])))
            after_block = False
        elif kind == "br":
            out.append("<br>")
            after_block = True
        elif kind == "open":
            name, color = event[1], event[2]
            out.append(f'<font color="{color}">' if color else f"<{name}>")
            after_block = name in _BLOCK
        else:
            out.append(f"</{event[1]}>")
            after_block = event[1] in _BLOCK
    flush()
    return Markup("".join(out))


@lru_cache(maxsize=_CACHE_SIZE)
def render(text: str, region: str | None) -> Markup:
    """The text as guest HTML: allowed tags rebuilt, the rest escaped, links made.

    `region` is the phone region, passed to app/linkify.py unchanged. This and
    linkify are the only places that mark the admin's text safe.
    """
    return _emit(_events(text), region)


def _first_line_events(text: str) -> list[tuple] | None:
    """The events of the first line with visible text, balanced, or None.

    Walks the same event stream render() emits and stops at the first line
    boundary after visible text: a typed newline (in text or in text shown as
    typed), a <br>, the close of a heading or list item, or the opening of a
    block that would start a new line. Before any visible text, blank lines,
    <br> and whitespace are skipped, while tags are kept, so "<b>\nHello"
    still comes out bold. Whatever is open at the cut is closed, innermost
    first, so a first list item keeps its list and its bullet.
    """
    line: list[tuple] = []
    stack: list[str] = []
    seen = False

    def add_text(kind, raw):
        """Add one run; True once a newline after visible text ends the line."""
        nonlocal seen
        for n, segment in enumerate(raw.split("\n")):
            if n and seen:
                return True
            if segment.strip() or (seen and segment):
                line.append((kind, segment))
                seen = seen or bool(segment.strip())
        return False

    for event in _events(text):
        kind = event[0]
        if kind in ("text", "literal"):
            if add_text(kind, event[1]):
                break
        elif kind == "br":
            if seen:
                break
        elif kind == "open":
            if seen and event[1] in _BLOCK:
                break
            line.append(event)
            stack.append(event[1])
        else:
            stack.pop()
            if line and line[-1][0] == "open" and line[-1][1] == event[1]:
                # Opened and closed with nothing in between: leave both out.
                line.pop()
            else:
                line.append(event)
            if seen and event[1] in ("li", *_HEADINGS):
                break
    if not seen:
        return None
    while stack:
        line.append(("close", stack.pop()))
    return line


@lru_cache(maxsize=_CACHE_SIZE)
def first_line(texts: tuple[str, ...], region: str | None) -> Markup:
    """The collapsed card's one line: the first line of `texts` with visible
    text, as the same sanitised HTML the full card shows for it.

    Built from the same event stream as render() and emitted by the same code,
    so it holds the same tags, the same text shown as typed and the same
    links, and nothing render() would not. Empty Markup when the texts hold
    no visible text at all.
    """
    for text in texts:
        line = _first_line_events(text)
        if line is not None:
            return _emit(line, region)
    return Markup("")
