import base64
import io
import logging
import math
import re

logger = logging.getLogger(__name__)

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.enum.shapes import MSO_SHAPE
    PPTX_AVAILABLE = True
except Exception:
    PPTX_AVAILABLE = False


def _rgb(hex_str):
    return RGBColor.from_string(hex_str or '000000')


# ============================================================
# Bullet / text sanitisation (v12)
# ============================================================
# All bullet text flows through _sanitize_bullet() before hitting a
# shape. Strips markdown, dashes, leading markers, duplicate phrases,
# stray spaces before punctuation, and fixes lowercase 'i' and first-letter
# capitalization so slides never show raw unedited log strings.

_BULLET_LEADING_RE = re.compile(r'^[\-\*\u2022\u00B7\d\.\)]+\s*')
_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
_UNDERSCORE_RE = re.compile(r'__(.+?)__')
_ITALIC_RE = re.compile(r'(?<!\w)\*(?!\s)([^*\n]+?)\*(?!\w)')
_DASH_SEP_RE = re.compile(r'\s+[\u2013\u2014\u2012\u2015]\s+')
_HYPHEN_SEP_RE = re.compile(r'\s+-\s+')
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_ANGLE_BRACKET_RE = re.compile(r'^[<\[]+|[>\]]+$')
_DUP_PHRASE_RE = re.compile(
    r'\b(tutorial on|workshop on|meeting on|training on|session on)\s+\1\b',
    re.IGNORECASE,
)
_SPACE_BEFORE_PUNCT_RE = re.compile(r'\s+([.,;:!?])')
_LOWER_I_RE = re.compile(r'\bi\b')


def _sanitize_bullet(text):
    """
    Clean and polish a single bullet line for a PowerPoint slide.
    """
    if not text:
        return ''
    text = _HTML_TAG_RE.sub('', text)
    text = _BOLD_RE.sub(r'\1', text)
    text = _UNDERSCORE_RE.sub(r'\1', text)
    text = _ITALIC_RE.sub(r'\1', text)
    text = _DASH_SEP_RE.sub(': ', text)
    text = _HYPHEN_SEP_RE.sub(': ', text)
    text = _BULLET_LEADING_RE.sub('', text.strip())
    text = _ANGLE_BRACKET_RE.sub('', text.strip())

    while True:
        updated = _DUP_PHRASE_RE.sub(r'\1', text)
        if updated == text:
            break
        text = updated

    text = _LOWER_I_RE.sub('I', text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r'\1', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()

    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    return text


def _strip_markup(text):
    """Kept for backwards-compat — now delegates to _sanitize_bullet."""
    return _sanitize_bullet(text)


# ============================================================
# v12 — Obstacle / solution filtering for Activities & Skills slides
# ============================================================

_OBSTACLE_MARKERS_PPTX = (
    # Challenges / difficulties
    'challeng', 'difficult', 'struggl', 'obstacle', 'barrier',
    'hinder', 'disrupt', 'delay', 'congest', 'overcrowd', 'shortage',
    'lack of', 'insufficient', 'bottleneck', 'downtime', 'slowdown',
    'high patient volume', 'heavy patient volume',
    # Solutions / responses to difficulty
    'extended working hours', 'extended hours', 'long hours',
    'cope with', 'manage high', 'manage heavy', 'manage large',
    'despite the', 'in spite of', 'under pressure',
    'recommend', 'solution', 'should extend', 'should be extended',
    'streamlin', 'restructur', 'optimi',
    'assisted with', 'helped manage',
)


def _is_obstacle_bullet(text):
    """
    True if the bullet contains obstacle/solution language that must
    not appear on the Activities or Skills slide.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _OBSTACLE_MARKERS_PPTX)


def _trim_to_slide(items, max_items=6, max_chars=220):
    out = []
    for item in items:
        for line in str(item or '').splitlines():
            cleaned = _sanitize_bullet(line)
            if not cleaned:
                continue
            if len(cleaned) > max_chars:
                cleaned = cleaned[:max_chars].rstrip() + '…'
            out.append(cleaned)
            if len(out) >= max_items:
                return out
    return out


def _add_para(text_frame, text, *, size=16, bold=False, color='1A1A1A',
              font='Calibri', align=PP_ALIGN.LEFT, first=False, space_after=8, bullet=False):
    p = text_frame.paragraphs[0] if first else text_frame.add_paragraph()
    p.alignment = align
    p.space_after = Pt(space_after)
    run = p.add_run()
    run.text = f"•  {text}" if bullet else text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = font
    run.font.color.rgb = _rgb(color)
    return p


def _fill_rect(slide, left, top, width, height, color, shape=MSO_SHAPE.RECTANGLE):
    s = slide.shapes.add_shape(shape, left, top, width, height)
    s.fill.solid()
    s.fill.fore_color.rgb = _rgb(color)
    s.line.fill.background()
    return s


class PracticumPptxBuilder:
    def __init__(self, report_builder, template_key='cohy_classic'):
        from .pptx_templates import get_template
        self.rb = report_builder
        # Shallow copy so font overrides don't leak into the shared template dict.
        self.template = {**get_template(template_key)}
        self.template_key = self.template['key']
        self.prs_w = None
        self.prs_h = None
        self.show_logo = bool(self.template.get('has_logo') or self.template.get('is_him'))
        self.slides_built_count = 0
        self.ai_fallback_count = 0
        # Warnings populated during _render_content, surfaced post-build.
        self.overflow_warnings = []
        self._apply_font_overrides()

    def _apply_font_overrides(self):
        """Apply user-selected font families on top of the template defaults."""
        opts = self._opts()
        heading = (opts.get('heading_font_family') or '').strip()
        body = (opts.get('body_font_family') or '').strip()
        if heading:
            self.template['heading_font'] = heading
        if body:
            self.template['body_font'] = body

    # ---------------- pptx_options accessors ----------------
    def _opts(self):
        return (self.rb.report_obj.pptx_options
                if self.rb.report_obj and self.rb.report_obj.pptx_options
                else {}) or {}

    def _max_bullets(self):
        try:
            return max(1, int(self._opts().get('max_bullets', 6)))
        except (TypeError, ValueError):
            return 6

    def _max_chars(self):
        try:
            return max(40, int(self._opts().get('max_chars_per_bullet', 220)))
        except (TypeError, ValueError):
            return 220

    def _font_size(self):
        """Body font size (pt) for content-slide bullets. 10–44 pt range."""
        try:
            return max(10, min(44, int(self._opts().get('font_size', 18))))
        except (TypeError, ValueError):
            return 18

    def _section_enabled(self, key):
        sections = (self._opts().get('sections') or {})
        return bool(sections.get(key, True))

    # ---------------- content helpers ----------------
    def _clean_list(self, text):
        """
        Turn a multi-line block of text (e.g. skills_acquired from the
        report) into a clean list of bullet strings. Every line goes
        through _sanitize_bullet, so bold, asterisks, dashes, lowercase
        starts, and leading markers are cleaned before reaching the slide.
        """
        out = []
        for line in (text or '').splitlines():
            line = _sanitize_bullet(line)
            if line:
                out.append(line)
        return out

    def _pptx_content(self):
        return (self.rb.report_obj.pptx_content
                if self.rb.report_obj and self.rb.report_obj.pptx_content
                else {}) or {}

    def _bullets(self, key, fallback_items, max_items=None):
        """
        Return the list of bullet strings for a given slide.

        Priority:
        1. AI-drafted bullets in report.pptx_content[key], sanitised.
        2. Fallback items derived from the report text, sanitised.
        """
        max_items = max_items or self._max_bullets()
        max_items = min(max_items, self._max_bullets())
        max_chars = self._max_chars()
        raw = (self._pptx_content().get(key) or '').strip()

        # Only the Activities and Skills slides are scrubbed for obstacle text.
        strip_obstacles = key in ('pptx_activities_bullets', 'pptx_skills_bullets')

        if raw:
            out = []
            for line in raw.splitlines():
                line = _sanitize_bullet(line)
                if not line:
                    continue
                if strip_obstacles and _is_obstacle_bullet(line):
                    logger.info(
                        'PPTX: dropped obstacle bullet from %r: %s',
                        key, line[:80],
                    )
                    continue
                if len(line) > max_chars:
                    line = line[:max_chars].rstrip() + '…'
                out.append(line)
                if len(out) >= max_items:
                    break
            if out:
                return out

        # Fallback path — count it and sanitise on the way through.
        self.ai_fallback_count += 1
        fallback = _trim_to_slide(fallback_items, max_items=max_items, max_chars=max_chars)
        if strip_obstacles:
            fallback = [b for b in fallback if not _is_obstacle_bullet(b)]
        return fallback

    def _parse_speaker_notes(self):
        if not self._opts().get('include_speaker_notes', True):
            return {}
        raw = (self._pptx_content().get('pptx_speaker_notes') or '').strip()
        notes = {}
        for line in raw.splitlines():
            if ':' not in line:
                continue
            head, _, body = line.partition(':')
            head = head.strip().upper()
            body = _sanitize_bullet(body.strip())
            if head and body:
                notes[head] = body
        return notes

    def _activity_fallback_bullets(self, unit_activities):
        """
        Build clean '<Unit>: <refined activity>' bullets when AI slide
        bullets are unavailable.
        """
        bullets = []
        for group in unit_activities:
            unit = _ANGLE_BRACKET_RE.sub('', (group.get('unit') or '')).rstrip(':').strip()
            for act in group.get('activities', []):
                summary = _sanitize_bullet(act.get('summary') or '')
                if not summary or _is_obstacle_bullet(summary):
                    continue
                if summary and summary[0].isupper():
                    summary_body = summary[0].lower() + summary[1:]
                else:
                    summary_body = summary
                line = f"{unit}: {summary_body}" if unit else summary
                bullets.append(line)
        return bullets

    def _slide_specs(self):
        d = self.rb._prepare()
        content = self.rb.effective_content()
        school = self.rb._school()

        specs = []

        if self._section_enabled('slide_cover'):
            specs.append({
                'type': 'cover',
                'data': {
                    'school_name': school['name'],
                    'dept_name': self.rb.user.get_department_display(),
                    'student_name': d['name'],
                    'index_number': self.rb.user.index_number,
                    'level': self.rb.user.level,
                    'logo': school.get('title_logo'),
                },
            })

        if self._section_enabled('slide_title'):
            specs.append({
                'type': 'title',
                'data': {
                    'title': 'VACATION PRACTICUM PRESENTATION',
                    'logo': school.get('title_logo'),
                },
            })

        content_blocks = [
            ('slide_intro', 'INTRODUCTION',
                self._bullets('pptx_intro_bullets',
                              re.split(r'(?<=[.!?])\s+', content.get('introduction', ''))),
                True),
            ('slide_facility', 'FACILITY PROFILE',
                self._bullets('pptx_facility_bullets',
                              re.split(r'(?<=[.!?])\s+', content.get('facility_profile', ''))),
                True),
            ('slide_activities', 'ACTIVITIES PERFORMED',
                self._bullets('pptx_activities_bullets',
                              self._activity_fallback_bullets(d['unit_activities'])),
                True),
            ('slide_skills', 'SKILLS ACQUIRED',
                self._bullets('pptx_skills_bullets',
                              [line for line in self._clean_list(content.get('skills_acquired', ''))
                               if not _is_obstacle_bullet(line)]),
                True),
            ('slide_challenges', 'CHALLENGES FACED',
                self._bullets('pptx_challenges_bullets',
                              re.split(r'(?<=[.!?])\s+', content.get('challenges', '')),
                              max_items=2),
                True),
            ('slide_observation', 'OBSERVATION AND RECOMMENDATION',
                self._bullets('pptx_observation_recommendation_bullets',
                              re.split(r'(?<=[.!?])\s+', content.get('observation', ''))[:2]
                              + self._clean_list(content.get('facility_recs', ''))[:1]
                              + self._clean_list(content.get('academic_recs', ''))[:1],
                              max_items=4),
                True),
            ('slide_conclusion', 'CONCLUSION',
                self._bullets('pptx_conclusion_bullets',
                              re.split(r'(?<=[.!?])\s+', content.get('conclusion', ''))),
                True),
        ]

        for toggle_key, heading, items, bullet in content_blocks:
            if not self._section_enabled(toggle_key):
                continue
            specs.append({
                'type': 'content',
                'data': {'heading': heading, 'items': items, 'bullet': bullet},
            })

        if self._section_enabled('slide_thankyou'):
            specs.append({
                'type': 'thankyou',
                'data': {
                    'title': 'THANK YOU',
                    'logo': school.get('title_logo'),
                },
            })

        self.slides_built_count = len(specs)
        return specs

    def build(self):
        if not PPTX_AVAILABLE:
            raise RuntimeError("python-pptx is not installed.")

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        self.prs_w = prs.slide_width
        self.prs_h = prs.slide_height

        notes = self._parse_speaker_notes()

        for spec in self._slide_specs():
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            self._bg(slide)

            if spec['type'] == 'cover':
                self._render_cover(slide, spec['data'])
            elif spec['type'] == 'title':
                self._render_title(slide, spec['data'])
            elif spec['type'] == 'content':
                self._render_content(slide, spec['data'])
            elif spec['type'] == 'thankyou':
                self._render_thankyou(slide, spec['data'])

            if spec['type'] == 'content':
                heading = spec['data'].get('heading', '').upper()
                note = notes.get(heading)
                if note:
                    try:
                        slide.notes_slide.notes_text_frame.text = note
                    except Exception:
                        pass

        if self.overflow_warnings:
            logger.warning(
                "PPTX build produced %d slide(s) with potential overflow: %s",
                len(self.overflow_warnings),
                [w['slide'] for w in self.overflow_warnings],
            )

        buf = io.BytesIO()
        prs.save(buf)
        return buf.getvalue()

    def build_pdf(self):
        from weasyprint import HTML
        from django.template.loader import render_to_string
        from django.conf import settings

        specs = self._slide_specs()
        html = render_to_string(
            'reports/pptx_slide_preview.html',
            {
                'specs': specs,
                'template': self.template,
                'body_font_size': self._font_size(),
            },
        )
        return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()

    def _bg(self, slide):
        if self.template['style'] == 'full_bg':
            _fill_rect(slide, 0, 0, self.prs_w, self.prs_h, self.template['bg_color'])

    @staticmethod
    def _logo_stream(school):
        uri = school.get('title_logo') if school else None
        if not uri or not uri.startswith('data:image'):
            return None
        try:
            return io.BytesIO(base64.b64decode(uri.split(',', 1)[1]))
        except Exception:
            return None

    def _add_logo(self, slide, data, x, y, size, align='center'):
        if not self.show_logo:
            return
        logo_data = data.get('logo') if isinstance(data, dict) else None
        stream = self._logo_stream({'title_logo': logo_data})
        if not stream:
            return
        try:
            lw = Inches(size)
            left = (self.prs_w - lw) // 2 if align == 'center' else x
            slide.shapes.add_picture(stream, left, y, lw, lw)
        except Exception:
            pass

    def _draw_frame(self, slide):
        W, H = self.prs_w, self.prs_h
        t = self.template
        style = t.get('style', 'side_accent')

        if style == 'classic_bar':
            _fill_rect(slide, 0, H - Inches(0.65), W, Inches(0.20), t['primary_color'])
            _fill_rect(slide, 0, H - Inches(0.45), W, Inches(0.10), '000000')
            _fill_rect(slide, 0, H - Inches(0.35), W, Inches(0.35), '8B4513')

        elif style == 'side_accent':
            _fill_rect(slide, 0, 0, Inches(0.35), H, t['primary_color'])
            _fill_rect(slide, 0, H - Inches(0.14), W, Inches(0.14), t['secondary_color'])

        elif style == 'top_band':
            _fill_rect(slide, 0, 0, W, Inches(0.95), t['primary_color'])
            _fill_rect(slide, 0, Inches(0.95), W, Inches(0.08), t['secondary_color'])

        elif style == 'minimal_line':
            _fill_rect(slide, Inches(1.0), H - Inches(0.45), W - Inches(2.0),
                       Inches(0.06), t['primary_color'])

        elif style == 'full_bg':
            _fill_rect(slide, 0, H - Inches(0.20), W, Inches(0.20), t['secondary_color'])

        elif style == 'corner_triangle':
            size = Inches(1.8)
            tri = slide.shapes.add_shape(MSO_SHAPE.RIGHT_TRIANGLE, W - size, 0, size, size)
            tri.rotation = 180
            tri.fill.solid()
            tri.fill.fore_color.rgb = _rgb(t['primary_color'])
            tri.line.fill.background()
            _fill_rect(slide, 0, H - Inches(0.12), W, Inches(0.12), t['primary_color'])

        else:
            _fill_rect(slide, 0, H - Inches(0.10), W, Inches(0.10), t['primary_color'])

    def _cover_colors(self):
        t = self.template
        if t.get('style') == 'classic_bar':
            return '1A1A1A', '4A4A4A'
        return t.get('title_color', '1A1A1A'), t.get('primary_color', '1A1A1A')

    def _render_cover(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)
        self._add_logo(slide, data, 0, Inches(0.35), 1.3)
        title_color, info_color = self._cover_colors()

        _fill_rect(slide, Inches(0.6), Inches(1.85), W - Inches(1.2), Inches(0.06), t['primary_color'])
        tb = slide.shapes.add_textbox(Inches(0.6), Inches(2.0), W - Inches(1.2), Inches(1.25))
        tb.text_frame.word_wrap = True
        _add_para(tb.text_frame, data['school_name'].upper(), size=36, bold=True,
                  color=title_color, font=t['heading_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=6)
        _add_para(tb.text_frame, f"Department of {data['dept_name']}", size=26, bold=True,
                  color=title_color, font=t['heading_font'], align=PP_ALIGN.CENTER, space_after=0)
        _fill_rect(slide, Inches(0.6), Inches(3.35), W - Inches(1.2), Inches(0.06), t['primary_color'])

        tb = slide.shapes.add_textbox(Inches(0.6), Inches(3.9), W - Inches(1.2), Inches(2.2))
        tb.text_frame.word_wrap = True
        _add_para(tb.text_frame, data['student_name'].upper(), size=30, bold=True,
                  color=info_color, font=t['body_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=10)
        _add_para(tb.text_frame, (data['index_number'] or '').upper(), size=26,
                  color=info_color, font=t['body_font'], align=PP_ALIGN.CENTER, space_after=8)
        _add_para(tb.text_frame, f"LEVEL {data['level']}".upper(), size=26,
                  color=info_color, font=t['body_font'], align=PP_ALIGN.CENTER, space_after=0)

    def _render_title(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)
        self._add_logo(slide, data, 0, Inches(0.6), 1.4)
        title_color = '1A1A1A' if t.get('style') == 'classic_bar' else t.get('title_color', '1A1A1A')
        tb = slide.shapes.add_textbox(Inches(0.8), Inches(2.6), W - Inches(1.6), Inches(2.0))
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _add_para(tb.text_frame, data['title'].upper(), size=42, bold=True,
                  color=title_color, font=t['heading_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=0)

    def _estimate_content_height(self, items, font_size, space_after, box_width_pt):
        """
        Rough estimate of the rendered text-block height in points.
        Errs on the side of "might overflow" so warnings are safe.
        """
        char_width_pt = font_size * 0.52
        line_height_pt = font_size * 1.25
        total = 0
        for item in items:
            text_len = len(item) + 3  # +3 for "•  " prefix
            chars_per_line = max(1, int(box_width_pt / char_width_pt))
            lines = math.ceil(text_len / chars_per_line)
            total += lines * line_height_pt + space_after
        return total

    def _render_content(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        style = t.get('style', 'side_accent')
        is_him = (style == 'classic_bar')

        self._draw_frame(slide)

        if self.show_logo:
            logo_data = self.rb._school().get('title_logo')
            if logo_data and logo_data.startswith('data:image'):
                lw = Inches(0.60)
                ls = self._logo_stream({'title_logo': logo_data})
                if ls:
                    try:
                        slide.shapes.add_picture(ls, Inches(0.35), Inches(0.18), lw, lw)
                    except Exception:
                        pass
                rs = self._logo_stream({'title_logo': logo_data})
                if rs:
                    try:
                        slide.shapes.add_picture(rs, W - lw - Inches(0.35), Inches(0.18), lw, lw)
                    except Exception:
                        pass

        if style == 'top_band':
            heading_top = Inches(0.15)
            heading_box_h = Inches(0.70)
            heading_color = 'FFFFFF'
        else:
            heading_top = Inches(0.20)
            heading_box_h = Inches(0.75)
            heading_color = '1A1A1A' if is_him else t.get('title_color', '1A1A1A')

        tb = slide.shapes.add_textbox(Inches(1.3), heading_top, W - Inches(2.6), heading_box_h)
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _add_para(tb.text_frame, data['heading'].upper(), size=30, bold=True,
                  color=heading_color, font=t['heading_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=0)

        if is_him:
            _fill_rect(slide, Inches(0.9), Inches(1.10), W - Inches(1.8),
                       Inches(0.05), t['primary_color'])

        # Defensive: even if a caller bypasses _bullets(), sanitise here too.
        raw_items = data.get('items') or []
        items = [_sanitize_bullet(str(i)) for i in raw_items]
        items = [i for i in items if i]
        if not items:
            items = ['(No content available for this slide)']

        body_top = Inches(1.30)
        body_h = H - body_top - Inches(0.85)
        body_tb = slide.shapes.add_textbox(Inches(0.9), body_top, W - Inches(1.8), body_h)
        body_tf = body_tb.text_frame
        body_tf.word_wrap = True

        estimated_chars = sum(len(i) for i in items)
        center_vertically = len(items) <= 3 and estimated_chars < 400
        body_tf.vertical_anchor = MSO_ANCHOR.MIDDLE if center_vertically else MSO_ANCHOR.TOP

        bullet = bool(data.get('bullet'))
        body_color = t.get('text_color', '1A1A1A')
        body_size = self._font_size()
        space_after = 16 if center_vertically else 14

        # Overflow detection — estimate rendered height vs. box height.
        try:
            box_width_pt = float(W - Inches(1.8)) / 12700     # EMU → points
            box_height_pt = float(body_h) / 12700
            est_height_pt = self._estimate_content_height(items, body_size, space_after, box_width_pt)
            if est_height_pt > box_height_pt * 0.98:          # 2% tolerance
                self.overflow_warnings.append({
                    'slide': data.get('heading', 'Untitled'),
                    'items': len(items),
                    'font_size': body_size,
                    'estimated_pt': round(est_height_pt),
                    'available_pt': round(box_height_pt),
                    'overflow_pct': round(100 * (est_height_pt - box_height_pt) / box_height_pt),
                })
        except Exception:
            pass

        for i, item in enumerate(items):
            _add_para(body_tf, item, size=body_size,
                      color=body_color, font=t['body_font'],
                      first=(i == 0), bullet=bullet,
                      space_after=space_after)

    def _render_thankyou(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)
        self._add_logo(slide, data, 0, Inches(0.6), 1.4)
        title_color = '1A1A1A' if t.get('style') == 'classic_bar' else t.get('title_color', '1A1A1A')
        tb = slide.shapes.add_textbox(Inches(0.8), Inches(2.6), W - Inches(1.6), Inches(2.0))
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _add_para(tb.text_frame, data['title'], size=50, bold=True,
                  color=title_color, font=t['heading_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=0)