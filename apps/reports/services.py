import base64
import io
import logging
import re
from collections import Counter, OrderedDict, defaultdict

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from apps.logs.department_config import get_department_config

from . import report_text as T
from .charts import bar_chart, line_chart, qr_data_uri
from .models import GeneratedReport, SchoolProfile
from .standards import (
    FIT_SPACINGS, FONT_FAMILIES, MAX_CONDENSE_LEVEL, MIN_PAGES_GUIDELINE, normalize_options,
)

logger = logging.getLogger(__name__)


# ✅ Every key that can appear in effective_content()'s output.
# build_context() and build_docx() rely on these existing, so we ALWAYS
# populate every key — empty string when no AI content and no template
# fallback exists. Prevents the KeyError: 'references' crash that hit on
# 23 Nov when AI timed out at 3/12 sections.
_CONTENT_SECTION_KEYS = (
    'abstract',
    'keywords',
    'acknowledgments',
    'introduction',
    'facility_profile',
    'activities_performed',
    'skills_acquired',
    'workshops',
    'lessons_learnt',
    'observation',
    'challenges',
    'recommendations',
    'conclusion',
    'references',
    'facility_recs',
    'academic_recs',
)


# ---------------------------------------------------------------- helpers

def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else round(number, 1)


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _paragraphs(text):
    return [p.strip() for p in re.split(r'\n\s*\n', text or '') if p.strip()]


def _lines(text):
    return [line.strip() for line in (text or '').splitlines() if line.strip()]


def _clean_list_lines(text):
    """Strips bullet points, hyphens, numbers, and asterisks from list items."""
    cleaned = []
    for line in (text or '').splitlines():
        line = line.strip()
        line = re.sub(r'^[\-\*\u2022\d\.\)]+\s*', '', line)
        if line:
            cleaned.append(line)
    return cleaned


def _first_sentence(text, limit=140):
    text = ' '.join((text or '').split())
    cut = re.split(r'(?<=[.!?])\s', text, maxsplit=1)[0]
    return cut if len(cut) <= limit else cut[:limit].rstrip() + '...'


def _has_real_sections(content):
    """True if `content` has at least one non-`_`-prefixed section key."""
    if not content:
        return False
    return any(not str(k).startswith('_') for k in content.keys())


def _is_real_pptx_bytes(data):
    """
    True if `data` starts with a ZIP magic header.

    python-pptx writes OOXML .pptx as a ZIP archive. A file starting with
    b'%PDF' is a PDF — a real .pptx can never start that way. This catches
    the "PDF saved as .pptx → uneditable in PowerPoint" bug at the source.
    """
    if not data:
        return False
    return data.startswith(b'PK\x03\x04') or data.startswith(b'PK\x05\x06')


# Markers that identify a paragraph as a *student narrative* (i.e. what a
# Facility Profile must NOT contain). Used to reject stale AI drafts.
_STUDENT_NARRATIVE_RE = re.compile(
    r'\bI\s+(?:was|am|have|had|completed|undertook|assisted|performed|entered|'
    r'used|utilized|helped|managed|worked|observed|learned|registered|booked|'
    r'provided|maintained|supported|processed|attended|noted|routinely|directly|'
    r'also|further|scheduled|verified|printed|transferred)\b',
    re.IGNORECASE,
)

# Stop words for activity dedup (rough; keeps signal words only).
_ACTIVITY_STOP_WORDS = {
    'a', 'an', 'the', 'and', 'or', 'of', 'in', 'on', 'at', 'to', 'for', 'with',
    'by', 'as', 'is', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had',
    'do', 'does', 'did', 'i', 'me', 'my', 'we', 'our', 'this', 'that', 'these',
    'those', 'from', 'into', 'within', 'also', 'other', 'some', 'various',
    'including', 'included', 'such',
}


def _activity_key_words(text):
    """Distinctive lowercase words (crude plural fold) for dedup."""
    words = re.findall(r'\w+', (text or '').lower())
    out = set()
    for w in words:
        if w in _ACTIVITY_STOP_WORDS or len(w) <= 2:
            continue
        if len(w) > 3 and w.endswith('s'):
            w = w[:-1]
        out.add(w)
    return out


def _dedupe_activities(items):
    """
    items: list of (log, summary_string).
    Removes semantic duplicates (e.g. 'patient registration' vs
    'Registration of patients') by comparing distinctive word sets.
    When two entries are duplicates, the longer (more descriptive) wins.
    """
    result = list(items)
    i = 0
    while i < len(result):
        _, sum_i = result[i]
        kw_i = _activity_key_words(sum_i)
        j = i + 1
        while j < len(result):
            _, sum_j = result[j]
            kw_j = _activity_key_words(sum_j)
            if kw_i and kw_j:
                denom = min(len(kw_i), len(kw_j))
                overlap = len(kw_i & kw_j) / denom if denom else 0.0
                if overlap >= 0.7:
                    if len(sum_j) > len(sum_i):
                        result[i] = result[j]
                        kw_i = kw_j
                    del result[j]
                    continue
            j += 1
        i += 1
    return result


def _build_activities_narrative(unit_activities):
    """
    Build the activities_performed default text from unit_activities.
    Used as fallback when the AI didn't draft this section — so the edit
    page and PDF still show the log-derived narrative instead of blank.
    """
    if not unit_activities:
        return ''
    parts = ["During the practicum period, I was involved in the following activities as recorded in my logbook:"]
    for group in unit_activities:
        parts.append(group['narrative'])
    return '\n\n'.join(parts)


def _build_recommendations_text(dynamic_facility_recs, recs):
    """
    Build the recommendations default text from log-derived solutions
    (dynamic_facility_recs) plus template recommendations. Used as fallback
    when the AI didn't draft this section.
    """
    lines = []
    if dynamic_facility_recs:
        lines.extend(dynamic_facility_recs)
    else:
        lines.extend(recs.get('facility', []))
    lines.extend(recs.get('academic', []))
    return '\n'.join(lines)


class PracticumReportBuilder:
    def __init__(self, attachment):
        self.attachment = attachment
        self.user = attachment.user
        self.facility = attachment.facility
        self.dept = get_department_config(self.user.department)
        self.logs = list(attachment.daily_logs.filter(is_deleted=False).order_by('date'))
        days = (attachment.end_date - attachment.start_date).days
        self.total_weeks = max(1, min(days // 7 + 1, 16))
        self.weeks_range = list(range(1, self.total_weeks + 1))
        self._figure_no = 0
        self._data = None
        self._charts_cache = None
        self._qr_cache = None
        self._school_cache = None
        self.result = {}

        self.report_obj = GeneratedReport.objects.filter(attachment=attachment).first()
        self.options = normalize_options(self.report_obj.options if self.report_obj else {})
        self.saved_content = (self.report_obj.content if self.report_obj and self.report_obj.content else {})

    # ---------- school branding ----------
    @staticmethod
    def _png_uri(image, box):
        copy = image.copy()
        copy.thumbnail(box)
        if copy.mode not in ('RGB', 'RGBA'):
            copy = copy.convert('RGBA')
        buf = io.BytesIO()
        copy.save(buf, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')

    def _school(self):
        """
        Resolve the school branding logo with three-source fallback:
          1. Per-report custom logo (uploaded by the student for this report)
          2. SchoolProfile.logo (uploaded via Django admin)
          3. static/branding/school-logo.png (committed to git — never missing)

        Render's free tier wipes /media/ on every deploy, so steps 1 & 2 can
        fail with FileNotFoundError. Step 3 guarantees the logo always shows.
        """
        if self._school_cache is None:
            from django.contrib.staticfiles import finders

            info = {'name': 'College of Health, Yamfo', 'motto': '', 'title_logo': None, 'header_logo': None}
            school = SchoolProfile.objects.first()

            if school:
                if school.name and 'COHY' not in school.name:
                    info['name'] = school.name
                info['motto'] = school.motto

            logo_image = None

            if self.report_obj and self.report_obj.custom_logo:
                try:
                    from PIL import Image
                    with self.report_obj.custom_logo.open('rb') as fh:
                        logo_image = Image.open(fh)
                        logo_image.load()
                except Exception as e:
                    logger.warning("Custom report logo missing or unreadable: %s", e)

            if logo_image is None and school and school.logo:
                try:
                    from PIL import Image
                    with school.logo.open('rb') as fh:
                        logo_image = Image.open(fh)
                        logo_image.load()
                except Exception as e:
                    logger.warning("School logo missing or unreadable: %s", e)

            if logo_image is None:
                static_path = finders.find('branding/school-logo.png')
                if static_path:
                    try:
                        from PIL import Image
                        logo_image = Image.open(static_path)
                        logo_image.load()
                        logger.info("Using static school logo fallback: %s", static_path)
                    except Exception as e:
                        logger.error("Static logo fallback failed: %s", e)

            if logo_image is not None:
                try:
                    info['title_logo'] = self._png_uri(logo_image, (600, 600))
                    info['header_logo'] = self._png_uri(logo_image, (220, 36))
                except Exception as e:
                    logger.error("Failed to build logo URIs: %s", e)

            self._school_cache = info
        return self._school_cache

    # ---------- cover-page metadata fallbacks ----------
    def _default_report_title(self):
        level = str(getattr(self.user, 'level', '') or '').strip()
        roman = {'100': 'I', '200': 'II', '300': 'III', '400': 'IV',
                 '1': 'I', '2': 'II', '3': 'III', '4': 'IV'}.get(level, 'I')
        return f'VACATION PRACTICUM {roman} REPORT'

    def _default_course_code(self):
        dept = getattr(self.user, 'department', '') or ''
        level = str(getattr(self.user, 'level', '') or '').strip()
        prefixes = {'HIM': 'BIHM', 'PA': 'BPA', 'MED_LAB': 'BMLS',
                    'DISEASE_CTRL': 'BDC', 'MENTAL_HEALTH': 'BMH',
                    'NUTRITION': 'BNUT', 'HEALTH_PROM': 'BHP'}
        prefix = prefixes.get(dept, 'B')
        if level and level.isdigit():
            return f'{prefix} {level[0]}15'
        return ''

    # ---------- facility profile (DB-only) ----------
    def _build_facility_profile(self):
        """
        Facility Profile is built PURELY from the facility record stored in
        the database. No student activities, no first-person narrative.
        """
        if not self.facility:
            return "The practicum was undertaken at a health facility."

        fac = self.facility
        parts = []

        opening = f"{fac.name} is a "
        opening += f"{fac.facility_type.strip().lower()}" if (fac.facility_type and fac.facility_type.strip()) else "health facility"
        if fac.district and fac.region:
            opening += f" located in the {fac.district} of the {fac.region} Region, Ghana."
        elif fac.district:
            opening += f" located in the {fac.district}."
        elif fac.region:
            opening += f" located in the {fac.region} Region, Ghana."
        else:
            opening += "."
        parts.append(opening)

        if getattr(fac, 'history_and_background', '') and fac.history_and_background.strip():
            parts.append(fac.history_and_background.strip())

        if getattr(fac, 'vision_mission', '') and fac.vision_mission.strip():
            parts.append(f"Vision and Mission: {fac.vision_mission.strip()}")

        if getattr(fac, 'organogram_description', '') and fac.organogram_description.strip():
            parts.append(f"Organisational Structure: {fac.organogram_description.strip()}")

        return '\n\n'.join(parts)

    # ---------- data ----------
    def _weeks(self):
        detail_labels = {d['key']: d['label'] for d in self.dept['details']}
        grouped = OrderedDict()
        for log in self.logs:
            grouped.setdefault(log.week_number, []).append(log)

        weeks = []
        for number, items in grouped.items():
            units = sorted({log.unit_sub_department for log in items})
            sentences, seen = [], set()
            for log in items:
                sentence = _first_sentence(log.activity_summary).rstrip('.')
                if sentence and sentence not in seen:
                    seen.add(sentence)
                    sentences.append(sentence)
                if len(sentences) == 3:
                    break
            digest = ('Main activities included: ' + '; '.join(sentences) + '.') if sentences else ''
            weeks.append({
                'number': number,
                'start': items[0].date,
                'end': items[-1].date,
                'days': len(items),
                'units': units,
                'digest': digest,
                'logs': [{
                    'date': log.date,
                    'unit': log.unit_sub_department,
                    'summary': log.activity_summary,
                    'details': [(detail_labels.get(k, k.replace('_', ' ').capitalize()), v)
                                for k, v in (log.department_details or {}).items()],
                } for log in items],
            })
        return weeks

    def _tally_stats(self):
        labels = {t['key']: t['label'] for t in self.dept['tallies']}
        totals = Counter()
        by_week = defaultdict(Counter)
        for log in self.logs:
            for key, value in (log.numeric_tallies or {}).items():
                try:
                    n = float(value)
                except (TypeError, ValueError):
                    continue
                totals[key] += n
                by_week[key][log.week_number] += n
                labels.setdefault(key, key.replace('_', ' ').capitalize())
        keys = [t['key'] for t in self.dept['tallies'] if totals.get(t['key'])]
        keys += [k for k in totals if k not in keys and totals[k]]
        return labels, totals, by_week, keys

    @staticmethod
    def _synthesize_challenges_prose(challenges):
        """
        Convert raw student challenge entries into professional academic prose.

        Used ONLY as a fallback when the AI failed to draft the 'challenges'
        section. Wraps each raw challenge in a sentence frame so the report
        never shows raw log text verbatim.
        """
        if not challenges:
            return ''

        selected = challenges[:4]
        connectives = [
            "One of the main challenges I encountered was that ",
            "I also experienced a situation where ",
            "Another obstacle was that ",
            "In addition, I had to deal with the fact that ",
        ]

        sentences = []
        for i, ch in enumerate(selected):
            raw = ch['challenge_text'].rstrip('. ').strip()
            if not raw:
                continue
            if raw[0].isupper() and len(raw) > 1:
                raw = raw[0].lower() + raw[1:]
            conn = connectives[i] if i < len(connectives) else "A further challenge was that "
            sentences.append(
                f"{conn}{raw}, which disrupted the smooth flow of work and required "
                f"me to adapt my approach to maintain service delivery."
            )

        if not sentences:
            return ''

        opening = (
            "During my attachment I encountered several challenges that required "
            "adaptability and problem-solving. "
        )
        return opening + ' '.join(sentences)

    def _prepare(self):
        if self._data is not None:
            return self._data

        s = self.attachment
        user = self.user
        dept_name = user.get_department_display()
        facility_name = self.facility.name if self.facility else 'the attachment facility'
        start = s.start_date.strftime('%d %B %Y').lstrip('0')
        end = s.end_date.strftime('%d %B %Y').lstrip('0')
        name = user.get_full_name() or user.username

        weeks = self._weeks()
        labels, totals, by_week, keys = self._tally_stats()
        unit_counter = Counter(log.unit_sub_department for log in self.logs)
        tag_counter = Counter(t for log in self.logs for t in (log.tags or []))
        total_days = len(self.logs)

        # ---- Activities Performed: dedupe + fix "unit unit" ----
        unit_groups = OrderedDict()
        for log in self.logs:
            unit_groups.setdefault(log.unit_sub_department, []).append(log)

        unit_activities = []
        for unit, items in unit_groups.items():
            raw = [(log, log.activity_summary.strip().rstrip('.')) for log in items]
            deduped = _dedupe_activities(raw)
            summaries = [summary for _, summary in deduped if summary]
            if not summaries:
                continue

            if re.search(r'\b(unit|department|ward|center|centre|clinic|division|section)\b', unit, re.IGNORECASE):
                unit_label = unit
            else:
                unit_label = f"{unit} unit"

            if len(summaries) == 1:
                narrative = f"In the {unit_label}, I was involved in {summaries[0]}."
            else:
                narrative = (
                    f"In the {unit_label}, I was involved in the following activities: "
                    + "; ".join(summaries) + "."
                )

            unit_activities.append({
                'unit': unit_label,
                'narrative': narrative,
                'activities': [{'date': log.date, 'summary': summary}
                               for (log, summary) in deduped],
            })

        all_workshops = list(self.attachment.workshops.all().order_by('date'))

        unit_rows = [{'unit': unit, 'days': days, 'pct': round(100 * days / total_days, 1)}
                     for unit, days in unit_counter.most_common()] if total_days else []

        tally_table = {
            'weeks': self.weeks_range,
            'rows': [{'label': labels[k],
                      'values': [_num(by_week[k].get(w, 0)) for w in self.weeks_range],
                      'total': _num(totals[k])} for k in keys],
        }

        results = [
            f"The student recorded {_plural(total_days, 'daily log')} across {len(weeks)} "
            f"of the {self.total_weeks} attachment weeks, working in {_plural(len(unit_counter), 'unit')}."
        ]
        if unit_counter:
            top_unit, top_days = unit_counter.most_common(1)[0]
            results.append(f"Most logged days were spent in {top_unit} ({_plural(top_days, 'day')}).")
        if keys:
            best = max(keys, key=lambda k: totals[k])
            results.append(f"Among the numerical measures recorded, {labels[best]} was highest, with a total of {_num(totals[best])}.")

        skills = [(log.date, log.skills_acquired) for log in self.logs if log.skills_acquired.strip()]

        challenges = []
        dynamic_facility_recs = []
        seen_challenges = set()
        seen_recs = set()

        for log in self.logs:
            if not log.challenges_faced.strip():
                continue
            ch = log.challenges_faced.strip().rstrip('.')
            sol = log.solutions_applied.strip().rstrip('.')

            if ch and ch.lower() not in seen_challenges:
                seen_challenges.add(ch.lower())
                ch_text = f"{ch[0].upper() + ch[1:]}."
                challenges.append({'challenge_text': ch_text, 'solution_text': ''})

            if sol and len(sol) >= 15 and sol.lower() not in seen_recs:
                seen_recs.add(sol.lower())
                rec = sol[0].upper() + sol[1:]
                if not rec.endswith('.'):
                    rec += '.'
                dynamic_facility_recs.append(rec)

        dynamic_lessons = []
        seen_lessons = set()
        for _, skill in skills:
            s_text = skill.strip()
            if s_text and s_text.lower() not in seen_lessons:
                seen_lessons.add(s_text.lower())
                dynamic_lessons.append(s_text)

        recs = T.RECOMMENDATIONS.get(user.department, T.DEFAULT_RECS)

        conclusion = [
            f"In conclusion, my practicum at {facility_name} from {start} to {end} was a highly valuable "
            f"learning experience. I was able to improve my knowledge and practical skills in {dept_name.lower()}.",
            f"Over the course of {_plural(total_days, 'day')}, I rotated through {_plural(len(unit_counter), 'unit')} "
            f"and gained hands-on experience. The practicum also helped me understand how classroom knowledge is applied in a real hospital setting.",
            f"I am grateful to the staff of {facility_name} for their guidance and support throughout this period.",
        ]

        supervisor_line = (
            f" I especially thank my supervisor, {s.assigned_supervisor_name}, for patient guidance and feedback."
            if s.assigned_supervisor_name else ''
        )
        fmt = dict(name=name, index=user.index_number, level=user.level,
                   dept=dept_name, facility=facility_name, start=start, end=end)

        top_units = ', '.join(u for u, _ in unit_counter.most_common(3))
        abstract = (
            f"This report presents the practicum attachment of {name}, a Level {user.level} student of "
            f"{dept_name}, at {facility_name} from {start} to {end}. Daily logs were kept throughout the "
            f"attachment and analysed using descriptive statistics. The student logged "
            f"{_plural(total_days, 'day')} across {_plural(len(unit_counter), 'unit')}"
            + (f", spending most time in {top_units}" if top_units else '')
            + f". Skills acquired were recorded on {_plural(len(skills), 'day')} and challenges on "
            f"{_plural(len(challenges), 'day')}, each with the action taken. The report ends with "
            "recommendations for the facility and the college."
        )
        facility_type = (self.facility.facility_type if self.facility else 'health facility').lower()
        keywords = f"practicum, clinical attachment, {dept_name.lower()}, {facility_type}"

        default_challenges = self._synthesize_challenges_prose(challenges)
        default_skills = '\n'.join(text for _, text in skills)

        facility_profile_text = self._build_facility_profile()

        # ✅ Build defaults for activities_performed and recommendations
        # so they're always available as fallback.
        default_activities = _build_activities_narrative(unit_activities)
        default_recommendations = _build_recommendations_text(dynamic_facility_recs, recs)

        defaults = {
            'abstract': abstract,
            'keywords': keywords,
            'acknowledgments': '\n\n'.join(t.format(**fmt, supervisor=supervisor_line)
                                           for t in T.ACKNOWLEDGMENTS),
            'conclusion': '\n\n'.join(conclusion),
            'facility_recs': '\n'.join(dynamic_facility_recs) if dynamic_facility_recs else '\n'.join(recs['facility']),
            'academic_recs': '\n'.join(recs['academic']),
            'references': '',
            'introduction': '\n\n'.join(getattr(T, 'INTRODUCTION_EXTRA', [])),
            'workshops': '\n\n'.join(getattr(T, 'WORKSHOPS', [])),
            'lessons_learnt': '\n'.join(dynamic_lessons) if dynamic_lessons else '\n'.join(getattr(T, 'LESSONS_LEARNT', [])),
            'observation': '\n\n'.join(getattr(T, 'OBSERVATION', [])),
            'challenges': default_challenges,
            'skills_acquired': default_skills,
            'facility_profile': facility_profile_text,
            'activities_performed': default_activities,
            'recommendations': default_recommendations,
        }

        self._data = {
            'dept_name': dept_name, 'facility_name': facility_name, 'name': name,
            'weeks': weeks, 'unit_activities': unit_activities, 'workshops': all_workshops,
            'labels': labels, 'totals': totals, 'by_week': by_week, 'keys': keys,
            'unit_counter': unit_counter, 'tag_counter': tag_counter, 'total_days': total_days,
            'unit_rows': unit_rows, 'tally_table': tally_table, 'results': results,
            'skills': skills, 'challenges': challenges,
            'declaration': T.DECLARATION.format(**fmt),
            'objectives': [t.format(**fmt) for t in T.OBJECTIVES],
            'methodology': [t.format(**fmt) for t in T.METHODOLOGY],
            'defaults': defaults,
        }
        return self._data

    def content_defaults(self):
        return self._prepare()['defaults']

    # ---------- content cleaning ----------
    def effective_content(self):
        """
        Merge template defaults with AI-drafted saved content.

        Always returns a dict containing EVERY key in _CONTENT_SECTION_KEYS.
        Missing data becomes an empty string — never absent — so callers can
        safely do content['references'] without KeyError.

        `_`-prefixed keys (e.g. `_source_hash`, `_ai_note`) are EXCLUDED
        from the returned dict. Callers who need to preserve them across
        a save must do so explicitly — see save() below.
        """
        defaults = self.content_defaults()
        saved = self.saved_content or {}

        title_variants = {
            'abstract':             ['abstract'],
            'acknowledgments':      ['acknowledgments', 'acknowledgements'],
            'introduction':         ['introduction'],
            'facility_profile':     ['facility profile', 'brief facility profile'],
            'activities_performed': ['activities performed'],
            'skills_acquired':      ['skills acquired'],
            'workshops':            ['workshops', 'workshops attended'],
            'lessons_learnt':       ['lessons learnt', 'lessons learned'],
            'observation':          ['observations', 'observation'],
            'challenges':           ['challenges', 'challenges faced'],
            'recommendations':      ['recommendations'],
            'conclusion':           ['conclusion'],
            'references':           ['references'],
        }

        result = {}

        for k in _CONTENT_SECTION_KEYS:
            default = defaults.get(k, '')

            # Facility Profile special-case: DB-only, reject student narratives.
            if k == 'facility_profile':
                saved_val = str(saved.get(k, '')).strip()
                if saved_val and not _STUDENT_NARRATIVE_RE.search(saved_val):
                    result[k] = saved_val
                else:
                    result[k] = default
                continue

            saved_val = str(saved.get(k, '')).strip()
            val = saved_val or default

            if val:
                val = re.sub(r'^[ \t]*#+[ \t]*', '', val, flags=re.MULTILINE).strip()
                for variant in title_variants.get(k, []):
                    pattern = r'^' + re.escape(variant) + r'[ \t]*\n+'
                    new_val = re.sub(pattern, '', val, count=1, flags=re.IGNORECASE)
                    if new_val != val:
                        val = new_val.strip()
                        break

            result[k] = val

        # Include extra non-private saved keys (future-proofing).
        for k, v in saved.items():
            if str(k).startswith('_') or k in result:
                continue
            sv = str(v).strip()
            if sv:
                result[k] = sv

        return result

    # ---------- charts and QR ----------
    def _figure(self, title, intro, uri):
        self._figure_no += 1
        return {'number': self._figure_no, 'title': title, 'intro': intro, 'uri': uri}

    def _get_charts(self):
        if self._charts_cache is not None:
            return self._charts_cache
        d = self._prepare()
        self._figure_no = 0
        charts = []
        if d['unit_counter']:
            items = d['unit_counter'].most_common(10)
            charts.append(self._figure(
                'Number of days logged per unit',
                'The distribution of logged days across units is shown in Figure {n}.',
                bar_chart([u for u, _ in items], [c for _, c in items], 'Days logged'),
            ))
        if d['keys']:
            labels, totals, by_week, keys = d['labels'], d['totals'], d['by_week'], d['keys']
            charts.append(self._figure(
                'Total recorded activity measures',
                'Figure {n} summarises the totals recorded for each measure over the attachment.',
                bar_chart([labels[k] for k in keys], [_num(totals[k]) for k in keys], 'Total', horizontal=True),
            ))
            for key in sorted(keys, key=lambda k: totals[k], reverse=True)[:2]:
                weekly = [_num(by_week[key].get(w, 0)) for w in self.weeks_range]
                charts.append(self._figure(
                    f'Weekly trend: {labels[key]}',
                    f'The weekly pattern for "{labels[key]}" is presented in Figure {{n}}.',
                    line_chart([f'Wk {w}' for w in self.weeks_range], weekly, labels[key]),
                ))
        if d['tag_counter']:
            items = d['tag_counter'].most_common(10)
            charts.append(self._figure(
                'Most frequent activity tags',
                'Figure {n} shows the tags used most often when describing daily activities.',
                bar_chart([t for t, _ in items], [c for _, c in items], 'Times used', horizontal=True),
            ))
        for chart in charts:
            chart['intro'] = chart['intro'].format(n=chart['number'])
        self._charts_cache = charts
        return charts

    def _verify_url(self):
        from django.core.signing import TimestampSigner
        from django.urls import reverse
        if not self.report_obj:
            return ''
        token = TimestampSigner().sign(f'report:{self.report_obj.pk}')
        path = reverse('verify_report', kwargs={'token': token})
        base = getattr(settings, 'SITE_URL', '').rstrip('/')
        return f"{base}{path}" if base else path

    def _qr(self):
        if self._qr_cache is None:
            url = self._verify_url()
            self._qr_cache = qr_data_uri(url) if url else ''
        return self._qr_cache

    def _css(self, spacing=None):
        opt = self.options
        size = opt['font_size']
        return {
            'font_stack': FONT_FAMILIES[opt['font_family']], 'font_size': size,
            'small_size': max(size - 1, 7), 'detail_size': max(size - 2, 7),
            'title_size': size + 2, 'line_height': spacing or opt['line_spacing'],
            'margin': opt['margin'],
        }

    def _features(self, level=0):
        opt = self.options
        f = {
            'title_page': True, 'declaration': opt['include_declaration'],
            'acknowledgments': opt['include_acknowledgments'],
            'abstract': opt['include_abstract'], 'toc': opt['include_toc'],
            'charts': opt['include_charts'], 'skills': opt['include_skills'],
            'appendix': opt['include_appendix'], 'references': opt['include_references'],
            'signoff': opt['include_signoff'], 'page_numbers': opt['page_numbers'],
            'logo_title': opt['logo_on_title'], 'logo_header': opt['logo_in_header'],
            'flow': False, 'compact_weeks': False, 'summary_only': False,
        }
        dropped = []
        if level >= 1:
            f['flow'] = True
            dropped.append('chapters run on without page breaks')
        if level >= 2 and f['appendix']:
            f['appendix'] = False
            dropped.append('Appendix A')
        if level >= 3 and f['charts']:
            f['charts'] = False
            dropped.append('charts')
        if level >= 4:
            f['compact_weeks'] = True
            dropped.append('daily tables shortened to weekly summaries')
        if level >= 5:
            for key, label in (('declaration', 'declaration'),
                               ('acknowledgments', 'acknowledgments'),
                               ('toc', 'table of contents'),
                               ('references', 'references page')):
                if f[key]:
                    f[key] = False
                    dropped.append(label)
        if level >= 6:
            f['title_page'] = False
            dropped.append('separate title page')
        if level >= 7:
            f['summary_only'] = True
            dropped = ['switched to a short summary report']
        return f, dropped

    def build_context(self, spacing=None, features=None):
        d = self._prepare()
        f = features or self._features(0)[0]
        content = self.effective_content()
        s = self.attachment
        school = self._school()
        charts = self._get_charts() if (f['charts'] and not f['summary_only']) else []

        report_title = (s.report_title or '').strip() or self._default_report_title()
        course_code = (s.course_code or '').strip() or self._default_course_code()
        semester = (s.semester or '').strip() or 'FIRST SEMESTER'

        table_base = 0 if f['compact_weeks'] else len(d['weeks'])
        tables, n = {}, table_base
        if d['unit_rows']: n += 1; tables['units'] = n
        if d['tally_table']['rows']: n += 1; tables['tally'] = n
        if d['challenges']: n += 1; tables['challenges'] = n

        nums, counter = {}, 0
        for key, present in (('overview', True),
                             ('units', bool(d['unit_rows'])),
                             ('tally', bool(d['tally_table']['rows'])),
                             ('charts', bool(charts)),
                             ('skills', f['skills'])):
            if present:
                counter += 1
                nums[key] = f'3.{counter}'

        references = _lines(content.get('references', ''))
        report = self.report_obj

        return {
            'f': f, 'css': self._css(spacing),
            's': s, 'student': self.user, 'facility': self.facility,
            'facility_name': d['facility_name'], 'student_name': d['name'], 'dept_name': d['dept_name'],
            'school_name': school['name'], 'school_motto': school['motto'],
            'title_logo': school['title_logo'], 'header_logo': school['header_logo'],
            'generated_on': timezone.localdate(),
            'report_title': report_title, 'course_code': course_code, 'semester': semester,
            'content': content,

            'content_skills_acquired': _clean_list_lines(content.get('skills_acquired', '')),
            'content_lessons_learnt': _clean_list_lines(content.get('lessons_learnt', '')),

            'introduction_paras': _paragraphs(content.get('introduction', '')),
            'workshops': d['workshops'], 'lessons_learnt': _lines(content.get('lessons_learnt', '')),
            'observation': _paragraphs(content.get('observation', '')), 'unit_activities': d['unit_activities'],
            'declaration': d['declaration'], 'acknowledgments': _paragraphs(content.get('acknowledgments', '')),
            'abstract_paras': _paragraphs(content.get('abstract', '')), 'keywords': content.get('keywords', ''),
            'objectives': d['objectives'], 'methodology': d['methodology'],
            'weeks': d['weeks'], 'table_base': table_base, 'tables': tables, 'nums': nums,
            'unit_rows': d['unit_rows'], 'tally_table': d['tally_table'],
            'charts': charts, 'results': d['results'], 'skills': d['skills'], 'challenges': d['challenges'],
            'top_skills': d['skills'][:5], 'top_challenges': d['challenges'][:5],
            'conclusion': _paragraphs(content.get('conclusion', '')),
            'facility_recs': _lines(content.get('facility_recs', '')),
            'academic_recs': _lines(content.get('academic_recs', '')),
            'references': references,
            'all_logs': self.logs, 'total_days': d['total_days'],
            'qr_uri': self._qr() if f['signoff'] else '', 'verify_url': self._verify_url(),
            'signed_off': bool(report and report.is_signed_off),
            'signed_date': report.supervisor_signature_date if report else None,
            'signoff_letter': 'B' if f['appendix'] else 'A',
        }

    def render_html(self, spacing=None):
        return render_to_string('reports/report.html', self.build_context(spacing))

    def _document(self, spacing, level):
        from weasyprint import HTML
        features, dropped = self._features(level)
        html = render_to_string('reports/report.html', self.build_context(spacing, features))
        document = HTML(string=html, base_url=str(settings.BASE_DIR)).render()
        return document, len(document.pages), dropped

    def build_pdf(self):
        opt = self.options
        target = opt.get('target_pages')
        spacing = opt['line_spacing']

        document, pages, dropped = self._document(spacing, 0)
        chosen = {'doc': document, 'pages': pages, 'spacing': spacing, 'level': 0, 'dropped': dropped}
        grow = bool(target) and pages < target

        if target and pages != target:
            if grow:
                steps = [(sp, 0) for sp in FIT_SPACINGS if sp > spacing]
            else:
                steps = [(sp, 0) for sp in sorted((x for x in FIT_SPACINGS if x < spacing), reverse=True)]
                steps += [(min(FIT_SPACINGS), lvl) for lvl in range(1, MAX_CONDENSE_LEVEL + 1)]

            for sp, lvl in steps:
                doc, n, drp = self._document(sp, lvl)
                candidate = {'doc': doc, 'pages': n, 'spacing': sp, 'level': lvl, 'dropped': drp}
                if grow:
                    if abs(n - target) < abs(chosen['pages'] - target):
                        chosen = candidate
                    if n >= target:
                        break
                else:
                    if n <= target:
                        chosen = candidate
                        break
                    if n < chosen['pages']:
                        chosen = candidate

        final_pages = chosen['pages']
        notes = []

        if target:
            log_count = len(self.logs)
            try:
                comp_count = self.attachment.competencies.count() if self.attachment else 0
            except Exception:
                comp_count = 0

            if final_pages == target:
                notes.append(f'Exactly {target} pages, as you asked.')
            elif final_pages < target and grow:
                if log_count < 10:
                    gap = 10 - log_count
                    notes.append(
                        f'Your report fills only {final_pages} pages at maximum spacing, '
                        f'short of your {target}-page target. Why: the AI has only '
                        f'{log_count} daily log(s) and {comp_count} competency area(s) to work '
                        f'from, which is not enough source material to generate {target} pages. '
                        f'Add at least {gap} more detailed daily logs (with specific activities, '
                        f'skills, and challenges for each unit) and regenerate to reach '
                        f'{target} pages.'
                    )
                else:
                    notes.append(
                        f'Your report fills only {final_pages} pages at maximum spacing, '
                        f'short of your {target}-page target. The AI used all available '
                        f'source material, but the draft is still shorter than {target}. '
                        f'To reach {target}, add more detailed daily logs — especially '
                        f'for units where you logged once or twice — and regenerate so '
                        f'the AI has more material to expand.'
                    )
            elif final_pages > target and not grow:
                notes.append(
                    f'The smallest we could fit is {final_pages} pages, above your '
                    f'{target}-page target. Why: your {log_count} daily entries produce more '
                    f'content than fits in {target} pages even at the tightest spacing. '
                    f'Either trim less-important logs, or accept the larger report.'
                )
            else:
                notes.append(f'The closest fit was {final_pages} pages (target {target}).')
            if chosen['dropped']:
                notes.append('To fit, we changed: ' + ', '.join(chosen['dropped']) + '.')
        else:
            if final_pages < MIN_PAGES_GUIDELINE:
                notes.append(
                    f"COHY's guideline is at least {MIN_PAGES_GUIDELINE} pages; this "
                    f"report is {final_pages}. To extend it, add more detailed daily "
                    f"logs, or set a personal target under 'Customise format' so the "
                    f"AI aims higher."
                )

        self.result = {
            'pages': final_pages, 'spacing': chosen['spacing'], 'level': chosen['level'],
            'dropped': chosen['dropped'], 'target': target, 'note': ' '.join(notes),
        }
        return chosen['doc'].write_pdf()

    @staticmethod
    def _add_rich_paragraph(doc, text, style=None):
        """
        Add a paragraph whose `<b>...</b>` and `**...**` markers render as
        real bold runs, instead of showing up as literal characters.
        """
        text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text or '')
        p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
        parts = re.split(r'(<b>|</b>)', text)
        is_bold = False
        for part in parts:
            if part == '<b>':
                is_bold = True
            elif part == '</b>':
                is_bold = False
            elif part:
                run = p.add_run(part)
                run.bold = is_bold
                run.font.name = 'Times New Roman'
        return p

    def build_docx(self):
        """Generates a Word document with the cover logo and official formatting."""
        doc = Document()

        for style_name in ['Normal', 'Heading 1', 'Heading 2', 'Heading 3', 'Title']:
            try:
                style = doc.styles[style_name]
                style.font.name = 'Times New Roman'
                style.font.color.rgb = RGBColor(0, 0, 0)
                if style_name == 'Normal':
                    style.font.size = Pt(12)
                    style.paragraph_format.line_spacing = 1.5
                    style.paragraph_format.space_after = Pt(6)
                style.element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')
            except Exception:
                pass

        # --- COVER PAGE ---
        school_info = self._school()

        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run(school_info['name'].upper())
        run.bold = True
        run.font.size = Pt(16)

        dept = doc.add_paragraph()
        dept.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = dept.add_run(f"DEPARTMENT OF {self.user.get_department_display().upper()}")
        run.bold = True
        run.font.size = Pt(13)

        doc.add_paragraph("\n")

        logo_uri = school_info.get('title_logo')
        if logo_uri and logo_uri.startswith('data:image'):
            try:
                b64_data = logo_uri.split(',')[1]
                image_bytes = base64.b64decode(b64_data)
                image_stream = io.BytesIO(image_bytes)
                doc.add_picture(image_stream, width=Inches(1.3))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            except Exception as e:
                logger.error("Failed to insert logo in DOCX: %s", e)

        doc.add_paragraph("\n")

        report_title = (self.attachment.report_title or '').strip() or self._default_report_title()
        rt = doc.add_paragraph()
        rt.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = rt.add_run(report_title)
        run.bold = True
        run.font.size = Pt(14)

        doc.add_paragraph("\n")

        name = doc.add_paragraph()
        name.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = name.add_run(self.user.get_full_name() or self.user.username)
        run.bold = True
        run.font.size = Pt(14)

        idx = doc.add_paragraph()
        idx.alignment = WD_ALIGN_PARAGRAPH.CENTER
        idx.add_run(self.user.index_number)

        doc.add_page_break()

        # --- BODY SECTIONS ---
        d = self._prepare()
        content = self.effective_content()

        def add_heading(text):
            h = doc.add_heading(text, level=1)
            for run in h.runs:
                run.font.name = 'Times New Roman'
                run.font.color.rgb = RGBColor(0, 0, 0)
            return h

        add_heading('INTRODUCTION')
        if content.get('introduction'):
            for para in _paragraphs(content['introduction']):
                self._add_rich_paragraph(doc, para)
        else:
            self._add_rich_paragraph(
                doc,
                f"This report presents the vacation practicum experience of "
                f"{self.user.get_full_name() or self.user.username}...",
            )

        add_heading('FACILITY PROFILE')
        if content.get('facility_profile'):
            for para in _paragraphs(content['facility_profile']):
                self._add_rich_paragraph(doc, para)
        else:
            if self.facility:
                self._add_rich_paragraph(
                    doc,
                    f"{self.facility.name} is a {self.facility.facility_type} located in the "
                    f"{self.facility.district} of the {self.facility.region} Region.",
                )

        add_heading('ACTIVITIES PERFORMED')
        if content.get('activities_performed'):
            for para in _paragraphs(content['activities_performed']):
                self._add_rich_paragraph(doc, para)
        else:
            self._add_rich_paragraph(
                doc,
                "During the practicum period, I was involved in the following activities as recorded in my logbook:",
            )
            for group in d['unit_activities']:
                self._add_rich_paragraph(doc, group['narrative'])

        add_heading('SKILLS ACQUIRED')
        if content.get('skills_acquired'):
            for line in _clean_list_lines(content['skills_acquired']):
                self._add_rich_paragraph(doc, line, style='List Bullet')
        else:
            for _, skill in d['skills']:
                self._add_rich_paragraph(doc, skill, style='List Bullet')

        add_heading('WORKSHOPS')
        if content.get('workshops'):
            for para in _paragraphs(content['workshops']):
                self._add_rich_paragraph(doc, para)
        else:
            self._add_rich_paragraph(doc, "No formal workshop was attended during the practicum period.")

        add_heading('LESSONS LEARNT')
        if content.get('lessons_learnt'):
            for line in _clean_list_lines(content['lessons_learnt']):
                self._add_rich_paragraph(doc, line, style='List Bullet')
        else:
            for l in _lines(content.get('lessons_learnt', '')):
                self._add_rich_paragraph(doc, l, style='List Bullet')

        add_heading('OBSERVATIONS')
        if content.get('observation'):
            for para in _paragraphs(content['observation']):
                self._add_rich_paragraph(doc, para)

        add_heading('CHALLENGES')
        if content.get('challenges'):
            for para in _paragraphs(content['challenges']):
                self._add_rich_paragraph(doc, para)
        else:
            self._add_rich_paragraph(doc, "No specific challenges were recorded.")

        add_heading('RECOMMENDATIONS')
        if content.get('recommendations'):
            for line in _clean_list_lines(content['recommendations']):
                self._add_rich_paragraph(doc, line, style='List Bullet')
        else:
            for r in _lines(content.get('facility_recs', '')):
                self._add_rich_paragraph(doc, r, style='List Bullet')
            for r in _lines(content.get('academic_recs', '')):
                self._add_rich_paragraph(doc, r, style='List Bullet')

        add_heading('CONCLUSION')
        if content.get('conclusion'):
            for para in _paragraphs(content['conclusion']):
                self._add_rich_paragraph(doc, para)

        add_heading('SIGNATURE AND DATE')
        self._add_rich_paragraph(doc, "\n\n______________________________________")
        self._add_rich_paragraph(doc, "Student's Signature")
        self._add_rich_paragraph(doc, "\n\n______________________________________")
        self._add_rich_paragraph(doc, "Date")

        if self.options.get('page_numbers', True):
            section = doc.sections[0]
            section.different_first_page_header_footer = True

            footer = section.footer
            footer.is_linked_to_previous = False
            fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
            fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in list(fp.runs):
                r._r.getparent().remove(r._r)

            run = fp.add_run()
            run.font.name = 'Times New Roman'
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(0, 0, 0)

            fld_begin = OxmlElement('w:fldChar')
            fld_begin.set(qn('w:fldCharType'), 'begin')

            instr = OxmlElement('w:instrText')
            instr.set(qn('xml:space'), 'preserve')
            instr.text = 'PAGE'

            fld_end = OxmlElement('w:fldChar')
            fld_end.set(qn('w:fldCharType'), 'end')

            run._r.append(fld_begin)
            run._r.append(instr)
            run._r.append(fld_end)

            first_footer = section.first_page_footer
            first_footer.is_linked_to_previous = False
            if first_footer.paragraphs:
                for r in list(first_footer.paragraphs[0].runs):
                    r._r.getparent().remove(r._r)

        buffer = io.BytesIO()
        doc.save(buffer)
        return buffer.getvalue()

    # ---------- PowerPoint ----------
    def build_pptx(self, template_key='cohy_classic'):
        """
        Build a real .pptx in memory and return its bytes.

        The bytes come from `PracticumPptxBuilder.build()` → `prs.save(buf)`,
        which is guaranteed to be a valid OOXML/ZIP. Callers that need to
        persist the file should use save_pptx() instead, which also writes
        the PDF preview and updates the report record.
        """
        from .pptx_builder import PracticumPptxBuilder
        return PracticumPptxBuilder(self, template_key).build()

    def save_pptx(self, template_key='cohy_classic', force_ai=False):
        """
        Build the .pptx + PDF preview, save both to the report record,
        and record the build summary.

        v8 fingerprint logic (defence-in-depth):

          If the caller already ran _ensure_pptx_content() (which views.py
          does), report.pptx_content will be fresh and this method does
          not re-draft. If called directly without that pre-step, this
          method ALSO checks the fingerprint and _has_real_sections()
          before deciding to draft — so it behaves correctly in both
          call paths.

        Magic-byte guard:
          Before saving, we assert the PPTX bytes really are ZIP/OOXML.
          If they're not (e.g. someone swapped build() to return PDF
          bytes), we fail loudly instead of shipping a file that
          Microsoft PowerPoint will open as read-only.
        """
        from .pptx_builder import PracticumPptxBuilder

        report, _ = GeneratedReport.objects.get_or_create(attachment=self.attachment)

        if force_ai:
            report.pptx_content = {}
            report.save(update_fields=['pptx_content'])

        # ---- Fingerprint-aware AI drafting (defence-in-depth) ----
        needs_pptx_draft = False
        try:
            from .ai_drafting import draft_presentation_with_ai, source_fingerprint

            current_fp = source_fingerprint(self.attachment)
            saved_fp = (report.pptx_content or {}).get('_source_hash')

            needs_pptx_draft = (
                force_ai
                or not _has_real_sections(report.pptx_content)
                or (saved_fp != current_fp)
            )

            if needs_pptx_draft:
                try:
                    drafts = draft_presentation_with_ai(self.attachment)
                    if drafts:
                        report.pptx_content = drafts   # includes '_source_hash'
                        report.save(update_fields=['pptx_content'])
                        logger.info(
                            'PPTX AI drafted %d section(s) for report %s (fingerprint=%s)',
                            len([k for k in drafts if not str(k).startswith('_')]),
                            report.pk, current_fp[:8],
                        )
                except Exception:
                    logger.exception(
                        'PPTX AI drafting failed — builder will fall back to '
                        'trimmed report text'
                    )
        except Exception:
            # If fingerprinting is unavailable for any reason, fall back to
            # the pre-v8 behaviour: draft only when content is empty.
            logger.exception(
                'PPTX fingerprint check failed — falling back to empty-content check'
            )
            if not report.pptx_content:
                try:
                    from .ai_drafting import draft_presentation_with_ai
                    drafts = draft_presentation_with_ai(self.attachment)
                    if drafts:
                        report.pptx_content = drafts
                        report.save(update_fields=['pptx_content'])
                except Exception:
                    logger.exception('PPTX AI drafting fallback also failed')

        # ---- Build ----
        builder = PracticumPptxBuilder(self, template_key)
        pptx_bytes = builder.build()
        pdf_bytes = builder.build_pdf()

        # ---- Magic-byte guard (do not ship corrupt PPTX) ----
        if not _is_real_pptx_bytes(pptx_bytes):
            preview = (pptx_bytes or b'')[:20]
            logger.error(
                'PPTX build produced non-ZIP bytes for report %s '
                '(first 20 bytes: %r). PracticumPptxBuilder.build() '
                'must return bytes from prs.save(buf).',
                report.pk, preview,
            )
            raise ValueError(
                'The presentation builder produced invalid bytes — the '
                '.pptx would open as read-only or be rejected by Microsoft '
                'PowerPoint. Check pptx_builder.PracticumPptxBuilder.build().'
            )

        opts = dict(report.pptx_options or {})
        if getattr(builder, 'overflow_warnings', None):
            opts['last_overflow_warnings'] = builder.overflow_warnings
        else:
            opts.pop('last_overflow_warnings', None)
        report.pptx_options = opts

        if report.pptx_file:
            report.pptx_file.delete(save=False)
        if report.pptx_pdf_file:
            report.pptx_pdf_file.delete(save=False)

        filename = f"practicum-presentation-{slugify(self.user.index_number or 'student')}"
        report.pptx_template = template_key
        report.pptx_file.save(f"{filename}.pptx", ContentFile(pptx_bytes), save=False)
        report.pptx_pdf_file.save(f"{filename}.pdf", ContentFile(pdf_bytes), save=True)

        # ---- Sanity log — catch accidental field swaps early ----
        if report.pptx_file and not report.pptx_file.name.endswith('.pptx'):
            logger.error(
                "pptx_file.name does not end with .pptx: %r (report=%s)",
                report.pptx_file.name, report.pk,
            )
        if report.pptx_pdf_file and not report.pptx_pdf_file.name.endswith('.pdf'):
            logger.error(
                "pptx_pdf_file.name does not end with .pdf: %r (report=%s)",
                report.pptx_pdf_file.name, report.pk,
            )

        sections = (report.pptx_options or {}).get('sections') or {}
        selected_count = sum(1 for v in sections.values() if v) if sections else None
        self._last_pptx_summary = {
            'slides_built': getattr(builder, 'slides_built_count', 0),
            'slides_selected': selected_count,
            'ai_fallback_count': getattr(builder, 'ai_fallback_count', 0),
            'template': template_key,
            'overflow_warnings': getattr(builder, 'overflow_warnings', []),
            'ai_drafted': needs_pptx_draft,
        }
        return report

    def save(self):
        pdf_bytes = self.build_pdf()
        docx_bytes = self.build_docx()

        report, _ = GeneratedReport.objects.get_or_create(attachment=self.attachment)
        if report.pdf_file:
            report.pdf_file.delete(save=False)
        if report.docx_file:
            report.docx_file.delete(save=False)

        # ✅ v8: persist the merged content (AI + defaults) so the edit page
        # shows exactly what went into this PDF — BUT preserve private
        # `_`-prefixed keys (notably `_source_hash`) so the fingerprint
        # check in views._build() can still detect staleness on the next
        # regeneration. Without this, every save() would wipe
        # `_source_hash` and force a needless AI re-draft next time.
        old_content = dict(report.content or {})
        new_content = self.effective_content()
        for k, v in old_content.items():
            if str(k).startswith('_'):
                new_content[k] = v
        report.content = new_content

        report.options = {
            **self.options,
            'last_page_count': self.result['pages'],
            'last_spacing': self.result['spacing'],
            'last_page_note': self.result.get('note', ''),
        }
        filename = f"practicum-report-{slugify(self.user.index_number)}"

        report.pdf_file.save(f"{filename}.pdf", ContentFile(pdf_bytes), save=False)
        report.docx_file.save(f"{filename}.docx", ContentFile(docx_bytes), save=True)
        return report