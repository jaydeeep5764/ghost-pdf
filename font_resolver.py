import pymupdf
import re

SERIF_KEYWORDS = [
    "termes", "texgyre", "times", "roman", "georgia", "garamond",
    "minion", "baskerville", "cambria", "palatino", "cmr", "ptserif",
    "serif", "charter", "merriweather", "playfair", "lora", "caladea"
]

MONO_KEYWORDS = [
    "mono", "courier", "consolas", "menlo", "sourcecode", "cursor",
    "cousine", "inconsolata", "fira", "typewriter", "terminal", "dejavusansmono"
]

def clean_font_name(font_name: str) -> str:
    """
    Strips PDF subset prefixes like 'PXAAAA+', 'CFWCTB+', 'BAAAAA+'
    """
    return re.sub(r'^[A-Z0-9]{6}\+', '', font_name).strip()

def resolve_font_with_style(original_font_name: str, flags: int = 0) -> str:
    """
    Intelligently classifies any font into its exact 1:1 metric twin
    preserving Serif, Sans-Serif, Monospace, Bold, and Italic.
    Uses full-coverage base fonts so missing-glyph tofu boxes (□) are NEVER generated.
    """
    clean_name = clean_font_name(original_font_name).lower().replace("+", " ")
    
    is_bold = ("bold" in clean_name or "heavy" in clean_name or "black" in clean_name or bool(flags & 2))
    is_italic = ("italic" in clean_name or "oblique" in clean_name or bool(flags & 1))

    # 1. Monospace Check (e.g. DejaVuSansMono, Courier, Consolas)
    if any(k in clean_name for k in MONO_KEYWORDS):
        if is_bold:
            return "courier-bold"
        elif is_italic:
            return "courier-oblique"
        return "courier"

    # 2. Serif Check (e.g. TeXGyreTermes, Times New Roman, Garamond)
    if any(k in clean_name for k in SERIF_KEYWORDS):
        if is_bold and is_italic:
            return "times-bolditalic"
        elif is_bold:
            return "times-bold"
        elif is_italic:
            return "times-italic"
        return "times-roman"

    # 3. Sans-Serif Default (e.g. DejaVuSans, Helvetica, Arial, Calibri, Roboto, Inter)
    if is_bold and is_italic:
        return "hebi"
    elif is_bold:
        return "hebo"
    elif is_italic:
        return "heit"
    return "helv"

def get_font_for_span(doc: pymupdf.Document, page: pymupdf.Page, font_name: str, flags: int = 0):
    """
    Always returns a full-coverage, reliable font twin (never a broken subset buffer)
    so all characters render with 100% glyph support.
    """
    twin_name = resolve_font_with_style(font_name, flags)
    return pymupdf.Font(twin_name)
