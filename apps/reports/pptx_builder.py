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

    def _bullets(self, key, fallback_items, max_items=6):
        raw = (self._pptx_content().get(key) or '').strip()
        if raw:
            out = []
            for line in raw.splitlines():
                line = re.sub(r'^[\-\*\u2022\d\.\)]+\s*', '', line.strip())
                if line:
                    out.append(line)
                if len(out) >= max_items:
                    break
            if out:
                return out
        return _trim_to_slide(fallback_items, max_items=max_items)

    def _parse_speaker_notes(self):
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

        specs.append({
            'type': 'title',
            'data': {
                'title': 'VACATION PRACTICUM PRESENTATION',
                'logo': school.get('title_logo'),
            },
        })

        content_blocks = [
            ('INTRODUCTION',
                self._bullets('pptx_intro_bullets', content.get('introduction', '').split('\n\n')),
                True),
            ('FACILITY PROFILE',
                self._bullets('pptx_facility_bullets', content.get('facility_profile', '').split('\n\n')),
                True),
            ('ACTIVITIES PERFORMED',
                self._bullets('pptx_activities_bullets', [g['narrative'] for g in d['unit_activities']]),
                True),
            ('SKILLS ACQUIRED',
                self._bullets('pptx_skills_bullets', self._clean_list(content.get('skills_acquired', ''))),
                True),
            ('CHALLENGES FACED',
                self._bullets('pptx_challenges_bullets', content.get('challenges', '').split('\n\n'), max_items=2),
                True),
            # Combined Observation & Recommendation
            ('OBSERVATION AND RECOMMENDATION',
                self._bullets('pptx_observation_recommendation_bullets',
                              content.get('observation', '').split('\n\n')
                              + self._clean_list(content.get('facility_recs', ''))
                              + self._clean_list(content.get('academic_recs', '')),
                              max_items=4),
                True),
            ('CONCLUSION',
                self._bullets('pptx_conclusion_bullets', content.get('conclusion', '').split('\n\n')),
                True),
        ]

        for heading, items, bullet in content_blocks:
            specs.append({
                'type': 'content',
                'data': {'heading': heading, 'items': items, 'bullet': bullet},
            })

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
            {'specs': specs, 'template': self.template},
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

    def _draw_frame(self, slide):
        """Bottom bands: Wine/Red, Black, Thick Deep Brown (No top line)."""
        W, H = self.prs_w, self.prs_h
        t = self.template
        
        # Bottom bands layout (Total 0.65 inches high)
        # Wine/Red: 0.20 in
        # Black: 0.10 in
        # Brown/Gold (Thickest & Deepest): 0.35 in
        _fill_rect(slide, 0, H - Inches(0.65), W, Inches(0.20), t['primary_color'])
        _fill_rect(slide, 0, H - Inches(0.45), W, Inches(0.10), '000000')
        _fill_rect(slide, 0, H - Inches(0.35), W, Inches(0.35), '8B4513') # Deep Brown

    def _render_cover(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)

        logo = self._logo_stream({'title_logo': data.get('logo')})
        if logo:
            try:
                lw = Inches(1.5)
                slide.shapes.add_picture(logo, (W - lw) // 2, Inches(0.5), lw, lw)
            except Exception:
                pass

        # Line 1 (Thicker)
        _fill_rect(slide, Inches(0.5), Inches(2.1), W - Inches(1.0), Inches(0.08), t['primary_color'])

        # College Name & Dept (Size 40 & 28)
        tb = slide.shapes.add_textbox(Inches(0.5), Inches(2.2), W - Inches(1.0), Inches(1.2))
        tb.text_frame.word_wrap = True
        _add_para(tb.text_frame, data['school_name'].upper(), size=40, bold=True,
                  color='1A1A1A', font=t['heading_font'],
                  align=PP_ALIGN.CENTER, first=True, space_after=4)
        _add_para(tb.text_frame, f"Department of {data['dept_name']}", size=28, bold=True,
                  color='1A1A1A', font=t['heading_font'], align=PP_ALIGN.CENTER, space_after=0)

        # Line 2 (Thicker)
        _fill_rect(slide, Inches(0.5), Inches(3.4), W - Inches(1.0), Inches(0.08), t['primary_color'])

        # Student Info (Size 32, Bold, Ash color)
        ash_color = '808080' 
        tb = slide.shapes.add_textbox(Inches(0.6), Inches(4.2), W - Inches(1.2), Inches(2.0))
        tb.text_frame.word_wrap = True
        
        _add_para(tb.text_frame, data['student_name'].upper(), size=32, bold=True,
                  color=ash_color, font=t['body_font'],
                  align=PP_ALIGN.CENTER, first=True, space_after=12)
        
        _add_para(tb.text_frame, data['index_number'].upper() or '', size=28,
                  color=ash_color, font=t['body_font'],
                  align=PP_ALIGN.CENTER, space_after=8)
        
        _add_para(tb.text_frame, f"LEVEL {data['level']}".upper(), size=28,
                  color=ash_color, font=t['body_font'],
                  align=PP_ALIGN.CENTER, space_after=0)

    def _render_title(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)

        logo = self._logo_stream({'title_logo': data.get('logo')})
        if logo:
            try:
                lw = Inches(1.5)
                slide.shapes.add_picture(logo, (W - lw) // 2, Inches(0.5), lw, lw)
            except Exception:
                pass

        tb = slide.shapes.add_textbox(Inches(0.6), Inches(3.0), W - Inches(1.2), Inches(1.5))
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _add_para(tb.text_frame, data['title'].upper(), size=44, bold=True, color='1A1A1A',
                  font=t['heading_font'], align=PP_ALIGN.CENTER, first=True, space_after=0)

    def _render_content(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)

        # Corner logos
        logo_data = self.rb._school().get('title_logo')
        if logo_data and logo_data.startswith('data:image'):
            lw = Inches(0.65)
            left_stream = self._logo_stream({'title_logo': logo_data})
            if left_stream:
                try:
                    slide.shapes.add_picture(left_stream, Inches(0.35), Inches(0.15), lw, lw)
                except Exception:
                    pass
            right_stream = self._logo_stream({'title_logo': logo_data})
            if right_stream:
                try:
                    slide.shapes.add_picture(right_stream, W - lw - Inches(0.35), Inches(0.15), lw, lw)
                except Exception:
                    pass

        # Heading
        tb = slide.shapes.add_textbox(Inches(1.4), Inches(0.2), W - Inches(2.8), Inches(0.8))
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _add_para(tb.text_frame, data['heading'].upper(), size=32, bold=True,
                  color='1A1A1A', font=t['heading_font'],
                  align=PP_ALIGN.CENTER, first=True, space_after=0)

        # Thin maroon rule under heading
        _fill_rect(slide, Inches(0.9), Inches(1.15), W - Inches(1.8), Inches(0.06), t['primary_color'])

        # Body
        raw_items = data.get('items') or []
        items = [str(i).strip() for i in raw_items if str(i or '').strip()]
        if not items:
            items = ['(No content available for this slide)']

        body_top = Inches(1.35)
        body_h = H - body_top - Inches(0.8)
        body_tb = slide.shapes.add_textbox(Inches(0.9), body_top, W - Inches(1.8), body_h)
        body_tf = body_tb.text_frame
        body_tf.word_wrap = True

        bullet = bool(data.get('bullet'))
        for i, item in enumerate(items):
            _add_para(body_tf, item, size=18 if bullet else 17,
                      color='1A1A1A', font=t['body_font'],
                      first=(i == 0), bullet=bullet, space_after=14)

    def _render_thankyou(self, slide, data):
        W, H = self.prs_w, self.prs_h
        t = self.template
        self._draw_frame(slide)

        logo = self._logo_stream({'title_logo': data.get('logo')})
        if logo:
            try:
                lw = Inches(1.5)
                slide.shapes.add_picture(logo, (W - lw) // 2, Inches(0.5), lw, lw)
            except Exception:
                pass

        # Black text, no subtitle
        tb = slide.shapes.add_textbox(Inches(0.6), Inches(3.0), W - Inches(1.2), Inches(1.5))
        tb.text_frame.word_wrap = True
        tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _add_para(tb.text_frame, data['title'], size=54, bold=True, color='1A1A1A',
                  font=t['heading_font'], align=PP_ALIGN.CENTER, first=True, space_after=0)