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
DRAFT_SECTIONS = [
    ('abstract', 'Abstract',
     "Write a single academic paragraph (150-200 words, third person). "
     "Synthesize the overall experience. Cover: purpose, facility, key units, skills, and main outcome. "
     "Reference at least two specific competency areas by name. "
     "CRITICAL: Do NOT copy raw logs. Refine the language to sound professional and academic. "
     "Do NOT include a keyword line or a heading."),

    ('acknowledgments', 'Acknowledgments',
     "Write one short formal paragraph thanking the facility, the clinical supervisor (if named), and the College. "
     "Use the first-person singular ('I'). Check grammar carefully. Do NOT include a heading."),

    ('introduction', 'Introduction',
     "Write exactly 2 short paragraphs (Max 100 words total). Be concise but highly academic. "
     "Use the first-person voice ('I'). Introduce the purpose of the attachment, the facility name, location, and period. "
     "Briefly mention the units rotated through. "
     "CRITICAL: Do NOT refer to yourself as 'the student' or 'the intern'. Write as if you are the student reflecting on the experience. "
     "Do NOT use bullet points and do NOT include a heading."),

    ('activities_performed', 'Activities Performed',
     "Write an introductory sentence. Then create sub-headings for each unit (e.g., 'Outpatient Department', 'RCH Unit'). "
     "Under each sub-heading, write 1 descriptive paragraph. Use first-person ('I'). "
     "CRITICAL: DO NOT just paste the raw daily logs. Synthesize and refine the activities into professional narratives "
     "(e.g., instead of 'entered NHIS claims', write 'I managed the processing of NHIS claims for facility reimbursement'). "
     "If the student only logged one activity for a unit, EXPAND it into a full, realistic paragraph using your knowledge "
     "of typical Ghanaian HIM workflows — but stay truthful to what they actually did. "
     "Do NOT include the main heading."),

    ('skills_acquired', 'Skills Acquired',
     "Provide exactly 4-5 specific skills acquired. Do NOT write an introductory sentence. "
     "Output ONLY the list items, one per line. "
     "CRITICAL: ABSOLUTELY NO numbers, hyphens, or asterisks at the start of the line. "
     "Use the format: <b>Skill Name</b>: Brief description. Use first-person ('I'). "
     "Refine the logs to create professional statements. Check grammar strictly. Do NOT include a heading."),

    ('workshops', 'Workshops',
     "Write exactly 1 short paragraph (Max 60 words) describing the workshops attended. If none were attended, state that no formal "
     "workshops were held, but practical on-the-job training was received. Use first-person ('I'). Do NOT include a heading."),

    ('lessons_learnt', 'Lessons Learnt',
     "Provide exactly 5-7 short bullet points detailing key lessons. Do NOT write an introductory sentence. "
     "Output ONLY the list items, one per line. "
     "CRITICAL: ABSOLUTELY NO numbers, hyphens, or asterisks at the start of the line. "
     "Use the first-person voice ('I'). Each point must be 1-2 sentences max. "
     "Do NOT duplicate the Skills Acquired section. Focus on broader insights (confidentiality, teamwork, data-driven decision making). "
     "Do NOT include a heading."),

    ('observation', 'Observations',
     "Write exactly 1 professional paragraph (Max 80 words) synthesizing your observations about workflows, staffing, or record systems. "
     "Use first-person ('I'). "
     "CRITICAL: YOU ARE FORBIDDEN FROM LISTING RAW LOG ENTRIES. You must weave the observations into a cohesive, academic narrative. "
     "Example: If the logs mention 'electronic system' and 'high patient volume', you must write: "
     "'I observed that while the electronic health information system streamlined data management, the high patient volume often led to bottlenecks during peak hours, revealing gaps in workflow efficiency.' "
     "Do NOT include a heading."),

    ('challenges', 'Challenges',
     "Write EXACTLY 1 short paragraph. HARD LIMIT: 120 words TOTAL. "
     "Use first-person ('I'). Do NOT include a heading, sub-heading, or bullet list. "
     "Do NOT split into multiple paragraphs.\n"
     "STRUCTURE (follow exactly):\n"
     "- Identify exactly TWO challenges and no more: "
     "(1) high patient volume causing congestion and delayed record retrieval, and "
     "(2) illegible handwriting in physical records complicating NHIS claims entry.\n"
     "- Write 2-3 sentences per challenge. Do NOT introduce a third challenge. "
     "Do NOT repeat the same phrases (e.g. avoid repeating 'undermining the reliability', "
     "'highlighted systemic gaps', or 'warrant targeted interventions').\n"
     "EXAMPLE (use this as a length and tone guide, do not copy verbatim): "
     "'I faced two main challenges during the attachment. First, the overwhelming patient volume often led to congestion at the records desk, which delayed the retrieval of folders and slowed down service delivery. Second, illegible handwriting in the physical registers complicated the accurate entry of NHIS claims and required meticulous cross-checking before submission.'\n"
     "FORBIDDEN: more than 1 paragraph. FORBIDDEN: more than 120 words. FORBIDDEN: more than 2 challenges."),

    ('recommendations', 'Recommendations',
     "Write an introductory sentence, then provide a numbered list of 4-5 actionable recommendations for the facility "
     "and the college. Ensure they directly address the challenges and competencies mentioned earlier. "
     "Check grammar carefully. Do NOT include a heading."),

    ('conclusion', 'Conclusion',
     "Write exactly 1 short paragraph (Max 100 words). Summarize the experience, units rotated, and readiness for professional practice. "
     "End with a brief thank you. Use first-person ('I') and refine the grammar to sound highly professional. Do NOT include a heading."),

    ('references', 'References',
     "APA-style reference list for any sources you cite. If you cite nothing, return an empty response. Do NOT include a heading."),
]

DEFAULT_MAX_SECONDS = 90  # Capped at 90s so it doesn't trigger Gunicorn timeouts


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
)


# ============================================================
# Entry point 1 — report prose drafting
# ============================================================

def draft_sections_with_ai(session, samples=None, target_pages=25):
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

    length_instruction = "Match the length specified in the section instruction."
    if target_pages and target_pages >= 20:
        length_instruction = (
            "Where the section instruction does NOT specify a cap, write in a "
            "detailed, comprehensive academic style with substantial paragraphs "
            "suitable for a 20+ page report."
        )
    elif target_pages and target_pages < 10:
        length_instruction = (
            "Keep the writing concise and brief, as the target report length is short."
        )

    # Dynamic Page-Target & Log Volume Warning
    log_count = session.daily_logs.filter(is_deleted=False).count()
    length_rule = (
        f"\nLENGTH & DETAIL RULE: The student is targeting approximately {target_pages} pages total. "
        "Expand the provided logs into detailed, professional, and descriptive narratives to achieve this length. "
    )
    if log_count < 5:
        length_rule += (
            "\nCRITICAL (EXPANSION REQUIRED): The student has very few daily logs. You MUST heavily expand on the units and activities "
            "mentioned using your extensive knowledge of Ghanaian Health Information Management workflows. "
            "Invent realistic, professional activities consistent with the facilities and units mentioned, "
            "to ensure the report reaches the targeted page count. Do NOT copy raw logs verbatim."
        )

    # Determine a warning note for the final build message
    note = ""
    if log_count < 5:
        note = f"Note: You have very few logs ({log_count}). The AI expanded the content, but adding more logs will improve depth and help reach your {target_pages}-page target."
    elif log_count < 15:
        note = f"Note: If the generated report falls short of your {target_pages}-page target, adding more daily logs will provide the AI with more material to synthesize."

    drafts = {}
    if note:
        drafts['_ai_note'] = note

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

        prompt = (
            f"You are an expert academic editor drafting the '{title}' section of a health-information "
            f"practicum report for a Ghanaian college (COHY).\n\n"
            f"CRITICAL RULES:\n"
            f"1. FIRST-PERSON VOICE: Write strictly in the first-person ('I'). Do NOT use phrases like 'the student', 'the trainee', or 'the intern'. The report MUST sound like the user wrote it themselves.\n"
            f"2. READ ALL DATA: Carefully review the Student Data below, including facility details, logs, workshops, and competencies.\n"
            f"3. REFINE AND ENHANCE: NEVER copy raw daily logs verbatim. Synthesize the information into polished, professional academic paragraphs.\n"
            f"4. GRAMMAR: Check grammar, spelling, and sentence flow rigorously. Sentences must sound professional and mature.\n"
            f"5. DO NOT INVENT: Use ONLY the student data below for personal details, but for standard hospital units, you may use general knowledge of the Ghanaian health system.\n"
            f"6. FORMATTING: Follow the formatting instructions for this specific section strictly. Return ONLY the section text.\n"
            f"7. LENGTH: {length_instruction}\n"
            f"7b. SECTION CAP OVERRIDE (absolute): If the section instruction below specifies "
            f"a maximum word count, a maximum number of paragraphs, a maximum number of sentences, "
            f"or an exact count (e.g. 'exactly 2 paragraphs', 'Max 150 words', 'exactly 1 paragraph'), "
            f"that cap takes ABSOLUTE PRECEDENCE over the general length guidance above. "
            f"Obey the section cap even if it contradicts rule 7. Exceeding a section cap is a FAILURE.\n"
            f"8. UNIQUENESS (Seed: {unique_seed}): Write with a fresh perspective. Avoid generic templates. "
            f"Vary your sentence structure and vocabulary completely from standard reports to ensure this report feels unique and personalized.\n"
            f"{_QUALITY_RULES}"
            f"{competencies_rule}"
            f"\n{length_rule}\n"
            f"\n{format_notes}\n"
            f"Section instructions (these are authoritative and override any general guidance above):\n"
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
        
        # Don't save empty replies
        if reply:
            drafts[key] = reply
            logger.debug('AI drafted section %r (%d chars)', key, len(reply))

    if drafts:
        logger.info(
            'AI drafting complete: %d/%d sections drafted in %.1fs (competencies=%d)',
            len(drafts), total, time.time() - total_start, len(competencies),
        )
    else:
        logger.info('AI drafting produced no sections — report will use template defaults')

    return drafts


# ============================================================
# Entry point 2 — presentation bullets + speaker notes
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