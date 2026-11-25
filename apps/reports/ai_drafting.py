"""
Best-effort AI drafting for report sections, with code-enforced quality gates.

Two entry points:
  - draft_sections_with_ai(session)        → report prose   → report.content
  - draft_presentation_with_ai(session)    → slide bullets  → report.pptx_content

Plus one refinement entry point (v9):
  - refine_user_content(session, content)  → polish user edits without redrafting

QUALITY ENFORCEMENT (v10):
  Every drafted section passes through a post-processing gate that detects:
    1. Verbatim copy from raw logs (7-word n-gram match)
    2. Placeholder text ('[insert', 'TODO', 'lorem ipsum', etc.)
    3. Markdown pollution (## headings, **bold**, *, -, • bullets)
    4. Under-length drafts (< 50% of section word floor)
    5. Over-length drafts (> 125% of section word target — soft warning only)
  Sections failing a HARD gate are retried once with a stricter prompt that
  names the specific problem. Retry budget is capped (3 retries / 30s) so
  the time budget stays intact.

SOURCE-OF-TRUTH (v10):
  Logs and competencies are the ONLY source of fact. Each log field is
  labelled in the context (ACTIVITY:, SKILL:, CHALLENGE:, SOLUTION:) so the
  AI maps each one to the correct report section — activities never absorb
  solutions, challenges are never invented from activities, and the
  Recommendations section builds from the challenges, not from log
  solutions.

SOURCE FINGERPRINT (v8):
  source_fingerprint(session) is written into the returned drafts dict as
  '_source_hash'. views.py compares this hash against the saved one to
  detect stale drafts (new logs, new workshops, new competencies) and
  trigger re-drafting.

MANUAL EDIT REFINEMENT (v9):
  refine_user_content(session, content) takes the user's exact edited text
  from the edit page and returns a polished version that preserves every
  fact, number, unit, activity, workshop, and skill — only the grammar,
  sentence flow, and academic tone change.

ORDER MATTERS: Sections are ordered by PRIORITY, not report sequence.
  If the 90s budget runs out, the sections that got drafted are the ones
  that matter most. Activities / Introduction / Skills first. Abstract and
  References last.
"""

import hashlib
import json
import logging
import random
import re
import time

from django.conf import settings

logger = logging.getLogger(__name__)


# ============================================================
# Report drafting — section priority order
# ============================================================
# NOTE: 'facility_profile' is deliberately NOT drafted by the AI.
# It's built purely from the facility record in the database.
#
# v10 prompt discipline:
#   - Introduction: 2 paragraphs, 150-250 words
#   - Activities:   unit-headed activity lines (bullets), source = log ACTIVITY field ONLY
#   - Skills:       5-7 items, each reflects a specific activity from Activities Performed
#   - Lessons:      5-7 reflections (NOT activity summaries); each connects to a competency
#   - Challenges:   actual challenges from logs (paragraphs)
#   - Recommendations: built from Challenges + Observations, NOT from log solutions

DRAFT_SECTIONS = [
    ('introduction', 'Introduction',
     "Write exactly 2 concise paragraphs using first-person voice ('I'). "
     "Paragraph 1 (4-5 sentences): purpose of the attachment, facility name, location, period, "
     "and the units you rotated through (one sentence per unit is enough). "
     "Paragraph 2 (3-4 sentences): the competency areas you targeted and a one-line preview "
     "of how this report documents each. "
     "CRITICAL: Do NOT refer to yourself as 'the student' or 'the intern'. Write AS the student. "
     "KEEP IT SHORT: 150-250 words total. Do NOT add extra paragraphs. "
     "Do NOT use bullet points, asterisks, hyphens, or em dashes. Do NOT include a heading."),

    ('activities_performed', 'Activities Performed',
     "Write ONE opening paragraph of 1-2 sentences framing the attachment's activities. "
     "Then for EACH unit that ACTUALLY appears in the logs, create a SUB-HEADING with the unit name "
     "(e.g. 'Records Unit', 'RCH') followed by 2-3 ACTIVITY LINES (one per line). "
     "Each activity line = ONE activity from the log 'ACTIVITY:' field, rephrased into polished, "
     "persuasive academic English. Preserve the exact MEANING of the log activity — you may extend "
     "the sentence with professional context, but do NOT change what the student did. "
     "CRITICAL: Do NOT include challenges, solutions, or observations here. Those belong to their own sections. "
     "Do NOT invent activities. Do NOT copy raw log text verbatim — refine grammar and phrasing. "
     "Do NOT use more than 3 activity lines per unit. Use first-person voice ('I'). "
     "Aim for 250-350 words total. Do NOT include the main heading."),

    ('skills_acquired', 'Skills Acquired',
     "Provide 5-7 specific skills the student developed. Output ONLY plain list items, one per line. "
     "CRITICAL: ABSOLUTELY NO numbers, hyphens, asterisks, bullets, or HTML tags at the start of the line. "
     "Use the format: Skill Name: 2 sentences describing HOW the skill was developed through a specific activity. "
     "Each skill MUST be a MORE DETAILED REFLECTION of an activity from the Activities Performed section — "
     "explain what the student actually learned from DOING that activity. "
     "Do NOT introduce a skill not evidenced by a logged activity. Use first-person ('I'). "
     "Connect each skill to a competency area where natural. Do NOT include a heading. "
     "Aim for 200-300 words total."),

    ('lessons_learnt', 'Lessons Learnt',
     "Provide 5-7 detailed LESSONS, one per line. Output ONLY plain list items. "
     "CRITICAL: ABSOLUTELY NO numbers, hyphens, asterisks, bullets, or HTML tags at the start of the line. "
     "CRITICAL: This is NOT a summary of activities. Do NOT list what you did. "
     "Each lesson MUST be a REFLECTION — an insight, realisation, or principle you learned. "
     "Start each lesson with 'I learnt', 'I realised', 'I appreciated', 'I now understand', or 'I discovered'. "
     "Each lesson MUST be 2-3 sentences: what you observed, and what broader principle you took from it. "
     "EACH lesson MUST connect to a competency area and broaden to real HIM practice: "
     "patient confidentiality, teamwork, data quality, professional ethics, workflow efficiency. "
     "Use first-person ('I'). Do NOT copy raw log text. Do NOT repeat Activities Performed or Skills Acquired. "
     "Do NOT include a heading. Aim for 200-300 words total."),

    ('challenges', 'Challenges',
     "Write 3-4 substantial paragraphs about the challenges the student ACTUALLY faced. "
     "CRITICAL FIRST STEP: Read the daily logs below and identify the SPECIFIC challenges the student recorded "
     "(the 'CHALLENGE:' field of each log). "
     "If the logs recorded challenges, SYNTHESIZE and REFINE them into academic prose — do NOT copy verbatim. "
     "If the logs recorded NO challenges, generate 2-3 realistic ones based on the units and competencies listed, "
     "drawing on typical Ghanaian HIM practice (patient congestion, illegible handwriting on paper records, "
     "network downtime on LHIMS, incomplete NHIS claim documentation, storage space limitations). "
     "STRUCTURE: Paragraph 1 introduces the challenges generally and their significance. "
     "Paragraphs 2-3 each describe ONE specific challenge in detail — what happened, its impact on the work, "
     "and how the student responded. Paragraph 4 (optional) reflects on what these challenges reveal about "
     "the facility's systems. "
     "Use first-person ('I'). Do NOT include a heading or bullet list. "
     "CRITICAL: NEVER paste raw log text. Always transform into polished academic English."),

    ('recommendations', 'Recommendations',
     "Write ONE opening paragraph of 2-3 sentences framing the recommendations, based on the challenges "
     "and observations described earlier in this report. "
     "Then provide a NUMBERED list of 6-8 actionable recommendations, each 2-3 sentences. "
     "Split them into two groups with bold sub-headings: 'For the Facility:' (3-4 items) and 'For the College:' (3-4 items). "
     "EACH recommendation MUST follow this structure: WHO should do WHAT, and WHY it matters based on a "
     "specific challenge you encountered. "
     "CRITICAL: Do NOT copy raw 'SOLUTION:' text from the logs. Do NOT copy template text. "
     "Generate fresh, specific, actionable recommendations based on the student's actual experience in this attachment. "
     "Use professional academic tone (first-person plural 'we' or third-person 'the facility should'). "
     "Do NOT include the main heading."),

    ('conclusion', 'Conclusion',
     "Write 3 substantial paragraphs using first-person ('I'). "
     "Paragraph 1: summarize the overall experience, units rotated, and skills gained. "
     "Paragraph 2: reflect on how the practicum prepares the student for professional HIM practice "
     "and connect back to the competency areas. "
     "Paragraph 3: express gratitude to the facility, staff, and College, and outline next steps in "
     "the student's academic and professional journey. "
     "Do NOT include a heading."),

    ('observation', 'Observations',
     "Write 3-4 substantial paragraphs synthesizing observations about workflows, staffing, "
     "record systems, data quality, and interdepartmental coordination. Use first-person ('I'). "
     "Weave specific details from the logs into a cohesive academic narrative. "
     "Connect observations to the competency areas where relevant. "
     "CRITICAL: YOU ARE FORBIDDEN FROM LISTING RAW LOG ENTRIES. "
     "Example synthesis: if logs mention 'electronic system' and 'high patient volume', write "
     "'I observed that while the LHIMS electronic health information system streamlined data management, "
     "the high patient volume often led to bottlenecks during peak hours, revealing gaps in workflow efficiency.' "
     "Do NOT include a heading."),

    ('abstract', 'Abstract',
     "Write a single academic paragraph in third person. "
     "Synthesize the whole experience: purpose, facility, units rotated, main skills developed, "
     "and primary outcome. Explicitly name AT LEAST THREE of the student's competency areas. "
     "Cover the arc of the attachment from start to finish. "
     "Do NOT copy raw logs. Refine every sentence into academic language. "
     "Do NOT include a heading or keyword line."),

    ('acknowledgments', 'Acknowledgments',
     "Write 2-3 formal paragraphs thanking the facility, the clinical supervisor (if named), "
     "the staff of each major unit the student rotated through, and the College. "
     "Use first-person singular ('I'). Each paragraph should be 3-5 sentences. "
     "Include a brief reflection on how the support received contributed to the student's learning. "
     "Check grammar rigorously. Do NOT include a heading."),

    ('workshops', 'Workshops',
     "Write exactly 2 paragraphs. "
     "Paragraph 1: describe each workshop or training session attended (date, topic, presenter if known), "
     "and the specific skills or knowledge gained from each. "
     "If NO formal workshops were attended, say exactly that and describe the practical on-the-job "
     "training received instead. "
     "Paragraph 2: reflect on how the workshops (or on-the-job training) complemented the practical "
     "experience and connected to the competencies. "
     "Use first-person ('I'). Do NOT include a heading."),

    ('references', 'References',
     "APA-style reference list for any sources you cite. If you cite nothing, return an empty response. "
     "Do NOT include a heading."),
]

DEFAULT_MAX_SECONDS = 90


# ============================================================
# Presentation drafting (slide bullets + speaker notes)
# ============================================================
# v10 discipline: each slide draws from the CORRECT section of the report.
# Activities slide = ACTIVITY field only. Challenges slide = CHALLENGE field only.
# Observation/Recs slide = OBSERVATION + recommendations. Never cross-contaminate.

PPTX_DRAFT_SECTIONS = [
    ('pptx_intro_bullets', 'Slide: Introduction',
     "Write 3 short bullet points for a presentation slide titled 'Introduction'. "
     "Each bullet MUST be 12 words or fewer. Output ONE bullet per line, no markers, no numbers. "
     "Use first-person ('I'). Cover: purpose of attachment, facility name, key units rotated. "
     "Example line: 'Attached to Adidome Government Hospital for four weeks'\n"
     "FORBIDDEN: more than 3 bullets. FORBIDDEN: any bullet longer than 12 words. "
     "FORBIDDEN: em dash, en dash, hyphen as bullet separator, asterisks, markdown."),

    ('pptx_facility_bullets', 'Slide: Facility Profile',
     "Write 3 short bullet points for a slide titled 'Facility Profile'. "
     "Each bullet MUST be 12 words or fewer. Output ONE bullet per line, no markers. "
     "Cover: facility type and location, key services (OPD, RCH, Laboratory, Pharmacy, Maternity), "
     "and the NHIS/DHIMS data workflow. Do NOT use first-person — describe the facility, not the student. "
     "FORBIDDEN: more than 3 bullets. FORBIDDEN: bullets longer than 12 words. "
     "FORBIDDEN: em dash, en dash, hyphen as bullet separator, asterisks, markdown."),

    ('pptx_activities_bullets', 'Slide: Activities Performed',
     "Write 4-6 short bullet points for a slide titled 'Activities Performed'. "
     "Each bullet MUST be 10 words or fewer. Output ONE bullet per line, no markers. "
     "Format each bullet as: '<Unit>: <one key activity>'. "
     "SOURCE: use ONLY the ACTIVITY field from the logs (labelled 'ACTIVITY:' in the data below). "
     "FORBIDDEN: including CHALLENGE or SOLUTION text from logs — those belong on the Challenges slide, not here. "
     "FORBIDDEN: including observations, recommendations, or anything that is not a direct activity. "
     "Only mention units that ACTUALLY appear in the logs. "
     "Example line: 'RCH: registered pregnant women and booked appointments'\n"
     "FORBIDDEN: more than 6 bullets. FORBIDDEN: bullets longer than 10 words. "
     "FORBIDDEN: em dash, en dash, hyphen as bullet separator, asterisks, markdown."),

    ('pptx_skills_bullets', 'Slide: Skills Acquired',
     "Write 4-5 short bullet points naming skills acquired. "
     "Each bullet MUST be 8 words or fewer. Output ONE bullet per line, no markers. "
     "Just the skill name plus one short qualifier. "
     "Example line: 'Patient registration and LHIMS data entry'\n"
     "FORBIDDEN: more than 5 bullets. FORBIDDEN: bullets longer than 8 words. "
     "FORBIDDEN: em dash, en dash, hyphen as bullet separator, asterisks, markdown."),

    ('pptx_challenges_bullets', 'Slide: Challenges Faced',
     "Write EXACTLY 2 short bullet points. Each bullet MUST be 12 words or fewer. "
     "Output ONE bullet per line, no markers. "
     "SOURCE: use the CHALLENGE field from the logs. "
     "Bullet 1: the most significant challenge from the logs (e.g. high patient volume causing delays). "
     "Bullet 2: the second most significant challenge (e.g. illegible handwriting in records). "
     "FORBIDDEN: more than 2 bullets. FORBIDDEN: bullets longer than 12 words. "
     "FORBIDDEN: em dash, en dash, hyphen as bullet separator, asterisks, markdown. "
     "FORBIDDEN: including activities or solutions — this slide is ONLY challenges."),

    ('pptx_observation_recommendation_bullets', 'Slide: Observation and Recommendation',
     "Write exactly 4 short bullet points for a slide titled 'Observation and Recommendation'. "
     "Each bullet MUST be 14 words or fewer. Output ONE bullet per line, no markers. "
     "The FIRST 2 bullets must be observations about workflows, staffing, or record systems "
     "drawn from the OBSERVATION section. "
     "The LAST 2 bullets must be actionable recommendations for the facility or the college "
     "drawn from the RECOMMENDATIONS section. "
     "FORBIDDEN: more than 4 bullets. "
     "FORBIDDEN: separating observations and recommendations into different headings. "
     "FORBIDDEN: em dash, en dash, hyphen as bullet separator, asterisks, markdown."),

    ('pptx_conclusion_bullets', 'Slide: Conclusion',
     "Write 2-3 short bullet points summarising the practicum. "
     "Each bullet MUST be 12 words or fewer. Output ONE bullet per line, no markers. "
     "Cover: overall experience, key takeaway, readiness for professional practice. "
     "FORBIDDEN: more than 3 bullets. "
     "FORBIDDEN: em dash, en dash, hyphen as bullet separator, asterisks, markdown."),

    ('pptx_speaker_notes', 'Speaker Notes for All Slides',
     "Write 2 short sentences of speaker notes (what the presenter should say aloud) for each slide. "
     "Cover exactly these 7 slides in this order: "
     "Introduction, Facility Profile, Activities, Skills, Challenges, Observation and Recommendation, Conclusion.\n"
     "Output format — one slide per line, exactly like this:\n"
     "Introduction: <2 sentences>\n"
     "Facility Profile: <2 sentences>\n"
     "(continue for all 7 slides)\n"
     "Do NOT use bullet points, asterisks, or markdown. Do NOT add a heading. "
     "FORBIDDEN: more than 7 lines total."),
]


# ============================================================
# Quality enforcement constants + helpers
# ============================================================

# A 7-word match against a raw log is a strong verbatim-copy signal.
# Shorter windows (5-6) trigger false positives on common English phrasing.
_VERBATIM_NGRAM_SIZE = 7

# Words too common to be meaningful in a verbatim-copy check.
_QUALITY_STOPWORDS = frozenset({
    'a','an','the','and','or','of','in','on','at','to','for','with','by','as',
    'is','was','were','be','been','being','have','has','had','do','does','did',
    'i','me','my','we','our','this','that','these','those','from','into','within',
    'also','other','some','various','including','included','such','it','its',
    'but','so','if','when','while','than','then','there','their','they','them',
    'he','she','his','her','him','you','your','will','would','can','could',
    'should','may','might','must','not','no','yes','very','just','only','been',
})

# Placeholder tokens — if any appear, the section is considered invalid.
_PLACEHOLDER_TOKENS = (
    '[insert', '[your name', '[facility name', '[date]', '[name]',
    'lorem ipsum', '<insert', 'todo:', 'placeholder:', 'xxxx', 'tbd',
)

# Retry budget: prevents retries from starving the time budget.
_MAX_RETRIES_TOTAL = 3
_MAX_RETRY_SECONDS = 30


# ============================================================
# Source-of-truth rules (included in every prompt)
# ============================================================
# These rules sit ABOVE the page-target/word-budget rules. They stop
# the AI from inventing units, activities, workshops, dates, and
# patient counts to fill space.

_SOURCE_BOUND_RULES = (
    "\nSOURCE-OF-TRUTH RULES (highest priority, overrides page target):\n"
    "A. The student's daily logs are the ONLY source for units, activities, "
    "workshops, dates, patient counts, and systems used. Do NOT invent units, "
    "workshops, dates, patient numbers, or activities that are not supported "
    "by the logs.\n"
    "B. Each log field is labelled in the data below:\n"
    "   - ACTIVITY → use ONLY for Activities Performed and slide Activities.\n"
    "   - SKILL → use ONLY for Skills Acquired.\n"
    "   - CHALLENGE → use ONLY for Challenges and slide Challenges.\n"
    "   - SOLUTION → use ONLY for Recommendations (paraphrase, do not copy).\n"
    "   Do NOT cross-map fields between sections.\n"
    "C. Competency areas are the second source. Use them to explain skills, "
    "lessons, challenges, observations, recommendations, and conclusion.\n"
    "D. The page target is ONLY a length guide. It never justifies inventing "
    "facts. If logs are sparse, write a shorter, well-structured report. "
    "Expand ONLY in Lessons Learnt, Observation, Recommendations, Conclusion, "
    "and Abstract — using the competency areas as reflection material.\n"
    "E. Activities Performed and Skills Acquired must mirror each other. "
    "Every skill MUST point to an activity/unit that appears in the logs.\n"
    "F. Lessons Learnt MUST be derived from Skills Acquired + Competency Areas. "
    "Do NOT list activities as lessons — list insights and principles.\n"
    "G. If no workshops were recorded, say exactly that: no formal workshops "
    "were attended; describe on-the-job training only.\n"
)


def source_fingerprint(session):
    """
    Deterministic hash of everything that drives AI drafting:
    logs, competencies, workshops, and attachment metadata.

    views.py stores this in report.content['_source_hash'] and
    report.pptx_content['_source_hash']. When the hash changes,
    the saved AI draft is stale and must be re-drafted.
    """
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
    """
    Precompute the set of n-word tuples appearing verbatim in the raw logs.
    Returns an empty set on error (fail-safe: no copy detection, but no crash).
    """
    ngrams = set()
    try:
        logs = session.daily_logs.filter(is_deleted=False)
        for log in logs:
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
                    # Skip windows that are mostly stopwords — "in the records
                    # unit and the" shouldn't trigger a copy flag.
                    content_words = [w for w in window if w not in _QUALITY_STOPWORDS]
                    if len(content_words) < 3:
                        continue
                    ngrams.add(window)
    except Exception:
        logger.exception('Could not build log n-grams for verbatim-copy detection')
    return ngrams


def _find_verbatim_copy(text, log_ngrams, n=_VERBATIM_NGRAM_SIZE):
    """Return the first n-word phrase in `text` that appears verbatim in logs, or None."""
    if not log_ngrams or not text:
        return None
    words = tuple(re.findall(r'\w+', text.lower()))
    for i in range(len(words) - n + 1):
        window = words[i:i + n]
        if window in log_ngrams:
            return ' '.join(window)
    return None


def _quality_issues(section_key, text, log_ngrams, min_word_floor=0, max_word=0):
    """
    Run every quality gate against a drafted section.

    Returns list of (code, detail) tuples. Empty list = section passes.

    Note: 'too_long' is a SOFT issue — callers must filter it out before
    deciding to retry. Too-long drafts are NOT rewritten because doing so
    usually costs a retry and rarely improves the section.
    """
    if not text or not text.strip():
        return [('empty', 'AI returned empty text')]

    issues = []

    # 1. Verbatim copy from raw logs
    copied = _find_verbatim_copy(text, log_ngrams)
    if copied:
        issues.append(('verbatim_copy', f'copied raw log phrase: "{copied}"'))

    # 2. Placeholder text
    lowered = text.lower()
    for token in _PLACEHOLDER_TOKENS:
        if token in lowered:
            issues.append(('placeholder', f'contains placeholder: "{token}"'))
            break

    # 3. Under-length drafts (only if we know the section's floor)
    if min_word_floor:
        actual_words = len(text.split())
        threshold = int(min_word_floor * 0.5)
        if actual_words < threshold:
            issues.append((
                'too_short',
                f'only {actual_words} words (minimum {threshold} expected)'
            ))

    # 4. Over-length drafts (SOFT — does NOT trigger retry)
    if max_word:
        actual_words = len(text.split())
        if actual_words > max_word:
            issues.append((
                'too_long',
                f'{actual_words} words (target max {max_word})'
            ))

    # 5. Markdown pollution that wasn't stripped
    if re.search(r'^[ \t]*#{1,6}[ \t]', text, re.MULTILINE):
        issues.append(('markdown_headers', 'contains raw markdown # headers'))
    if '**' in text or re.search(r'^\s*[*\-\u2022]\s+', text, re.MULTILINE):
        issues.append(('markdown_noise', 'contains ** or bullet/asterisk markers'))

    return issues


def _clean_draft_text(text):
    """
    Universal cleanup applied to every AI reply before quality check.

    v10: the markdown-heading strip now matches leading whitespace AND
    missing spaces after #, so `##CHALLENGES` and ` ## CHALLENGES` are
    both removed. The old regex (`^#+\s+.*$`) missed both of those cases,
    which is why `## CHALLENGES` was leaking into the final PDF.
    """
    if not text:
        return ''
    # 1. Strip markdown heading lines (leading whitespace + # + optional space)
    text = re.sub(r'^[ \t]*#+[ \t]*.*$', '', text, flags=re.MULTILINE)
    # 2. Strip **bold** and __italic__ markdown
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # 3. Strip single-asterisk emphasis *text* (careful: not bullet markers)
    text = re.sub(r'(?<!\w)\*(?!\s)([^*\n]+?)\*(?!\w)', r'\1', text)
    # 4. Strip leading bullet / asterisk / hyphen / numbered markers
    text = re.sub(r'^\s*[*\-\u2022]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[\.\)]\s+', '', text, flags=re.MULTILINE)
    # 5. Strip a leading ALL-CAPS section title line (e.g. "INTRODUCTION\n\n")
    text = re.sub(r'^[A-Z][A-Z\s]{3,}\n+', '', text, count=1)
    # 6. Em dash / en dash → colon (keeps prose clean, avoids slide dashes)
    text = re.sub(r'\s+[\u2013\u2014]\s+', ': ', text)
    # 7. Collapse 3+ consecutive blank lines to a single paragraph break
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _build_retry_prompt(original_prompt, section_title, issues):
    """
    Retry prompt that names the specific problem so the AI knows what to fix.
    """
    problems = '\n'.join(f'  - {code}: {detail}' for code, detail in issues)
    return (
        f"⚠️ RETRY REQUIRED — YOUR PREVIOUS DRAFT OF '{section_title}' FAILED QUALITY CHECKS.\n\n"
        f"Specific problems detected:\n{problems}\n\n"
        f"MANDATORY FIXES:\n"
        f"1. If 'verbatim_copy' was flagged: rephrase EVERY phrase from the "
        f"raw logs into your own academic prose. No sequence of 5 or more words "
        f"from the raw logs may appear in your output. Transform sentence "
        f"structures completely — change verbs, add context, restructure.\n"
        f"2. If 'too_short' was flagged: expand the section by 2x or more. Add "
        f"descriptive context, worked examples, and academic analysis drawn from "
        f"the LOGS and the COMPETENCY AREAS. Do NOT invent activities, units, "
        f"workshops, dates, or patient counts.\n"
        f"3. If 'placeholder' was flagged: remove ALL placeholder text like "
        f"[insert], TODO, lorem ipsum. Write real content.\n"
        f"4. If 'markdown_headers' was flagged: remove every ## or # line. Use "
        f"plain prose only.\n"
        f"5. If 'markdown_noise' was flagged: remove ALL **bold**, *asterisk*, "
        f"-dash, • bullet, and numbered-list markers. Plain text only.\n\n"
        f"Now write the COMPLETE section again from scratch. Do not apologize, do not "
        f"explain — output only the section text.\n\n"
        f"{original_prompt}"
    )


# ============================================================
# Shared context builder + AI caller
# ============================================================

def _build_context(session):
    """
    Build the STUDENT DATA block passed to every prompt.

    v10: each log field is EXPLICITLY LABELLED (ACTIVITY / SKILLS /
    CHALLENGE / SOLUTION) so the AI can't confuse fields across
    sections. This is what fixes "challenges appearing on the
    Activities slide" — the AI now knows ACTIVITY is the only field
    it should use for the Activities section.
    """
    user = session.user
    lines = [
        f"STUDENT PROFILE:",
        f"Name: {user.get_full_name() or user.username}",
        f"Index number: {getattr(user, 'index_number', '')}",
        f"Level: {getattr(user, 'level', '?')}",
        f"Department: {user.get_department_display() if hasattr(user, 'get_department_display') else getattr(user, 'department', '')}",
        f"\nATTACHMENT DETAILS:",
        f"Facility: {session.facility.name if session.facility else '(not set)'}",
        f"Period: {session.start_date.strftime('%d %B %Y')} to {session.end_date.strftime('%d %B %Y')}",
    ]

    if session.facility:
        if session.facility.facility_type:
            lines.append(f"Facility type: {session.facility.facility_type}")
        if session.facility.district:
            lines.append(f"District: {session.facility.district}")
        if session.facility.region:
            lines.append(f"Region: {session.facility.region}")

        background = getattr(session.facility, 'history_and_background', '')
        if background:
            lines.append(f"Facility background info: {background[:600]}")
        else:
            lines.append(f"Facility background info: (none provided in database)")

    if session.assigned_supervisor_name:
        lines.append(f"Clinical supervisor: {session.assigned_supervisor_name}")

    competencies = []
    try:
        competencies = list(session.competencies.values_list('text', flat=True))
    except Exception:
        logger.exception('ai_drafting: could not read competencies')
    if competencies:
        lines.append(f"\nCOMPETENCY AREAS ({len(competencies)} total — the attachment is meant to develop these):")
        for c in competencies:
            lines.append(f"- {c}")
    else:
        lines.append("\nCOMPETENCY AREAS: (none provided by the student)")

    logs = list(session.daily_logs.filter(is_deleted=False).order_by('date'))
    if logs:
        lines.append(f"\nDAILY LOGS ({len(logs)} total — each field is labelled for correct section mapping):")
        for log in logs:
            summary = (log.activity_summary or '').strip().replace('\n', ' ')
            entry = f"\n- {log.date.strftime('%Y-%m-%d')} [{log.unit_sub_department}]"
            entry += f"\n  ACTIVITY (use for Activities Performed ONLY): {summary[:400]}"
            if log.skills_acquired:
                entry += f"\n  SKILLS (use for Skills Acquired ONLY): {log.skills_acquired[:200]}"
            if log.challenges_faced:
                entry += f"\n  CHALLENGE (use for Challenges section ONLY): {log.challenges_faced[:200]}"
                if log.solutions_applied:
                    entry += f"\n  SOLUTION (use for Recommendations ONLY — paraphrase, do not copy): {log.solutions_applied[:200]}"
            lines.append(entry)
    else:
        lines.append("\nDAILY LOGS: (No logs recorded yet.)")

    try:
        workshops = list(session.workshops.all().order_by('date'))
        if workshops:
            lines.append(f"\nWORKSHOPS ATTENDED ({len(workshops)}):")
            for w in workshops:
                w_type = getattr(w, 'type', '') or 'workshop'
                lines.append(f"- {w.date.strftime('%Y-%m-%d')}: {w_type} on {w.activity}")
        else:
            lines.append("\nWORKSHOPS ATTENDED: (None recorded.)")
    except Exception:
        logger.exception('ai_drafting: could not read workshops')

    return '\n'.join(lines)


def _call_ai(prompt):
    try:
        from importlib import import_module
        path = getattr(settings, 'REPORT_AI_CALLABLE', '')
        if not path:
            logger.info('AI drafting: REPORT_AI_CALLABLE not set — skipping')
            return None
        module_path, func_name = path.rsplit('.', 1)
        fn = getattr(import_module(module_path), func_name)
        return fn(prompt)
    except Exception:
        logger.exception('AI drafting: call failed')
        return None


def get_ai_reply_with_image(prompt, image_bytes, mime='image/jpeg'):
    vision_path = getattr(settings, 'REPORT_AI_VISION_CALLABLE', '') or ''
    if not vision_path:
        ai_path = getattr(settings, 'REPORT_AI_CALLABLE', '')
        if ai_path:
            module_path = ai_path.rsplit('.', 1)[0]
            vision_path = f'{module_path}.get_ai_reply_with_image'

    if not vision_path:
        return None

    try:
        from importlib import import_module
        module_path, func_name = vision_path.rsplit('.', 1)
        fn = getattr(import_module(module_path), func_name)
        return fn(prompt, image_bytes, mime=mime)
    except Exception:
        logger.exception('Vision OCR: call failed (path=%s)', vision_path)
        return None


# ============================================================
# Word budgets
# ============================================================
# v10: floors significantly reduced. Previous floors (Intro 300, Activities
# 800, Skills/Lessons 350) forced the AI to pad even when logs were thin,
# producing the wall-of-text the user complained about. New floors target
# concise sections: ~150 for Intro, ~300 for Activities, ~180 for
# Skills/Lessons. The AI is also told to stay within ±25% of target.

_SECTION_FLOORS = {
    'abstract':              120,
    'acknowledgments':       80,
    'introduction':          150,   # was 300
    'activities_performed':  300,   # was 800 — biggest cut
    'skills_acquired':       180,   # was 350
    'workshops':             60,    # was 120
    'lessons_learnt':        180,   # was 350
    'observation':           150,   # was 250
    'challenges':            180,   # was 250
    'recommendations':       180,   # was 250
    'conclusion':            120,   # was 180
}

_BUDGET_DISTRIBUTION = {
    'abstract':              0.040,
    'acknowledgments':       0.030,
    'introduction':          0.065,   # was 0.080
    'activities_performed':  0.150,   # was 0.220 — biggest cut
    'skills_acquired':       0.090,   # was 0.100
    'workshops':             0.030,
    'lessons_learnt':        0.090,   # was 0.100
    'observation':           0.080,
    'challenges':            0.075,
    'recommendations':       0.075,
    'conclusion':            0.055,
}


def _compute_word_budget(target_pages, log_count=0, competency_count=0):
    """
    Return per-section word targets.

    The ceiling is the MINIMUM of:
      - the user's requested page count × 300 words, and
      - the source capacity of the logs + competencies.
    This stops the AI from inventing content just to hit a page target.

    If the per-section FLOORS push the total well past the requested
    capacity, we scale all budgets down proportionally.
    """
    if target_pages and target_pages >= 5:
        effective_pages = target_pages
    else:
        if log_count < 5:
            effective_pages = 5
        elif log_count < 10:
            effective_pages = 8
        elif log_count < 20:
            effective_pages = 12
        else:
            effective_pages = 18

    requested = int(effective_pages * 300)
    source_capacity = (
        500
        + log_count * 180
        + competency_count * 100
    )
    total_prose_words = min(requested, source_capacity)

    budgets = {
        section: max(int(total_prose_words * pct), _SECTION_FLOORS.get(section, 0))
        for section, pct in _BUDGET_DISTRIBUTION.items()
    }

    # Proportional scaling — prevent floors from blowing the total past
    # the requested capacity. Allow 20% headroom for natural over-write.
    budget_sum = sum(budgets.values())
    headroom_ceiling = int(total_prose_words * 1.2)
    if budget_sum > headroom_ceiling and headroom_ceiling > 0:
        scale = headroom_ceiling / budget_sum
        budgets = {
            section: max(80, int(word_count * scale))
            for section, word_count in budgets.items()
        }

    return budgets


# ============================================================
# Shared quality rules (included in every prompt)
# ============================================================

_QUALITY_RULES = (
    "\nQUALITY RULES (Apply to every section):\n"
    "9. NEVER COPY RAW LOG TEXT. Transform sentence structure completely — "
    "no sequence of 7+ words from the raw logs may appear. Change verbs, add "
    "context, restructure. Write professional prose: "
    "'I processed approximately fifteen outpatient registrations per session' "
    "— not 'Registered 15 patients'. Fix all lowercase 'i' to capital 'I'.\n"
    "10. DO NOT INVENT ACTIVITIES, UNITS, DATES, WORKSHOPS, OR PATIENT COUNTS "
    "to hit a word target. Logs are the source of fact.\n"
    "11. EXPAND ONLY WHERE REFLECTION IS APPROPRIATE: Lessons Learnt, "
    "Observation, Recommendations, Conclusion, and Abstract. Use competency "
    "areas — not invented events — as the raw material for reflection.\n"
    "12. BUILD AROUND COMPETENCIES. Every paragraph should visibly connect "
    "to at least one listed competency area. Name the competency explicitly "
    "where it fits naturally.\n"
    "13. PLAIN PROSE ONLY. No markdown headings (#), bold (**text**), "
    "asterisks, bullet markers (*, -, •), numbered lists (unless the section "
    "instruction explicitly asks for them), or HTML tags.\n"
    "14. SIMPLE ENGLISH. Clear, accessible English. Technical HIM terms are fine.\n"
    "15. TARGET WORD COUNT IS A RANGE, NOT A FLOOR TO GAME. Under-writing "
    "fails; overwriting by more than 25% also fails. Sparse logs justify a "
    "shorter section — the page target is not a reason to invent.\n"
)


# ============================================================
# Entry point 1 — report prose drafting
# ============================================================

def draft_sections_with_ai(session, samples=None, target_pages=None):
    """
    Draft the prose sections with AI, with code-enforced quality gates.

    Returns a dict {section_key: text}. Sections that fail quality after
    retry return '' (so services.py fills defaults).

    Always includes:
      '_source_hash' : fingerprint of the inputs that drove drafting
      '_ai_note'     : human-readable retry summary when useful
    """
    if not getattr(settings, 'REPORT_AI_DRAFTING_ENABLED', True):
        return {}

    context = _build_context(session)
    if not context.strip():
        return {}

    log_ngrams = _build_log_ngrams(session)

    try:
        competencies = list(session.competencies.values_list('text', flat=True))
    except Exception:
        competencies = []

    competencies_rule = ''
    if competencies:
        competencies_rule = (
            "\nCOMPETENCY RULES (Apply to every section):\n"
            "- The student's competency areas are listed in the student data below.\n"
            "- Every chapter MUST visibly support at least one competency area. "
            "Name them explicitly where appropriate.\n"
            "- When selecting which log entries to feature, prioritise entries "
            "that map to a competency.\n"
            "- 'Lessons Learnt' must primarily reflect the competencies.\n"
            "- 'Challenges' should describe obstacles encountered while trying "
            "to achieve these competencies.\n"
            "- 'Recommendations' must address how future students can better "
            "achieve these competencies at the facility.\n"
            "- Do NOT invent competencies. If a section is unmappable, still "
            "write it — but keep the tone focused on skills and outcomes.\n"
        )

    format_notes = ''
    if samples:
        notes = [s.style_notes.strip() for s in samples if getattr(s, 'style_notes', '')]
        if notes:
            format_notes = (
                "\nFormat guidance from the department's official samples "
                "(follow this strictly):\n"
                + "\n".join(f"- {n}" for n in notes) + "\n"
            )

    log_count = session.daily_logs.filter(is_deleted=False).count()

    if log_count < 5:
        expansion_rule = (
            "\nCRITICAL SOURCE LIMIT: The student has very few daily logs "
            f"({log_count}). Do NOT invent units, activities, workshops, dates, "
            "or patient counts. For Activities Performed and Skills Acquired, "
            "write ONLY what the logs support. For Lessons Learnt, Observation, "
            "Recommendations, Conclusion, and Abstract, expand using the listed "
            "competency areas and general HIM principles — present it as "
            "reflection, not as a new log entry.\n"
        )
    elif log_count < 15:
        expansion_rule = (
            f"\nNOTE: The student has {log_count} daily logs. Synthesize them. "
            "Do not add units or activities that are not present in the logs.\n"
        )
    else:
        expansion_rule = ''

    if target_pages and target_pages >= 15:
        expansion_rule += (
            "\nLENGTH NOTE: The user chose a long target. Increase depth by "
            "adding analysis, reflection, competency links, and professional "
            "implications. Do NOT add new activities, units, workshops, or "
            "patient counts to reach the target.\n"
        )

    note = ""
    if log_count < 5:
        if target_pages:
            note = (
                f"Note: You have very few logs ({log_count}). The AI stayed close "
                f"to what you actually logged and used your competency areas for "
                f"reflection. Adding more logs will strengthen depth and help reach "
                f"your {target_pages}-page target."
            )
        else:
            note = (
                f"Note: You have very few logs ({log_count}). The AI stayed close "
                f"to what you actually logged and used your competency areas for "
                f"reflection. Adding more logs will further improve depth."
            )
    elif log_count < 15 and target_pages:
        note = (
            f"Note: If the generated report falls short of your {target_pages}-page "
            f"target, adding more daily logs will provide the AI with more material."
        )

    drafts = {}
    if note:
        drafts['_ai_note'] = note

    budgets = _compute_word_budget(
        target_pages,
        log_count=log_count,
        competency_count=len(competencies),
    )

    max_seconds = getattr(settings, 'REPORT_AI_DRAFTING_MAX_SECONDS', DEFAULT_MAX_SECONDS)
    unique_seed = random.randint(1000, 9999)

    total_start = time.time()
    retry_start = None
    retries_used = 0
    retry_flagged_sections = []
    total = len(DRAFT_SECTIONS)

    for idx, (key, title, instruction) in enumerate(DRAFT_SECTIONS, start=1):
        elapsed = time.time() - total_start
        if elapsed > max_seconds:
            completed = len([k for k in drafts if not k.startswith('_')])
            logger.warning(
                'AI drafting: time budget %ds exhausted after %d/%d sections '
                '(elapsed %.0fs) — bailing; remaining sections use defaults',
                max_seconds, completed, total, elapsed,
            )
            break

        word_budget = budgets.get(key, 0)
        max_word = int(word_budget * 1.25) if word_budget else 0
        budget_hint = ""
        if word_budget:
            lo = int(word_budget * 0.8)
            hi = int(word_budget * 1.2)
            if target_pages:
                context_sentence = (
                    f"Write enough to fill your share of the {target_pages}-page report, "
                    f"using ONLY what the logs and competencies support."
                )
            else:
                context_sentence = (
                    "Write enough to make this section substantive, using ONLY "
                    "what the logs and competencies support."
                )
            budget_hint = (
                f"\nTARGET WORD COUNT FOR THIS SECTION: approximately {word_budget} words "
                f"(acceptable range {lo}-{hi}). {context_sentence} "
                f"Under-writing is a FAILURE. Over-writing by more than 25% is also a FAILURE.\n"
            )

        prompt = (
            f"You are an expert academic editor drafting the '{title}' section of a "
            f"health-information practicum report for a Ghanaian college (COHY).\n\n"
            f"CRITICAL RULES:\n"
            f"1. FIRST-PERSON VOICE: Write strictly in the first-person ('I'). Do NOT use "
            f"phrases like 'the student', 'the trainee', or 'the intern'.\n"
            f"2. READ ALL DATA: Carefully review the Student Data below.\n"
            f"3. REFINE AND ENHANCE: NEVER copy raw daily logs verbatim. Synthesize the "
            f"information into polished, professional academic paragraphs.\n"
            f"4. GRAMMAR: Check grammar, spelling, and sentence flow rigorously. "
            f"Fix any lowercase 'i' to capital 'I'.\n"
            f"5. DO NOT INVENT personal details, activities, units, or workshops. "
            f"Logs are the source of fact.\n"
            f"6. STRUCTURE: Follow the structural requirements in the section instruction "
            f"(bullet count, sub-heading organization, format).\n"
            f"7. UNIQUENESS (Seed: {unique_seed}): Vary sentence structure from standard "
            f"reports to ensure this feels unique and personalized.\n"
            f"{_QUALITY_RULES}"
            f"{_SOURCE_BOUND_RULES}"
            f"{competencies_rule}"
            f"{budget_hint}"
            f"{expansion_rule}"
            f"\n{format_notes}\n"
            f"Section instructions (authoritative):\n"
            f"{instruction}\n\n"
            f"--- STUDENT DATA ---\n{context}\n-------------------\n"
        )

        reply = _call_ai(prompt)

        if reply is None:
            logger.warning(
                'AI drafting: section %d/%d (%r) — AI call returned None, skipping',
                idx, total, key,
            )
            continue

        reply = _clean_draft_text(reply)

        issues = _quality_issues(
            key, reply, log_ngrams,
            min_word_floor=_SECTION_FLOORS.get(key, 0),
            max_word=max_word,
        )

        # 'too_long' is informational only — do NOT fail the section for it.
        hard_issues = [i for i in issues if i[0] != 'too_long']

        if not hard_issues:
            drafts[key] = reply
            if issues:
                logger.info(
                    'AI drafted section %r (%d words) — passed; soft warnings: %s',
                    key, len(reply.split()),
                    '; '.join(f'{c}:{d}' for c, d in issues),
                )
            else:
                logger.debug('AI drafted section %r (%d words) — passed quality',
                             key, len(reply.split()))
            continue

        logger.warning(
            'AI drafting: section %r failed quality (%s) — attempting retry',
            key, '; '.join(f'{c}:{d}' for c, d in hard_issues),
        )

        can_retry = (
            retries_used < _MAX_RETRIES_TOTAL
            and (retry_start is None or time.time() - retry_start < _MAX_RETRY_SECONDS)
            and (time.time() - total_start) < (max_seconds - 15)
        )

        if not can_retry:
            logger.warning(
                'AI drafting: section %r — retry budget exhausted, using original draft',
                key,
            )
            drafts[key] = reply
            retry_flagged_sections.append((key, title))
            continue

        if retry_start is None:
            retry_start = time.time()
        retries_used += 1

        retry_prompt = _build_retry_prompt(prompt, title, hard_issues)
        retry_reply = _call_ai(retry_prompt)

        if retry_reply is None:
            logger.warning('AI drafting: section %r retry returned None — keeping original',
                           key)
            drafts[key] = reply
            retry_flagged_sections.append((key, title))
            continue

        retry_reply = _clean_draft_text(retry_reply)
        retry_issues = _quality_issues(
            key, retry_reply, log_ngrams,
            min_word_floor=_SECTION_FLOORS.get(key, 0),
            max_word=max_word,
        )
        hard_retry_issues = [i for i in retry_issues if i[0] != 'too_long']

        if not hard_retry_issues:
            drafts[key] = retry_reply
            logger.info('AI drafting: section %r PASSED on retry', key)
        else:
            logger.warning(
                'AI drafting: section %r failed retry too (%s) — using longer attempt',
                key, '; '.join(f'{c}:{d}' for c, d in hard_retry_issues),
            )
            drafts[key] = retry_reply if len(retry_reply) > len(reply) else reply
            retry_flagged_sections.append((key, title))

    if drafts:
        completed = len([k for k in drafts if not k.startswith('_')])
        logger.info(
            'AI drafting complete: %d/%d sections drafted in %.1fs '
            '(competencies=%d, target_pages=%s, log_count=%d, retries_used=%d, flagged=%d)',
            completed, total, time.time() - total_start, len(competencies),
            target_pages, log_count, retries_used, len(retry_flagged_sections),
        )
    else:
        logger.info('AI drafting produced no sections — report will use template defaults')

    if retry_flagged_sections:
        names = ', '.join(title for _, title in retry_flagged_sections)
        existing_note = drafts.get('_ai_note', '')
        retry_note = (
            f"{names} section(s) were drafted but flagged for manual review "
            f"(possible copied phrasing, markdown pollution, or length mismatch). "
            f"Open 'Edit Report Manually' to refine them."
        )
        drafts['_ai_note'] = (existing_note + ' ' + retry_note).strip() if existing_note else retry_note

    drafts['_source_hash'] = source_fingerprint(session)

    return drafts


# ============================================================
# Entry point 2 — presentation bullets + speaker notes
# ============================================================

def draft_presentation_with_ai(session, target_slides=7):
    """
    Draft slide-specific bullets + speaker notes for the PPTX.

    Returns a dict {pptx_key: text} plus '_source_hash'. Callers store this
    in report.pptx_content and use the hash to detect stale drafts.
    """
    if not getattr(settings, 'REPORT_AI_DRAFTING_ENABLED', True):
        return {}

    context = _build_context(session)
    if not context.strip():
        return {}

    try:
        competencies = list(session.competencies.values_list('text', flat=True))
    except Exception:
        competencies = []

    competency_hint = ''
    if competencies:
        competency_hint = (
            "\nCOMPETENCY HINT: The student developed these competencies — reflect them "
            "in the Skills and Lessons bullet points where natural:\n"
            + "\n".join(f"- {c}" for c in competencies) + "\n"
        )

    log_count = session.daily_logs.filter(is_deleted=False).count()
    if log_count < 5:
        competency_hint += (
            "\nCRITICAL: The student has very few daily logs. "
            "Produce truthful bullets — do NOT invent units, workshops, or "
            "activities. It is better to have 3 solid, truthful bullets than "
            "6 fabricated ones.\n"
        )

    max_seconds = getattr(settings, 'REPORT_AI_DRAFTING_MAX_SECONDS', DEFAULT_MAX_SECONDS)

    drafts = {}
    total_start = time.time()
    total = len(PPTX_DRAFT_SECTIONS)

    for idx, (key, title, instruction) in enumerate(PPTX_DRAFT_SECTIONS, start=1):
        if time.time() - total_start > max_seconds:
            logger.warning(
                'PPTX AI drafting: time budget %ds exhausted after %d/%d sections',
                max_seconds, len(drafts), total,
            )
            break

        prompt = (
            f"You are preparing '{title}' content for a PowerPoint presentation "
            f"summarising a health-information practicum report for a Ghanaian college (COHY).\n\n"
            f"CRITICAL RULES:\n"
            f"1. BULLET FORMAT: Every line is a standalone bullet.\n"
            f"2. WORD LIMITS ARE ABSOLUTE: exceeding a word limit is a FAILURE.\n"
            f"3. REFINE, DON'T COPY: never paste raw logs.\n"
            f"4. PRESENTATION VOICE: active verbs, present tense, short clauses.\n"
            f"5. NO HEADINGS: no section titles, bullet markers, or markdown.\n"
            f"6. STRUCTURE & GRAMMAR: perfect grammar, clean professional action statements.\n"
            f"7. NO DASHES: do NOT use em dash (—), en dash (–), or hyphen (-) as a "
            f"bullet separator. Use a colon (:) or rephrase instead.\n"
            f"8. SECTION-FIELD MAPPING: each log field has a specific section:\n"
            f"   - Activities slide → use ACTIVITY field ONLY. Not challenges, not solutions.\n"
            f"   - Challenges slide → use CHALLENGE field ONLY. Not activities.\n"
            f"   - Observation/Recs slide → use OBSERVATION + RECOMMENDATIONS.\n"
            f"9. SIMPLE ENGLISH: short words, plain sentences.\n"
            f"{_SOURCE_BOUND_RULES}"
            f"{competency_hint}"
            f"\nSection instruction (authoritative):\n{instruction}\n\n"
            f"--- STUDENT DATA ---\n{context}\n-------------------\n"
        )

        reply = _call_ai(prompt)

        if reply is None:
            logger.warning(
                'PPTX AI drafting: section %d/%d (%r) — returned None, skipping',
                idx, total, key,
            )
            continue

        reply = _clean_pptx_text(reply)
        drafts[key] = reply
        logger.debug('PPTX AI drafted %r (%d chars)', key, len(reply))

    if drafts:
        logger.info(
            'PPTX AI drafting complete: %d/%d sections in %.1fs',
            len(drafts), total, time.time() - total_start,
        )
    else:
        logger.info('PPTX AI drafting produced no sections — builder will trim report text')

    drafts['_source_hash'] = source_fingerprint(session)

    return drafts


def _clean_pptx_text(text):
    """
    Light cleanup for slide bullets: strip markdown, convert em/en/hyphen
    separators between words to a colon, collapse whitespace.
    """
    if not text:
        return ''
    # Strip bold / italic markdown
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # Em dash / en dash used as a separator → colon
    text = re.sub(r'\s+[\u2013\u2014]\s+', ': ', text)
    # Hyphen used as a separator between words → colon
    text = re.sub(r'\s+-\s+', ': ', text)
    # Strip markdown heading lines
    text = re.sub(r'^[ \t]*#+[ \t]*.*$', '', text, flags=re.MULTILINE)
    # Collapse 3+ blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ============================================================
# v9 — AI refinement of user-edited content
# ============================================================
# Used by the "Regenerate with AI" button on the edit page.
# Unlike draft_sections_with_ai(), this does NOT redraft from logs.
# It takes the user's actual text (from the edit page) and returns a
# polished version that:
#   - preserves every fact, number, unit, activity, workshop, skill
#   - preserves paragraph / bullet structure
#   - fixes grammar, sentence flow, and academic tone
#   - never adds or removes substantive content

_REFINE_INSTRUCTIONS = {
    'introduction':         'Polish into 2 academic paragraphs in first person. Preserve all facts, units, dates. Fix grammar, vary sentences, improve flow. Keep it SHORT — under 250 words.',
    'facility_profile':     'Polish into third-person academic prose about the facility. Preserve all facts. Do NOT use "I". Fix grammar and flow.',
    'activities_performed': 'Polish each unit sub-heading and activity line. Preserve every unit and activity mentioned. Keep sub-headings if present. Fix grammar and flow.',
    'skills_acquired':      'Polish each skill line. Keep one item per line. Preserve every skill and its description. Do NOT add new skills. Fix grammar.',
    'workshops':            'Polish into 2 clean paragraphs. Preserve all facts about workshops or on-the-job training. Fix grammar.',
    'lessons_learnt':       'Polish each lesson line into 2-3 sentences. Preserve every lesson. Do NOT add new lessons. Each lesson must remain an INSIGHT, not an activity summary. Fix grammar and flow.',
    'observation':          'Polish into 3-4 academic paragraphs in first person. Preserve every observation. Fix grammar.',
    'observations':         'Polish into 3-4 academic paragraphs in first person. Preserve every observation. Fix grammar.',
    'challenges':           'Polish into 3-4 academic paragraphs in first person. Preserve every challenge. Fix grammar.',
    'recommendations':      'Polish each recommendation line. Keep one item per line. Use professional tone. Preserve every recommendation. Fix grammar.',
    'conclusion':           'Polish into 3 academic paragraphs in first person. Preserve every point. Fix grammar and flow.',
    'abstract':             'Polish into one academic paragraph in third person. Preserve every fact and competency mentioned. Fix grammar and flow.',
    'acknowledgments':      'Polish into 2-3 formal paragraphs in first person. Preserve every name and every gratitude expressed. Fix grammar.',
    'references':           'Preserve every reference. Fix punctuation and APA formatting only. Do NOT remove any reference.',
}


def refine_user_content(session, current_content):
    """
    Refine user-edited content with AI.

    Takes the user's exact text (from the edit page) and returns a
    polished version that:
      - preserves every fact, number, unit, activity, and structure
      - improves grammar, sentence flow, and academic tone
      - does NOT add or remove substantive content

    Safety net: if the AI returns content wildly different in length
    from the input (<40% or >250% of input word count), we discard the
    refined version and keep the user's original.

    Never raises — on any error, returns the input unchanged.
    """
    if not getattr(settings, 'REPORT_AI_DRAFTING_ENABLED', True):
        return dict(current_content or {})

    refined = {}
    for key, value in (current_content or {}).items():
        if str(key).startswith('_'):
            refined[key] = value
            continue

        text = (value or '').strip()
        if not text:
            refined[key] = ''
            continue

        instruction = _REFINE_INSTRUCTIONS.get(key)
        if not instruction:
            refined[key] = text
            continue

        prompt = (
            "You are polishing a section of a health-information practicum "
            "report written by a student. Your ONLY job is to fix grammar, "
            "improve sentence flow, and raise the academic tone.\n\n"
            "CRITICAL RULES:\n"
            "1. PRESERVE every fact, number, unit, activity, workshop, and skill "
            "in the text below. Do not invent anything.\n"
            "2. PRESERVE structure — same number of paragraphs / bullets as input.\n"
            "3. KEEP first-person voice ('I') where the input uses it. Fix any "
            "lowercase 'i' to capital 'I'.\n"
            "4. For bullet sections: one item per line, plain text. Do NOT add "
            "bullet characters (•, -, *) — the renderer adds them.\n"
            "5. Do NOT add a heading. Do NOT use markdown (**, ##).\n"
            "6. Do NOT apologise, do NOT explain — output ONLY the refined section text.\n\n"
            f"Section: {key}\n"
            f"Refinement instruction: {instruction}\n\n"
            f"--- INPUT ---\n{text}\n--- END INPUT ---\n\n"
            "Refined section text:"
        )

        try:
            reply = _call_ai(prompt)
        except Exception:
            logger.exception('refine_user_content: AI call failed for section=%s', key)
            reply = None

        if not reply:
            refined[key] = text
            continue

        cleaned = _clean_draft_text(reply)
        if not cleaned:
            refined[key] = text
            continue

        orig_words = len(text.split())
        ref_words = len(cleaned.split())
        if orig_words > 20 and (ref_words < orig_words * 0.4 or ref_words > orig_words * 2.5):
            logger.warning(
                'refine_user_content: section %s length drift (orig=%d, refined=%d) '
                '— keeping original',
                key, orig_words, ref_words,
            )
            refined[key] = text
            continue

        refined[key] = cleaned

    return refined