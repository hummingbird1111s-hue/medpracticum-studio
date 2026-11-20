import base64
import io
import logging
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


def _strip_markup(text):
    text = re.sub(r'<[^>]+>', '', text or '')
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    return text.strip()


def _trim_to_slide(items, max_items=6, max_chars=220):
    out = []
    for item in items:
        item = _strip_markup(item)
        if not item:
            continue
        if len(item) > max_chars:
            item = item[:max_chars].rstrip() + '…'
        out.append(item)
        if len(out) >= max_items:
            break
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
        self.template = get_template(template_key)
        self.template_key = self.template['key']
        self.prs_w = None
        self.prs_h = None
        self.show_logo = bool(self.template.get('has_logo') or self.template.get('is_him'))

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

    # ✅ NEW: body font size for content-slide bullets (10–28 pt, default 18)
    def _font_size(self):
        try:
            return max(10, min(28, int(self._opts().get('font_size', 18))))
        except (TypeError, ValueError):
            return 18

    def _section_enabled(self, key):
        sections = (self._opts().get('sections') or {})
        return bool(sections.get(key, True))

    # ---------------- content helpers (unchanged) ----------------
    def _clean_list(self, text):
        out = []
        for line in (text or '').splitlines():
            line = re.sub(r'^[\-\*\u2022\d\.\)]+\s*', '', line.strip())
            if line:
                out.append(line)
        return out

    def _pptx_content(self):
        return (self.rb.report_obj.pptx_content
                if self.rb.report_obj and self.rb.report_obj.pptx_content
                else {}) or {}

    def _bullets(self, key, fallback_items, max_items=None):
        max_items = max_items or self._max_bullets()
        max_items = min(max_items, self._max_bullets())
        max_chars = self._max_chars()
        raw = (self._pptx_content().get(key) or '').strip()
        if raw:
            out = []
            for line in raw.splitlines():
                line = re.sub(r'^[\-\*\u2022\d\.\)]+\s*', '', line.strip())
                if len(line) > max_chars:
                    line = line[:max_chars].rstrip() + '…'
                if line:
                    out.append(line)
                if len(out) >= max_items:
                    break
            if out:
                return out
        return _trim_to_slide(fallback_items, max_items=max_items, max_chars=max_chars)

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
            body = body.strip()
            if head and body:
                notes[head] = body
        return notes

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
                self._bullets('pptx_intro_bullets', content.get('introduction', '').split('\n\n')),
                True),
            ('slide_facility', 'FACILITY PROFILE',
                self._bullets('pptx_facility_bullets', content.get('facility_profile', '').split('\n\n')),
                True),
            ('slide_activities', 'ACTIVITIES PERFORMED',
                self._bullets('pptx_activities_bullets', [g['narrative'] for g in d['unit_activities']]),
                True),
            ('slide_skills', 'SKILLS ACQUIRED',
                self._bullets('pptx_skills_bullets', self._clean_list(content.get('skills_acquired', ''))),
                True),
            ('slide_challenges', 'CHALLENGES FACED',
                self._bullets('pptx_challenges_bullets', content.get('challenges', '').split('\n\n'), max_items=2),
                True),
            ('slide_observation', 'OBSERVATION AND RECOMMENDATION',
                self._bullets('pptx_observation_recommendation_bullets',
                              content.get('observation', '').split('\n\n')
                              + self._clean_list(content.get('facility_recs', ''))
                              + self._clean_list(content.get('academic_recs', '')),
                              max_items=4),
                True),
            ('slide_conclusion', 'CONCLUSION',
                self._bullets('pptx_conclusion_bullets', content.get('conclusion', '').split('\n\n')),
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
                # ✅ NEW: font size for the HTML preview so it matches the .pptx
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
            return '1A1A1A', '808080'
        return t.get('title_color', '1A1A1A'), t.get('primary_color', '1A1A1A')

    def _render_cover(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)
        self._add_logo(slide, data, 0, Inches(0.5), 1.5)
        title_color, info_color = self._cover_colors()

        _fill_rect(slide, Inches(0.5), Inches(2.1), W - Inches(1.0), Inches(0.08), t['primary_color'])
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(2.2), W - Inches(1.0), Inches(1.2))
        tb.text_frame.word_wrap = True
        _add_para(tb.text_frame, data['school_name'].upper(), size=40, bold=True,
                  color=title_color, font=t['heading_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=4)
        _add_para(tb.text_frame, f"Department of {data['dept_name']}", size=28, bold=True,
                  color=title_color, font=t['heading_font'], align=PP_ALIGN.CENTER, space_after=0)
        _fill_rect(slide, Inches(0.5), Inches(3.4), W - Inches(1.0), Inches(0.08), t['primary_color'])
        tb = slide.shapes.add_textbox(Inches(0.6), Inches(4.2), W - Inches(1.2), Inches(2.0))
        tb.text_frame.word_wrap = True
        _add_para(tb.text_frame, data['student_name'].upper(), size=32, bold=True,
                  color=info_color, font=t['body_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=12)
        _add_para(tb.text_frame, (data['index_number'] or '').upper(), size=28,
                  color=info_color, font=t['body_font'], align=PP_ALIGN.CENTER, space_after=8)
        _add_para(tb.text_frame, f"LEVEL {data['level']}".upper(), size=28,
                  color=info_color, font=t['body_font'], align=PP_ALIGN.CENTER, space_after=0)

    def _render_title(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)
        self._add_logo(slide, data, 0, Inches(0.5), 1.5)
        title_color = '1A1A1A' if t.get('style') == 'classic_bar' else t.get('title_color', '1A1A1A')
        tb = slide.shapes.add_textbox(Inches(0.6), Inches(3.0), W - Inches(1.2), Inches(1.5))
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _add_para(tb.text_frame, data['title'].upper(), size=44, bold=True,
                  color=title_color, font=t['heading_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=0)

    def _render_content(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        style = t.get('style', 'side_accent')
        is_him = (style == 'classic_bar')

        self._draw_frame(slide)

        if self.show_logo:
            logo_data = self.rb._school().get('title_logo')
            if logo_data and logo_data.startswith('data:image'):
                lw = Inches(0.65)
                ls = self._logo_stream({'title_logo': logo_data})
                if ls:
                    try:
                        slide.shapes.add_picture(ls, Inches(0.35), Inches(0.15), lw, lw)
                    except Exception:
                        pass
                rs = self._logo_stream({'title_logo': logo_data})
                if rs:
                    try:
                        slide.shapes.add_picture(rs, W - lw - Inches(0.35), Inches(0.15), lw, lw)
                    except Exception:
                        pass

        if style == 'top_band':
            heading_top = Inches(0.15)
            heading_box_h = Inches(0.70)
            heading_color = 'FFFFFF'
        else:
            heading_top = Inches(0.20)
            heading_box_h = Inches(0.80)
            heading_color = '1A1A1A' if is_him else t.get('title_color', '1A1A1A')

        tb = slide.shapes.add_textbox(Inches(1.4), heading_top, W - Inches(2.8), heading_box_h)
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _add_para(tb.text_frame, data['heading'].upper(), size=32, bold=True,
                  color=heading_color, font=t['heading_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=0)

        if is_him:
            _fill_rect(slide, Inches(0.9), Inches(1.15), W - Inches(1.8),
                       Inches(0.06), t['primary_color'])

        raw_items = data.get('items') or []
        items = [str(i).strip() for i in raw_items if str(i or '').strip()]
        if not items:
            items = ['(No content available for this slide)']

        body_top = Inches(1.35)
        full_body_h = H - body_top - Inches(0.8)

        estimated_chars = sum(len(i) for i in items)
        center_vertically = len(items) <= 3 and estimated_chars < 400

        # ✅ PHASE 7 — cap the centering box so short-bullet slides don't drift down.
        # Previously, centering happened in a ~5.35in tall box, so the middle of
        # that box sat at ~4.0in — well below the heading — leaving a huge gap.
        # Now the box height scales with font size: ~2.6in at 18pt, ~4.0in at 28pt,
        # ~1.44in at 10pt. Bullets start right below the divider.
        body_font_size = self._font_size()
        if center_vertically:
            base_cap_in = 2.6
            capped_in = base_cap_in * (body_font_size / 18.0)
            body_h = min(full_body_h, Inches(capped_in))
        else:
            body_h = full_body_h

        body_tb = slide.shapes.add_textbox(Inches(0.9), body_top, W - Inches(1.8), body_h)
        body_tf = body_tb.text_frame
        body_tf.word_wrap = True
        body_tf.vertical_anchor = MSO_ANCHOR.MIDDLE if center_vertically else MSO_ANCHOR.TOP

        bullet = bool(data.get('bullet'))
        body_color = t.get('text_color', '1A1A1A')
        # ✅ NEW: use the user-configured body font size (defaults to 18 pt)
        body_size = body_font_size

        for i, item in enumerate(items):
            _add_para(body_tf, item, size=body_size,
                      color=body_color, font=t['body_font'],
                      first=(i == 0), bullet=bullet,
                      space_after=16 if center_vertically else 14)

    def _render_thankyou(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)
        self._add_logo(slide, data, 0, Inches(0.5), 1.5)
        title_color = '1A1A1A' if t.get('style') == 'classic_bar' else t.get('title_color', '1A1A1A')
        tb = slide.shapes.add_textbox(Inches(0.6), Inches(3.0), W - Inches(1.2), Inches(1.5))
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _add_para(tb.text_frame, data['title'], size=54, bold=True,
                  color=title_color, font=t['heading_font'], align=PP_ALIGN.CENTER,
                  first=True, space_after=0)