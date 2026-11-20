"""
16 PowerPoint template definitions for the practicum presentation.

Template #1 (`cohy_classic`) is the *strict HIM* template matching the
department's official sample deck (maroon / gold, Times New Roman, classic
top-bar layout). The remaining 15 are colourful variants using 6 distinct
slide layouts: `classic_bar`, `side_accent`, `top_band`, `minimal_line`,
`full_bg`, `corner_triangle`.

Only the HIM template (`cohy_classic`) renders the college logo — all other
templates are logo-free by design (`has_logo=False`).
"""

from collections import OrderedDict


PPTX_TEMPLATES = OrderedDict()


def _t(key, name, description, **kw):
    base = {
        'key': key,
        'name': name,
        'description': description,
        'is_him': False,
        'has_logo': False,   # ✅ FIX v3: only the HIM template shows the college logo
        'bg_color': 'FFFFFF',
        'primary_color': '1A3A6C',
        'secondary_color': '4A90E2',
        'accent_color': 'E8EEF7',
        'title_color': '1A3A6C',
        'text_color': '1A1A1A',
        'heading_font': 'Calibri',
        'body_font': 'Calibri',
        'style': 'side_accent',
        'cover_style': 'centered',
    }
    base.update(kw)
    PPTX_TEMPLATES[key] = base


# ---------------------------------------------------------------- HIM strict
_t(
    'cohy_classic',
    'COHY Classic (HIM Official)',
    'Maroon & gold academic bar layout in Times New Roman — department standard.',
    is_him=True,
    has_logo=True,   # ✅ FIX v3: this is the one template that shows the logo
    bg_color='FFFFFF',
    primary_color='800000',
    secondary_color='C9A227',
    accent_color='F5E6C8',
    title_color='800000',
    text_color='1A1A1A',
    heading_font='Times New Roman',
    body_font='Times New Roman',
    style='classic_bar',
)

# ---------------------------------------------------------------- 15 colourful
_t('modern_navy', 'Modern Navy',
   'Deep navy with side accent bar and soft blue highlights.',
   primary_color='1A3A6C', secondary_color='4A90E2', accent_color='E8EEF7',
   title_color='1A3A6C', text_color='1F2C3D', style='side_accent')

_t('emerald_green', 'Emerald Green',
   'Fresh emerald band on top, clean white body.',
   primary_color='0B6E4F', secondary_color='2ECC71', accent_color='D5F5E3',
   title_color='0B6E4F', text_color='173A2E', style='top_band',
   bg_color='F7FBF9')

_t('sunset_orange', 'Sunset Orange',
   'Bold orange corner accent with warm cream background.',
   primary_color='D35400', secondary_color='F39C12', accent_color='FAD7A0',
   title_color='A04000', text_color='3E2723', style='corner_triangle',
   bg_color='FFF8F0')

_t('royal_purple', 'Royal Purple',
   'Deep purple side accent with lavender highlights.',
   primary_color='4A148C', secondary_color='8E44AD', accent_color='E8DAEF',
   title_color='4A148C', text_color='2C1B3D', style='side_accent',
   bg_color='FAF7FD')

_t('teal_minimal', 'Teal Minimal',
   'Minimal white slide with a thin teal underline.',
   primary_color='00695C', secondary_color='16A085', accent_color='D1F2EB',
   title_color='00695C', text_color='1B2F2B', style='minimal_line')

_t('rose_elegant', 'Rose Elegant',
   'Elegant rose side stripe with soft pink accents.',
   primary_color='AD1457', secondary_color='E91E63', accent_color='FCE4EC',
   title_color='AD1457', text_color='3D1A28', style='side_accent',
   bg_color='FFF7FA')

_t('graphite_dark', 'Graphite Dark',
   'Dark graphite slide with white text — high contrast.',
   primary_color='2C3E50', secondary_color='7F8C8D', accent_color='4A5D6F',
   title_color='FFFFFF', text_color='ECF0F1', style='full_bg',
   bg_color='2C3E50')

_t('gold_luxury', 'Gold Luxury',
   'Black-and-gold bar layout, refined Times New Roman.',
   primary_color='B8860B', secondary_color='D4AF37', accent_color='F5E6A8',
   title_color='1A1A1A', text_color='1A1A1A', style='classic_bar',
   heading_font='Times New Roman', body_font='Times New Roman',
   bg_color='FFFBEA')

_t('crimson_edge', 'Crimson Edge',
   'Crimson top band with cream body for strong contrast.',
   primary_color='C62828', secondary_color='EF5350', accent_color='FFEBEE',
   title_color='8E0000', text_color='2B1010', style='top_band',
   bg_color='FFF9F9')

_t('sky_clean', 'Sky Clean',
   'Light sky-blue slide with a single accent underline.',
   primary_color='0277BD', secondary_color='4FC3F7', accent_color='E1F5FE',
   title_color='01579B', text_color='102A3D', style='minimal_line',
   bg_color='F7FDFF')

_t('forest_olive', 'Forest Olive',
   'Earthy olive side accent with warm beige body.',
   primary_color='558B2F', secondary_color='8BC34A', accent_color='F1F8E9',
   title_color='33691E', text_color='1E2A16', style='side_accent',
   bg_color='FBFDF5')

_t('midnight_blue', 'Midnight Blue',
   'Deep midnight background with cyan highlights.',
   primary_color='0D1B2A', secondary_color='1B9AAA', accent_color='1F3A5F',
   title_color='FFFFFF', text_color='E8F1F5', style='full_bg',
   bg_color='0D1B2A')

_t('copper_warm', 'Copper Warm',
   'Warm copper band on soft parchment background.',
   primary_color='A0522D', secondary_color='E67E22', accent_color='FDEBD0',
   title_color='7E3F1F', text_color='3E2723', style='top_band',
   bg_color='FFF9F2')

_t('aqua_fresh', 'Aqua Fresh',
   'Bright aqua accent line on a clean white slide.',
   primary_color='00838F', secondary_color='26C6DA', accent_color='E0F7FA',
   title_color='006064', text_color='163A3D', style='minimal_line')

_t('slate_pro', 'Slate Pro',
   'Professional slate-grey side accent, neutral tones.',
   primary_color='455A64', secondary_color='90A4AE', accent_color='ECEFF1',
   title_color='263238', text_color='1E2A30', style='side_accent',
   bg_color='F9FBFC')


def get_template(key):
    return PPTX_TEMPLATES.get(key) or PPTX_TEMPLATES['cohy_classic']


def template_list():
    return [
        {'key': k, 'name': v['name'], 'description': v['description'],
         'is_him': v['is_him'],
         'has_logo': v.get('has_logo', False),   # ✅ FIX v3: exposed for UI badges
         'style': v['style'],
         'primary_color': v['primary_color'],
         'secondary_color': v['secondary_color'],
         'bg_color': v['bg_color']}
        for k, v in PPTX_TEMPLATES.items()
    ]