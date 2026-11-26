"""
Local (non-AI) fallback for PPTX slide drafting.

Derives slide bullets from the already-generated report content.
The report is guaranteed complete after Phase A (AI or local fallback),
so this module always has good source material.

Each drafter returns a newline-separated string of bullet lines matching
the same format that the AI path produces, so pptx_builder.py consumes
both paths identically.
"""

import logging
import re

logger = logging.getLogger(__name__)


def _text(content, key):
    if not content:
        return ''
    return (content.get(key) or '').strip()


def _sentences(text, max_n=None):
    parts = re.split(r'(?<=[.!?])\s+', (text or '').strip())
    parts = [p.strip() for p in parts if p.strip() and len(p.split()) >= 3]
    return parts[:max_n] if max_n else parts


def _to_bullet(sentence, max_words=15):
    s = ' '.join((sentence or '').split()).strip().rstrip('.').rstrip()
    if not s:
        return ''
    words = s.split()
    if len(words) > max_words:
        s = ' '.join(words[:max_words])
    return s[0].upper() + s[1:]


def _bullets_from(text, max_n=3, max_words=15):
    return [_to_bullet(s, max_words) for s in _sentences(text, max_n)][:max_n]


# ============================================================
# Per-slide drafters
# ============================================================

def _intro_bullets(session, content):
    bullets = _bullets_from(_text(content, 'introduction'), max_n=3, max_words=14)
    if len(bullets) < 3:
        fac = getattr(session, 'facility', None)
        fac_name = getattr(fac, 'name', '') if fac else ''
        if fac_name and not any(fac_name in b for b in bullets):
            bullets.append(_to_bullet(f"Attachment at {fac_name}", 14))
    return '\n'.join(bullets[:3])


def _facility_bullets(session, content):
    fac = getattr(session, 'facility', None)
    bullets = []
    if fac:
        name = getattr(fac, 'name', '') or ''
        ftype = getattr(fac, 'facility_type', '') or ''
        district = getattr(fac, 'district', '') or ''
        region = getattr(fac, 'region', '') or ''
        if name:
            bullets.append(_to_bullet(f"Facility: {name}", 14))
        if ftype:
            bullets.append(_to_bullet(f"Type: {ftype}", 14))
        if district or region:
            loc = ', '.join(p for p in [district, region] if p)
            bullets.append(_to_bullet(f"Located in {loc}", 14))
    if len(bullets) < 3:
        obs = _text(content, 'observation')
        for s in _sentences(obs, 3 - len(bullets)):
            bullets.append(_to_bullet(s, 14))
    return '\n'.join(bullets[:3])


def _activity_bullets(session, content):
    activities = _text(content, 'activities_performed')
    lines = activities.splitlines()
    current_unit = ''
    bullets = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line[-1] not in '.!?' and len(line.split()) <= 8:
            current_unit = line
            continue
        text = line.rstrip('.')
        words = text.split()
        if len(words) > 10:
            text = ' '.join(words[:10])
        bullet = f"{current_unit}: {text}" if current_unit else text
        bullets.append(_to_bullet(bullet, 14))
        if len(bullets) >= 6:
            break
    return '\n'.join(bullets)


def _skills_bullets(session, content):
    skills = _text(content, 'skills_acquired')
    lines = [l.strip() for l in skills.splitlines() if l.strip()]
    bullets = []
    for line in lines[:5]:
        stripped = re.sub(
            r'^I (?:learnt how to|learned how to|was able to|gained the ability to|'
            r'developed the skill of)\s+',
            '', line, flags=re.IGNORECASE,
        ).strip().rstrip('.').rstrip()
        if stripped:
            bullets.append(_to_bullet(stripped[0].upper() + stripped[1:], 10))
    return '\n'.join(bullets[:5])


def _challenges_bullets(session, content):
    ch = _text(content, 'challenges')
    low = ch.lower()
    if 'no major challenge' in low or 'did not encounter' in low:
        return "No major challenges were recorded"
    bullets = _bullets_from(ch, max_n=3, max_words=14)
    return '\n'.join(bullets) or "No major challenges were recorded"


def _observation_recommendation_bullets(session, content):
    obs_bullets = _bullets_from(_text(content, 'observation'), max_n=2, max_words=14)
    rec_bullets = _bullets_from(_text(content, 'recommendations'), max_n=2, max_words=14)
    bullets = obs_bullets + rec_bullets
    while len(bullets) < 4:
        bullets.append("Further detail noted during the practicum")
    return '\n'.join(bullets[:4])


def _conclusion_bullets(session, content):
    bullets = _bullets_from(_text(content, 'conclusion'), max_n=3, max_words=14)
    while len(bullets) < 3:
        bullets.append("Overall, the practicum provided valuable professional experience")
    return '\n'.join(bullets[:3])


SLIDE_TITLES = (
    'Introduction',
    'Facility Profile',
    'Activities Performed',
    'Skills Acquired',
    'Challenges Faced',
    'Observation and Recommendation',
    'Conclusion',
)

_NOTES_SOURCE = {
    'Introduction': 'introduction',
    'Facility Profile': 'observation',
    'Activities Performed': 'activities_performed',
    'Skills Acquired': 'skills_acquired',
    'Challenges Faced': 'challenges',
    'Observation and Recommendation': 'recommendations',
    'Conclusion': 'conclusion',
}


def _speaker_notes(session, content):
    lines = []
    for title in SLIDE_TITLES:
        src_key = _NOTES_SOURCE[title]
        sentences = _sentences(_text(content, src_key), 2)
        if not sentences:
            sentences = [f"This slide summarises the {title.lower()} of the practicum."]
        if len(sentences) == 1:
            sentences.append("The key points are presented on the slide.")
        body = ' '.join(sentences[:2])
        lines.append(f"{title}: {body}")
    return '\n'.join(lines)


DRAFTERS = {
    'pptx_intro_bullets': _intro_bullets,
    'pptx_facility_bullets': _facility_bullets,
    'pptx_activities_bullets': _activity_bullets,
    'pptx_skills_bullets': _skills_bullets,
    'pptx_challenges_bullets': _challenges_bullets,
    'pptx_observation_recommendation_bullets': _observation_recommendation_bullets,
    'pptx_conclusion_bullets': _conclusion_bullets,
    'pptx_speaker_notes': _speaker_notes,
}


def draft_local_pptx_section(session, key, content):
    """Draft one slide section locally. Never raises; returns '' only if key unknown."""
    fn = DRAFTERS.get(key)
    if fn is None:
        return ''
    try:
        return (fn(session, content) or '').strip()
    except Exception:
        logger.exception('local_pptx_drafter failed for %s', key)
        return ''


def draft_local_pptx_sections(session, content, keys=None):
    """Draft every slide (or just the given keys). Returns {key: text}."""
    keys = keys or list(DRAFTERS.keys())
    return {k: draft_local_pptx_section(session, k, content) for k in keys}