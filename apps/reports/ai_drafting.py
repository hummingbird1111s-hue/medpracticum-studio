"""
Best-effort AI drafting for report sections, with code-enforced quality gates.

v11 — DEEP CLEANUP of section isolation:
  - Activities Performed MUST be bullet format under unit sub-headings
  - Skills Acquired MUST derive from specific activities, in bullet format
  - Lessons Learnt MUST be reflections on skills, not activity restatements
  - Obstacles/solutions are FORBIDDEN from Activities and Skills
  - Sparse logs (<5) trigger ENRICHMENT of real activities using competencies
  - Filler phrases are stripped from all output

Two entry points:
  - draft_sections_with_ai(session)        → report prose   → report.content
  - draft_presentation_with_ai(session)    → slide bullets  → report.pptx_content

Plus one refinement entry point:
  - refine_user_content(session, content)  → polish user edits without redrafting
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
     "Write the 'Activities Performed' section as BULLET-FORMAT ACTIVITY LINES grouped "
     "under UNIT SUB-HEADINGS. Follow this structure EXACTLY:\n\n"
     "  [Opening paragraph of 2-3 sentences]\n\n"
     "  <Unit Name 1>\n"
     "  Activity line 1\n"
     "  Activity line 2\n"
     "  Activity line 3\n\n"
     "  <Unit Name 2>\n"
     "  Activity line 1\n"
     "  Activity line 2\n"
     "  Activity line 3\n\n"
     "Each activity line = ONE concrete action, starting with a past-tense verb "
     "(Registered, Captured, Booked, Processed, Verified, Maintained, Recorded, Prepared, Reconciled). "
     "3-4 activity lines per unit.\n\n"
     "CRITICAL BANS — do NOT include in this section:\n"
     "  - Any challenge, obstacle, difficulty, delay, congestion, overcrowding, shortage, or barrier.\n"
     "  - Any solution, response, workaround, or recommendation.\n"
     "  - Any phrasing like 'due to high patient volume', 'to manage overcrowding', "
     "'extended working hours', 'streamlined', 'address delays', 'cope with demand', "
     "'under pressure', 'despite', 'in spite of'.\n"
     "  Those belong to the Challenges and Recommendations sections, NOT here.\n\n"
     "SPARSE-LOG ENRICHMENT (if fewer than 5 logs): For each real activity in the logs, "
     "expand it with professional context — name the data system used (LHIMS, HAMS, paper register), "
     "the data quality check applied, the professional standard followed, or the competency supported. "
     "Example: log says 'registered 15 patients' → enriched activity: "
     "'Registered approximately fifteen new patients daily, capturing complete demographic, "
     "address, and NHIS details in the LHIMS electronic system while maintaining data quality "
     "and confidentiality standards.' "
     "Do NOT invent new units, new activities, new workshops, or new patient counts — enrich ONLY "
     "the real activities by adding professional context.\n\n"
     "FORMAT RULES:\n"
     "  - Do NOT prefix any line with '- ', '* ', '• ', or numbers. The renderer adds bullets.\n"
     "  - Do NOT use markdown (**bold**, ## headings). Plain text.\n"
     "  - Do NOT include the main heading.\n"
     "  - Use first-person voice ('I').\n"
     "Aim for 300-400 words total."),

    ('skills_acquired', 'Skills Acquired',
     "Write the 'Skills Acquired' section as BULLET-FORMAT SKILL LINES. "
     "EACH skill MUST trace back to a specific activity from the Activities Performed section.\n\n"
     "Format each skill as: <Skill Name>: 2-3 sentences describing the skill, the specific "
     "activity that developed it, and the HIM competency supported.\n\n"
     "Provide 5-7 skills. One skill per line. No bullet markers.\n\n"
     "CRITICAL RULES:\n"
     "  - Every skill MUST be evidenced by an activity in the logs (or by the enriched "
     "activities above). Never invent a skill not supported by an activity.\n"
     "  - Name the activity that produced the skill: 'Through registering new patients...', "
     "'By booking follow-up appointments...', 'While capturing infant demographics...'\n"
     "  - Do NOT restate the activity verbatim — describe what was LEARNED from doing it.\n\n"
     "CRITICAL BANS — do NOT include in this section:\n"
     "  - Any obstacle, challenge, difficulty, or condition endured. Skills describe "
     "CAPABILITIES GAINED, not conditions survived.\n"
     "  - Forbidden phrases: 'under pressure', 'despite high volumes', "
     "'while managing congestion', 'maintained workflow despite', 'coped with', "
     "'adapted to', 'handled the workload', 'managed stress'.\n\n"
     "SPARSE-LOG ENRICHMENT (if fewer than 5 logs): You may name sub-skills within a single "
     "activity to reach 5-6 skills. Legitimate sub-skills of 'patient registration' include: "
     "'data quality verification during intake', 'demographic capture for pediatric cases', "
     "'NHIS verification during registration', 'records confidentiality enforcement'. "
     "These are truthful expansions of one activity, not new inventions.\n\n"
     "Plain text, one skill per line. No markdown. No main heading. Use first-person ('I').\n"
     "Aim for 250-350 words total."),

    ('lessons_learnt', 'Lessons Learnt',
     "Write the 'Lessons Learnt' section as REFLECTIONS drawn from the Skills Acquired and "
     "Activities Performed sections above.\n\n"
     "Output 5-7 lessons, one per line. OR present them as 2-3 short connected paragraphs. "
     "One lesson per line is preferred.\n\n"
     "CRITICAL — each lesson MUST be a REFLECTION, not an activity restatement:\n"
     "  BAD: 'I registered 15 new patients and booked appointments for them.' "
     "(This is just restating an activity.)\n"
     "  GOOD: 'I learnt that accurate patient registration is the foundation of every "
     "subsequent clinical encounter. When a demographic field is wrong at intake, every "
     "downstream process — from NHIS claims to appointment reminders — inherits that error. "
     "This reinforced the HIM principle that data quality must be enforced at the source.'\n\n"
     "Each lesson starts with a reflection cue: 'I learnt', 'I realised', 'I appreciated', "
     "'I now understand', or 'I discovered'.\n\n"
     "Each lesson is 2-3 sentences: WHAT you did, WHAT you learned from it, WHAT broader "
     "principle or competency this connects to (data quality, confidentiality, ethics, "
     "teamwork, workflow efficiency).\n\n"
     "Do NOT mention obstacles, delays, or challenges as a lesson theme — reflect on "
     "professional principles, not on working conditions.\n"
     "Plain text, no bullet markers, no markdown. Use first-person ('I'). No heading.\n"
     "Aim for 250-300 words total."),

    ('challenges', 'Challenges',
     "Write the 'Challenges' section as 3-4 short paragraphs. Every challenge must come "
     "FROM THE LOGS (the CHALLENGE field of each daily log). If the logs recorded no "
     "challenges, generate 2-3 realistic ones for this facility type in Ghanaian HIM "
     "practice (patient congestion, illegible paper records, LHIMS network downtime, "
     "incomplete NHIS claim documentation, storage space constraints).\n\n"
     "STRUCTURE:\n"
     "  - Paragraph 1: introduce the challenges generally and their professional significance.\n"
     "  - Paragraphs 2-3: each describes ONE specific challenge in detail — what happened, "
     "how it impacted the work, how you responded.\n"
     "  - Paragraph 4 (optional): reflect on what these reveal about the facility's systems.\n\n"
     "RULES:\n"
     "  - NEVER copy raw log text. Rephrase into polished academic prose.\n"
     "  - Do NOT invent challenges contradicted by the logs.\n"
     "  - Do NOT mention activities or solutions here — challenges ONLY.\n"
     "  - First-person voice ('I'). No bullets, no markdown, no heading. Plain prose.\n"
     "Aim for 250-350 words total."),

    ('recommendations', 'Recommendations',
     "Write the 'Recommendations' section. Base every recommendation on the challenges "
     "and observations described earlier in this report — NOT on the SOLUTION field of "
     "the logs (paraphrase solutions if present, but generate fresh recommendations too).\n\n"
     "STRUCTURE:\n"
     "  - One opening paragraph of 2-3 sentences framing the recommendations.\n"
     "  - Two groups under bold labels: 'For the Facility:' (3-4 items) and "
     "'For the College:' (3-4 items).\n"
     "  - Each recommendation MUST follow this structure: WHO should do WHAT, and WHY "
     "it matters based on a specific challenge you encountered.\n\n"
     "RULES:\n"
     "  - Do NOT copy raw SOLUTION text from logs verbatim.\n"
     "  - Each recommendation should be 2-3 sentences, actionable, and specific.\n"
     "  - Use professional tone (third-person 'the facility should' or plural 'we').\n"
     "  - Do NOT include the main heading.\n"
     "Aim for 250-350 words total."),

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
     "CRITICAL: Do NOT repeat challenges here — this section describes HOW the facility works, "
     "not WHAT went wrong. Save obstacle details for the Challenges section. "
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

PPTX_DRAFT_SECTIONS = [
    ('pptx_intro_bullets', 'Slide: Introduction',
     "Write 3 short bullet points for a presentation slide titled 'Introduction'. "
     "Each bullet MUST be 12 words or fewer. Output ONE bullet per line, no markers, no numbers. "
     "Use first-person ('I'). Cover: purpose of attachment, facility name, key units rotated. "
     "Example line: 'Attached to Adigbe Government Hospital for four weeks'\n"
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
     "SOURCE: use ONLY the ACTIVITY field from the logs (labelled 'ACTIVITY:' in the data below).\n\n"
     "CRITICAL BANS — do NOT include in this slide:\n"
     "  - Any challenge, obstacle, difficulty, delay, or barrier.\n"
     "  - Any solution, workaround, response to pressure, or recommendation.\n"
     "  - Forbidden phrases: 'extended working hours', 'managing high patient volume', "
     "'overcrowding', 'streamlining', 'addressing delays', 'coping with', 'under pressure'.\n\n"
     "FORBIDDEN: more than 6 bullets. FORBIDDEN: bullets longer than 10 words. "
     "FORBIDDEN: em dash, en dash, hyphen as bullet separator, asterisks, markdown. "
     "FORBIDDEN: including observations, recommendations, or anything that is not a direct activity. "
     "Only mention units that ACTUALLY appear in the logs. "
     "Example line: 'RCH: registered pregnant women and booked appointments'\n"),

    ('pptx_skills_bullets', 'Slide: Skills Acquired',
     "Write 4-5 short bullet points naming skills acquired. "
     "Each bullet MUST be 8 words or fewer. Output ONE bullet per line, no markers. "
     "Just the skill name plus one short qualifier. "
     "FORBIDDEN: any challenge, obstacle, or condition-endured language. "
     "FORBIDDEN: phrases like 'under pressure', 'despite high volumes', 'coping with workload'.\n"
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

_VERBATIM_NGRAM_SIZE = 7

_QUALITY_STOPWORDS = frozenset({
    'a','an','the','and','or','of','in','on','at','to','for','with','by','as',
    'is','was','were','be','been','being','have','has','had','do','does','did',
    'i','me','my','we','our','this','that','these','those','from','into','within',
    'also','other','some','various','including','included','such','it','its',
    'but','so','if','when','while','than','then','there','their','they','them',
    'he','she','his','her','him','you','your','will','would','can','could',
    'should','may','might','must','not','no','yes','very','just','only','been',
})

_PLACEHOLDER_TOKENS = (
    '[insert', '[your name', '[facility name', '[date]', '[name]',
    'lorem ipsum', '<insert', 'todo:', 'placeholder:', 'xxxx', 'tbd',
)

_MAX_RETRIES_TOTAL = 3
_MAX_RETRY_SECONDS = 30


# ------------------------------------------------------------
# NEW: obstacle / solution markers — used to strip these from
# the Activities and Skills sections, where they must never appear.
# ------------------------------------------------------------

_OBSTACLE_MARKERS = (
    'challeng', 'difficult', 'struggl', 'obstacle', 'barrier',
    'hinder', 'disrupt', 'delay', 'congest', 'overcrowd', 'shortage',
    'lack of', 'insufficient', 'bottleneck', 'downtime', 'slowdown',
    'high patient volume', 'heavy patient volume', 'workload',
    'extended working hours', 'extended hours', 'long hours',
    'cope with', 'manage high', 'manage heavy', 'manage large',
    'despite the', 'in spite of', 'under pressure',
    'struggled with', 'forced to', 'had to contend',
)

_SOLUTION_MARKERS = (
    'recommend', 'solution', 'should extend', 'should be extended',
    'address the', 'resolve the', 'mitigate', 'streamlin',
    'restructur', 'enhance the workflow', 'optimi', 'reduc the ',
    'assisted with', 'helped manage',
)

# Filler phrases stripped from all output.
_FILLER_PATTERNS = [
    re.compile(r'It is worth noting that\s+', re.IGNORECASE),
    re.compile(r'It can be observed that\s+', re.IGNORECASE),
    re.compile(r'It is important to note that\s+', re.IGNORECASE),
    re.compile(r'It should be noted that\s+', re.IGNORECASE),
    re.compile(r'Through this experience,?\s+', re.IGNORECASE),
    re.compile(r'In the context of this attachment,?\s+', re.IGNORECASE),
    re.compile(r'As a matter of fact,?\s+', re.IGNORECASE),
]


# ============================================================
# Source-of-truth rules — extended in v11 with H/I/J/K
# ============================================================

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
    "H. STRICT SECTION ISOLATION — Obstacles, challenges, delays, shortages, "
    "and workflow difficulties MUST NEVER appear in Activities Performed or "
    "Skills Acquired. Those sections describe WHAT the student DID and WHAT "
    "they LEARNED — not the CONDITIONS they endured or HOW they worked "
    "around difficulties. Move all obstacle content to Challenges. Move all "
    "solution content to Recommendations.\n"
    "I. SPARSE-LOG ENRICHMENT — When the student has fewer than 5 daily "
    "logs, do NOT pad with invented activities or units. Instead, EXPAND each "
    "REAL activity with professional context (system used, data quality "
    "check, professional standard followed, competency supported). Enrichment "
    "stays truthful to the log — no new activities invented.\n"
    "J. BULLET-FORMAT ENFORCEMENT — Activities Performed MUST be bullet-format "
    "activity lines grouped under unit sub-headings. Skills Acquired MUST be "
    "bullet-format skill lines. Do NOT return these sections as undifferentiated "
    "paragraphs.\n"
    "K. CONCISENESS — Remove filler phrases ('It is worth noting that', "
    "'It can be observed that', 'Through this experience', 'In the context "
    "of this attachment'). Prefer direct statements over elaborate constructions.\n"
)


# ============================================================
# Post-processing — strip obstacle/solution content from
# sections where they do not belong
# ============================================================

def _strip_obstacle_content(text, section_key):
    """
    Remove obstacle/solution content from a section that must describe
    ONLY activities or skills.

    Works at LINE granularity — any line containing an obstacle/solution
    marker is dropped entirely. Because Activities Performed and Skills
    Acquired use one-line-per-item formats, this preserves structure.

    Returns (cleaned_text, removed_items) where removed_items is a list
    of (kind, snippet) tuples for logging.
    """
    if section_key not in ('activities_performed', 'skills_acquired'):
        return text, []
    if not text:
        return text, []

    removed = []
    kept_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept_lines.append(line)
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

        kept_lines.append(line)

    return '\n'.join(kept_lines), removed


def _strip_filler(text):
    """
    Remove filler phrases that add no content.
    Applied to every AI-generated section after _clean_draft_text.
    """
    if not text:
        return text
    for pat in _FILLER_PATTERNS:
        text = pat.sub('', text)
    # Re-capitalize any sentence that now starts lowercase after a stripped
    # phrase like "It is worth noting that the student..." → "The student..."
    text = re.sub(r'^([a-z])', lambda m: m.group(1).upper(), text, flags=re.MULTILINE)
    # Collapse double spaces
    text = re.sub(r'  +', ' ', text)
    return text


# ============================================================
# Existing helpers (unchanged)
# ============================================================

def source_fingerprint(session):
    """
    Deterministic hash of everything that drives AI drafting.
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
                    content_words = [w for w in window if w not in _QUALITY_STOPWORDS]
                    if len(content_words) < 3:
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

    if min_word_floor:
        actual_words = len(text.split())
        threshold = int(min_word_floor * 0.5)
        if actual_words < threshold:
            issues.append((
                'too_short',
                f'only {actual_words} words (minimum {threshold} expected)'
            ))

    if max_word:
        actual_words = len(text.split())
        if actual_words > max_word:
            issues.append((
                'too_long',
                f'{actual_words} words (target max {max_word})'
            ))

    if re.search(r'^[ \t]*#{1,6}[ \t]', text, re.MULTILINE):
        issues.append(('markdown_headers', 'contains raw markdown # headers'))
    if '**' in text or re.search(r'^\s*[*\-\u2022]\s+', text, re.MULTILINE):
        issues.append(('markdown_noise', 'contains ** or bullet/asterisk markers'))

    return issues


def _clean_draft_text(text):
    """
    Universal cleanup applied to every AI reply before quality check.
    Extended in v11 to also strip filler phrases.
    """
    if not text:
        return ''
    text = re.sub(r'^[ \t]*#+[ \t]*.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\w)\*(?!\s)([^*\n]+?)\*(?!\w)', r'\1', text)
    text = re.sub(r'^\s*[*\-\u2022]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[\.\)]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[A-Z][A-Z\s]{3,}\n+', '', text, count=1)
    text = re.sub(r'\s+[\u2013\u2014]\s+', ': ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # NEW in v11: strip filler phrases
    text = _strip_filler(text)
    return text.strip()


def _build_retry_prompt(original_prompt, section_title, issues):
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


_SECTION_FLOORS = {
    'abstract':              120,
    'acknowledgments':       80,
    'introduction':          150,
    'activities_performed':  300,
    'skills_acquired':       180,
    'workshops':             60,
    'lessons_learnt':        180,
    'observation':           150,
    'challenges':            180,
    'recommendations':       180,
    'conclusion':            120,
}

_BUDGET_DISTRIBUTION = {
    'abstract':              0.040,
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


def _compute_word_budget(target_pages, log_count=0, competency_count=0):
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

    budget_sum = sum(budgets.values())
    headroom_ceiling = int(total_prose_words * 1.2)
    if budget_sum > headroom_ceiling and headroom_ceiling > 0:
        scale = headroom_ceiling / budget_sum
        budgets = {
            section: max(80, int(word_count * scale))
            for section, word_count in budgets.items()
        }

    return budgets


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
    "16. CONCISENESS — write directly. Remove filler phrases. Prefer short "
    "declarative sentences over elaborate constructions.\n"
)


def draft_sections_with_ai(session, samples=None, target_pages=None):
    """
    Draft the prose sections with AI, with code-enforced quality gates.

    v11: adds obstacle-stripping pass for activities_performed and
    skills_acquired after the standard cleaning step. Also strips filler
    phrases from all sections.
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
            "or patient counts. HOWEVER — enrich each REAL activity with "
            "professional context (system used, data quality check applied, "
            "professional standard followed, competency supported). For "
            "Activities Performed and Skills Acquired, you MAY name sub-skills "
            "within a single activity to reach the target count. For Lessons "
            "Learnt, Observation, Recommendations, Conclusion, and Abstract, "
            "expand using the listed competency areas and general HIM "
            "principles — present it as reflection, not as new log entries.\n"
        )
    elif log_count < 15:
        expansion_rule = (
            f"\nNOTE: The student has {log_count} daily logs. Synthesize them. "
            "Do not add units or activities that are not present in the logs. "
            "Enrich real activities with professional context where useful.\n"
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
                f"to what you actually logged, and enriched each real activity with "
                f"professional context using your competency areas. Adding more "
                f"logs will strengthen depth and help reach your "
                f"{target_pages}-page target."
            )
        else:
            note = (
                f"Note: You have very few logs ({log_count}). The AI stayed close "
                f"to what you actually logged, and enriched each real activity with "
                f"professional context using your competency areas. Adding more "
                f"logs will further improve depth."
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

        # NEW v11: strip obstacle/solution content from sections where it
        # must never appear.
        reply, removed_items = _strip_obstacle_content(reply, key)
        if removed_items:
            logger.info(
                'AI drafting: stripped %d obstacle/solution line(s) from %r: %s',
                len(removed_items), key,
                '; '.join(f'{kind}:{snippet}' for kind, snippet in removed_items[:3]),
            )

        issues = _quality_issues(
            key, reply, log_ngrams,
            min_word_floor=_SECTION_FLOORS.get(key, 0),
            max_word=max_word,
        )

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
        retry_reply, retry_removed = _strip_obstacle_content(retry_reply, key)
        if retry_removed:
            logger.info(
                'AI drafting: retry stripped %d obstacle/solution line(s) from %r',
                len(retry_removed), key,
            )

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


def draft_presentation_with_ai(session, target_slides=7):
    """
    Draft slide-specific bullets + speaker notes for the PPTX.

    v11: obstacle content is stripped from pptx_activities_bullets and
    pptx_skills_bullets after the standard cleanup step.
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
        # Strip obstacle content from the two bullet sections where it must never appear.
        if key in ('pptx_activities_bullets', 'pptx_skills_bullets'):
            kept_lines = []
            dropped = 0
            for line in reply.splitlines():
                lowered = line.lower()
                if any(m in lowered for m in _OBSTACLE_MARKERS) or \
                   any(m in lowered for m in _SOLUTION_MARKERS):
                    dropped += 1
                    continue
                kept_lines.append(line)
            if dropped:
                logger.info(
                    'PPTX AI drafting: dropped %d obstacle/solution bullet(s) from %r',
                    dropped, key,
                )
            reply = '\n'.join(kept_lines)

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
    if not text:
        return ''
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\s+[\u2013\u2014]\s+', ': ', text)
    text = re.sub(r'\s+-\s+', ': ', text)
    text = re.sub(r'^[ \t]*#+[ \t]*.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = _strip_filler(text)
    return text.strip()


# ============================================================
# v9 — AI refinement of user-edited content (unchanged)
# ============================================================

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