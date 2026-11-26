"""
Best-effort AI drafting for report sections and slides, with code-enforced
quality gates.

v13 — Official-ready, Issah-style output
----------------------------------------
* Every call that used to crash silently is now defined in this file:
  _build_context, _call_ai, _SECTION_FLOORS.  (Previously these were missing,
  so every draft raised NameError, views.py swallowed it, and the report fell
  back to raw-log template text.)
* Sections are drafted IN PARALLEL (ThreadPoolExecutor) instead of one after
  another, so all of them finish inside the time budget.
* Prompts match the reference report (Issah Abdul): concise bullets for
  Activities and Skills, short paragraphs for everything else.
* Code-level normalisers guarantee the structure the renderers expect
  (unit heading + bullet lines, "I learnt how to ..." skills, clean
  paragraphs, no < >, no markdown, capital letters, full stops).
* Deterministic fallbacks (fallback_skill_line, fallback_lessons_text, ...)
  so even when the AI is unavailable the report never shows raw lowercase
  log text.
* PROMPT_VERSION is part of source_fingerprint(), so drafts written by an
  older prompt version are automatically re-drafted.

Settings (all optional)
-----------------------
REPORT_AI_DRAFTING_ENABLED       default True
REPORT_AI_DRAFTING_MAX_SECONDS   default 90   (whole report, all sections)
REPORT_AI_DRAFTING_WORKERS       default 6
REPORT_AI_CALL_TIMEOUT           default 60   (seconds per API call)
REPORT_AI_CALLABLE               dotted path to YOUR existing text-generation
                                 function:  fn(prompt, max_tokens=...) -> str
REPORT_AI_PROVIDER               'anthropic' | 'openai' | 'gemini'
                                 (auto-detected from the API key if omitted)
REPORT_AI_MODEL                  override the default model name
ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY (or GOOGLE_API_KEY)
"""

import concurrent.futures as cf
import hashlib
import json
import logging
import os
import re
import time

from django.conf import settings

logger = logging.getLogger(__name__)

PROMPT_VERSION = 'v13'
DEFAULT_MAX_SECONDS = 90


# ============================================================
# Shared prompt fragments
# ============================================================

_COMMON_RULES = (
    "RULES (apply to every section):\n"
    "1. Write as the student, in the first person ('I'). Never say 'the student' "
    "or 'the intern' (the Abstract is the only exception).\n"
    "2. The daily logs, workshops and competency areas in the STUDENT DATA are the "
    "ONLY source of facts. Never invent units, activities, workshops, dates, patient "
    "numbers, systems or people.\n"
    "3. NEVER copy log text. Rewrite every item into polished, official English. "
    "Every sentence and every bullet starts with a capital letter, 'I' is always a "
    "capital, grammar and punctuation are correct, and finished work is in the past "
    "tense.\n"
    "4. Field mapping: ACTIVITY feeds Activities Performed; SKILL feeds Skills Acquired "
    "and Lessons Learnt; CHALLENGE feeds Challenges; SOLUTION feeds Recommendations "
    "only (rephrase it, never copy it). Obstacles, delays, congestion, shortages, "
    "network problems and their solutions must NEVER appear in Activities Performed "
    "or Skills Acquired.\n"
    "5. Plain text only: no markdown, no asterisks, no '#', no angle brackets "
    "'< >', no square brackets '[ ]', no bullet symbols, no em dashes.\n"
    "6. Be concise and direct, like a good student report. No filler such as 'It is "
    "worth noting that'. No heading. No closing remark. No explanation of what you "
    "did.\n"
)

_STYLE_EXAMPLES_NOTE = (
    "The examples below show STYLE ONLY. Never reuse their facts.\n"
)


DRAFT_SECTIONS = [
    ('introduction', 'Introduction',
     "Write ONE paragraph of 80 to 130 words. Say who you are (name, level and "
     "department), where you did the attachment (facility name and location), the "
     "period, the units you worked in, and the practical areas you gained experience "
     "in (take these from the competency areas). End with one sentence saying the "
     "work was carried out with the support and supervision of the staff.\n"
     + _STYLE_EXAMPLES_NOTE +
     "Style example: 'I am Ama Mensah, a Level 100 Health Information Management "
     "student at the College of Health, Yamfo. I undertook my second semester clinical "
     "attachment at Example Hospital. The practicum gave me practical experience in "
     "patient registration, appointment booking and the use of LHIMS.'"),

    ('activities_performed', 'Activities Performed',
     "Write the activities as BULLET LINES grouped under plain UNIT HEADINGS, "
     "exactly in this layout (the renderer adds the bullets):\n\n"
     "Records Unit\n"
     "Registered new patients by capturing their personal details in LHIMS.\n"
     "Booked and rescheduled patient appointments using the scheduling module.\n\n"
     "Reproductive and Child Health (RCH) Unit\n"
     "Registered infants younger than three months under their mother's insurance.\n\n"
     "START RULE: begin immediately with the first unit heading. No opening "
     "paragraph, no introduction. A unit heading is a short line with no full stop. "
     "Never wrap a unit name in < > or [ ].\n"
     "Use ONE heading per unit that appears in the logs (merge spellings of the same "
     "unit, for example 'RCH' and 'Reproductive and Child Health').\n"
     "Every activity line is ONE clear sentence of 10 to 25 words that starts with a "
     "past-tense action verb (Registered, Booked, Captured, Updated, Verified, "
     "Printed, Recorded, Prepared, Calculated). Write one line per distinct logged "
     "activity; you may split one rich log into at most two lines. Where useful add "
     "brief professional context that is true to the log (the system used such as "
     "LHIMS, or the data-quality check applied). Do NOT invent extra activities to "
     "reach a length."),

    ('skills_acquired', 'Skills Acquired',
     "Write 4 to 8 bullet lines, one skill per line. Each line is ONE sentence of "
     "8 to 22 words that starts with 'I learnt how to', 'I was able to', 'I gained "
     "the ability to' or 'I developed the skill of' (vary the openers) and names the "
     "skill together with the activity it came from. Follow the same order as the "
     "Activities Performed section. Every skill must trace back to an activity in the "
     "logs. No introduction, no obstacles.\n"
     + _STYLE_EXAMPLES_NOTE +
     "Style examples: 'I learnt how to use LHIMS to register patients.' / "
     "'I was able to book and reschedule patient appointments.' / "
     "'I learnt how to register newborn babies using their mother's insurance.'"),

    ('workshops', 'Workshops',
     "Write one short paragraph for each workshop, tutorial, meeting or training "
     "session in the data. Each paragraph: state the date and the clean topic in one "
     "sentence ('On 4 November 2026, I attended a tutorial on the use of protective "
     "equipment.'), then add one or two sentences explaining in clear English what "
     "such a session covers and how it supported your practicum work. Keep it general "
     "and truthful; do not invent speakers, numbers or details. Never repeat words "
     "such as 'tutorial on Tutorial on'.\n"
     "If NO workshop was recorded, write one short paragraph saying that no formal "
     "workshop was attended and that you received practical guidance from the staff "
     "while working."),

    ('lessons_learnt', 'Lessons Learnt',
     "Write 1 or 2 paragraphs (100 to 170 words in total). Explain in clear detail "
     "what you learnt from your experience: connect your activities, the skills you "
     "acquired and any workshops to Health Information Management principles such as "
     "accurate data capture at registration, patient confidentiality, record "
     "integrity, the value of electronic systems, and how classroom theory applies in "
     "the hospital. Do not list or restate the activities.\n"
     + _STYLE_EXAMPLES_NOTE +
     "Style example: 'The practicum helped me to understand how electronic health "
     "information systems are used in the hospital. I learnt that accurate patient "
     "information is important for proper service delivery and record keeping.'"),

    ('observation', 'Observations',
     "Write 1 or 2 short paragraphs (80 to 140 words in total) describing HOW the "
     "facility works: its record systems, the use of electronic and manual records, "
     "staff support, data quality and coordination between units. Base it on the logs "
     "and competency areas; general Health Information Management observations are "
     "allowed but do not invent facility-specific facts. Do NOT describe problems "
     "or obstacles here; those belong to Challenges."),

    ('challenges', 'Challenges',
     "Write 2 to 4 short paragraphs (70 to 150 words in total), one paragraph per "
     "challenge, each 1 or 2 sentences in the first person, describing what happened "
     "and how it affected your work. Use ONLY the CHALLENGE entries in the logs, "
     "rewritten in polished English. If the logs contain no challenge, write one "
     "short paragraph saying you met no major challenge. Do not invent challenges. "
     "No activities and no solutions here."),

    ('recommendations', 'Recommendations',
     "Write 1 or 2 short paragraphs (80 to 150 words in total). The first paragraph "
     "gives recommendations to the facility, the second (if needed) gives "
     "recommendations to the College or to future students. Base every "
     "recommendation on a challenge or observation in this report and say what "
     "should be done and why. Rephrase any SOLUTION entries; never copy them. Write "
     "professionally ('I recommend that the hospital ...'). No labels, no bullets.\n"
     + _STYLE_EXAMPLES_NOTE +
     "Style example: 'I recommend that the hospital continue to provide reliable "
     "internet connectivity and enough computers for the Health Information "
     "Department.'"),

    ('conclusion', 'Conclusion',
     "Write 1 or 2 paragraphs (90 to 150 words in total). Summarise the experience "
     "(facility, units, main skills), say how it prepared you for professional "
     "practice and connected classroom knowledge to the hospital, and end with "
     "thanks to the staff of the facility for their guidance and support."),

    ('abstract', 'Abstract',
     "Write ONE academic paragraph of 90 to 130 words in the THIRD person "
     "('The student ...'). Cover the purpose, facility, units, main skills and the "
     "overall outcome, and name at least two competency areas. Do not copy logs."),

    ('acknowledgments', 'Acknowledgments',
     "Write 1 or 2 short formal paragraphs in the first person thanking the "
     "facility, the clinical supervisor (only if one is named in the data), the staff "
     "of the units you worked in, and the College. Keep it sincere and brief."),
]


# Sections that are plain bullet lists / structured / prose (used by finalize_section)
_PROSE_KEYS = frozenset({
    'introduction', 'workshops', 'lessons_learnt', 'observation', 'observations',
    'challenges', 'recommendations', 'conclusion', 'abstract', 'acknowledgments',
    'facility_profile',
})
_BULLET_KEYS = frozenset({'skills_acquired'})

_SECTION_MAX_TOKENS = {
    'activities_performed': 1100,
    'skills_acquired': 600,
    'abstract': 400,
}
_DEFAULT_MAX_TOKENS = 700


# Minimum sensible word counts per section (the quality gate rejects drafts
# shorter than half of these).
_SECTION_FLOORS = {
    'introduction': 60,
    'activities_performed': 40,
    'skills_acquired': 40,
    'workshops': 25,
    'lessons_learnt': 70,
    'observation': 50,
    'challenges': 25,
    'recommendations': 50,
    'conclusion': 60,
    'abstract': 60,
    'acknowledgments': 40,
}

_BUDGET_DISTRIBUTION = {
    'acknowledgments':       0.030,
    'introduction':          0.065,
    'activities_performed':  0.150,
    'skills_acquired':       0.090,
    'workshops':             0.030,
    'lessons_learnt':        0.090,
    'observation':           0.080,
    'challenges':            0.075,
    'recommendations':       0.075,
    'conclusion':            0.055,
}


# ============================================================
# Presentation drafting (slide bullets + speaker notes)
# ============================================================

PPTX_SLIDE_TITLES = (
    'Introduction',
    'Facility Profile',
    'Activities Performed',
    'Skills Acquired',
    'Challenges Faced',
    'Observation and Recommendation',
    'Conclusion',
)

_PPTX_RULES = (
    "RULES:\n"
    "1. Every line is one standalone bullet. Output ONE bullet per line with no "
    "bullet symbols, numbers, markdown or headings.\n"
    "2. Word limits are absolute.\n"
    "3. Refine, never copy: write clean, properly capitalised presentation English "
    "(first letter of every bullet capital, 'I' capital).\n"
    "4. Use the PAST tense for things already done ('Completed', 'Registered', "
    "'Worked'). Never write 'I complete' or 'I rotate'.\n"
    "5. Do not use dashes as separators. Use a colon or rephrase.\n"
    "6. Use ONLY facts from the STUDENT DATA. Do not invent units, workshops or "
    "activities. Name each unit once (merge 'RCH' and 'Reproductive and Child "
    "Health').\n"
    "7. Do not put obstacles, delays or their solutions on the Activities or Skills "
    "slides.\n"
)

PPTX_DRAFT_SECTIONS = [
    ('pptx_intro_bullets', 'Slide: Introduction',
     "Write 3 bullets, each 12 words or fewer, in the first person and past tense: "
     "the purpose of the attachment, the facility name and location, and the units "
     "worked in.\n"
     "Style example: 'Completed a one month attachment at Example Hospital'"),

    ('pptx_facility_bullets', 'Slide: Facility Profile',
     "Write 3 bullets, each 12 words or fewer, describing the FACILITY (not the "
     "student): type and location, main role or services, and how it uses health "
     "information systems. Use only the FACILITY facts in the data. Do not use "
     "'I'."),

    ('pptx_activities_bullets', 'Slide: Activities Performed',
     "Write 4 to 6 bullets, each 10 words or fewer, in the form 'Unit: key "
     "activity' using ONLY the ACTIVITY entries of the logs.\n"
     "Style example: 'Records: registered new patients in LHIMS'"),

    ('pptx_skills_bullets', 'Slide: Skills Acquired',
     "Write 4 or 5 bullets, each 8 words or fewer, naming skills gained from the "
     "logged activities.\n"
     "Style example: 'Patient registration using LHIMS'"),

    ('pptx_challenges_bullets', 'Slide: Challenges Faced',
     "Write 1 to 3 bullets, each 12 words or fewer, one per distinct CHALLENGE in "
     "the logs, in clean English. If the logs record no challenge, write one bullet: "
     "'No major challenges were recorded'. Challenges only: no activities and no "
     "solutions."),

    ('pptx_observation_recommendation_bullets', 'Slide: Observation and Recommendation',
     "Write exactly 4 bullets, each 14 words or fewer. The first 2 are observations "
     "about workflows, staff support or record systems (from the OBSERVATION text). "
     "The last 2 are actionable recommendations for the facility or the College "
     "(from the RECOMMENDATIONS text)."),

    ('pptx_conclusion_bullets', 'Slide: Conclusion',
     "Write 3 bullets, each 12 words or fewer, in the first person and past tense: "
     "the overall experience, the key skills or lesson gained, and readiness for "
     "professional practice."),

    ('pptx_speaker_notes', 'Speaker Notes for All Slides',
     "Write 2 short sentences of speaker notes (what the presenter says aloud) for "
     "each of these 7 slides, using EXACTLY these slide names, one slide per line, "
     "in this order: "
     + ', '.join(PPTX_SLIDE_TITLES) + ".\n"
     "Format of each line: 'Slide Name: sentence one. sentence two.'\n"
     "Exactly 7 lines. No bullets, no markdown."),
]

_PPTX_MAX_BULLETS = {
    'pptx_intro_bullets': 3,
    'pptx_facility_bullets': 3,
    'pptx_activities_bullets': 6,
    'pptx_skills_bullets': 5,
    'pptx_challenges_bullets': 3,
    'pptx_observation_recommendation_bullets': 4,
    'pptx_conclusion_bullets': 3,
}


# ============================================================
# Quality enforcement constants
# ============================================================

_VERBATIM_NGRAM_SIZE = 7

_QUALITY_STOPWORDS = frozenset({
    'a', 'an', 'the', 'and', 'or', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'by', 'as',
    'is', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
    'i', 'me', 'my', 'we', 'our', 'this', 'that', 'these', 'those', 'from', 'into', 'within',
    'also', 'other', 'some', 'various', 'including', 'included', 'such', 'it', 'its',
    'but', 'so', 'if', 'when', 'while', 'than', 'then', 'there', 'their', 'they', 'them',
    'he', 'she', 'his', 'her', 'him', 'you', 'your', 'will', 'would', 'can', 'could',
    'should', 'may', 'might', 'must', 'not', 'no', 'yes', 'very', 'just', 'only',
})

_PLACEHOLDER_TOKENS = (
    '[insert', '[your name', '[facility name', '[date]', '[name]',
    'lorem ipsum', '<insert', 'todo:', 'placeholder:', 'xxxx', 'tbd',
)

# Obstacle / solution language that must not appear in Activities or Skills.
# (Deliberately narrow: legitimate phrases such as "assisted with registration"
# or "optimised the filing order" are NOT removed.)
_OBSTACLE_MARKERS = (
    'challeng', 'difficult', 'struggl', 'obstacle', 'barrier',
    'hinder', 'disrupt', 'delay', 'congest', 'overcrowd', 'shortage',
    'lack of', 'insufficient', 'bottleneck', 'downtime', 'slowdown',
    'high patient volume', 'heavy patient volume',
    'extended working hours', 'extended hours', 'long hours',
    'cope with', 'manage high', 'manage heavy', 'manage large',
    'despite the', 'in spite of', 'under pressure',
    'forced to', 'had to contend',
)

_SOLUTION_MARKERS = (
    'recommend', 'solution', 'should extend', 'should be extended',
    'mitigate', 'streamlin', 'enhance the workflow',
)

_FILLER_PATTERNS = [
    re.compile(r'It is worth noting that\s+', re.IGNORECASE),
    re.compile(r'It can be observed that\s+', re.IGNORECASE),
    re.compile(r'It is important to note that\s+', re.IGNORECASE),
    re.compile(r'It should be noted that\s+', re.IGNORECASE),
    re.compile(r'Through this experience,?\s+', re.IGNORECASE),
    re.compile(r'In the context of this attachment,?\s+', re.IGNORECASE),
    re.compile(r'As a matter of fact,?\s+', re.IGNORECASE),
]

_DUP_PHRASE_RE = re.compile(
    r'\b(tutorial on|workshop on|meeting on|training on|session on)\s+\1\b',
    re.IGNORECASE,
)
_SPACE_BEFORE_PUNCT_RE = re.compile(r'\s+([.,;:!?])')
_LOWER_I_RE = re.compile(r'\bi\b')


# ============================================================
# Text polishing helpers (also imported by service.py / pptx_builder.py)
# ============================================================

def _polish_english(text: str) -> str:
    """
    Normalise capitalisation, duplicate prefixes (e.g. 'tutorial on Tutorial on'),
    a stray lowercase 'i', and spacing before punctuation.
    """
    if not text:
        return ''
    while True:
        updated = _DUP_PHRASE_RE.sub(r'\1', text)
        if updated == text:
            break
        text = updated

    text = _LOWER_I_RE.sub('I', text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r'\1', text)
    text = re.sub(r'^([a-z])', lambda m: m.group(1).upper(), text, flags=re.MULTILINE)
    text = re.sub(r'([.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip()


def _strip_filler(text):
    if not text:
        return text
    for pat in _FILLER_PATTERNS:
        text = pat.sub('', text)
    text = re.sub(r'  +', ' ', text)
    return _polish_english(text)


def _clean_unit_heading(heading):
    cleaned = re.sub(r'^[<\[\s]+|[>\]\s]+$', '', (heading or '')).strip()
    return cleaned.rstrip(':').strip()


def _ensure_period(line):
    line = (line or '').strip()
    if line and line[-1] not in '.!?':
        line += '.'
    return line


def _capitalise_first(line):
    line = (line or '').strip()
    return line[0].upper() + line[1:] if line else line


_IRREGULAR_PAST = {
    'wrote': 'write', 'made': 'make', 'took': 'take', 'ran': 'run', 'kept': 'keep',
    'gave': 'give', 'held': 'hold', 'led': 'lead', 'met': 'meet', 'sent': 'send',
    'built': 'build', 'began': 'begin', 'did': 'do', 'saw': 'see', 'got': 'get',
    'found': 'find', 'taught': 'teach', 'learnt': 'learn', 'learned': 'learn',
    'brought': 'bring', 'read': 'read', 'put': 'put', 'set': 'set', 'cut': 'cut',
    'filed': 'file', 'wrote': 'write', 'drew': 'draw', 'spoke': 'speak',
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


def _to_base_verb(word):
    """Past-tense verb -> base form (small heuristic, good for typical log verbs)."""
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
    """'register 15 patients and booked appointments' -> '... and book appointments'."""
    def repl(m):
        joiner, word = m.group(1), m.group(2)
        if _is_past_verb(word):
            return f"{joiner} {_to_base_verb(word)}"
        return m.group(0)
    return re.sub(r'\b(and|then|also)\s+([A-Za-z]+)\b', repl, text)


def fallback_activity_line(raw):
    """Raw log activity -> a clean, capitalised, full-stopped line (no AI needed)."""
    text = ' '.join((raw or '').split())
    text = re.sub(r'^(?:i\s+)', '', text, flags=re.IGNORECASE)
    text = _polish_english(text)
    return _ensure_period(_capitalise_first(text))


def fallback_skill_line(raw):
    """
    Raw log skill -> 'I learnt how to ...' (no AI needed).
    'i registered 15 new patients and booked appointments for them'
        -> 'I learnt how to register 15 new patients and book appointments for them.'
    """
    text = ' '.join((raw or '').split()).strip().rstrip('.')
    if not text:
        return ''
    text = re.sub(r'^(?:i\s+(?:was able to|learnt how to|learned how to)\s+)', '',
                  text, flags=re.IGNORECASE)
    text = re.sub(r'^(?:i\s+)', '', text, flags=re.IGNORECASE)
    parts = text.split(' ', 1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ''
    if _is_past_verb(first):
        base = _to_base_verb(first)
        rest = _convert_following_verbs(rest)
        return _ensure_period(f"I learnt how to {base} {rest}".strip())
    lowered = text[0].lower() + text[1:]
    return _ensure_period(f"I learnt about {lowered}")


def fallback_lessons_text(skill_lines):
    """
    Build 1 short, clean lessons paragraph from skill lines when the AI is
    unavailable. Never echoes raw log text.
    """
    phrases = []
    for line in skill_lines or []:
        m = re.match(r'^I learnt how to\s+(.*?)\.?$', (line or '').strip(), flags=re.IGNORECASE)
        if m and m.group(1):
            phrases.append(m.group(1).strip())
    para = (
        "The practicum helped me to understand how health information work is carried "
        "out in a real hospital and how the knowledge gained in the classroom is "
        "applied in practice."
    )
    if phrases:
        shown = phrases[:3]
        joined = shown[0] if len(shown) == 1 else ', '.join(shown[:-1]) + ' and ' + shown[-1]
        para += f" In practice, I learnt how to {joined}."
    para += (
        " I also learnt that accurate patient information is important for proper "
        "service delivery and record keeping."
    )
    return para


# ---------------- unit-name canonicalisation ----------------

_UNIT_ALIASES = {
    'rch': 'Reproductive and Child Health (RCH) Unit',
    'reproductive and child health': 'Reproductive and Child Health (RCH) Unit',
    'reproductive child health': 'Reproductive and Child Health (RCH) Unit',
    'reproductive and child health rch': 'Reproductive and Child Health (RCH) Unit',
    'mch': 'Maternal and Child Health (MCH) Unit',
    'maternal and child health': 'Maternal and Child Health (MCH) Unit',
    'opd': 'Outpatient Department (OPD)',
    'out patient': 'Outpatient Department (OPD)',
    'outpatient': 'Outpatient Department (OPD)',
    'out patient department': 'Outpatient Department (OPD)',
    'outpatient department': 'Outpatient Department (OPD)',
    'opd outpatient department': 'Outpatient Department (OPD)',
    'medical records': 'Records Unit',
    'records': 'Records Unit',
    'record': 'Records Unit',
    'health information': 'Records Unit',
}

_UNIT_NOISE_WORDS = frozenset({'unit', 'department', 'dept', 'section', 'the'})


def canonical_unit_key(name):
    """Lower-case key used to group spellings of the same unit."""
    text = re.sub(r'[()\[\]<>.,:;/&-]', ' ', (name or '').lower())
    words = [w for w in text.split() if w not in _UNIT_NOISE_WORDS]
    return ' '.join(words)


def canonical_unit_label(name):
    """Display name for a unit; merges spellings such as 'RCH' and 'Reproductive and Child Health'."""
    cleaned = _clean_unit_heading(' '.join((name or '').split()))
    key = canonical_unit_key(cleaned)
    if key in _UNIT_ALIASES:
        return _UNIT_ALIASES[key]
    if cleaned.islower():
        return cleaned.title()
    return cleaned


# ============================================================
# Structure normalisers
# ============================================================

_UNICODE_FIXES = {
    '\u2011': '-', '\u2010': '-', '\u00a0': ' ', '\u200b': '', '\ufeff': '',
}


def _normalize_unicode(text):
    for bad, good in _UNICODE_FIXES.items():
        text = text.replace(bad, good)
    return text


_KNOWN_HTML_TAG_RE = re.compile(
    r'</?(?:b|i|u|p|br|div|span|strong|em|ul|ol|li|h[1-6])\b[^>]*>', re.IGNORECASE)


def _looks_like_activity_line(line):
    words = (line or '').split()
    if not words:
        return False
    first = re.sub(r'[^A-Za-z]', '', words[0]).lower()
    return first == 'i' or _is_past_verb(first)


def _normalize_activities(text, fallback_unit=''):
    """
    Re-build Activities Performed so the renderers always get
        Heading
        Bullet sentence.
        Bullet sentence.
        <blank line>
        Heading ...
    Headings: short line, no terminal punctuation, not a past-tense activity.
    """
    lines = [l.strip() for l in (text or '').splitlines() if l.strip()]
    blocks = []
    cur_head, cur = None, []

    for line in lines:
        words = len(line.split())
        is_heading = (
            line[-1] not in '.!?'
            and words <= 8
            and len(line) < 80
            and not _looks_like_activity_line(line)
        )
        if is_heading:
            if cur_head is not None or cur:
                blocks.append((cur_head, cur))
            cur_head, cur = canonical_unit_label(line), []
        else:
            cur.append(_ensure_period(_capitalise_first(line)))
    if cur_head is not None or cur:
        blocks.append((cur_head, cur))

    out_blocks, seen_heads = [], {}
    for head, bullets in blocks:
        if not bullets:
            continue
        head = head or canonical_unit_label(fallback_unit) or 'Other Activities'
        key = canonical_unit_key(head)
        if key in seen_heads:                       # merge repeated headings
            seen_heads[key][1].extend(bullets)
            continue
        entry = [head, list(bullets)]
        seen_heads[key] = entry
        out_blocks.append(entry)

    rendered = []
    for head, bullets in out_blocks:
        uniq, seen = [], set()
        for b in bullets:
            k = b.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(b)
        rendered.append('\n'.join([head] + uniq))
    return '\n\n'.join(rendered)


def _normalize_bullets(text):
    lines, seen = [], set()
    for raw in (text or '').splitlines():
        line = _ensure_period(_capitalise_first(raw.strip()))
        if len(line) < 4:
            continue
        k = line.lower()
        if k in seen:
            continue
        seen.add(k)
        lines.append(line)
    return '\n'.join(lines)


def _normalize_prose(text):
    paras = [p.strip() for p in re.split(r'\n\s*\n', text or '') if p.strip()]
    if len(paras) == 1 and '\n' in paras[0]:
        lines = [l.strip() for l in paras[0].splitlines() if l.strip()]
        if all(l[-1] in '.!?"\u201d' for l in lines):
            paras = lines
        else:
            paras = [' '.join(lines)]
    paras = [' '.join(p.split()) for p in paras]
    return '\n\n'.join(paras)


def _strip_obstacle_content(text, section_key):
    if section_key not in ('activities_performed', 'skills_acquired'):
        return text, []
    if not text:
        return text, []

    removed, kept = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        lowered = stripped.lower()
        hit = None
        for marker in _OBSTACLE_MARKERS:
            if marker in lowered:
                hit = ('obstacle', marker)
                break
        if not hit:
            for marker in _SOLUTION_MARKERS:
                if marker in lowered:
                    hit = ('solution', marker)
                    break
        if hit:
            removed.append((hit[0], stripped[:100]))
            continue
        kept.append(line)
    return '\n'.join(kept), removed


def _clean_draft_text(text, section_title=''):
    """Remove markdown / brackets / bullets / numbering and polish capitalisation."""
    if not text:
        return ''
    text = _normalize_unicode(text)
    text = re.sub(r'^[ \t]*#+[ \t]*.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\w)\*(?!\s)([^*\n]+?)\*(?!\w)', r'\1', text)
    # <Records Unit>  /  [Records Unit]:  ->  Records Unit
    text = re.sub(r'^[ \t]*[<\[][ \t]*([^<>\[\]\n]+?)[ \t]*[>\]][ \t]*:?[ \t]*$',
                  r'\1', text, flags=re.MULTILINE)
    text = _KNOWN_HTML_TAG_RE.sub('', text)
    text = re.sub(r'^[ \t]*[<>]+[ \t]*|[ \t]*[<>]+[ \t]*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[*\-\u2022\u00b7]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[\.\)]\s+', '', text, flags=re.MULTILINE)
    if section_title:
        text = re.sub(
            r'^\s*' + re.escape(section_title) + r'\s*:?\s*\n+', '', text,
            count=1, flags=re.IGNORECASE)
    text = re.sub(r'\s+[\u2013\u2014]\s+', ', ', text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = _strip_filler(text)
    return text.strip()


def finalize_section(key, text, fallback_unit='', section_title=''):
    """
    Clean + structurally normalise one section. Used for freshly drafted AND
    refined text, so both paths produce renderer-ready output.
    """
    text = _clean_draft_text(text or '', section_title)
    text, _removed = _strip_obstacle_content(text, key)
    if not text.strip():
        return ''
    if key == 'activities_performed':
        return _normalize_activities(text, fallback_unit)
    if key in _BULLET_KEYS:
        return _normalize_bullets(text)
    if key in _PROSE_KEYS:
        return _normalize_prose(text)
    return text.strip()


# ============================================================
# AI client
# ============================================================

def _ai_setting(name, default=None):
    value = getattr(settings, name, None)
    return value or os.environ.get(name) or default


def _ai_provider():
    explicit = str(_ai_setting('REPORT_AI_PROVIDER') or '').lower().strip()
    if explicit:
        return explicit
    if _ai_setting('ANTHROPIC_API_KEY'):
        return 'anthropic'
    if _ai_setting('OPENAI_API_KEY'):
        return 'openai'
    if _ai_setting('GEMINI_API_KEY') or _ai_setting('GOOGLE_API_KEY'):
        return 'gemini'
    return ''


def _ai_configured():
    return bool(_ai_setting('REPORT_AI_CALLABLE') or _ai_provider())


def _http_json(url, headers, payload, timeout):
    import requests
    last = None
    for attempt in range(2):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except Exception as exc:                          # network / timeout
            last = exc
            logger.warning('AI HTTP error (attempt %d): %s', attempt + 1, exc)
            time.sleep(1.5)
            continue
        if resp.status_code in (429, 500, 502, 503, 529) and attempt == 0:
            logger.warning('AI HTTP %s — retrying once', resp.status_code)
            time.sleep(2.0)
            continue
        if resp.status_code >= 400:
            logger.error('AI HTTP %s: %s', resp.status_code, resp.text[:300])
            return None
        try:
            return resp.json()
        except Exception:
            logger.error('AI returned non-JSON body')
            return None
    if last:
        logger.error('AI call failed after retry: %s', last)
    return None


def _call_ai(prompt, max_tokens=None):
    """
    Send ONE prompt, return the reply text or None on any failure.

    Priority:
      1. settings.REPORT_AI_CALLABLE  (dotted path to your existing function)
      2. Anthropic / OpenAI / Gemini over HTTPS, chosen by REPORT_AI_PROVIDER
         or by whichever API key is configured.
    """
    max_tokens = max_tokens or _DEFAULT_MAX_TOKENS
    timeout = float(_ai_setting('REPORT_AI_CALL_TIMEOUT', 60))

    dotted = _ai_setting('REPORT_AI_CALLABLE')
    if dotted:
        try:
            from django.utils.module_loading import import_string
            fn = import_string(dotted)
            try:
                result = fn(prompt, max_tokens=max_tokens)
            except TypeError:
                result = fn(prompt)
            return (result or '').strip() or None
        except Exception:
            logger.exception('REPORT_AI_CALLABLE %s failed', dotted)
            return None

    provider = _ai_provider()
    model = _ai_setting('REPORT_AI_MODEL')

    try:
        if provider == 'anthropic':
            data = _http_json(
                'https://api.anthropic.com/v1/messages',
                {
                    'x-api-key': _ai_setting('ANTHROPIC_API_KEY'),
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                {
                    'model': model or 'claude-sonnet-4-6',
                    'max_tokens': max_tokens,
                    'temperature': 0.4,
                    'messages': [{'role': 'user', 'content': prompt}],
                },
                timeout,
            )
            if not data:
                return None
            text = ''.join(b.get('text', '') for b in data.get('content', [])
                           if b.get('type') == 'text')
            return text.strip() or None

        if provider == 'openai':
            data = _http_json(
                'https://api.openai.com/v1/chat/completions',
                {
                    'Authorization': f"Bearer {_ai_setting('OPENAI_API_KEY')}",
                    'Content-Type': 'application/json',
                },
                {
                    'model': model or 'gpt-4o-mini',
                    'max_tokens': max_tokens,
                    'temperature': 0.4,
                    'messages': [{'role': 'user', 'content': prompt}],
                },
                timeout,
            )
            if not data:
                return None
            return (data['choices'][0]['message']['content'] or '').strip() or None

        if provider == 'gemini':
            key = _ai_setting('GEMINI_API_KEY') or _ai_setting('GOOGLE_API_KEY')
            gem_model = model or 'gemini-2.0-flash'
            data = _http_json(
                f'https://generativelanguage.googleapis.com/v1beta/models/{gem_model}:generateContent?key={key}',
                {'Content-Type': 'application/json'},
                {
                    'contents': [{'parts': [{'text': prompt}]}],
                    'generationConfig': {'temperature': 0.4, 'maxOutputTokens': max_tokens},
                },
                timeout,
            )
            if not data:
                return None
            parts = data['candidates'][0]['content']['parts']
            return ''.join(p.get('text', '') for p in parts).strip() or None
    except Exception:
        logger.exception('AI call failed (provider=%s)', provider)
        return None

    logger.error('No AI provider configured (set REPORT_AI_CALLABLE or an API key)')
    return None


# ============================================================
# Student-data context
# ============================================================

def _clean_workshop_topic(text):
    t = ' '.join((text or '').split())
    t = re.sub(r'^(?:on\s+)+', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(\w+(?:\s+\w+){0,2}?\s+on)\s+\1\b', r'\1', t, flags=re.IGNORECASE)
    return t.strip()


def _fmt_date(value):
    try:
        return value.strftime('%d %B %Y').lstrip('0')
    except Exception:
        return str(value or '')


def _session_units(session):
    """OrderedDict-like list of (canonical label, day count) in order of appearance."""
    units, order = {}, []
    for log in session.daily_logs.filter(is_deleted=False).order_by('date'):
        label = canonical_unit_label(getattr(log, 'unit_sub_department', '') or '')
        if not label:
            continue
        key = canonical_unit_key(label)
        if key not in units:
            units[key] = [label, 0]
            order.append(key)
        units[key][1] += 1
    return [tuple(units[k]) for k in order]


def _build_context(session, include_report=False):
    """
    Labelled plain-text dump of everything the AI may use as fact.
    Field labels (ACTIVITY / SKILL / CHALLENGE / SOLUTION) are referenced by
    the prompt rules, so do not rename them.
    """
    lines = []
    user = getattr(session, 'user', None)
    facility = getattr(session, 'facility', None)

    if user is not None:
        name = (user.get_full_name() or user.username) if hasattr(user, 'get_full_name') else str(user)
        try:
            dept = user.get_department_display()
        except Exception:
            dept = getattr(user, 'department', '')
        lines.append(f"STUDENT NAME: {name}")
        lines.append(f"LEVEL: {getattr(user, 'level', '')}")
        lines.append(f"DEPARTMENT: {dept}")

    if facility is not None:
        lines.append(f"FACILITY NAME: {getattr(facility, 'name', '')}")
        lines.append(f"FACILITY TYPE: {getattr(facility, 'facility_type', '')}")
        lines.append(f"FACILITY LOCATION: {getattr(facility, 'district', '')}, "
                     f"{getattr(facility, 'region', '')} Region, Ghana")
        for attr, label in (('history_and_background', 'FACILITY BACKGROUND'),
                            ('vision_mission', 'FACILITY VISION AND MISSION'),
                            ('organogram_description', 'FACILITY STRUCTURE')):
            val = (getattr(facility, attr, '') or '').strip()
            if val:
                lines.append(f"{label}: {val[:600]}")

    lines.append(f"ATTACHMENT PERIOD: {_fmt_date(getattr(session, 'start_date', ''))} "
                 f"to {_fmt_date(getattr(session, 'end_date', ''))}")
    supervisor = (getattr(session, 'assigned_supervisor_name', '') or '').strip()
    if supervisor:
        lines.append(f"CLINICAL SUPERVISOR: {supervisor}")

    units = _session_units(session)
    if units:
        lines.append("UNITS ROTATED: " + '; '.join(f"{u} ({d} day{'s' if d != 1 else ''})" for u, d in units))

    logs = list(session.daily_logs.filter(is_deleted=False).order_by('date'))
    if logs:
        lines.append("\nDAILY LOGS:")
    for i, log in enumerate(logs, start=1):
        parts = [f"Log {i} | {_fmt_date(log.date)} | UNIT: {canonical_unit_label(log.unit_sub_department or '')}"]
        for attr, label in (('activity_summary', 'ACTIVITY'),
                            ('skills_acquired', 'SKILL'),
                            ('challenges_faced', 'CHALLENGE'),
                            ('solutions_applied', 'SOLUTION')):
            val = (getattr(log, attr, '') or '').strip()
            if val:
                parts.append(f"{label}: {val}")
        lines.append(' | '.join(parts))

    try:
        workshops = list(session.workshops.all().order_by('date'))
    except Exception:
        workshops = []
    if workshops:
        lines.append("\nWORKSHOPS:")
        for w in workshops:
            lines.append(
                f"{_fmt_date(w.date)} | TYPE: {(getattr(w, 'type', '') or '').strip() or 'session'} "
                f"| TOPIC: {_clean_workshop_topic(getattr(w, 'activity', ''))}"
            )
    else:
        lines.append("\nWORKSHOPS: none recorded")

    try:
        competencies = list(session.competencies.values_list('text', flat=True))
    except Exception:
        competencies = []
    if competencies:
        lines.append("\nCOMPETENCY AREAS:")
        lines.extend(f"- {c}" for c in competencies)

    if include_report:
        try:
            from .models import GeneratedReport
            report = GeneratedReport.objects.filter(attachment=session).first()
            content = (report.content or {}) if report else {}
        except Exception:
            content = {}
        for key, label in (('observation', 'OBSERVATION'),
                           ('recommendations', 'RECOMMENDATIONS'),
                           ('challenges', 'REPORT CHALLENGES'),
                           ('conclusion', 'REPORT CONCLUSION')):
            val = (content.get(key) or '').strip()
            if val:
                lines.append(f"\n{label} (from the written report):\n{val[:1200]}")

    return '\n'.join(lines)


# ============================================================
# Fingerprint + quality gates
# ============================================================

def source_fingerprint(session):
    try:
        logs = list(session.daily_logs.filter(is_deleted=False).order_by('date'))
        competencies = list(session.competencies.values_list('text', flat=True))
        workshops = [
            {
                'date': str(w.date),
                'activity': w.activity or '',
                'type': getattr(w, 'type', '') or '',
            }
            for w in session.workshops.all().order_by('date')
        ]
        payload = {
            'prompt_version': PROMPT_VERSION,
            'facility': session.facility_id,
            'start': str(session.start_date),
            'end': str(session.end_date),
            'logs': [
                {
                    'date': str(l.date),
                    'unit': l.unit_sub_department or '',
                    'activity': l.activity_summary or '',
                    'skills': l.skills_acquired or '',
                    'challenges': l.challenges_faced or '',
                    'solutions': l.solutions_applied or '',
                }
                for l in logs
            ],
            'competencies': competencies,
            'workshops': workshops,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()
    except Exception:
        logger.exception('source_fingerprint failed — using empty hash')
        return ''


def _build_log_ngrams(session, n=_VERBATIM_NGRAM_SIZE):
    ngrams = set()
    try:
        for log in session.daily_logs.filter(is_deleted=False):
            for field in ('activity_summary', 'skills_acquired',
                          'challenges_faced', 'solutions_applied'):
                text = (getattr(log, field, '') or '').strip()
                if not text:
                    continue
                words = tuple(re.findall(r'\w+', text.lower()))
                if len(words) < n:
                    continue
                for i in range(len(words) - n + 1):
                    window = words[i:i + n]
                    if len([w for w in window if w not in _QUALITY_STOPWORDS]) < 3:
                        continue
                    ngrams.add(window)
    except Exception:
        logger.exception('Could not build log n-grams for verbatim-copy detection')
    return ngrams


def _find_verbatim_copy(text, log_ngrams, n=_VERBATIM_NGRAM_SIZE):
    if not log_ngrams or not text:
        return None
    words = tuple(re.findall(r'\w+', text.lower()))
    for i in range(len(words) - n + 1):
        window = words[i:i + n]
        if window in log_ngrams:
            return ' '.join(window)
    return None


def _quality_issues(section_key, text, log_ngrams, min_word_floor=0, max_word=0):
    if not text or not text.strip():
        return [('empty', 'AI returned empty text')]

    issues = []

    copied = _find_verbatim_copy(text, log_ngrams)
    if copied:
        issues.append(('verbatim_copy', f'copied raw log phrase: "{copied}"'))

    lowered = text.lower()
    for token in _PLACEHOLDER_TOKENS:
        if token in lowered:
            issues.append(('placeholder', f'contains placeholder: "{token}"'))
            break

    actual_words = len(text.split())
    if min_word_floor and actual_words < int(min_word_floor * 0.5):
        issues.append(('too_short',
                       f'only {actual_words} words (minimum {int(min_word_floor * 0.5)} expected)'))
    if max_word and actual_words > max_word:
        issues.append(('too_long', f'{actual_words} words (target max {max_word})'))

    if '<' in text or '>' in text:
        issues.append(('angle_brackets', 'contains < or > characters'))
    if '**' in text:
        issues.append(('markdown_noise', 'contains ** markers'))
    if section_key == 'activities_performed' and '\n' not in text:
        issues.append(('bad_structure',
                       'needs a unit heading line followed by activity lines'))

    return issues


def _build_retry_prompt(original_prompt, section_title, issues):
    problems = '\n'.join(f'  - {code}: {detail}' for code, detail in issues)
    return (
        f"{original_prompt}\n\n"
        f"REVISION REQUIRED FOR '{section_title}':\n"
        f"Your previous draft had these issues:\n{problems}\n"
        f"Fix every issue while keeping all facts accurate, properly capitalised, "
        f"and free of raw log copying. Follow the required layout exactly."
    )


def _compute_word_budget(target_pages, log_count=0, competency_count=0):
    try:
        target_pages = int(target_pages) if target_pages else 0
    except (TypeError, ValueError):
        target_pages = 0

    if target_pages >= 3:
        effective_pages = target_pages
    elif log_count < 5:
        effective_pages = 3
    elif log_count < 10:
        effective_pages = 5
    elif log_count < 20:
        effective_pages = 8
    else:
        effective_pages = 12

    requested = int(effective_pages * 300)
    source_capacity = 300 + log_count * 150 + competency_count * 60
    total_prose_words = min(requested, max(source_capacity, 600))

    budgets = {
        section: max(int(total_prose_words * pct), _SECTION_FLOORS.get(section, 0))
        for section, pct in _BUDGET_DISTRIBUTION.items()
    }
    return budgets


# ============================================================
# Parallel runner
# ============================================================

def _run_parallel(tasks, max_seconds, workers):
    """
    tasks: list of (key, callable). Returns {key: result} for the tasks that
    finished inside max_seconds. Unfinished threads are abandoned.
    """
    results = {}
    if not tasks:
        return results
    ex = cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks))))
    futures = {ex.submit(fn): key for key, fn in tasks}
    try:
        done, not_done = cf.wait(list(futures), timeout=max_seconds)
        for fut in done:
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception:
                logger.exception('AI drafting task %r raised', key)
        for fut in not_done:
            logger.warning('AI drafting task %r did not finish in %ds', futures[fut], max_seconds)
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:                                  # Python < 3.9
            ex.shutdown(wait=False)
    return results


def _section_prompt(title, instruction, context, extras):
    return (
        f"You are an expert academic editor drafting the '{title}' section of a "
        f"health-information practicum report for a Ghanaian college (COHY).\n\n"
        f"{_COMMON_RULES}\n"
        f"{extras}"
        f"Section instructions (authoritative):\n{instruction}\n\n"
        f"--- STUDENT DATA ---\n{context}\n-------------------\n"
    )


# ============================================================
# Report drafting
# ============================================================

def draft_sections_with_ai(session, samples=None, target_pages=None):
    if not getattr(settings, 'REPORT_AI_DRAFTING_ENABLED', True):
        return {}

    if not _ai_configured():
        logger.error('AI drafting skipped: no REPORT_AI_CALLABLE or API key configured')
        return {'_ai_note': (
            'AI drafting is not configured on the server, so the template text was '
            'used. Ask the administrator to set an AI provider key.'
        )}

    context = _build_context(session)
    if not context.strip():
        return {}

    log_ngrams = _build_log_ngrams(session)
    try:
        competencies = list(session.competencies.values_list('text', flat=True))
    except Exception:
        competencies = []

    units = _session_units(session)
    fallback_unit = units[0][0] if len(units) == 1 else ''
    log_count = session.daily_logs.filter(is_deleted=False).count()

    extras = ''
    if competencies:
        extras += (
            "COMPETENCY RULE: where natural, let each section support at least one "
            "of the listed competency areas.\n"
        )
    if samples:
        notes = [s.style_notes.strip() for s in samples if getattr(s, 'style_notes', '')]
        if notes:
            extras += ("FORMAT GUIDANCE from the department's official samples "
                       "(follow strictly):\n" + "\n".join(f"- {n}" for n in notes) + "\n")
    if log_count < 5:
        extras += (
            f"SPARSE LOGS: the student has only {log_count} daily log(s). Never pad "
            "with invented activities, units, workshops or numbers. Refine and "
            "enrich each REAL activity with true professional context (the system "
            "used, the data-quality check applied, the competency supported). A "
            "shorter, accurate section is better than an invented one.\n"
        )
    elif log_count < 15:
        extras += ("NOTE: synthesise the logs; do not add units or activities that "
                   "are not in them.\n")

    budgets = _compute_word_budget(target_pages, log_count=log_count,
                                   competency_count=len(competencies))

    note = ''
    if log_count < 5:
        note = (f"Note: you have very few logs ({log_count}). The AI stayed close to what you "
                f"logged. Adding more detailed daily logs will make the report fuller"
                + (f" and help reach your {target_pages}-page target." if target_pages else "."))
    elif log_count < 15 and target_pages:
        note = (f"Note: if the report falls short of your {target_pages}-page target, "
                f"adding more daily logs will give the AI more material.")

    max_seconds = getattr(settings, 'REPORT_AI_DRAFTING_MAX_SECONDS', DEFAULT_MAX_SECONDS)
    workers = getattr(settings, 'REPORT_AI_DRAFTING_WORKERS', 6)
    deadline = time.time() + max_seconds

    def make_task(key, title, instruction):
        budget = budgets.get(key, 0)
        hint = ''
        if budget:
            hint = (f"LENGTH: aim for roughly {int(budget * 0.8)} to {int(budget * 1.2)} "
                    f"words, no more than the source material supports.\n")
        prompt = _section_prompt(title, instruction, context, extras + hint)
        max_tokens = _SECTION_MAX_TOKENS.get(key, _DEFAULT_MAX_TOKENS)
        floor = _SECTION_FLOORS.get(key, 0)
        max_word = int(budget * 1.5) if budget else 0

        def run():
            reply = _call_ai(prompt, max_tokens=max_tokens)
            if reply is None:
                return None, ['no_reply']
            text = finalize_section(key, reply, fallback_unit, title)
            issues = [i for i in _quality_issues(key, text, log_ngrams, floor, max_word)
                      if i[0] != 'too_long']
            if not issues:
                return text, []
            if time.time() < deadline - 12:
                retry_reply = _call_ai(_build_retry_prompt(prompt, title, issues),
                                       max_tokens=max_tokens)
                if retry_reply is not None:
                    retry_text = finalize_section(key, retry_reply, fallback_unit, title)
                    retry_issues = [i for i in _quality_issues(key, retry_text, log_ngrams, floor, max_word)
                                    if i[0] != 'too_long']
                    if not retry_issues:
                        return retry_text, []
                    if retry_text and (not text or len(retry_text) > len(text)):
                        text = retry_text
                    issues = retry_issues
            return (text or None), [code for code, _ in issues]

        return run

    tasks = [(key, make_task(key, title, instruction))
             for key, title, instruction in DRAFT_SECTIONS]
    results = _run_parallel(tasks, max_seconds, workers)

    drafts = {}
    failed_titles, flagged_titles = [], []
    for key, title, _instr in DRAFT_SECTIONS:
        text, issue_codes = results.get(key, (None, ['timeout']))
        if text:
            drafts[key] = text
            if issue_codes:
                flagged_titles.append(title)
        else:
            failed_titles.append(title)

    notes_out = [note] if note else []
    if failed_titles:
        notes_out.append(
            "These sections could not be drafted by the AI and use clean fallback "
            "text instead: " + ', '.join(failed_titles) +
            ". Click Generate again to retry them."
        )
    if flagged_titles:
        notes_out.append(
            ', '.join(flagged_titles) + " were drafted but flagged for review. "
            "Open 'Edit Report Manually' to check them."
        )

    logger.info('AI drafting finished: %d drafted, %d failed, %d flagged (v=%s)',
                len(drafts), len(failed_titles), len(flagged_titles), PROMPT_VERSION)

    out = {}
    if notes_out:
        out['_ai_note'] = ' '.join(notes_out)
    if not drafts:
        return out                    # nothing usable: no fingerprint, so it retries next time
    out.update(drafts)
    out['_source_hash'] = source_fingerprint(session)
    return out


# ============================================================
# Presentation drafting
# ============================================================

def _clean_pptx_text(text):
    if not text:
        return ''
    text = _normalize_unicode(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\s+[\u2013\u2014]\s+', ': ', text)
    text = re.sub(r'\s+-\s+', ': ', text)
    text = re.sub(r'^[ \t]*#+[ \t]*.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*[<\[][ \t]*([^<>\[\]\n]+?)[ \t]*[>\]][ \t]*$', r'\1',
                  text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = _strip_filler(text)
    return text.strip()


def _finalize_pptx(key, text):
    text = _clean_pptx_text(text)
    if not text:
        return ''

    if key == 'pptx_speaker_notes':
        notes = {}
        for line in text.splitlines():
            if ':' not in line:
                continue
            head, _, body = line.partition(':')
            head, body = head.strip(), body.strip()
            for title in PPTX_SLIDE_TITLES:
                if head.lower().startswith(title.lower()[:8]) and body:
                    notes[title] = body
                    break
        return '\n'.join(f"{t}: {notes[t]}" for t in PPTX_SLIDE_TITLES if t in notes)

    lines = []
    for raw in text.splitlines():
        line = re.sub(r'^\s*[*\-\u2022\u00b7]\s+|^\s*\d+[\.\)]\s+', '', raw).strip()
        line = re.sub(r'[<>]', '', line).strip().rstrip('.')
        if not line:
            continue
        line = _polish_english(_capitalise_first(line))
        lowered = line.lower()
        if key in ('pptx_activities_bullets', 'pptx_skills_bullets'):
            if any(m in lowered for m in _OBSTACLE_MARKERS) or \
               any(m in lowered for m in _SOLUTION_MARKERS):
                continue
        lines.append(line)

    seen, uniq = set(), []
    for line in lines:
        if line.lower() not in seen:
            seen.add(line.lower())
            uniq.append(line)
    return '\n'.join(uniq[:_PPTX_MAX_BULLETS.get(key, 6)])


def draft_presentation_with_ai(session, target_slides=7):
    if not getattr(settings, 'REPORT_AI_DRAFTING_ENABLED', True):
        return {}
    if not _ai_configured():
        logger.error('PPTX AI drafting skipped: no AI provider configured')
        return {}

    context = _build_context(session, include_report=True)
    if not context.strip():
        return {}

    try:
        competencies = list(session.competencies.values_list('text', flat=True))
    except Exception:
        competencies = []
    log_count = session.daily_logs.filter(is_deleted=False).count()

    extras = _PPTX_RULES
    if competencies:
        extras += "Reflect the competency areas in the Skills and Conclusion bullets where natural.\n"
    if log_count < 5:
        extras += "The student has very few logs: stay truthful and do not invent anything.\n"

    max_seconds = getattr(settings, 'REPORT_AI_DRAFTING_MAX_SECONDS', DEFAULT_MAX_SECONDS)
    workers = getattr(settings, 'REPORT_AI_DRAFTING_WORKERS', 6)
    deadline = time.time() + max_seconds

    def make_task(key, title, instruction):
        prompt = (
            f"You are preparing '{title}' content for a PowerPoint presentation "
            f"summarising a health-information practicum report for a Ghanaian "
            f"college (COHY).\n\n"
            f"{extras}\n"
            f"Section instruction (authoritative):\n{instruction}\n\n"
            f"--- STUDENT DATA ---\n{context}\n-------------------\n"
        )

        def run():
            reply = _call_ai(prompt, max_tokens=500)
            text = _finalize_pptx(key, reply) if reply else ''
            if not text and time.time() < deadline - 10:
                reply = _call_ai(prompt + "\nYour previous answer was empty or invalid. "
                                 "Answer again following the format exactly.", max_tokens=500)
                text = _finalize_pptx(key, reply) if reply else ''
            return text

        return run

    tasks = [(key, make_task(key, title, instruction))
             for key, title, instruction in PPTX_DRAFT_SECTIONS]
    results = _run_parallel(tasks, max_seconds, workers)

    drafts = {key: text for key, text in results.items() if text}
    missing = [k for k, _t, _i in PPTX_DRAFT_SECTIONS if k not in drafts]
    if missing:
        logger.warning('PPTX AI drafting: %d slide section(s) missing: %s', len(missing), missing)
    if not drafts:
        return {}
    drafts['_source_hash'] = source_fingerprint(session)
    return drafts


# ============================================================
# Manual-edit refinement
# ============================================================

_REFINE_INSTRUCTIONS = {
    'introduction':         'Polish into ONE concise first-person paragraph. Preserve all facts, units and dates. Fix grammar and flow. Under 140 words.',
    'facility_profile':     'Polish into third-person prose about the facility. Preserve all facts. Do NOT use "I". Fix grammar and flow.',
    'activities_performed': 'Keep the layout: a plain unit heading line (no < > or [ ]) followed by one past-tense sentence per line. Preserve every unit and activity. Fix grammar, capitalisation and full stops.',
    'skills_acquired':      'Keep one skill per line, each starting with "I learnt how to" or "I was able to" and naming the activity it came from. Preserve every skill. Fix grammar.',
    'workshops':            'Polish into one short paragraph per workshop, in clear English without duplicate words. Preserve every workshop fact.',
    'lessons_learnt':       'Polish into 1 or 2 cohesive first-person paragraphs. Preserve every point. Fix grammar and flow.',
    'observation':          'Polish into 1 or 2 first-person paragraphs. Preserve every observation. Fix grammar.',
    'observations':         'Polish into 1 or 2 first-person paragraphs. Preserve every observation. Fix grammar.',
    'challenges':           'Polish into short first-person paragraphs, one per challenge. Preserve every challenge. Fix grammar.',
    'recommendations':      'Polish into 1 or 2 professional paragraphs. Preserve every recommendation. Fix grammar.',
    'conclusion':           'Polish into 1 or 2 first-person paragraphs. Preserve every point. Fix grammar and flow.',
    'abstract':             'Polish into one third-person academic paragraph. Preserve every fact. Fix grammar and flow.',
    'acknowledgments':      'Polish into 1 or 2 formal first-person paragraphs. Preserve every name and thanks. Fix grammar.',
    'references':           'Preserve every reference. Fix punctuation and APA formatting only. Do NOT remove any reference.',
}


def refine_user_content(session, current_content):
    if not getattr(settings, 'REPORT_AI_DRAFTING_ENABLED', True):
        return dict(current_content or {})

    units = _session_units(session)
    fallback_unit = units[0][0] if len(units) == 1 else ''
    ai_ok = _ai_configured()

    refined, tasks = {}, []

    for key, value in (current_content or {}).items():
        if str(key).startswith('_'):
            refined[key] = value
            continue

        text = (value or '').strip()
        if not text:
            refined[key] = ''
            continue

        instruction = _REFINE_INSTRUCTIONS.get(key)
        polished_original = finalize_section(key, text, fallback_unit) or _polish_english(text)
        refined[key] = polished_original               # safe default

        if not instruction or not ai_ok:
            continue

        prompt = (
            "You are polishing one section of a health-information practicum report "
            "written by a student. Your ONLY job is to fix grammar, capitalisation "
            "and flow and to raise the tone to official English.\n\n"
            "RULES:\n"
            "1. PRESERVE every fact, number, unit, activity, workshop and skill. Do not invent anything.\n"
            "2. PRESERVE the structure (same paragraphs or lines).\n"
            "3. Keep the first person ('I') where the input uses it. Every sentence and line starts with a capital letter.\n"
            "4. No bullet symbols, no markdown, no angle brackets, no heading.\n"
            "5. Output ONLY the refined section text, nothing else.\n\n"
            f"Section: {key}\nInstruction: {instruction}\n\n"
            f"--- INPUT ---\n{text}\n--- END INPUT ---\n"
        )

        def make_run(k=key, orig=text, p=prompt, polished=polished_original):
            def run():
                reply = _call_ai(p, max_tokens=_SECTION_MAX_TOKENS.get(k, _DEFAULT_MAX_TOKENS))
                if not reply:
                    return polished
                cleaned = finalize_section(k, reply, fallback_unit)
                if not cleaned:
                    return polished
                o, r = len(orig.split()), len(cleaned.split())
                if o > 20 and (r < o * 0.4 or r > o * 2.5):
                    logger.warning('refine_user_content: %s length drift (orig=%d, refined=%d); '
                                   'keeping polished original', k, o, r)
                    return polished
                return cleaned
            return run

        tasks.append((key, make_run()))

    max_seconds = getattr(settings, 'REPORT_AI_REFINE_MAX_SECONDS', 80)
    workers = getattr(settings, 'REPORT_AI_DRAFTING_WORKERS', 6)
    for key, result in _run_parallel(tasks, max_seconds, workers).items():
        if result:
            refined[key] = result

    return refined