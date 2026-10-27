"""Derive a full color palette from the brand colours.

Two colours are always set (the light background and the primary). Seven more
are optional and blank by default: secondary, accent, and a card colour, text
colour and page background for each of light and dark mode. A blank one is
worked out from the others, so a deployment that sets nothing new gets a
palette that is only better, never different in kind.

Everything ends up as the RGB-triplet CSS custom properties that Tailwind reads
(``--color-bg-light: 242 240 233``). Derived colours are checked for contrast:
text is pushed lighter or darker until it is readable on the page and on a
card, and the dark palette keeps clearly separated steps for page, card and
border instead of three near-blacks.

A deployment on the default background and primary keeps the hand-tuned
palette that ``static/input.css`` ships. Derivation only replaces the parts that
a setting actually changed.
"""
import colorsys
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_BG = "#F2F0E9"
DEFAULT_PRIMARY = "#D9523C"

# The colour settings: the two that are always set, then every optional one in
# the order the dashboard lists them. The other modules import these.
REQUIRED_KEYS = ("brand_primary", "brand_bg")
OPTIONAL_KEYS = (
    "brand_secondary",
    "brand_accent",
    "brand_surface",
    "brand_text",
    "brand_dark_bg",
    "brand_dark_surface",
    "brand_dark_text",
)
COLOR_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS

RGB = tuple[int, int, int]

WHITE: RGB = (255, 255, 255)
BLACK: RGB = (0, 0, 0)

# Minimum contrast ratios (WCAG 2.x). Body text gets the stricter AAA figure so
# a derived palette has headroom; muted text and UI accents get the AA ones.
TEXT_CONTRAST = 7.0
MUTED_CONTRAST = 4.5
ACCENT_CONTRAST = 3.0
# White on the primary button unless it would be harder than this to read.
# Lower than the text figure on purpose: it keeps the shipped coral button
# white, and only a genuinely pale primary (yellow, light green) flips to dark.
ON_PRIMARY_MIN = 3.0


@dataclass(frozen=True)
class Brand:
    """The colour settings, as hex strings. Blank means "work it out"."""

    bg: str = DEFAULT_BG
    primary: str = DEFAULT_PRIMARY
    secondary: str = ""
    accent: str = ""
    surface: str = ""
    text: str = ""
    dark_bg: str = ""
    dark_surface: str = ""
    dark_text: str = ""


def brand_from_colors(colors) -> Brand:
    """Build a Brand from a mapping of the colour settings by key."""
    return Brand(**{key[len("brand_"):]: colors[key] for key in COLOR_KEYS})


def brand_from_settings(s) -> Brand:
    """Build a Brand from the live settings object (or anything shaped like it)."""
    return brand_from_colors({key: getattr(s, key) for key in COLOR_KEYS})


# ── colour maths ──────────────────────────────────────────────────────────

def _parse(value: str) -> RGB | None:
    """A ``#rrggbb`` string as RGB, or None if it is blank or malformed.

    Malformed is treated like blank rather than raising: these values can come
    from an environment variable nobody validated, and a bad one should fall
    back to the derived colour, not take down every page.
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if len(v) != 7 or v[0] != "#":
        return None
    try:
        return int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16)
    except ValueError:
        return None


def _hex(c: RGB) -> str:
    return f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}"


def _clamp(v: float) -> int:
    return max(0, min(255, round(v)))


def _mix(c1: RGB, c2: RGB, t: float) -> RGB:
    """Linearly interpolate between c1 and c2.  t=0 gives c1, t=1 gives c2."""
    return (
        _clamp(c1[0] + (c2[0] - c1[0]) * t),
        _clamp(c1[1] + (c2[1] - c1[1]) * t),
        _clamp(c1[2] + (c2[2] - c1[2]) * t),
    )


def _darken(c: RGB, factor: float) -> RGB:
    return _mix(c, BLACK, factor)


def _lighten(c: RGB, factor: float) -> RGB:
    return _mix(c, WHITE, factor)


def _trip(c: RGB) -> str:
    """Format as a Tailwind RGB triplet string."""
    return f"{c[0]} {c[1]} {c[2]}"


def _hls(c: RGB) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(c[0] / 255, c[1] / 255, c[2] / 255)


def _from_hls(h: float, l: float, s: float) -> RGB:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return _clamp(r * 255), _clamp(g * 255), _clamp(b * 255)


def _channel(v: int) -> float:
    s = v / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def luminance(c: RGB) -> float:
    """WCAG relative luminance."""
    return 0.2126 * _channel(c[0]) + 0.7152 * _channel(c[1]) + 0.0722 * _channel(c[2])


def contrast(a: RGB, b: RGB) -> float:
    """WCAG contrast ratio, 1.0 to 21.0."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def is_dark(c: RGB) -> bool:
    """True when white reads better on this colour than black does."""
    return contrast(WHITE, c) > contrast(BLACK, c)


def ensure_contrast(fg: RGB, bg: RGB, minimum: float) -> RGB:
    """Return fg, moved toward white or black until it reaches ``minimum`` on bg.

    The direction is whichever extreme the background is further from. If even
    that cannot reach the ratio (a mid-grey background can top out near 4.6),
    the best attainable colour is returned.
    """
    if contrast(fg, bg) >= minimum:
        return fg
    target = BLACK if contrast(BLACK, bg) >= contrast(WHITE, bg) else WHITE
    for step in range(1, 21):
        candidate = _mix(fg, target, step / 20)
        if contrast(candidate, bg) >= minimum:
            return candidate
    return target


def _readable_on(fg: RGB, backgrounds: tuple[RGB, ...], minimum: float) -> RGB:
    """ensure_contrast against several backgrounds, the hardest last."""
    for bg in backgrounds:
        fg = ensure_contrast(fg, bg, minimum)
    return fg


# ── the shipped palette ───────────────────────────────────────────────────
# Mirrors static/input.css. Keep the two in step; tests/test_theme.py checks it.

DEFAULT_COLORS: dict[str, RGB] = {
    "primary": (217, 82, 60),
    "primary_hover": (196, 69, 52),
    "on_primary": (255, 255, 255),
    "surface_light": (255, 255, 255),
    "surface_dark": (42, 32, 28),
    "bg_light": (242, 240, 233),
    "bg_dark": (33, 20, 17),
    "border_light": (229, 224, 216),
    "border_dark": (62, 52, 48),
    "accent": (212, 163, 115),
    "ink": (74, 111, 165),
    "muted_light": (107, 99, 96),
    "muted_dark": (160, 150, 146),
    "soot": (44, 36, 32),
    "fg_light": (44, 36, 32),
    "fg_dark": (243, 244, 246),
}

# CSS variable for each key above. muted_dark is not here: it is not a :root
# variable but the value of --color-muted inside ``.dark``.
_VARS = {
    "primary": "--color-primary",
    "primary_hover": "--color-primary-hover",
    "on_primary": "--color-on-primary",
    "surface_light": "--color-surface-light",
    "surface_dark": "--color-surface-dark",
    "bg_light": "--color-bg-light",
    "bg_dark": "--color-bg-dark",
    "border_light": "--color-border-light",
    "border_dark": "--color-border-dark",
    "accent": "--color-accent",
    "ink": "--color-ink",
    "muted_light": "--color-muted",
    "soot": "--color-soot",
    "fg_light": "--color-fg-light",
    "fg_dark": "--color-fg-dark",
}


# ── derivation ────────────────────────────────────────────────────────────

def _derive_light(bg: RGB) -> dict[str, RGB]:
    """Light-mode page, card, border and text colours from a page background.

    "Light mode" is the palette used when the guest is not in dark mode, so the
    background may itself be dark (a deployment that sets a dark green here).
    The text colour follows the background, not the mode's name.
    """
    if is_dark(bg):
        surface = _lighten(bg, 0.07)
        border = _lighten(bg, 0.18)
        base_text = _lighten(bg, 0.92)
    else:
        surface = _lighten(bg, 0.55)
        border = _darken(bg, 0.08)
        base_text = _darken(bg, 0.82)
    return {
        "bg_light": bg,
        "surface_light": surface,
        "border_light": border,
        "soot": _darken(bg, 0.82),
        "fg_light": _readable_on(base_text, (surface, bg), TEXT_CONTRAST),
    }


def _derive_dark(base: RGB) -> dict[str, RGB]:
    """Dark-mode colours from a page background, with clear steps between them.

    Hue and a limited amount of saturation come from ``base``; lightness is
    stepped (page, then card a little lighter, then border lighter again) so the
    three read as three things. Darkening to a fixed fraction of black, as this
    used to, collapses them into near-identical blacks.
    """
    h, _, s = _hls(base)
    if is_dark(base):
        # Already a dark colour (the user's own dark background): keep its
        # lightness, only guarantee room above it for card and border.
        bg = base
    else:
        bg = _from_hls(h, 0.09, min(s, 0.40))
    hb, lb, sb = _hls(bg)
    lb = min(lb, 0.40)
    surface = _from_hls(hb, lb + 0.06, sb)
    border = _from_hls(hb, lb + 0.15, sb * 0.9)
    text = _from_hls(hb, 0.94, min(sb, 0.15))
    return {
        "bg_dark": bg,
        "surface_dark": surface,
        "border_dark": border,
        "fg_dark": _readable_on(text, (surface, bg), TEXT_CONTRAST),
    }


def _muted(text: RGB, bg: RGB, surface: RGB) -> RGB:
    """Secondary text: the body text pulled toward the background, kept legible."""
    return _readable_on(_mix(text, bg, 0.42), (surface, bg), MUTED_CONTRAST)


def _derive_accents(primary: RGB, bg_light: RGB, bg_dark: RGB) -> dict[str, RGB]:
    """Secondary and accent colours that go with the primary and read in both modes.

    The accent is the primary's neighbour on the colour wheel. The secondary is a
    muted tone of the primary's own hue, so it always looks chosen alongside it
    (a green primary gets a soft grey-green, not its magenta opposite). Both are
    held to a middle lightness, then checked against the two page backgrounds,
    because one value has to serve light and dark mode.
    """
    h, _, s = _hls(primary)
    # A grey primary has no hue to rotate, so its derived colours stay neutral.
    sat = max(s, 0.35) if s > 0.05 else 0.0

    def pick(hue: float, sat_: float) -> RGB:
        best = _from_hls(hue, 0.52, sat_)
        for lightness in (0.52, 0.48, 0.56, 0.44, 0.60, 0.40, 0.64, 0.36):
            cand = _from_hls(hue, lightness, sat_)
            if min(contrast(cand, bg_light), contrast(cand, bg_dark)) >= ACCENT_CONTRAST:
                return cand
            if min(contrast(cand, bg_light), contrast(cand, bg_dark)) > min(
                contrast(best, bg_light), contrast(best, bg_dark)
            ):
                best = cand
        return best

    return {
        "accent": pick(h + 0.08, min(sat, 0.75)),
        "ink": pick(h, min(sat, 0.30)),
    }


def _on_primary(primary: RGB) -> RGB:
    """White button text, unless the primary is too pale for it."""
    if contrast(WHITE, primary) >= ON_PRIMARY_MIN:
        return WHITE
    return DEFAULT_COLORS["soot"]


def resolve(brand: Brand) -> dict[str, RGB]:
    """Every colour of the palette as RGB, with the defaults filled in."""
    bg = _parse(brand.bg) or DEFAULT_COLORS["bg_light"]
    primary = _parse(brand.primary) or DEFAULT_COLORS["primary"]
    c = dict(DEFAULT_COLORS)

    light_changed = _hex(bg) != DEFAULT_BG
    dark_changed = light_changed
    if light_changed:
        c.update(_derive_light(bg))
        c.update(_derive_dark(bg))

    # An explicit dark background wins over the one derived from the light one,
    # and its card, border and text are derived from it in turn.
    dark_bg = _parse(brand.dark_bg)
    if dark_bg:
        c.update(_derive_dark(dark_bg))
        c["bg_dark"] = dark_bg
        dark_changed = True

    primary_changed = _hex(primary) != DEFAULT_PRIMARY
    if primary_changed:
        c["primary"] = primary
        c["primary_hover"] = _darken(primary, 0.10)
    # The shipped accent and secondary were picked for the shipped backgrounds,
    # so a new page colour in either mode gets new ones as well.
    if primary_changed or light_changed or dark_changed:
        c.update(_derive_accents(c["primary"], c["bg_light"], c["bg_dark"]))
    c["on_primary"] = _on_primary(c["primary"])

    # Explicit overrides win over everything derived.
    for key, name in (
        (brand.surface, "surface_light"),
        (brand.text, "fg_light"),
        (brand.dark_surface, "surface_dark"),
        (brand.dark_text, "fg_dark"),
        (brand.secondary, "ink"),
        (brand.accent, "accent"),
    ):
        parsed = _parse(key)
        if parsed:
            c[name] = parsed
            if name in ("surface_light", "fg_light"):
                light_changed = True
            elif name in ("surface_dark", "fg_dark"):
                dark_changed = True

    # Surface and text each move the muted colour, so re-derive it last.
    if light_changed:
        c["muted_light"] = _muted(c["fg_light"], c["bg_light"], c["surface_light"])
    if dark_changed:
        c["muted_dark"] = _muted(c["fg_dark"], c["bg_dark"], c["surface_dark"])
    return c


# ── output ────────────────────────────────────────────────────────────────

def build_palette(brand: Brand) -> dict[str, str]:
    """Return a dict of CSS custom property name to value (the ``:root`` block).

    Keys match the variables in ``static/input.css``.
    """
    c = resolve(brand)
    out = {var: _trip(c[key]) for key, var in _VARS.items()}
    out["--hex-primary"] = _hex(c["primary"])
    out["--hex-bg-light"] = _hex(c["bg_light"])
    out["--hex-bg-dark"] = _hex(c["bg_dark"])
    out["--hex-accent"] = _hex(c["accent"])
    return out


def dark_bg(brand: Brand) -> str:
    """The dark-mode page background as hex (used for the browser theme colour)."""
    return _hex(resolve(brand)["bg_dark"])


def palette_css(
    brand_bg: str | Brand = DEFAULT_BG,
    brand_primary: str = DEFAULT_PRIMARY,
    **optional: str,
) -> str:
    """Return the CSS that overrides the shipped palette, or "" if nothing differs.

    Takes a Brand, or the two required colours plus any optional ones by keyword
    (``secondary=``, ``dark_bg=``, ...).
    """
    brand = brand_bg if isinstance(brand_bg, Brand) else Brand(
        bg=brand_bg, primary=brand_primary, **optional
    )
    c = resolve(brand)
    lines = []
    for key, var in _VARS.items():
        if c[key] != DEFAULT_COLORS[key]:
            lines.append(f"  {var}: {_trip(c[key])};")
    if c["bg_light"] != DEFAULT_COLORS["bg_light"]:
        lines.append(f"  --hex-bg-light: {_hex(c['bg_light'])};")
    if c["bg_dark"] != DEFAULT_COLORS["bg_dark"]:
        lines.append(f"  --hex-bg-dark: {_hex(c['bg_dark'])};")
    if c["primary"] != DEFAULT_COLORS["primary"]:
        lines.append(f"  --hex-primary: {_hex(c['primary'])};")
    if c["accent"] != DEFAULT_COLORS["accent"]:
        lines.append(f"  --hex-accent: {_hex(c['accent'])};")

    blocks = []
    if lines:
        blocks.append(":root {\n" + "\n".join(lines) + "\n}")
    # Dark muted text is the one value that differs by mode on the same
    # variable, so it needs its own rule. The shipped one is in input.css.
    if c["muted_dark"] != DEFAULT_COLORS["muted_dark"]:
        blocks.append(f".dark {{ --color-muted: {_trip(c['muted_dark'])}; }}")
    return "\n".join(blocks)


@lru_cache(maxsize=128)
def brand_theme(brand: Brand) -> tuple[str, str]:
    """(palette <style> body, dark background hex) for the given brand colours.

    Keyed on the colours rather than computed once at import: they can be
    overridden from the dashboard (app/settings_store.py) while the process
    runs, and a palette baked in at startup would keep serving the old one. The
    cache makes the per-request lookup free for the colours actually in use.
    """
    return palette_css(brand), dark_bg(brand)


# ── dashboard preview and warnings ────────────────────────────────────────

def preview(brand: Brand) -> dict:
    """What the dashboard needs to draw a light and a dark sample.

    ``light`` and ``dark`` are hex colours for one sample card each.
    ``resolved`` maps each colour setting to the colour in effect, so a blank
    (auto) field can show what blank currently works out to. ``warnings`` lists
    pairs that are hard to read, as ``[mode, what]``.
    """
    c = resolve(brand)

    def sample(mode: str) -> dict[str, str]:
        return {
            "bg": _hex(c[f"bg_{mode}"]),
            "surface": _hex(c[f"surface_{mode}"]),
            "border": _hex(c[f"border_{mode}"]),
            "text": _hex(c[f"fg_{mode}"]),
            "muted": _hex(c[f"muted_{mode}"]),
            "primary": _hex(c["primary"]),
            "on_primary": _hex(c["on_primary"]),
            "accent": _hex(c["accent"]),
            "secondary": _hex(c["ink"]),
        }

    warnings: list[list[str]] = []
    for mode in ("light", "dark"):
        bg, surface = c[f"bg_{mode}"], c[f"surface_{mode}"]
        if min(contrast(c[f"fg_{mode}"], bg), contrast(c[f"fg_{mode}"], surface)) < MUTED_CONTRAST:
            warnings.append([mode, "text"])
        if min(contrast(c[f"muted_{mode}"], bg), contrast(c[f"muted_{mode}"], surface)) < 3.0:
            warnings.append([mode, "muted"])
        if contrast(bg, surface) < 1.05 and contrast(bg, c[f"border_{mode}"]) < 1.15:
            warnings.append([mode, "separation"])
    if contrast(c["on_primary"], c["primary"]) < ACCENT_CONTRAST:
        warnings.append(["both", "button"])

    return {
        "light": sample("light"),
        "dark": sample("dark"),
        "resolved": {
            "brand_primary": _hex(c["primary"]),
            "brand_secondary": _hex(c["ink"]),
            "brand_accent": _hex(c["accent"]),
            "brand_bg": _hex(c["bg_light"]),
            "brand_surface": _hex(c["surface_light"]),
            "brand_text": _hex(c["fg_light"]),
            "brand_dark_bg": _hex(c["bg_dark"]),
            "brand_dark_surface": _hex(c["surface_dark"]),
            "brand_dark_text": _hex(c["fg_dark"]),
        },
        "warnings": warnings,
    }
