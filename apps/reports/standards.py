"""COHY's official report format, and the range of choices students may make."""

# The official format. Every option here can be changed by a student unless
# REPORT_ALLOW_CUSTOM_FORMAT = False in settings.py.
SCHOOL_STANDARD = {
    'font_family': 'Times New Roman',
    'font_size': 12,
    'line_spacing': 1.5,
    'margin': 1.0,          # inches
    'target_pages': None,   # None = natural length
    'page_numbers': True,
    'include_declaration': True,
    'include_acknowledgments': True,
    'include_abstract': True,
    'include_toc': True,
    'include_charts': True,
    'include_skills': True,
    'include_appendix': True,
    'include_references': True,
    'include_signoff': True,
    'logo_on_title': True,
    'logo_in_header': False,
}

BOOLEAN_OPTIONS = [key for key, value in SCHOOL_STANDARD.items() if isinstance(value, bool)]

MIN_PAGES_GUIDELINE = 25

# Fallbacks are metric-compatible fonts, so layouts match on Linux servers too
FONT_FAMILIES = {
    'Times New Roman': '"Times New Roman", "Liberation Serif", Tinos, serif',
    'Arial': 'Arial, "Liberation Sans", Arimo, sans-serif',
    'Calibri': 'Calibri, Carlito, "Liberation Sans", sans-serif',
    'Cambria': 'Cambria, Caladea, "Liberation Serif", serif',
    'Georgia': 'Georgia, "DejaVu Serif", serif',
    'Verdana': 'Verdana, "DejaVu Sans", sans-serif',
    'Courier New': '"Courier New", "Liberation Mono", monospace',
}

FONT_SIZES = list(range(8, 25))                      # 8 to 24 pt
LINE_SPACINGS = [1.0, 1.15, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
MARGINS = [0.5, 0.75, 1.0, 1.25, 1.5]                # inches
FIT_SPACINGS = LINE_SPACINGS                         # used when fitting to a page target
MIN_TARGET, MAX_TARGET = 1, 300
MAX_CONDENSE_LEVEL = 7


def normalize_options(raw):
    """Merge saved/submitted choices onto the school standard, ignoring anything invalid."""
    raw = raw or {}
    opts = dict(SCHOOL_STANDARD)

    if raw.get('font_family') in FONT_FAMILIES:
        opts['font_family'] = raw['font_family']
    if raw.get('font_size') in FONT_SIZES:
        opts['font_size'] = int(raw['font_size'])
    if raw.get('line_spacing') in LINE_SPACINGS:
        opts['line_spacing'] = float(raw['line_spacing'])
    if raw.get('margin') in MARGINS:
        opts['margin'] = float(raw['margin'])

    if 'target_pages' in raw:
        try:
            target = int(raw['target_pages'])
        except (TypeError, ValueError):
            target = None
        opts['target_pages'] = target if target and MIN_TARGET <= target <= MAX_TARGET else None

    for key in BOOLEAN_OPTIONS:
        if key in raw:
            opts[key] = bool(raw[key])

    return opts