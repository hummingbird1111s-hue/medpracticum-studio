"""
Non-AI fallback drafter for report sections.

When the AI providers are unavailable (rate limit, network, quota), every
report section must still be drafted — never raw log copy, never empty.

Design:
  * Section-level templates, not line-level — produces file-8-style prose
  * Random variation from 2-3 templates per section → different English
    on every regeneration without changing facts
  * All helpers (past-tense, capitalisation, unit labels) are self-contained
  * Fast: a full report is drafted in < 200 ms
"""

import logging
import random
import re
from collections import OrderedDict

logger = logging.getLogger(__name__)

SCHOOL_NAME = 'the College of Health, Yamfo'   # overridable via settings


def _school_name():
    try:
        from django.conf import settings
        return getattr(settings, 'SCHOOL_NAME', SCHOOL_NAME)
    except Exception:
        return SCHOOL_NAME


# ============================================================
# Text helpers (self-contained)
# ============================================================

def _capitalise_first(line):
    line = (line or '').strip()
    return line[0].upper() + line[1:] if line else line


def _ensure_period(line):
    line = (line or '').strip()
    if line and line[-1] not in '.!?':
        line += '.'
    return line


_IRREGULAR_PAST = {
    'wrote': 'write', 'made': 'make', 'took': 'take', 'ran': 'run', 'kept': 'keep',
    'gave': 'give', 'held': 'hold', 'led': 'lead', 'met': 'meet', 'sent': 'send',
    'built': 'build', 'began': 'begin', 'did': 'do', 'saw': 'see', 'got': 'get',
    'found': 'find', 'taught': 'teach', 'learnt': 'learn', 'learned': 'learn',
    'brought': 'bring', 'read': 'read', 'put': 'put', 'set': 'set', 'cut': 'cut',
    'filed': 'file', 'drew': 'draw', 'spoke': 'speak',
}

_E_VERBS = frozenset({
    'schedule', 'update', 'create', 'use', 'calculate', 'generate', 'communicate',
    'locate', 'manage', 'capture', 'compare', 'organize', 'organise', 'retrieve',
    'archive', 'file', 'store', 'receive', 'provide', 'prepare', 'complete',
    'operate', 'navigate', 'validate', 'trace', 'observe', 'change', 'cancel',
    'issue', 'revise', 'review', 'initiate', 'indicate', 'estimate', 'translate',
    'withdraw', 'close', 'combine', 'compile', 'continue', 'determine', 'ensure',
    'explore', 'handle', 'involve', 'name', 'note', 'practise', 'practice',
    'promote', 'reschedule', 'share', 'type', 'verify', 'rotate', 'integrate',
})


def _base_verb(word):
    w = (word or '').lower()
    if w in _IRREGULAR_PAST:
        return _IRREGULAR_PAST[w]
    if w.endswith('ied') and len(w) > 4:
        return w[:-3] + 'y'
    if w.endswith('ed') and len(w) > 3:
        with_e = w[:-1]
        if with_e in _E_VERBS:
            return with_e
        base = w[:-2]
        if len(base) > 3 and base[-1] == base[-2] and base[-1] not in 'lsz':
            base = base[:-1]
        return base
    return w


def _is_past_verb(word):
    w = (word or '').lower()
    return w in _IRREGULAR_PAST or (w.endswith('ed') and len(w) > 3)


def _convert_following_verbs(text):
    def repl(m):
        joiner, word = m.group(1), m.group(2)
        if _is_past_verb(word):
            return f"{joiner} {_base_verb(word)}"
        return m.group(0)
    return re.sub(r'\b(and|then|also)\s+([A-Za-z]+)\b', repl, text)


def _polish_english(text):
    if not text:
        return ''
    text = re.sub(r'\bi\b', 'I', text)
    text = re.sub(r'\s+([.,;:!?])', r'\1', text)
    text = re.sub(r'^([a-z])', lambda m: m.group(1).upper(), text, flags=re.MULTILINE)
    text = re.sub(r'([.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip()


# Base-verb → past-tense map (for local activity lines)
_BASE_TO_PAST = {
    'register': 'registered', 'book': 'booked', 'capture': 'captured',
    'update': 'updated', 'verify': 'verified', 'print': 'printed',
    'record': 'recorded', 'prepare': 'prepared', 'calculate': 'calculated',
    'schedule': 'scheduled', 'retrieve': 'retrieved', 'file': 'filed',
    'organize': 'organized', 'organise': 'organised', 'enter': 'entered',
    'check': 'checked', 'assist': 'assisted', 'sort': 'sorted',
    'archive': 'archived', 'process': 'processed', 'handle': 'handled',
    'serve': 'served', 'attend': 'attended', 'work': 'worked',
    'use': 'used', 'manage': 'managed', 'clean': 'cleaned',
    'consult': 'consulted', 'compile': 'compiled', 'generate': 'generated',
    'write': 'wrote', 'make': 'made', 'take': 'took', 'give': 'gave',
    'do': 'did', 'go': 'went', 'come': 'came', 'put': 'put',
    'keep': 'kept', 'send': 'sent', 'find': 'found', 'get': 'got',
    'see': 'saw', 'run': 'ran', 'read': 'read', 'type': 'typed',
    'review': 'reviewed', 'verify': 'verified', 'transfer': 'transferred',
}


def _to_past_start(text):
    """Ensure the first word is past-tense. 'register patients' -> 'Registered patients.'"""
    text = ' '.join((text or '').split()).strip()
    if not text:
        return ''
    text = re.sub(r'^(?:i\s+(?:was\s+)?|we\s+|they\s+)', '', text, flags=re.IGNORECASE).strip()
    if not text:
        return ''
    first, _, rest = text.partition(' ')
    first_l = first.lower().strip('.,;:')
    if first_l in _BASE_TO_PAST:
        first = _BASE_TO_PAST[first_l]
    elif first_l.endswith('ing') and len(first_l) > 4:
        stem = first_l[:-3]
        if stem.endswith('e'):
            first = stem + 'd'
        elif len(stem) > 3 and stem[-1] == stem[-2] and stem[-1] not in 'lsz':
            first = stem[:-1] + 'ed'
        else:
            first = stem + 'ed'
    text = (first + ' ' + rest).strip() if rest else first
    return _ensure_period(_capitalise_first(text))


# Enrichers — brief true-to-context clauses to append when the log is bare
_ENRICHERS = (
    (r'\bregist(?:er|ered|ering)\b', 'entering their demographic details into the records system'),
    (r'\bbook(?:ed|ing)?\b',         'scheduling appointments to support patient flow'),
    (r'\bverif(?:y|ied|ying)\b',     'confirming patient information against existing records'),
    (r'\bupdat(?:e|ed|ing)\b',       'ensuring records reflected the most current patient information'),
    (r'\bfil(?:e|ed|ing)\b',         'maintaining the order and integrity of the filing system'),
    (r'\bretriev(?:e|ed|ing)\b',     'locating records quickly for clinical staff use'),
    (r'\battend(?:ed|ing)?\b',       'participating in the day-to-day activities of the unit'),
)


def _enrich_activity(sentence, original_log_word_count):
    """Only enrich if the log was very brief (< 12 words) — avoids padding."""
    if original_log_word_count >= 12:
        return sentence
    lowered = sentence.lower()
    for pattern, clause in _ENRICHERS:
        if re.search(pattern, lowered) and ' by ' not in lowered:
            base = sentence.rstrip('.').rstrip()
            if ' in ' in base or ' using ' in base or ' with ' in base:
                return sentence   # already has context
            return f"{base}, {clause}."
    return sentence


# ============================================================
# Formatting helpers
# ============================================================

_UNIT_ALIASES = {
    'rch': 'Reproductive and Child Health (RCH) Unit',
    'reproductive and child health': 'Reproductive and Child Health (RCH) Unit',
    'reproductive child health': 'Reproductive and Child Health (RCH) Unit',
    'mch': 'Maternal and Child Health (MCH) Unit',
    'maternal and child health': 'Maternal and Child Health (MCH) Unit',
    'opd': 'Outpatient Department (OPD)',
    'out patient': 'Outpatient Department (OPD)',
    'outpatient': 'Outpatient Department (OPD)',
    'out patient department': 'Outpatient Department (OPD)',
    'outpatient department': 'Outpatient Department (OPD)',
    'medical records': 'Records Unit',
    'records': 'Records Unit',
    'record': 'Records Unit',
    'health information': 'Records Unit',
}
_UNIT_NOISE = frozenset({'unit', 'department', 'dept', 'section', 'the'})


def _canonical_unit_key(name):
    text = re.sub(r'[()\[\]<>.,:;/&-]', ' ', (name or '').lower())
    return ' '.join(w for w in text.split() if w not in _UNIT_NOISE)


def _canonical_unit_label(name):
    cleaned = re.sub(r'^[<\[\s]+|[>\]\s]+$', '', (name or '')).strip().rstrip(':')
    key = _canonical_unit_key(cleaned)
    if key in _UNIT_ALIASES:
        return _UNIT_ALIASES[key]
    if cleaned.islower():
        return cleaned.title()
    return cleaned or 'General Duties'


def _ordinal_date(value):
    """Date → '4th November 2026'."""
    try:
        day = value.day
        suffix = 'th' if 11 <= day % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        return f"{day}{suffix} {value.strftime('%B %Y')}"
    except Exception:
        return str(value or '')


def _fmt_date(value):
    try:
        return _ordinal_date(value)
    except Exception:
        return str(value or '')


# ============================================================
# Data extraction from session
# ============================================================

def _units(session):
    """Ordered list of (canonical label, day count)."""
    units, order = {}, []
    try:
        logs = session.daily_logs.filter(is_deleted=False).order_by('date')
        for log in logs:
            label = _canonical_unit_label(getattr(log, 'unit_sub_department', '') or '')
            if not label:
                continue
            key = _canonical_unit_key(label)
            if key not in units:
                units[key] = [label, 0]
                order.append(key)
            units[key][1] += 1
    except Exception:
        pass
    return [tuple(units[k]) for k in order]


def _competencies(session):
    try:
        return [str(c).strip() for c in session.competencies.values_list('text', flat=True) if c]
    except Exception:
        return []


def _workshops(session):
    try:
        return list(session.workshops.all().order_by('date'))
    except Exception:
        return []


def _logs(session):
    try:
        return list(session.daily_logs.filter(is_deleted=False).order_by('date'))
    except Exception:
        return []


def _user_info(session):
    user = getattr(session, 'user', None)
    if user is None:
        return {'name': 'the student', 'level': '', 'dept': 'Health Information Management'}
    name = (user.get_full_name() or user.username) if hasattr(user, 'get_full_name') else str(user)
    try:
        dept = user.get_department_display()
    except Exception:
        dept = getattr(user, 'department', '') or 'Health Information Management'
    return {'name': name, 'level': getattr(user, 'level', '') or '', 'dept': dept}


def _facility_info(session):
    fac = getattr(session, 'facility', None)
    if fac is None:
        return {'name': 'the attachment facility', 'location': ''}
    name = getattr(fac, 'name', '') or 'the attachment facility'
    district = getattr(fac, 'district', '') or ''
    region = getattr(fac, 'region', '') or ''
    location = ', '.join(p for p in [district, f'{region} Region' if region else '', 'Ghana'] if p)
    return {'name': name, 'location': location or 'Ghana'}


# ============================================================
# Section drafters
# ============================================================

def _draft_introduction(session):
    u = _user_info(session)
    f = _facility_info(session)
    start = _fmt_date(getattr(session, 'start_date', ''))
    end = _fmt_date(getattr(session, 'end_date', ''))
    units = _units(session)
    comps = _competencies(session)

    unit_text = ' and '.join(u[0] for u in units) if units else 'the health information units of the facility'
    if len(units) > 2:
        unit_text = ', '.join(u[0] for u in units[:-1]) + ', and ' + units[-1][0]
    comp_text = ', '.join(comps[:3]) if comps else 'patient registration, records management, and data reporting'

    templates = [
        f"I am {u['name']}, a Level {u['level']} {u['dept']} student at {_school_name()}. "
        f"I undertook my clinical attachment at {f['name']}{(' in ' + f['location']) if f['location'] else ''}, "
        f"from {start} to {end}. During this period, I rotated through {unit_text}, where I gained "
        f"practical experience in {comp_text}. The work was carried out with the support and "
        f"supervision of the facility's staff, whose guidance shaped my understanding of health "
        f"information practice in a real clinical environment.",

        f"My name is {u['name']}, and I am a Level {u['level']} {u['dept']} student at {_school_name()}. "
        f"The clinical attachment I undertook at {f['name']}{(' in ' + f['location']) if f['location'] else ''} "
        f"took place between {start} and {end}. My rotations through {unit_text} exposed me to "
        f"{comp_text}, giving me hands-on experience in the day-to-day operations of a health "
        f"information department. I completed the placement under the close supervision of the "
        f"facility's staff, whose mentorship enriched my professional development.",

        f"This report documents the clinical attachment I, {u['name']}, a Level {u['level']} "
        f"{u['dept']} student of {_school_name()}, completed at {f['name']}"
        f"{(' in ' + f['location']) if f['location'] else ''} from {start} to {end}. "
        f"In the course of the attachment, I worked in {unit_text} and gained practical "
        f"exposure to {comp_text}. Throughout the period, I worked under the guidance of "
        f"experienced staff, whose supervision enriched my understanding of health information "
        f"management as applied in a hospital setting.",
    ]
    return random.choice(templates)


def _draft_activities(session):
    logs = _logs(session)
    if not logs:
        return ''

    by_unit = OrderedDict()
    for log in logs:
        unit = _canonical_unit_label(getattr(log, 'unit_sub_department', '') or '') or 'General Duties'
        activity = (getattr(log, 'activity_summary', '') or '').strip()
        if not activity:
            continue
        raw_word_count = len(activity.split())
        line = _to_past_start(activity)
        if not line:
            continue
        line = _enrich_activity(line, raw_word_count)
        by_unit.setdefault(unit, []).append(line)

    if not by_unit:
        return ''

    parts = []
    for unit, lines in by_unit.items():
        # dedupe within unit
        seen, uniq = set(), []
        for l in lines:
            if l.lower() not in seen:
                seen.add(l.lower())
                uniq.append(l)
        parts.append(unit)
        parts.extend(uniq)
        parts.append('')
    return '\n'.join(parts).strip()


def _draft_skills(session):
    logs = _logs(session)
    if not logs:
        return ''

    lines = []
    seen = set()
    for log in logs:
        raw = (getattr(log, 'skills_acquired', '') or '').strip()
        if not raw:
            continue
        # 'i registered patients and booked appointments'
        text = ' '.join(raw.split()).rstrip('.').strip()
        text = re.sub(r'^(?:i\s+(?:was able to|learnt how to|learned how to|gained)\s+)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^(?:i\s+)', '', text, flags=re.IGNORECASE)
        if not text:
            continue
        first, _, rest = text.partition(' ')
        first_l = first.lower().strip('.,;:')
        if _is_past_verb(first_l):
            base = _base_verb(first_l)
            rest = _convert_following_verbs(rest)
            line = f"I learnt how to {base} {rest}".strip() + '.'
        else:
            line = f"I gained knowledge of {text[0].lower() + text[1:]}" + '.'
        line = _polish_english(line)
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)

    # fallback: derive skills from activities if none logged
    if not lines:
        for log in logs[:6]:
            raw = (getattr(log, 'activity_summary', '') or '').strip()
            if not raw:
                continue
            text = re.sub(r'^(?:i\s+)', '', raw, flags=re.IGNORECASE).strip()
            first, _, rest = text.partition(' ')
            base = _base_verb(first.lower()) if _is_past_verb(first.lower()) else first.lower()
            line = _polish_english(f"I learnt how to {base} {rest}".strip() + '.')
            if line.lower() in seen:
                continue
            seen.add(line.lower())
            lines.append(line)

    return '\n'.join(lines[:8])


def _draft_workshops(session):
    workshops = _workshops(session)
    if not workshops:
        return (
            "No formal workshop or training session was recorded during the attachment. "
            "However, I received practical guidance from the staff of the units I worked "
            "in, and this on-the-job support contributed significantly to my learning "
            "throughout the practicum."
        )

    paras = []
    for w in workshops:
        date = _fmt_date(getattr(w, 'date', ''))
        wtype = (getattr(w, 'type', '') or 'session').strip().lower()
        topic = ' '.join((getattr(w, 'activity', '') or '').split()).strip().rstrip('.')
        topic = re.sub(r'^(?:on\s+)', '', topic, flags=re.IGNORECASE)
        wtype_label = 'workshop' if 'workshop' in wtype else ('tutorial' if 'tutorial' in wtype else 'training session')
        paras.append(
            f"On {date}, I attended a {wtype_label} on {topic[0].lower() + topic[1:] if topic else 'the use of health information systems'}. "
            f"The session reinforced the importance of accuracy, confidentiality and proper "
            f"record handling in health information work, and it supported the day-to-day "
            f"activities I carried out in the unit."
        )
    return '\n\n'.join(paras)


def _draft_lessons(session):
    u = _user_info(session)
    units = _units(session)
    comps = _competencies(session)
    logs = _logs(session)

    has_challenges = any((getattr(l, 'challenges_faced', '') or '').strip() for l in logs)
    unit_summary = (units[0][0] if len(units) == 1 else 'the various units I rotated through')
    comp_summary = ', '.join(comps[:2]) if comps else 'accurate data capture and patient confidentiality'

    templates = [
        f"The practicum at {_facility_info(session)['name']} gave me a clear picture of how "
        f"health information work is carried out in a real hospital. Through my rotations in "
        f"{unit_summary}, I came to appreciate that the accuracy of patient information "
        f"recorded at the point of registration affects every subsequent stage of care — from "
        f"booking to clinical decision-making and, ultimately, to how the facility reports on "
        f"the services it provides. I also came to understand that confidentiality is not an "
        f"abstract rule but a daily discipline. Classroom theory on {comp_summary} came alive "
        f"as I saw how these principles guide practice. I leave the attachment with a stronger "
        f"commitment to data integrity and a clearer sense of what professional practice in "
        f"health information management demands.",

        f"My time at {_facility_info(session)['name']} deepened my understanding of how the "
        f"principles taught in the classroom translate into hospital practice. Working in "
        f"{unit_summary}, I learnt that accurate patient information is not just a clerical "
        f"task — it determines the quality of care, the reliability of reports, and the trust "
        f"that patients place in the health system. I also saw how electronic systems such as "
        f"LHIMS support data integrity when used with care. What stood out most to me was the "
        f"importance of confidentiality: patient records carry sensitive personal information, "
        f"and handling them with discretion is part of the professional responsibility of every "
        f"health information officer.",

        f"Reflecting on my practicum at {_facility_info(session)['name']}, I can see how the "
        f"work in {unit_summary} bridged the gap between the theory I studied in class and the "
        f"actual delivery of health services. Each activity — from registering patients to "
        f"updating records and compiling reports — taught me something about the discipline "
        f"that health information management requires. I learnt that careful attention to detail "
        f"in data capture prevents errors later, and that confidentiality, when practised "
        f"consistently, protects both the patient and the facility. The attachment confirmed my "
        f"interest in the field and gave me practical confidence for future roles.",
    ]
    return random.choice(templates)


def _draft_observation(session):
    f = _facility_info(session)
    units = _units(session)
    unit_names = ' and '.join(u[0] for u in units) if units else 'the departments I worked in'

    templates = [
        f"During the attachment, I observed that {f['name']} relies on a combination of "
        f"electronic and manual record systems. Registration and appointment data are entered "
        f"into the electronic system, while certain referral and archival documents are still "
        f"held in physical files. Staff in {unit_names} worked closely with one another to keep "
        f"patient records up to date, and there was a clear internal structure that defined who "
        f"handled each stage of the record lifecycle. Overall, the facility's health information "
        f"workflows appeared organised and well-supervised.",

        f"At {f['name']}, I observed a well-structured approach to patient record management. "
        f"The health information systems support registration, booking and reporting, and staff "
        f"across {unit_names} coordinate to maintain continuity of patient records. I also noted "
        f"that staff made regular use of data-quality checks — such as verifying patient "
        f"demographics at the point of entry — which helped to prevent duplication and errors. "
        f"The facility appeared committed to reliable record keeping, and this shaped my own "
        f"approach to data integrity during the attachment.",
    ]
    return random.choice(templates)


def _draft_challenges(session):
    logs = _logs(session)
    challenges = []
    seen = set()
    for log in logs:
        raw = (getattr(log, 'challenges_faced', '') or '').strip()
        if not raw:
            continue
        text = ' '.join(raw.split()).rstrip('.').strip()
        text = re.sub(r'^(?:i\s+|we\s+)', '', text, flags=re.IGNORECASE)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        challenges.append(
            f"During the attachment, I encountered a situation where {text[0].lower() + text[1:]}. "
            f"This affected the pace of my work in the unit and required me to adjust my approach "
            f"in order to keep records accurate and complete."
        )
    if not challenges:
        return (
            "I did not encounter any major challenge that affected my work during the "
            "attachment. The staff in the units I rotated through were supportive, and the "
            "workflow was smooth enough for me to carry out my assigned tasks without "
            "significant difficulty."
        )
    return '\n\n'.join(challenges[:4])


def _draft_recommendations(session):
    logs = _logs(session)
    solutions = []
    seen = set()
    for log in logs:
        raw = (getattr(log, 'solutions_applied', '') or '').strip()
        if not raw:
            continue
        text = ' '.join(raw.split()).rstrip('.').strip()
        text = re.sub(r'^(?:i\s+|we\s+)', '', text, flags=re.IGNORECASE)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        solutions.append(text)

    paras = []
    if solutions:
        joined = '; '.join(s[0].lower() + s[1:] for s in solutions[:3])
        paras.append(
            f"Based on the challenges I observed during the attachment, I recommend that the "
            f"facility continue to apply the measures that were used to address them — "
            f"specifically, {joined}. Strengthening these practices will support the accuracy "
            f"and completeness of patient records and improve the overall efficiency of the "
            f"health information workflow."
        )
    paras.append(
        f"I also recommend that the College continue to prepare students with strong practical "
        f"exposure before attachment, and that future students take full advantage of the "
        f"opportunity to rotate through multiple units. Exposure to more than one unit builds "
        f"a broader understanding of the record lifecycle and gives students the confidence to "
        f"handle varied tasks on the job."
    )
    return '\n\n'.join(paras)


def _draft_conclusion(session):
    f = _facility_info(session)
    units = _units(session)
    unit_names = ', '.join(u[0] for u in units) if units else 'the health information units'

    templates = [
        f"The practicum at {f['name']} was a valuable step in my professional development. "
        f"Through my rotations in {unit_names}, I applied the principles of health information "
        f"management to real hospital situations and developed practical skills in patient "
        f"registration, records management and data reporting. The experience connected "
        f"classroom knowledge to actual practice and gave me a clearer picture of what a career "
        f"in this field requires. I am grateful to the staff of {f['name']} for their guidance, "
        f"patience and support throughout the attachment.",

        f"My attachment at {f['name']} was both instructive and rewarding. The time I spent in "
        f"{unit_names} allowed me to translate theory into practice and to appreciate the "
        f"discipline required in health information work. I now feel better prepared for "
        f"professional practice, and I leave with a stronger commitment to accuracy, "
        f"confidentiality and data integrity. I extend my sincere thanks to the staff of "
        f"{f['name']} for their mentorship and encouragement.",
    ]
    return random.choice(templates)


def _draft_abstract(session):
    u = _user_info(session)
    f = _facility_info(session)
    units = _units(session)
    comps = _competencies(session)
    unit_names = ', '.join(u[0] for u in units[:3]) if units else 'the health information units'
    comp_text = ', '.join(comps[:2]) if comps else 'patient registration and records management'

    return (
        f"This report presents the activities and experiences of a Level {u['level']} "
        f"{u['dept']} student during a clinical attachment at {f['name']}. "
        f"The practicum, undertaken from {_fmt_date(getattr(session, 'start_date', ''))} "
        f"to {_fmt_date(getattr(session, 'end_date', ''))}, allowed the student to apply "
        f"classroom knowledge in a real hospital environment. The student rotated through "
        f"{unit_names}, where they gained practical skills in {comp_text}. The report "
        f"documents the activities performed, skills acquired, workshops attended, "
        f"challenges faced and lessons learnt. It concludes that the attachment provided "
        f"the student with a strong foundation in health information management practice "
        f"and prepared them for professional roles in the field."
    )


def _draft_acknowledgments(session):
    f = _facility_info(session)
    units = _units(session)
    unit_names = ', '.join(u[0] for u in units) if units else 'the health information units'
    supervisor = (getattr(session, 'assigned_supervisor_name', '') or '').strip()

    paras = []
    if supervisor:
        paras.append(
            f"I would like to express my sincere gratitude to my clinical supervisor, "
            f"{supervisor}, for the guidance, patience and encouragement provided throughout "
            f"my attachment at {f['name']}."
        )
    else:
        paras.append(
            f"I would like to express my sincere gratitude to the management and staff of "
            f"{f['name']} for the opportunity to undertake my clinical attachment at the facility."
        )
    paras.append(
        f"I am also grateful to the staff of {unit_names} for their time, patience and "
        f"willingness to teach me throughout the practicum. Finally, I thank {_school_name()} "
        f"for preparing me academically for this experience and for the continued support "
        f"throughout my programme."
    )
    return '\n\n'.join(paras)


# ============================================================
# Public API
# ============================================================

DRAFTERS = {
    'introduction':         _draft_introduction,
    'activities_performed': _draft_activities,
    'skills_acquired':      _draft_skills,
    'workshops':            _draft_workshops,
    'lessons_learnt':       _draft_lessons,
    'observation':          _draft_observation,
    'challenges':           _draft_challenges,
    'recommendations':      _draft_recommendations,
    'conclusion':           _draft_conclusion,
    'abstract':             _draft_abstract,
    'acknowledgments':      _draft_acknowledgments,
}


def draft_local_section(session, key):
    """Draft one section without AI. Never raises. Returns '' only if truly impossible."""
    fn = DRAFTERS.get(key)
    if fn is None:
        return ''
    try:
        text = fn(session)
        return (text or '').strip()
    except Exception:
        logger.exception('local_drafter failed for section %s', key)
        return ''


def draft_local_sections(session, keys=None):
    """Draft every section (or just the given keys) locally. Returns {key: text}."""
    keys = keys or list(DRAFTERS.keys())
    return {k: draft_local_section(session, k) for k in keys}