"""
Best-effort AI drafting for report sections.

Two entry points:
  - draft_sections_with_ai(session)        → report prose   → report.content
  - draft_presentation_with_ai(session)    → slide bullets  → report.pptx_content
"""

import logging
import time
import random
import re

from django.conf import settings

logger = logging.getLogger(__name__)


# ============================================================
# Report drafting (prose sections — used by the PDF/Word flow)
# ============================================================
# NOTE: 'facility_profile' is deliberately NOT drafted by the AI.
# The Facility Profile is built purely from the facility record in the
# database (see PracticumReportBuilder._build_facility_profile).
#
# LENGTH PHILOSOPHY (v5):
#   The old prompts hard-capped sections at 80–200 words, which caused the
#   AI to under-write and the report to fall short of the user's page target.
#   These prompts instead give the AI a per-section WORD TARGET computed from
#   the user's chosen page count — under-writing is now treated as failure.
#   When the user has NOT set a target, the budget is estimated from log
#   volume (sparse logs → short report; rich logs → near COHY 25 pages).
#   The only remaining hard constraints are STRUCTURAL (bullet count, sub-
#   heading organization), never word count.

DRAFT_SECTIONS = [
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

    ('workshops', 'Workshops',
     "Write exactly 2 paragraphs. "
     "Paragraph 1: describe each workshop or training session attended (date, topic, presenter if known), "
     "and the specific skills or knowledge gained from each. "
     "Paragraph 2: reflect on how the workshops complemented the practical experience and connected to the competencies. "
     "If NO formal workshops were attended, write 2 paragraphs on the practical on-the-job training received instead. "
     "Use first-person ('I'). Do NOT include a heading."),

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

    ('recommendations', 'Recommendations',
     "Write an opening paragraph of 2-3 sentences framing the recommendations. "
     "Then provide a numbered list of 6-8 actionable recommendations. "
     "Split them into two clear groups: (A) 3-4 for the FACILITY, (B) 3-4 for the COLLEGE. "
     "EACH recommendation must directly address a challenge or competency mentioned earlier in the report. "
     "Each recommendation should be 2-3 sentences — specify WHO should do WHAT and WHY it matters. "
     "Use professional academic tone (first-person plural 'we' or third-person 'the facility should'). "
     "Do NOT include a heading."),

    ('conclusion', 'Conclusion',
     "Write 3 substantial paragraphs using first-person ('I'). "
     "Paragraph 1: summarize the overall experience, units rotated, and skills gained. "
     "Paragraph 2: reflect on how the practicum prepares the student for professional HIM practice "
     "and connect back to the competency areas. "
     "Paragraph 3: express gratitude to the facility, staff, and College, and outline next steps in "
     "the student's academic and professional journey. "
     "Do NOT include a heading."),

    ('references', 'References',
     "APA-style reference list for any sources you cite. If you cite nothing, return an empty response. "
     "Do NOT include a heading."),
]

DEFAULT_MAX_SECONDS = 90  # Capped at 90s so it doesn't trigger Gunicorn timeouts


# ============================================================
# Presentation drafting (slide bullets + speaker notes)
# ============================================================
# Unchanged — PPTX output is intentionally terse.

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
# Per-section word budgets — computed from target_pages
# ============================================================

# Absolute floors so short reports don't produce a 67-word abstract.
# These match the pre-v5 hardcoded fallback budget (~3,000 words) so nothing
# regresses for users with very few logs and no explicit target.
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

# Distribution percentages (sum ≈ 1.0) — governs how a page-target's word
# budget is split across sections.
_BUDGET_DISTRIBUTION = {
    'abstract':              0.045,
    'acknowledgments':       0.030,
    'introduction':          0.070,
    'activities_performed':  0.340,   # biggest section
    'skills_acquired':       0.115,
    'workshops':             0.025,
    'lessons_learnt':        0.115,
    'observation':           0.075,
    'challenges':            0.070,
    'recommendations':       0.060,
    'conclusion':            0.050,
}


def _compute_word_budget(target_pages, log_count=0):
    """
    Per-section word targets.

    Two modes:
      • target_pages set (>= 5)  → scale budgets to fill that page count
      • target_pages None/< 5    → estimate natural length from log volume
                                    (sparse → short, rich → near COHY 25)

    Floors are applied so tiny targets still produce a substantive abstract,
    conclusion, etc. ~300 prose words per page.
    """
    if target_pages and target_pages >= 5:
        effective_pages = target_pages
    else:
        # Natural-length estimate — no padding, matches what the source
        # material can realistically support.
        if log_count < 5:
            effective_pages = 5
        elif log_count < 10:
            effective_pages = 8
        elif log_count < 20:
            effective_pages = 12
        else:
            effective_pages = 18   # rich logs — just under COHY guideline

    total_prose_words = int(effective_pages * 300)
    return {
        section: max(int(total_prose_words * pct), _SECTION_FLOORS.get(section, 0))
        for section, pct in _BUDGET_DISTRIBUTION.items()
    }


# ============================================================
# Shared quality rules (used by both drafting functions)
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
    "they actually did. Add context, describe typical workflows, mention standard tools (LHIMS, DHIMS, "
    "NHIS). Never invent activities they didn't do, but do add realistic detail around what they did.\n"
    "11. REFINE ABUNDANT LOGS. If the student's logs for this section are rich (over 200 words), do NOT "
    "pad with filler. Synthesize, distill, and remove repetition. Keep the strongest 3-5 details and "
    "skip the mundane ones. Quality over quantity.\n"
    "12. BUILD AROUND COMPETENCIES. Every paragraph should visibly connect to at least one of the "
    "student's listed competency areas. Name the competency explicitly where it fits naturally.\n"
    "13. SIMPLE ENGLISH. Write in clear, accessible English. Avoid Latin abbreviations, complex "
    "subordinate clauses, and academic jargon when a plain word works. (Exception: technical HIM terms "
    "like NHIS, DHIMS, LHIMS, ICD-10 are fine and should be used.)\n"
    "14. TARGET WORD COUNT IS REAL. The section instruction below includes a TARGET WORD COUNT. "
    "Write within ±20% of that target. Under-writing is treated as FAILURE — the report will fall "
    "short of expectations and the user will be disappointed. When in doubt, write MORE, not less.\n"
)


# ============================================================
# Entry point 1 — report prose drafting
# ============================================================

def draft_sections_with_ai(session, samples=None, target_pages=None):
    """
    Draft the prose sections with AI.

    target_pages:
        The user's explicit page preference (from report.options.target_pages).
        None means "no target set" — the AI will aim for a natural length
        estimated from log volume.
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
                "(follow this strictly):\n" +
                "\n".join(f"- {n}" for n in notes) + "\n"
            )

    # Log volume affects how aggressively the AI must expand
    log_count = session.daily_logs.filter(is_deleted=False).count()
    if log_count < 5:
        if target_pages:
            length_phrase = f"to reach the {target_pages}-page target"
        else:
            length_phrase = (
                "so the report is substantive and professional — not a bare summary"
            )
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
            "into a fuller narrative. Stay truthful to what they did.\n"
        )
    else:
        expansion_rule = ''

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
    elif log_count < 15:
        if target_pages:
            note = (
                f"Note: If the generated report falls short of your {target_pages}-page "
                f"target, adding more daily logs will provide the AI with more material "
                f"to synthesize."
            )
        else:
            note = ""

    drafts = {}
    if note:
        drafts['_ai_note'] = note

    # Compute per-section word budgets (from target_pages, or natural length)
    budgets = _compute_word_budget(target_pages, log_count=log_count)

    max_seconds = getattr(settings, 'REPORT_AI_DRAFTING_MAX_SECONDS', DEFAULT_MAX_SECONDS)
    unique_seed = random.randint(1000, 9999)

    total_start = time.time()
    total = len(DRAFT_SECTIONS)

    for idx, (key, title, instruction) in enumerate(DRAFT_SECTIONS, start=1):
        elapsed = time.time() - total_start
        if elapsed > max_seconds:
            logger.warning(
                'AI drafting: time budget %ds exhausted after %d/%d sections '
                '(elapsed %.0fs) — bailing; remaining sections use defaults',
                max_seconds, len(drafts), total, elapsed,
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
                context_sentence = (
                    "Write enough to make this section substantive — not a bare summary."
                )
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
            f"phrases like 'the student', 'the trainee', or 'the intern'. The report MUST "
            f"sound like the user wrote it themselves.\n"
            f"2. READ ALL DATA: Carefully review the Student Data below, including facility "
            f"details, logs, workshops, and competencies.\n"
            f"3. REFINE AND ENHANCE: NEVER copy raw daily logs verbatim. Synthesize the "
            f"information into polished, professional academic paragraphs.\n"
            f"4. GRAMMAR: Check grammar, spelling, and sentence flow rigorously. Sentences "
            f"must sound professional and mature.\n"
            f"5. DO NOT INVENT: Use ONLY the student data below for personal details, but "
            f"for standard hospital units, you may use general knowledge of the Ghanaian "
            f"health system.\n"
            f"6. STRUCTURE: Follow the structural requirements in the section instruction "
            f"(bullet count, sub-heading organization, format). These are authoritative.\n"
            f"7. UNIQUENESS (Seed: {unique_seed}): Write with a fresh perspective. Avoid "
            f"generic templates. Vary your sentence structure and vocabulary completely "
            f"from standard reports to ensure this report feels unique and personalized.\n"
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
                'AI drafting: section %d/%d (%r) failed — skipping; '
                'will use template default for this section only',
                idx, total, key,
            )
            continue

        # Clean up echoed context or markdown headers
        reply = re.sub(r'^#+\s+.*$', '', reply, flags=re.MULTILINE).strip()

        if reply:
            drafts[key] = reply
            logger.debug('AI drafted section %r (%d chars ≈ %d words)',
                         key, len(reply), len(reply.split()))

    if drafts:
        logger.info(
            'AI drafting complete: %d/%d sections drafted in %.1fs (competencies=%d, '
            'target_pages=%s, log_count=%d)',
            len([k for k in drafts if not k.startswith('_')]),
            total, time.time() - total_start, len(competencies),
            target_pages, log_count,
        )
    else:
        logger.info('AI drafting produced no sections — report will use template defaults')

    return drafts


# ============================================================
# Entry point 2 — presentation bullets + speaker notes (unchanged)
# ============================================================

def draft_presentation_with_ai(session, target_slides=7):
    """
    Draft slide-specific bullets + speaker notes for the PPTX.

    Called only from PracticumReportBuilder.save_pptx() — never during
    report generation — so the report flow stays fast.

    Returns a dict: {section_key: raw_text} suitable for
    report.pptx_content. Keys cover all 7 content slides plus
    'pptx_speaker_notes'.
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
            "You MUST heavily expand on the units and activities using your knowledge of Ghanaian HIM workflows "
            "to ensure there are enough detailed bullets to cover the slides.\n"
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
            f"1. BULLET FORMAT: Every line is a standalone bullet. No markers, no numbering, "
            f"no trailing full stops unless the instruction requires them.\n"
            f"2. WORD LIMITS ARE ABSOLUTE: If the instruction says '12 words or fewer', "
            f"every line must respect that. Exceeding a word limit is a FAILURE.\n"
            f"3. REFINE, DON'T COPY: Synthesize from the student data — never paste raw logs.\n"
            f"   Example: if the raw log says 'Registered 15 patients', do NOT write "
            f"'Registered 15 patients'. Write 'Patient registration and LHIMS data entry'.\n"
            f"4. PRESENTATION VOICE: Slides are spoken, not read. Use active verbs, "
            f"present tense, short clauses.\n"
            f"5. NO HEADINGS: Do NOT include section titles, bullet markers, or markdown.\n"
            f"6. STRUCTURE & GRAMMAR: Ensure perfect grammar. Transform rough log notes into "
            f"clean, professional action statements.\n"
            f"7. EXPAND IF SPARSE: If the student's raw logs for this slide are thin (under 30 words "
            f"or only one short line), do NOT write fewer than the requested bullet count. Use "
            f"realistic Ghanaian HIM knowledge to EXPAND into the full requested number of bullets, "
            f"staying truthful to what they did.\n"
            f"8. REFINE IF ABUNDANT: If the logs are dense (over 200 words), distill to the strongest "
            f"points. Do NOT pad. Pick the top bullets that best represent the section.\n"
            f"9. SIMPLE ENGLISH: Short words, plain sentences. Technical HIM terms are fine.\n"
            f"{competency_hint}"
            f"\nSection instruction (authoritative):\n{instruction}\n\n"
            f"--- STUDENT DATA ---\n{context}\n-------------------\n"
        )

        reply = _call_ai(prompt)

        if reply is None:
            logger.warning(
                'PPTX AI drafting: section %d/%d (%r) failed — skipping; '
                'builder will trim report text for this slide only',
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