"""The admin's text as guest HTML (app/richtext.py).

What is pinned here: only the allowlisted formatting tags ever come out as
tags, bare and lower-case, with font's color the one attribute and only when
it parses; every other tag, attribute, entity and stray closing tag is shown
as the text that was typed; the output is always balanced; links are made in
the text between tags and never inside one; and the collapsed card's first
line is cut from the same parse, so it is the card's own first line,
formatting and links included, and nothing after it.

The checker at the end runs a battery of hostile input through the renderer
and reads the output back with html.parser, so it tests what a browser would
be handed rather than the strings these tests expect.
"""
from html.parser import HTMLParser

import pytest
from markupsafe import Markup

from app.richtext import CSS_NAMED_COLORS, MAX_DEPTH, first_line, font_color, render

CA_NATIONAL = "613-996-2000"
CA_E164 = "+16139962000"
ANCHOR = 'target="_blank" rel="noopener noreferrer"'


def r(text: str, region: str | None = None) -> str:
    return str(render(text, region))


def test_returns_markup():
    assert isinstance(render("hi", None), Markup)
    assert isinstance(render("", "CA"), Markup)


# ---------------------------------------------------------------------------
# Allowed tags
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tag", [
    "b", "strong", "i", "em", "u", "s", "small", "mark",
    "h1", "h2", "h3", "h4", "h5", "h6", "font",
])
def test_every_allowed_tag_renders(tag):
    assert r(f"<{tag}>x</{tag}>") == f"<{tag}>x</{tag}>"


def test_lists_render():
    assert r("<ul><li>a</li><li>b</li></ul>") == "<ul><li>a</li><li>b</li></ul>"
    assert r("<ol><li>a</li></ol>") == "<ol><li>a</li></ol>"


@pytest.mark.parametrize("text", ["a<br>b", "a<BR>b", "a<br/>b", "a<br />b", "a<br class=x>b"])
def test_br_variants(text):
    assert r(text) == "a<br>b"


def test_stray_closing_br_is_text():
    assert r("a</br>b") == "a&lt;/br&gt;b"


@pytest.mark.parametrize("text", ["<B>x</B>", "<b>x</B>", "<B >x</b >", "<b\n>x</b>"])
def test_tag_names_are_case_insensitive_and_emitted_lower_case(text):
    assert r(text) == "<b>x</b>"


@pytest.mark.parametrize("attrs", [
    'onclick="alert(1)"', "style='color:red'", "class=x", "id=y",
    'title="t" data-x="1"', "onmouseover=alert(1)", 'href="https://example.com"',
])
def test_attributes_are_stripped_on_every_tag_but_font(attrs):
    for tag in ("b", "h2", "li", "ul", "mark"):
        out = r(f"<ul><{tag} {attrs}>x</{tag}></ul>") if tag == "li" else r(f"<{tag} {attrs}>x</{tag}>")
        assert f"<{tag}>" in out
        assert "=" not in out.replace("&#", "")


# ---------------------------------------------------------------------------
# font color
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("given, emitted", [
    ("red", "red"),
    ("RED", "red"),
    ("RebeccaPurple", "rebeccapurple"),
    ("#abc", "#abc"),
    ("#ABC", "#abc"),
    ("#12aBcD", "#12abcd"),
    ("rgb(255, 0, 10)", "#ff000a"),
    ("rgb(0,0,0)", "#000000"),
    ("RGB( 1 , 2 , 3 )", "#010203"),
])
def test_accepted_font_colors_are_rebuilt(given, emitted):
    assert r(f'<font color="{given}">x</font>') == f'<font color="{emitted}">x</font>'


@pytest.mark.parametrize("given", [
    "rgba(1, 2, 3, 0.5)", "hsl(0, 100%, 50%)", "var(--x)", "url(x)",
    "javascript:alert(1)", "expression(alert(1))", "notacolor", "transparent",
    "#12", "#1234", "#12345g", "rgb(300,0,0)", "rgb(1,2)", "rgb(1,2,3,4)",
    "rgb(-1,2,3)", "rgb(1.5,2,3)", "rgb(1,\n2,3)", "red\n", " red", "red>",
    "red;background:url(x)", "",
])
def test_rejected_font_colors_drop_the_attribute(given):
    assert font_color(given) is None
    out = r(f'<font color="{given}">x</font>')
    assert out == "<font>x</font>"


def test_quote_injection_in_color_is_dropped():
    out = r("""<font color='red" onmouseover="alert(1)'>x</font>""")
    assert out == "<font>x</font>"
    # Malformed enough that html.parser does not read a tag at all: all text.
    out = r('<font color=red" onmouseover="alert(1)>x</font>')
    assert out == "&lt;font color=red&#34; onmouseover=&#34;alert(1)&gt;x&lt;/font&gt;"


def test_color_with_a_greater_than_sign_is_dropped():
    out = r("<font color='red>'>x</font>")
    assert out == "<font>x</font>"


def test_other_font_attributes_are_dropped():
    out = r('<font size=7 face="Comic Sans" style="x" onclick="y" color="blue">x</font>')
    assert out == '<font color="blue">x</font>'


def test_named_color_list_is_the_standard_148():
    assert len(CSS_NAMED_COLORS) == 148
    assert {"red", "rebeccapurple", "lightgoldenrodyellow"} <= CSS_NAMED_COLORS
    assert "transparent" not in CSS_NAMED_COLORS


# ---------------------------------------------------------------------------
# Everything else is shown as typed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, shown", [
    ("<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
    ("<style>body{display:none}</style>", "&lt;style&gt;body{display:none}&lt;/style&gt;"),
    ('<a href="https://evil.example">x</a>', "&lt;a href=&#34;https://evil.example&#34;&gt;x&lt;/a&gt;"),
    ("<img src=x onerror=alert(1)>", "&lt;img src=x onerror=alert(1)&gt;"),
    ('<iframe src="https://e.example"></iframe>', "&lt;iframe src=&#34;https://e.example&#34;&gt;&lt;/iframe&gt;"),
    ("<svg onload=alert(1)>", "&lt;svg onload=alert(1)&gt;"),
    ("<form><input name=pin></form>", "&lt;form&gt;&lt;input name=pin&gt;&lt;/form&gt;"),
    ('<meta http-equiv="refresh" content="0">', "&lt;meta http-equiv=&#34;refresh&#34; content=&#34;0&#34;&gt;"),
    ("<div>x</div><span>y</span>", "&lt;div&gt;x&lt;/div&gt;&lt;span&gt;y&lt;/span&gt;"),
    ("a < b", "a &lt; b"),
    ("5 <3", "5 &lt;3"),
    ("<b", "&lt;b"),
    ("x <b", "x &lt;b"),
    ("<<b>>x", "&lt;<b>&gt;x</b>"),
    ("<b/>x", "&lt;b/&gt;x"),
    ("< b>x", "&lt; b&gt;x"),
    ("<!-- <b>x</b> -->", "&lt;!-- &lt;b&gt;x&lt;/b&gt; --&gt;"),
    ("<?php echo 1 ?>", "&lt;?php echo 1 ?&gt;"),
    ("<!DOCTYPE html>", "&lt;!DOCTYPE html&gt;"),
    ("</>", "&lt;/&gt;"),
    ("</ b>", "&lt;/ b&gt;"),
    ("<h7>x</h7>", "&lt;h7&gt;x&lt;/h7&gt;"),
    ("<bold>x</bold>", "&lt;bold&gt;x&lt;/bold&gt;"),
])
def test_other_markup_is_shown_as_typed(text, shown):
    assert r(text) == shown


def test_a_tag_split_by_a_newline_is_still_a_tag():
    # HTML allows a newline anywhere whitespace may go inside a tag.
    assert r("<b\n>x</b\n>") == "<b>x</b>"
    assert r("<b\nonclick=x>y</b>") == "<b>y</b>"


def test_a_disallowed_tag_split_by_a_newline_stays_text():
    assert r("<img\nsrc=x\nonerror=alert(1)>") == "&lt;img\nsrc=x\nonerror=alert(1)&gt;"


def test_null_bytes_cannot_hide_a_tag():
    out = r("<scr\x00ipt>alert(1)</scr\x00ipt>")
    assert "<scr" not in out and "\x00" not in out
    assert r("<b\x00>x</b>").startswith("&lt;")


@pytest.mark.parametrize("text, shown", [
    ("&lt;script&gt;", "&amp;lt;script&amp;gt;"),
    ("&#60;b&#62;x", "&amp;#60;b&amp;#62;x"),
    ("&#x3c;b&#x3e;", "&amp;#x3c;b&amp;#x3e;"),
    ("AT&T", "AT&amp;T"),
    ("&amp", "&amp;amp"),
    ("<b>&lt;i&gt;</b>", "<b>&amp;lt;i&amp;gt;</b>"),
])
def test_entities_are_not_decoded(text, shown):
    assert r(text) == shown


def test_quotes_are_escaped_in_text():
    assert r("""say "hi" and 'bye'""") == "say &#34;hi&#34; and &#39;bye&#39;"


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------

def test_unclosed_tags_are_closed_at_the_end():
    assert r("<b><i>x") == "<b><i>x</i></b>"
    assert r("<ul><li>a") == "<ul><li>a</li></ul>"


def test_a_close_closes_what_was_opened_after_it_first():
    assert r("<b><i>x</b>y") == "<b><i>x</i></b>y"


def test_misnested_close_after_its_tag_closed_is_text():
    assert r("<b><i>x</b>y</i>") == "<b><i>x</i></b>y&lt;/i&gt;"


@pytest.mark.parametrize("text, shown", [
    ("</b>x", "&lt;/b&gt;x"),
    ("x</li>", "x&lt;/li&gt;"),
    ("<b>x</b></b>", "<b>x</b>&lt;/b&gt;"),
    ("</font>", "&lt;/font&gt;"),
])
def test_stray_closing_tags_are_text(text, shown):
    assert r(text) == shown


def test_depth_is_capped():
    text = "<b>" * (MAX_DEPTH + 3) + "x"
    out = r(text)
    assert out.count("<b>") == MAX_DEPTH
    assert out.count("</b>") == MAX_DEPTH
    assert out.count("&lt;b&gt;") == 3


def test_list_item_outside_a_list_is_text():
    assert r("<li>x</li>") == "&lt;li&gt;x&lt;/li&gt;"


def test_an_open_list_item_closes_before_the_next():
    assert r("<ul><li>a<li>b</ul>") == "<ul><li>a</li><li>b</li></ul>"


def test_a_heading_inside_a_heading_closes_it():
    assert r("<h1>a<h2>b</h2>") == "<h1>a</h1><h2>b</h2>"


# ---------------------------------------------------------------------------
# Line breaks around block tags
# ---------------------------------------------------------------------------

def test_a_newline_after_a_block_tag_is_not_doubled():
    assert r("<h2>Title</h2>\nBody") == "<h2>Title</h2>Body"
    assert r("<ul>\n<li>a</li>\n<li>b</li>\n</ul>\nafter") == "<ul><li>a</li><li>b</li></ul>after"
    assert r("a<br>\nb") == "a<br>b"


def test_only_one_newline_is_dropped():
    assert r("<h2>T</h2>\n\nBody") == "<h2>T</h2>\nBody"


def test_newlines_elsewhere_are_kept():
    assert r("<b>a</b>\nb\n\nc") == "<b>a</b>\nb\n\nc"


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

def test_email_inside_bold_links():
    assert r("<b>host@example.com</b>") == (
        f'<b><a href="mailto:host@example.com" {ANCHOR}>host@example.com</a></b>'
    )


def test_url_inside_a_heading_links():
    assert r("<h2>https://example.com/in</h2>") == (
        f'<h2><a href="https://example.com/in" {ANCHOR}>https://example.com/in</a></h2>'
    )


def test_phone_inside_a_list_item_links():
    out = r(f"<ul><li>Call {CA_NATIONAL}</li></ul>", "CA")
    assert f'<li>Call <a href="tel:{CA_E164}" {ANCHOR}>{CA_NATIONAL}</a></li>' in out


@pytest.mark.parametrize("text", [
    '<font color="https://example.com">x</font>',
    '<b title="https://example.com">x</b>',
    '<a href="https://example.com">x</a>',
    '<img src="https://example.com/i.png">',
    '<b data-mail="host@example.com">x</b>',
])
def test_no_link_is_made_inside_a_tag(text):
    assert "<a " not in r(text, "CA")


def test_a_phone_number_split_by_a_tag_does_not_link():
    assert "tel:" not in r("Call 613-<b>996</b>-2000", "CA")


def test_url_and_text_around_tags():
    out = r("See <i>https://example.com/a?b=1&c=2</i>.")
    assert out == f'See <i><a href="https://example.com/a?b=1&amp;c=2" {ANCHOR}>https://example.com/a?b=1&amp;c=2</a></i>.'


# ---------------------------------------------------------------------------
# First line
# ---------------------------------------------------------------------------

def fl(*texts: str, region: str | None = None) -> str:
    return str(first_line(texts, region))


def test_first_line_returns_markup():
    assert isinstance(first_line(("hi",), None), Markup)
    assert isinstance(first_line(("",), None), Markup)


@pytest.mark.parametrize("text, line", [
    ("<h2>Welcome!</h2>\nmore", "<h2>Welcome!</h2>"),
    ("Check-in is <b>after 3 PM</b> and more\nline2", "Check-in is <b>after 3 PM</b> and more"),
    ("\n\n  \nSecond\nThird", "Second"),
    ("a<br>b", "a"),
    ("<br>\n<br>a<br>b", "a"),
    ("<b>Hello\nworld", "<b>Hello</b>"),
    ("<b>\nHello</b>", "<b>Hello</b>"),
    ("<ul>\n<li>one</li>\n<li>two</li>\n</ul>", "<ul><li>one</li></ul>"),
    ("<ol><li><b>one</b> more<li>two</ol>", "<ol><li><b>one</b> more</li></ol>"),
    ('<font color="RED">Red</font> line\nnext', '<font color="red">Red</font> line'),
    ('<font color="rgb(1,2,3)" size=9>x</font>', '<font color="#010203">x</font>'),
    ("<mark>m</mark> <u>u</u> <s>s</s> <i>i</i> <small>sm</small> <em>e</em>\nz",
     "<mark>m</mark> <u>u</u> <s>s</s> <i>i</i> <small>sm</small> <em>e</em>"),
    ("<h1>Title</h1>Body", "<h1>Title</h1>"),
    ("Text<h2>Sub</h2>", "Text"),
    ("<b></b>\n<i>x</i>", "<i>x</i>"),
    ("<b>bold <i>both\nitalic</i></b>", "<b>bold <i>both</i></b>"),
])
def test_first_line(text, line):
    assert fl(text) == line


@pytest.mark.parametrize("text, line", [
    ("&lt;b&gt; &amp; <b>x</b>\ny", "&amp;lt;b&amp;gt; &amp;amp; <b>x</b>"),
    ("<script>x</script>\ny", "&lt;script&gt;x&lt;/script&gt;"),
    ("<img\nsrc=x onerror=alert(1)>\ny", "&lt;img"),
    ("</b> stray\ny", "&lt;/b&gt; stray"),
])
def test_first_line_shows_literal_text_as_the_card_does(text, line):
    assert fl(text) == line


def test_first_line_text_is_the_cards_own_first_line():
    """For a plain first line the strip is exactly the start of the card."""
    text = 'Hi <b>there</b>, call 613-996-2000 or host@example.com <font color="red">now</font>\nmore'
    line = fl(text, region="CA")
    assert r(text, "CA").startswith(line + "\n")


def test_first_line_links_equal_the_cards_links():
    import re
    text = "Book https://example.com/a?b=1&c=2 or 613-996-2000, host@example.com\nhttps://two.example"
    anchors = re.compile(r"<a [^>]*>.*?</a>")
    line = fl(text, region="CA")
    card = r(text, "CA")
    assert anchors.findall(line) == anchors.findall(card)[:3]
    assert "two.example" not in line


def test_first_line_of_only_a_long_url():
    url = "https://example.com/" + "a" * 900
    assert fl(url + "\nmore") == f'<a href="{url}" {ANCHOR}>{url}</a>'


def test_first_line_of_a_long_line_is_whole():
    long = "word " * 150
    assert fl(long + "\nsecond") == long


@pytest.mark.parametrize("texts", [
    ("<b><i></i></b>",), ("",), ("   \n\t\n",), ("<b></b><br><ul></ul>",), ("<br><br>", "  "),
])
def test_first_line_with_nothing_visible_is_empty(texts):
    assert fl(*texts) == ""


def test_first_line_moves_on_to_the_next_text():
    assert fl("<b></b><br>", "Own <b>text</b>\nmore") == "Own <b>text</b>"


LATER = "SECRETLINE"


@pytest.mark.parametrize("text", [
    f"one\n{LATER}",
    f"<b>one\n{LATER}</b>",
    f"one<br>{LATER}",
    f"<h2>one</h2>{LATER}",
    f"<ul><li>one</li><li>{LATER}</li></ul>",
    f"<ul><li>one<li>{LATER}</ul>",
    f'one\n<font color="red">{LATER}</font>',
    f'one\n<a href="https://x.example/{LATER}" title="{LATER}">x</a>',
    f"one\nhttps://x.example/{LATER}",
    f"one\n<!-- {LATER} -->",
    f"one<h1>{LATER}</h1>",
    f"one\n\n\n{LATER}",
])
def test_nothing_after_the_first_line_ever_appears(text):
    line = fl(text, region="CA")
    assert LATER not in line
    assert "one" in line


# ---------------------------------------------------------------------------
# Checker: parse the output and allow only what we generate
# ---------------------------------------------------------------------------

ALLOWED_OUT = {"b", "strong", "i", "em", "u", "s", "small", "mark", "br",
               "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "font", "a"}

HOSTILE = [
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<img src=x onerror=alert(1)>",
    "<svg/onload=alert(1)>",
    "<svg><script>alert(1)</script></svg>",
    "<iframe src=javascript:alert(1)>",
    "<a href=javascript:alert(1)>x</a>",
    "<a href=\"https://evil.example\" onclick=alert(1)>x</a>",
    "<b onclick=alert(1)>x</b>",
    "<b style=background:url(javascript:alert(1))>x</b>",
    "<font color=red onmouseover=alert(1)>x</font>",
    "<font color=\"red\" style=\"x\">x</font>",
    "<font color='red\" onmouseover=\"alert(1)'>x</font>",
    "<font color=javascript:alert(1)>x</font>",
    "<font color=\"expression(alert(1))\">x</font>",
    "<font color=\"url(javascript:alert(1))\">x</font>",
    "<font color=\"#fff\" color=\"red\" onfocus=x>x</font>",
    "<body onload=alert(1)>",
    "<meta http-equiv=refresh content=\"0;url=javascript:alert(1)\">",
    "<link rel=stylesheet href=https://evil.example/x.css>",
    "<style>@import 'https://evil.example';</style>",
    "<object data=javascript:alert(1)>",
    "<embed src=javascript:alert(1)>",
    "<form action=https://evil.example><input name=pin><button>go</button></form>",
    "<details open ontoggle=alert(1)>",
    "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
    "<noscript><p title=\"</noscript><img src=x onerror=alert(1)>\">",
    "<textarea><script>alert(1)</script></textarea>",
    "<title><script>alert(1)</script></title>",
    "<plaintext><script>alert(1)</script>",
    "<!--><script>alert(1)</script>-->",
    "<!-- --!><script>alert(1)</script>",
    "<![CDATA[<script>alert(1)</script>]]>",
    "<?xml version=\"1.0\"?><script>alert(1)</script>",
    "<b <script>alert(1)</script>>",
    "<b/onclick=alert(1)>x",
    "<b\tonclick=alert(1)>x</b>",
    "<b\x00onclick=alert(1)>x</b>",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    "&#60;script&#62;alert(1)&#60;/script&#62;",
    "<<script>script>alert(1)<</script>/script>",
    "<scr<script>ipt>alert(1)</script>",
    "javascript:alert(1)",
    "https://example.com/\"><script>alert(1)</script>",
    "https://example.com/?q=<b onclick=x>y</b>",
    "<ul><li onclick=x><b>" * 15 + "deep",
    "<h1>" * 30 + "x",
    "</div></section><script>alert(1)</script>",
    "</b></li></ul></h1>",
    "<li>x</li><ul><li><ul><li>y</ul></li></ul>",
    "<font color=\"rgb(255,0,0)\" color=\"blue\">x</font>",
    "host@example.com\"onmouseover=\"alert(1)",
]


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []

    def handle_starttag(self, tag, attrs):
        self.found.append((tag, attrs))

    handle_startendtag = handle_starttag

    def handle_comment(self, data):
        self.found.append(("comment", []))

    def handle_decl(self, decl):
        self.found.append(("decl", []))

    def handle_pi(self, data):
        self.found.append(("pi", []))

    def unknown_decl(self, data):
        self.found.append(("decl", []))


def test_the_hostile_battery_is_big_enough():
    assert len(HOSTILE) >= 40


def _first(payload, region):
    return str(first_line((payload,), region))


@pytest.mark.parametrize("make", [r, _first], ids=["render", "first_line"])
@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_output_holds_only_allowlisted_tags_and_our_attributes(payload, make):
    out = make(payload, "CA")
    checker = _Checker()
    checker.feed(out)
    checker.close()
    for tag, attrs in checker.found:
        assert tag in ALLOWED_OUT, (tag, out)
        names = [k for k, _ in attrs]
        if tag == "a":
            assert names == ["href", "target", "rel"], out
            href = dict(attrs)["href"]
            assert href.startswith(("https://", "mailto:", "tel:")), out
        elif tag == "font":
            assert names in ([], ["color"]), out
            if names:
                assert font_color(attrs[0][1]) == attrs[0][1], out
        else:
            assert names == [], out
        for name, value in attrs:
            assert not name.startswith("on"), out
            assert "javascript:" not in (value or "").lower(), out
    assert "<script" not in out.lower()
