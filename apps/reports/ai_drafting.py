"""
Best-effort AI drafting for report sections, with code-enforced quality gates.

Two entry points:
  - draft_sections_with_ai(session)        → report prose   → report.content
  - draft_presentation_with_ai(session)    → slide bullets  → report.pptx_content

QUALITY ENFORCEMENT (v7):
  Every drafted section passes through a post-processing gate that detects:
    1. Verbatim copy from raw logs (7-word n-gram match)
    2. Placeholder text ('[insert', 'TODO', 'lorem ipsum', etc.)
    3. Markdown pollution (## headings)
    4. Under-length drafts (< 50% of section word floor)
  Sections failing the gate are retried once with a stricter prompt that
  names the specific problem. Retry budget is capped (3 retries / 30s) so
  the time budget stays intact.

ORDER MATTERS (v7): Sections are ordered by PRIORITY, not report sequence.
  If the 90s budget runs out, the sections that got drafted are the ones
  that matter most. Activities / Introduction / Skills first. Abstract and
  References last.
"""

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

DRAFT_SECTIONS = [
    ('introduction', 'Introduction',
     "Write 3-4 substantial paragraphs using first-person voice ('I'). "
     "Paragraph 1: purpose of the attachment, facility name, location, period, and why HIM practice matters in Ghana. "
     "Paragraph 2: units rotated through, with one specific activity per unit to show breadth. "
     "Paragraph 3: the competency areas the student targeted and how this report will document achievement of each. "
     "Paragraph 4: the structure of the report that follows (chapter by chapter, in one line). "
     "CRITICAL: Do NOT refer to yourself as 'the student' or 'the intern'. Write AS the student. "
     "Do NOT use bullet points and do NOT include a heading."),

    ('activities_performed', 'Activities Performed',
     "Write a 2-sentence opening paragraph framing the attachment's activities. "
     "Then create a SUB-HEADING for EACH unit the student rotated through (use the unit names from the logs). "
     "Under EACH sub-heading, write 2-3 substantial paragraphs. "
     "Each paragraph MUST: (a) describe specific activities performed in that unit, "
     "(b) name the competency area being developed there, "
     "(c) mention the tools/systems used (LHIMS, DHIMS, NHIS claims portal, ICD-10 coding, etc.) where relevant, "
     "and (d) use first-person voice ('I'). "
     "CRITICAL: DO NOT paste raw daily logs. Synthesize and refine into professional academic narrative. "
     "If a unit was logged only once or twice, EXPAND into a full realistic narrative using your knowledge "
     "of typical Ghanaian HIM workflows for that unit type — but stay truthful to what the student actually did. "
     "Do NOT include the main heading."),

    ('skills_acquired', 'Skills Acquired',
     "Provide 8-10 specific skills the student developed. Output ONLY the list items, one per line. "
     "CRITICAL: ABSOLUTELY NO numbers, hyphens, or asterisks at the start of the line. "
     "Use the format: <b>Skill Name</b>: 2-3 sentence description showing HOW the skill was developed. "
     "Each description must reference a specific unit or activity from the logs. "
     "Use first-person ('I'). Explicitly connect each skill back to one of the listed competency areas where natural. "
     "Do NOT include a heading."),

    ('lessons_learnt', 'Lessons Learnt',
     "Provide 10-12 detailed bullet points. Output ONLY the list items, one per line. "
     "CRITICAL: ABSOLUTELY NO numbers, hyphens, or asterisks at the start of the line. "
     "Each point MUST be 3-4 full sentences that develop a single idea. "
     "Use first-person voice ('I'). "
     "EACH LESSON MUST EXPLICITLY NAME OR CLEARLY CONNECT TO AT LEAST ONE OF THE STUDENT'S COMPETENCY AREAS. "
     "Do NOT duplicate the Skills Acquired section — focus on broader insights: patient confidentiality, "
     "teamwork and communication, data-driven decision making, professional ethics, workflow efficiency, "
     "and the realities of HIM practice in Ghana. "
     "Do NOT include a heading."),

    ('challenges', 'Challenges',
     "Write 3-4 substantial paragraphs about the challenges the student ACTUALLY faced. "
     "CRITICAL FIRST STEP: Read the daily logs below and identify the SPECIFIC challenges the student recorded. "
     "If the logs recorded challenges, SYNTHESIZE and REFINE them into academic prose — do NOT copy verbatim. "
     "If the logs recorded NO challenges, generate 2-3 realistic ones based on the units and competencies listed, "
     "drawing on typical Ghanaian HIM practice (patient congestion, illegible handwriting on paper records, "
     "network downtime on LHIMS, incomplete NHIS claim documentation, storage space limitations). "
     "STRUCTURE: Paragraph 1 introduces the challenges generally and their significance. "
     "Paragraphs 2-3 each describe ONE specific challenge in detail — what happened, its impact on the work, "
     "and how the student responded. Paragraph 4 (optional) reflects on what these challenges reveal about the facility's systems. "
     "Use first-person ('I'). Do NOT include a heading or bullet list. "
     "CRITICAL: NEVER paste raw log text. Always transform into polished academic English."),

    ('conclusion', 'Conclusion',
     "Write 3 substantial paragraphs using first-person ('I'). "
     "Paragraph 1: summarize the overall experience, units rotated, and skills gained. "
     "Paragraph 2: reflect on how the practicum prepares the student for professional HIM practice "
     "and connect back to the competency areas. "
     "Paragraph 3: express gratitude to the facility, staff, and College, and outline next steps in "
     "the student's academic and professional journey. "
     "Do NOT include a heading."),

    ('recommendations', 'Recommendations',
     "Write an opening paragraph of 2-3 sentences framing the recommendations. "
     "Then provide a numbered list of 6-8 actionable recommendations. "
     "Split them into two clear groups: (A) 3-4 for the FACILITY, (B) 3-4 for the COLLEGE. "
     "EACH recommendation must directly address a challenge or competency mentioned earlier in the report. "
     "Each recommendation should be 2-3 sentences — specify WHO should do WHAT and WHY it matters. "
     "Use professional academic tone (first-person plural 'we' or third-person 'the facility should'). "
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
     "Paragraph 2: reflect on how the workshops complemented the practical experience and connected to the competencies. "
     "If NO formal workshops were attended, write 2 paragraphs on the practical on-the-job training received instead. "
     "Use first-person ('I'). Do NOT include a heading."),

    ('references', 'References',
     "APA-style reference list for any sources you cite. If you cite nothing, return an empty response. "
     "Do NOT include a heading."),
]

DEFAULT_MAX_SECONDS = 90


# ============================================================
# Presentation drafting (slide bullets + speaker notes)
# ============================================================

PPTX_DRAFT_SECTIONS = [
    ('pptx_intro_bullets', 'Slide: Introduction',
     "Write 3 short bullet points for a presentation slide titled 'Introduction'. "
     "Each bullet MUST be 12 words or fewer. Output ONE bullet per line, no markers, no numbers. "
     "Use first-person ('I'). Cover: purpose of attachment, facility name, key units rotated. "
     "Example line: 'Attached to Adidome Government Hospital for four weeks'\n"
     "FORBIDDEN: more than 3 bullets. FORBIDDEN: any bullet longer than 12 words."),

    ('pptx_facility_bullets', 'Slide: Facility Profile',
     "Write 3 short bullet points for a slide titled 'Facility Profile'. "
     "Each bullet MUST be 12 words or fewer. Output ONE bullet per line, no markers. "
     "Cover: facility type and location, key services (OPD, RCH, Laboratory, Pharmacy, Maternity), "
     "and the NHIS/DHIMS data workflow. Do NOT use first-person — describe the facility, not the student. "
     "FORBIDDEN: more than 3 bullets. FORBIDDEN: bullets longer than 12 words."),

    ('pptx_activities_bullets', 'Slide: Activities Performed',
     "Write 4-6 short bullet points for a slide titled 'Activities Performed'. "
     "Each bullet MUST be 10 words or fewer. Output ONE bullet per line, no markers. "
     "Format each bullet as: '<Unit> — <one key activity>'. "
     "Example line: 'RCH — registered pregnant women and booked appointments'\n"
     "FORBIDDEN: more than 6 bullets. FORBIDDEN: bullets longer than 10 words."),

    ('pptx_skills_bullets', 'Slide: Skills Acquired',
     "Write 4-5 short bullet points naming skills acquired. "
     "Each bullet MUST be 8 words or fewer. Output ONE bullet per line, no markers. "
     "Just the skill name plus one short qualifier. "
     "Example line: 'Patient registration and LHIMS data entry'\n"
     "FORBIDDEN: more than 5 bullets. FORBIDDEN: bullets longer than 8 words."),

    ('pptx_challenges_bullets', 'Slide: Challenges Faced',
     "Write EXACTLY 2 short bullet points. Each bullet MUST be 12 words or fewer. "
     "Output ONE bullet per line, no markers. "
     "Bullet 1: high patient volume causing congestion and record delays. "
     "Bullet 2: illegible handwriting in physical records complicating NHIS claims entry. "
     "FORBIDDEN: more than 2 bullets. FORBIDDEN: bullets longer than 12 words."),

    ('pptx_observation_recommendation_bullets', 'Slide: Observation and Recommendation',
     "Write exactly 4 short bullet points for a slide titled 'Observation and Recommendation'. "
     "Each bullet MUST be 14 words or fewer. Output ONE bullet per line, no markers. "
     "The FIRST 2 bullets must be observations about workflows, staffing, or record systems. "
     "The LAST 2 bullets must be actionable recommendations for the facility or the college. "
     "FORBIDDEN: more than 4 bullets. FORBIDDEN: separating observations and recommendations into different headings."),

    ('pptx_conclusion_bullets', 'Slide: Conclusion',
     "Write 2-3 short bullet points summarising the practicum. "
     "Each bullet MUST be 12 words or fewer. Output ONE bullet per line, no markers. "
     "Cover: overall experience, key takeaway, readiness for professional practice. "
     "FORBIDDEN: more than 3 bullets."),

    ('pptx_speaker_notes', 'Speaker Notes for All Slides',
     "Write 2 short sentences of speaker notes (what the presenter should say aloud) for each slide. "
     "Cover exactly these 7 slides in this order: "
     "Introduction, Facility Profile, Activities, Skills, Challenges, Observation and Recommendation, Conclusion.\n"
     "Output format — one slide per line, exactly like this:\n"
     "Introduction: <2 sentences>\n"
     "Facility Profile: <2 sentences>\n"
     "(continue for all 7 slides)\n"
     "Do NOT use bullet points or asterisks. Do NOT add a heading. "
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


def _quality_issues(section_key, text, log_ngrams, min_word_floor=0):
    """
    Run every quality gate against a drafted section.

    Returns list of (code, detail) tuples. Empty list = section passes.
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

    # 4. Markdown pollution that wasn't stripped
    if re.search(r'^#{1,6}\s', text, re.MULTILINE):
        issues.append(('markdown_headers', 'contains raw markdown # headers'))

    return issues


def _clean_draft_text(text):
    """Universal cleanup applied to every AI reply before quality check."""
    if not text:
        return ''
    # Strip markdown heading lines that slipped past the prompt instruction.
    text = re.sub(r'^#+\s+.*$', '', text, flags=re.MULTILINE)
    # Strip a leading section-title line (e.g. "INTRODUCTION\n\n").
    text = re.sub(r'^[A-Z][A-Z\s]{3,}\n+', '', text, count=1)
    # Collapse 3+ consecutive blank lines to a single paragraph break.
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
        f"1. If 'verbatim_copy' was flagged: you must rephrase EVERY phrase from the "
        f"raw logs into your own academic prose. No sequence of 5 or more words from the "
        f"raw logs may appear in your output. Transform sentence structures completely — "
        f"change verbs, add context, restructure.\n"
        f"2. If 'too_short' was flagged: expand the section by 2x or more. Add descriptive "
        f"context, worked examples, and academic analysis. Use your knowledge of Ghanaian "
        f"HIM workflows to enrich.\n"
        f"3. If 'placeholder' was flagged: remove ALL placeholder text like [insert], TODO, "
        f"lorem ipsum. Write real content.\n"
        f"4. If 'markdown_headers' was flagged: remove every ## or # line. Use plain prose only.\n\n"
        f"Now write the COMPLETE section again from scratch. Do not apologize, do not "
        f"explain — output only the section text.\n\n"
        f"{original_prompt}"
    )


# ============================================================
# Shared context builder + AI caller
# ============================================================

def _build_context(session):
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
        lines.append(f"\nDAILY LOGS ({len(logs)} total):")
        for log in logs:
            summary = (log.activity_summary or '').strip().replace('\n', ' ')
            entry = f"- {log.date.strftime('%Y-%m-%d')} [{log.unit_sub_department}]: {summary[:400]}"
            if log.skills_acquired:
                entry += f" | Skills noted: {log.skills_acquired[:200]}"
            if log.challenges_faced:
                entry += f" | RAW Challenge: {log.challenges_faced[:200]}"
                if log.solutions_applied:
                    entry += f" -> RAW Solution: {log.solutions_applied[:200]}"
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

_SECTION_FLOORS = {
    'abstract':              180,
    'acknowledgments':       120,
    'introduction':          300,
    'activities_performed':  800,
    'skills_acquired':       350,
    'workshops':             120,
    'lessons_learnt':        350,
    'observation':           250,
    'challenges':            250,
    'recommendations':       250,
    'conclusion':            180,
}

_BUDGET_DISTRIBUTION = {
    'abstract':              0.045,
    'acknowledgments':       0.030,
    'introduction':          0.070,
    'activities_performed':  0.340,
    'skills_acquired':       0.115,
    'workshops':             0.025,
    'lessons_learnt':        0.115,
    'observation':           0.075,
    'challenges':            0.070,
    'recommendations':       0.060,
    'conclusion':            0.050,
}


def _compute_word_budget(target_pages, log_count=0):
    """Return per-section word targets, scaled by page count or log volume."""
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

    total_prose_words = int(effective_pages * 300)
    return {
        section: max(int(total_prose_words * pct), _SECTION_FLOORS.get(section, 0))
        for section, pct in _BUDGET_DISTRIBUTION.items()
    }


# ============================================================
# Shared quality rules (included in every prompt)
# ============================================================

_QUALITY_RULES = (
    "\nQUALITY RULES (Apply to every section):\n"
    "9. NEVER COPY RAW LOG TEXT. The student's raw logs may say things like 'Registered 15 patients' "
    "or 'Entered NHIS claims'. NEVER use that phrasing verbatim. Instead write it as professional prose: "
    "'I processed approximately fifteen outpatient registrations per session, ensuring all demographic "
    "data was accurately captured before filing.' Transform, do not transcribe.\n"
    "10. EXPAND SPARSE LOGS. If the student's logs for this section are thin (under 40 words or a single "
    "short line), do NOT write a bare 1-sentence paragraph. Instead, use your professional knowledge of "
    "typical Ghanaian HIM practice to EXPAND into a full, realistic narrative that still reflects what "
    "they actually did.\n"
    "11. REFINE ABUNDANT LOGS. If the student's logs for this section are rich (over 200 words), do NOT "
    "pad with filler. Synthesize, distill, and remove repetition.\n"
    "12. BUILD AROUND COMPETENCIES. Every paragraph should visibly connect to at least one of the "
    "student's listed competency areas. Name the competency explicitly where it fits naturally.\n"
    "13. SIMPLE ENGLISH. Write in clear, accessible English. Technical HIM terms are fine.\n"
    "14. TARGET WORD COUNT IS REAL. Write within ±20% of the target below. Under-writing is FAILURE.\n"
)


# ============================================================
# Entry point 1 — report prose drafting
# ============================================================

def draft_sections_with_ai(session, samples=None, target_pages=None):
    """
    Draft the prose sections with AI, with code-enforced quality gates.

    Returns a dict {section_key: text}. Sections that fail quality after
    retry return '' (so services.py fills defaults).
    Adds '_ai_note' string with human-readable retry summary when useful.
    """
    if not getattr(settings, 'REPORT_AI_DRAFTING_ENABLED', True):
        return {}

    context = _build_context(session)
    if not context.strip():
        return {}

    # ✅ Build the verbatim-copy reference set ONCE per call.
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

    # Expansion rules
    log_count = session.daily_logs.filter(is_deleted=False).count()

    if target_pages and target_pages >= 15:
        expansion_rule = (
            f"\nCRITICAL (EXPANSION REQUIRED): The user has set a {target_pages}-page "
            "target. This is a long report. Even if source logs are sparse, you MUST "
            "expand into full, realistic narratives using your knowledge of Ghanaian "
            "HIM practice. Under-writing here means the report will fail to hit the "
            "user's explicit page target — this is not acceptable.\n"
        )
    elif log_count < 5:
        if target_pages:
            length_phrase = f"to reach the {target_pages}-page target"
        else:
            length_phrase = "so the report is substantive and professional — not a bare summary"
        expansion_rule = (
            "\nCRITICAL (EXPANSION REQUIRED): The student has very few daily logs "
            f"({log_count}). You MUST heavily expand on the units and activities "
            "mentioned using your extensive knowledge of Ghanaian Health Information "
            "Management workflows. Invent realistic, professional activities consistent "
            "with the facilities and units mentioned, "
            f"{length_phrase}. Do NOT copy raw logs verbatim.\n"
        )
    elif log_count < 15:
        expansion_rule = (
            f"\nNOTE: The student has {log_count} daily logs — enough for synthesis but "
            "not rich. Where a section seems thin, use your HIM knowledge to expand "
            "into a fuller narrative.\n"
        )
    else:
        expansion_rule = ''

    # User-facing note about missing logs
    note = ""
    if log_count < 5:
        if target_pages:
            note = (
                f"Note: You have very few logs ({log_count}). The AI expanded the content, "
                f"but adding more logs will improve depth and help reach your "
                f"{target_pages}-page target."
            )
        else:
            note = (
                f"Note: You have very few logs ({log_count}). The AI expanded the content "
                f"using your competency areas; adding more logs will further improve depth."
            )
    elif log_count < 15 and target_pages:
        note = (
            f"Note: If the generated report falls short of your {target_pages}-page "
            f"target, adding more daily logs will provide the AI with more material."
        )

    drafts = {}
    if note:
        drafts['_ai_note'] = note

    budgets = _compute_word_budget(target_pages, log_count=log_count)

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
        budget_hint = ""
        if word_budget:
            lo = int(word_budget * 0.8)
            hi = int(word_budget * 1.2)
            if target_pages:
                context_sentence = (
                    f"Write enough to fill your share of the {target_pages}-page report."
                )
            else:
                context_sentence = "Write enough to make this section substantive."
            budget_hint = (
                f"\nTARGET WORD COUNT FOR THIS SECTION: approximately {word_budget} words "
                f"(acceptable range {lo}-{hi}). {context_sentence} "
                f"Under-writing is a FAILURE.\n"
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
            f"4. GRAMMAR: Check grammar, spelling, and sentence flow rigorously.\n"
            f"5. DO NOT INVENT personal details. Use general Ghanaian health-system "
            f"knowledge for standard units when helpful.\n"
            f"6. STRUCTURE: Follow the structural requirements in the section instruction "
            f"(bullet count, sub-heading organization, format).\n"
            f"7. UNIQUENESS (Seed: {unique_seed}): Vary sentence structure from standard "
            f"reports to ensure this feels unique and personalized.\n"
            f"{_QUALITY_RULES}"
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

        # ✅ QUALITY GATE
        issues = _quality_issues(
            key, reply, log_ngrams,
            min_word_floor=_SECTION_FLOORS.get(key, 0),
        )

        if not issues:
            drafts[key] = reply
            logger.debug('AI drafted section %r (%d words) — passed quality',
                         key, len(reply.split()))
            continue

        # Quality failed — attempt ONE retry if budget allows
        logger.warning(
            'AI drafting: section %r failed quality (%s) — attempting retry',
            key, '; '.join(f'{c}:{d}' for c, d in issues),
        )

        # Check retry budgets
        can_retry = (
            retries_used < _MAX_RETRIES_TOTAL
            and (retry_start is None or time.time() - retry_start < _MAX_RETRY_SECONDS)
            and (time.time() - total_start) < (max_seconds - 15)  # 15s reserve for tail work
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

        retry_prompt = _build_retry_prompt(prompt, title, issues)
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
        )

        if not retry_issues:
            drafts[key] = retry_reply
            logger.info('AI drafting: section %r PASSED on retry', key)
        else:
            # Both attempts have issues — pick the longer one (usually richer content)
            logger.warning(
                'AI drafting: section %r failed retry too (%s) — using longer attempt',
                key, '; '.join(f'{c}:{d}' for c, d in retry_issues),
            )
            drafts[key] = retry_reply if len(retry_reply) > len(reply) else reply
            retry_flagged_sections.append((key, title))

    # ---- Summary logging ----
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

    # ---- Human-readable note about retry outcomes ----
    if retry_flagged_sections:
        names = ', '.join(title for _, title in retry_flagged_sections)
        existing_note = drafts.get('_ai_note', '')
        retry_note = (
            f"{names} section(s) were drafted but flagged for manual review "
            f"(possible copied phrasing or under-length draft). "
            f"Open 'Edit Report Manually' to refine them."
        )
        drafts['_ai_note'] = (existing_note + ' ' + retry_note).strip() if existing_note else retry_note

    return drafts


# ============================================================
# Entry point 2 — presentation bullets + speaker notes
# ============================================================

def draft_presentation_with_ai(session, target_slides=7):
    """
    Draft slide-specific bullets + speaker notes for the PPTX.
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
            "\nCRITICAL (EXPANSION REQUIRED): The student has very few daily logs. "
            "You MUST heavily expand on the units and activities using your knowledge of "
            "Ghanaian HIM workflows to ensure there are enough detailed bullets.\n"
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
            f"7. EXPAND IF SPARSE: even with few logs, produce the full bullet count.\n"
            f"8. REFINE IF ABUNDANT: distill to strongest points.\n"
            f"9. SIMPLE ENGLISH: short words, plain sentences.\n"
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

        drafts[key] = reply.strip()
        logger.debug('PPTX AI drafted %r (%d chars)', key, len(reply))

    if drafts:
        logger.info(
            'PPTX AI drafting complete: %d/%d sections in %.1fs',
            len(drafts), total, time.time() - total_start,
        )
    else:
        logger.info('PPTX AI drafting produced no sections — builder will trim report text')

    return drafts