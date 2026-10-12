"""
TransPDF — font resolution.

Font strategy for every translated text block:
  Chinese text uses regional Source Han Serif / Sans, including real bold.
  WenKai is an optional reading style for regular Chinese text.
  Other text keeps the source / Liberation / Base14 selection below.
  1. Embedded in the source PDF — full fonts only (subset fonts skipped;
     hash/obfuscated names skipped — both are glyph-limited).
  2. Bundled Liberation TTF that matches the font family and style.
  3. Base14 fallback (helv/hebo/heit/hebi, tiro/…, cour/…).
  Translated text is checked for glyph coverage; unsupported characters use
  PyMuPDF's bundled Droid Sans Fallback, embedded in the output PDF.

Public API:
  extract_doc_fonts(doc)             → {norm_name: bytes}
  resolve_font(orig_name, flags, embedded_fonts, text="") → (alias, bytes | None)
"""

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Paths and lookup tables
# ---------------------------------------------------------------------------

FONTS_DIR = Path(__file__).parent / "fonts"

# base14 variants keyed by family and (is_bold, is_italic)
_BASE14: dict[str, dict[tuple[bool, bool], str]] = {
    "sans":  {(False, False): "helv", (True, False): "hebo",
              (False, True):  "heit", (True, True):  "hebi"},
    "serif": {(False, False): "tiro", (True, False): "tibo",
              (False, True):  "tiit", (True, True):  "tibi"},
    "mono":  {(False, False): "cour", (True, False): "cobo",
              (False, True):  "coit", (True, True):  "cobi"},
}

# Liberation font file names keyed by family and (is_bold, is_italic)
_LIBERATION: dict[str, dict[tuple[bool, bool], str]] = {
    "sans": {
        (False, False): "LiberationSans-Regular.ttf",
        (True,  False): "LiberationSans-Bold.ttf",
        (False, True):  "LiberationSans-Italic.ttf",
        (True,  True):  "LiberationSans-BoldItalic.ttf",
    },
    "serif": {
        (False, False): "LiberationSerif-Regular.ttf",
        (True,  False): "LiberationSerif-Bold.ttf",
        (False, True):  "LiberationSerif-Italic.ttf",
        (True,  True):  "LiberationSerif-BoldItalic.ttf",
    },
    "mono": {
        (False, False): "LiberationMono-Regular.ttf",
        (True,  False): "LiberationMono-Bold.ttf",
        (False, True):  "LiberationMono-Italic.ttf",
        (True,  True):  "LiberationMono-BoldItalic.ttf",
    },
}

# Font name substrings → family (first match wins)
_FAMILY_KEYWORDS: dict[str, list[str]] = {
    "sans":  ["arial", "helvetica", "verdana", "tahoma", "calibri",
              "trebuchet", "futura", "gill", "frank", "sans", "simhei",
              "heiti", "yahei", "pingfang", "gothic"],
    "serif": ["times", "georgia", "garamond", "palatino", "baskerville",
              "caslon", "minion", "cambria", "serif", "simsun", "songti",
              "mingliu", "mincho"],
    "mono":  ["courier", "consolas", "lucida", "monaco", "menlo",
              "inconsolata", "mono", "typewriter"],
}

# All family keywords flattened — used to detect hash/obfuscated font names
_ALL_FONT_KEYWORDS: list[str] = [kw for kws in _FAMILY_KEYWORDS.values() for kw in kws]

_BOLD_KEYWORDS   = ["bold", "-bd", "demi", "heavy", "black", "semibold", "extrabold"]
_ITALIC_KEYWORDS = ["italic", "oblique", "slant"]

# Lazy-loaded font bytes cache (avoids re-reading TTF files per page)
_font_bytes_cache: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_font_bytes(path: Path) -> bytes:
    key = str(path)
    if key not in _font_bytes_cache:
        _font_bytes_cache[key] = path.read_bytes()
    return _font_bytes_cache[key]


def normalize_font_name(name: str) -> str:
    """Strip ABCDEF+ subset prefix and lowercase."""
    if "+" in name:
        name = name.split("+", 1)[1]
    return name.lower().strip()


def _family_from_name(norm: str, flags: int = 0) -> str:
    """Map a normalised font name to 'sans', 'serif', or 'mono'. Default: 'sans'."""
    for family, keywords in _FAMILY_KEYWORDS.items():
        if any(kw in norm for kw in keywords):
            return family
    # PDF span flags retain useful style information for obfuscated font names.
    if flags & 8:
        return "mono"
    if flags & 4:
        return "serif"
    return "sans"


def _span_style(norm_name: str, flags: int) -> tuple[bool, bool]:
    """
    Return (is_bold, is_italic) for a span.
    Font name keywords are the primary indicator; flags are used as fallback
    when the name carries no style information (e.g. hash/obfuscated names).
    """
    name_bold   = any(x in norm_name for x in _BOLD_KEYWORDS)
    name_italic = any(x in norm_name for x in _ITALIC_KEYWORDS)
    flag_bold   = bool(flags & 16)
    flag_italic = bool(flags & 2)
    name_has_style = name_bold or name_italic
    is_bold   = name_bold   or (flag_bold   and not name_has_style)
    is_italic = name_italic or (flag_italic and not name_has_style)
    return is_bold, is_italic


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_doc_fonts(doc: fitz.Document) -> dict[str, bytes]:
    """
    Extract full embedded fonts from a PDF.
    Returns {normalised_name: font_bytes}.

    Subset fonts (basefont contains '+') are skipped — they only contain
    glyphs from the original text and would render translated characters
    as boxes.
    """
    seen: set[int] = set()
    result: dict[str, bytes] = {}
    for page in doc:
        for entry in page.get_fonts():
            xref: int    = entry[0]
            basefont: str = entry[3]
            if xref == 0 or xref in seen:
                continue
            seen.add(xref)
            if "+" in (basefont or ""):
                continue  # subset font — glyph-limited
            try:
                font_info = doc.extract_font(xref)
                if isinstance(font_info, (list, tuple)):
                    content = font_info[3] if len(font_info) > 3 else b""
                    fname   = font_info[0] if font_info else basefont
                else:
                    content = font_info.get("content", b"")
                    fname   = font_info.get("name", basefont)
                if content:
                    norm = normalize_font_name(basefont or fname or "")
                    if norm:
                        result[norm] = content
            except Exception:
                pass
    return result


def _resolve_preferred_font(
    orig_name: str,
    flags: int,
    embedded_fonts: dict[str, bytes],
) -> tuple[str, bytes | None]:
    """
    Three-tier font resolution for one text span.

    Returns (alias, font_bytes_or_None).
    - font_bytes not None → call page.insert_font(fontname=alias, fontbuffer=font_bytes)
      before using alias in insert_textbox.
    - font_bytes is None  → alias is a base14 name, usable directly.
    """
    norm    = normalize_font_name(orig_name)
    is_bold, is_italic = _span_style(norm, flags)
    variant = (is_bold, is_italic)
    family  = _family_from_name(norm, flags)

    # Tier 1: embedded full font with a recognisable name
    is_recognizable = any(kw in norm for kw in _ALL_FONT_KEYWORDS)
    if norm in embedded_fonts and is_recognizable:
        # Avoid reserved Base14 names: insert_font ignores their font buffers.
        alias = "emb_" + (re.sub(r"[^a-zA-Z0-9]", "", norm)[:20] or "font")
        return alias, embedded_fonts[norm]

    # Tier 2: bundled Liberation TTF
    filename = _LIBERATION.get(family, {}).get(variant)
    if filename:
        path = FONTS_DIR / filename
        if path.exists():
            abbr   = {"sans": "lsans", "serif": "lserif", "mono": "lmono"}[family]
            suffix = ("b" if is_bold else "") + ("i" if is_italic else "") or "r"
            return f"{abbr}_{suffix}", _load_font_bytes(path)

    # Tier 3: base14 fallback
    base14 = _BASE14.get(family, _BASE14["sans"]).get(variant, "helv")
    return base14, None


@lru_cache(maxsize=32)
def _measurement_font(alias: str, font_bytes: bytes | None) -> fitz.Font:
    """Reuse font objects for coverage checks across blocks and pages."""
    if font_bytes is not None:
        return fitz.Font(fontbuffer=font_bytes)
    return fitz.Font(fontname=alias)


def _missing_glyphs(font: fitz.Font, text: str) -> set[str]:
    # Whitespace and formatting controls do not require visible glyphs.
    return {
        char for char in set(text)
        if not char.isspace() and unicodedata.category(char) not in ("Cc", "Cf")
        and not font.has_glyph(ord(char), fallback=False)
    }


@lru_cache(maxsize=1)
def _unicode_font_data() -> tuple[fitz.Font, bytes]:
    font = fitz.Font("cjk")
    return font, font.buffer


def unicode_fallback_font(text: str = "") -> tuple[str, bytes]:
    """Return the embedded CJK font; supplemental symbols use TextWriter fallback.

    No single font covers all Unicode. Missing symbols must not abort an entire
    PDF export just because this primary CJK font does not contain them.
    """
    _, font_bytes = _unicode_font_data()
    return "unicode_cjk", font_bytes


def glyph_fallback_font(alias: str, font_bytes: bytes | None, text: str) -> fitz.Font | None:
    """Use TextWriter's automatic supplemental fonts only for uncovered glyphs."""
    font = _measurement_font(alias, font_bytes)
    # Base14 page insertion uses an 8-bit encoding even when Font.has_glyph
    # reports a glyph for a Unicode symbol. TextWriter handles that encoding.
    needs_unicode_encoding = font_bytes is None and any(ord(char) > 255 for char in text)
    return font if needs_unicode_encoding or _missing_glyphs(font, text) else None


def _has_han_characters(text: str) -> bool:
    """Recognise Han ideographs without treating Japanese kana as Chinese."""
    return any(
        0x3400 <= ord(char) <= 0x4DBF or 0x4E00 <= ord(char) <= 0x9FFF
        or 0xF900 <= ord(char) <= 0xFAFF or 0x20000 <= ord(char) <= 0x323AF
        for char in text
    )


def _chinese_region(target_lang: str) -> str:
    """Select locale-specific glyphs; generic Traditional Chinese uses TW."""
    language = target_lang.lower().replace("_", "-")
    if "hk" in language or "mo" in language:
        return "HK"
    if "tw" in language or "hant" in language:
        return "TW"
    return "CN"


def _resolve_chinese_font(
    orig_name: str, flags: int, target_lang: str, cjk_font_family: str,
) -> tuple[str, bytes] | None:
    norm = normalize_font_name(orig_name)
    family = _family_from_name(norm, flags)
    bold, _ = _span_style(norm, flags)
    region = _chinese_region(target_lang)
    style = "serif" if family == "serif" else "sans"
    if cjk_font_family in ("serif", "sans-serif"):
        style = "serif" if cjk_font_family == "serif" else "sans"

    candidates: list[tuple[str, str]] = []
    if cjk_font_family == "wenkai" and not bold:
        # WenKai has no bundled bold: retain real Source Han bold for headings.
        script_region = "GB" if region == "CN" else "TC"
        candidates.append((
            f"wenkai_{script_region.lower()}",
            f"LXGWWenKai{script_region}-Regular.1.520.ttf",
        ))
    weight = "Bold" if bold else "Regular"
    font_family = "Serif" if style == "serif" else "Sans"
    candidates.append((
        f"sourcehan_{style}_{region.lower()}_{'b' if bold else 'r'}",
        f"SourceHan{font_family}{region}-{weight}.ttf",
    ))
    for alias, filename in candidates:
        path = FONTS_DIR / filename
        if not path.exists():
            continue
        try:
            data = _load_font_bytes(path)
            _measurement_font(alias, data)
            return alias, data
        except Exception:
            # A partial installation should still be able to export via Droid.
            continue
    return None


def resolve_font(
    orig_name: str,
    flags: int,
    embedded_fonts: dict[str, bytes],
    text: str = "",
    target_lang: str = "",
    cjk_font_family: str = "auto",
) -> tuple[str, bytes | None]:
    """Preserve source style when possible, with glyph coverage for translated text.

    Pass the actual translation so a Latin source font cannot turn Chinese
    characters into missing-glyph boxes. Font buffers must be registered on the
    output page before inserting text. Omitting text retains the legacy API.
    """
    language = target_lang.lower().replace("_", "-")
    has_han = _has_han_characters(text)
    is_chinese = language == "zh" or language.startswith("zh-")
    if has_han and (not language or is_chinese):
        chinese_font = _resolve_chinese_font(orig_name, flags, target_lang, cjk_font_family)
        if chinese_font is not None:
            return chinese_font
        return unicode_fallback_font(text)

    alias, font_bytes = _resolve_preferred_font(orig_name, flags, embedded_fonts)
    if text:
        try:
            missing = _missing_glyphs(_measurement_font(alias, font_bytes), text)
            if not missing or not has_han:
                # Keep Latin styling even when a few symbols need TextWriter
                # fallback, rather than replacing the whole block with Droid.
                return alias, font_bytes
        except Exception:
            # Invalid source fonts must not silently degrade Chinese to Base14.
            pass
        return unicode_fallback_font(text)
    return alias, font_bytes
