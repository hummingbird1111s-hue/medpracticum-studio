"""COHY's official report format, and the range of choices students may make."""

# The official format. Every option here can be changed by a student unless
# REPORT_ALLOW_CUSTOM_FORMAT = False in settings.py.
SCHOOL_STANDARD = {
    'font_family': 'Times New Roman',
    'font_size': 12,
    'line_spacing': 1.5,
    'margin': 1.0,          # inches
    # target_pages: None means "no preference — use natural length".
    # COHY's 25-page guideline lives in MIN_PAGES_GUIDELINE (below) and is
    # shown to the user as a reference, NOT enforced as a default. A value
    # is only written here when the student explicitly sets one in the
    # custom format form.
    'target_pages': None,
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


def _coerce_target_pages(raw_value):
    """
    Coerce a submitted/deserialized target into None or a valid int.

    Accepts: int, numeric string ("8"), float-like ("8.0" — rejected, we
    don't round-trip partial pages), empty string, None. Anything invalid
    becomes None ("no preference").
    """
    if raw_value is None or raw_value == '':
        return None
    try:
        target = int(raw_value)
    except (TypeError, ValueError):
        return None
    return target if target and MIN_TARGET <= target <= MAX_TARGET else None


def normalize_options(raw):
    """
    Merge saved/submitted choices onto the school standard, ignoring anything
    invalid.

    Two classes of keys are handled:
      • User-facing presentation choices → validated against the allowed
        ranges; invalid values silently revert to SCHOOL_STANDARD defaults.
      • Diagnostic fields written back by the builder after generation
        (`last_page_count`, `last_spacing`, `last_page_note`) → preserved
        verbatim if present, so a normalize→save round-trip doesn't lose
        the "report ready" banner data.
    """
    raw = raw or {}
    opts = dict(SCHOOL_STANDARD)

    # ── Presentation choices (validated) ──────────────────────────────
    if raw.get('font_family') in FONT_FAMILIES:
        opts['font_family'] = raw['font_family']
    if raw.get('font_size') in FONT_SIZES:
        opts['font_size'] = int(raw['font_size'])
    if raw.get('line_spacing') in LINE_SPACINGS:
        opts['line_spacing'] = float(raw['line_spacing'])
    if raw.get('margin') in MARGINS:
        opts['margin'] = float(raw['margin'])

    # ── Page target (user's preference, or None for natural length) ───
    # The custom format form submits target_pages as a string ('8') or an
    # empty string. Either way, _coerce_target_pages produces None or int.
    if 'target_pages' in raw:
        opts['target_pages'] = _coerce_target_pages(raw['target_pages'])

    # ── Section toggles (boolean) ─────────────────────────────────────
    for key in BOOLEAN_OPTIONS:
        if key in raw:
            opts[key] = bool(raw[key])

    # ── Diagnostic fields from the last generation (preserved, not
    #    validated — they're produced by the builder, not the user) ────
    if 'last_page_count' in raw:
        opts['last_page_count'] = raw['last_page_count']
    if 'last_spacing' in raw:
        opts['last_spacing'] = raw['last_spacing']
    if 'last_page_note' in raw:
        opts['last_page_note'] = raw['last_page_note'] or ''

    return opts