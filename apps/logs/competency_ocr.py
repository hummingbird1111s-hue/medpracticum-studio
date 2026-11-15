# apps/logs/competency_ocr.py
import logging

logger = logging.getLogger(__name__)


_OCR_PROMPT = (
    "You are an OCR assistant for a Health Information Management (HIM) "
    "practicum log book. The image is a photo, screenshot, or scan of a "
    "page listing PRACTICUM COMPETENCY AREAS — also called 'competencies', "
    "'skills to be acquired', 'learning outcomes', or 'objectives'.\n\n"
    "TASK: Extract every competency line exactly as written.\n\n"
    "OUTPUT RULES:\n"
    "- Return ONLY the competency lines, one per line.\n"
    "- Strip leading bullets, numbering, letters, and section headers.\n"
    "- Do NOT add any introductory or closing commentary.\n"
    "- Do NOT wrap in quotes, code fences, or markdown.\n"
    "- If the page contains NO competencies, return an empty string.\n"
    "- Keep each line under 500 characters (truncate only if forced)."
)


def _resolve_vision_fn():
    """
    Lazily locate get_ai_reply_with_image across every plausible module path.
    Keeps URL registration alive even if the AI client moves.

    If your AI client file is at apps/reports/ai_drafting.py, the first
    candidate wins. If you ever relocate it, add the new path here.
    """
    candidates = [
        'apps.reports.ai_drafting',
        'reports.ai_drafting',
        'ai_drafting',
        'apps.reports.ai_client',
        'services.ai_client',
        'apps.services.ai_client',
    ]
    for mod_path in candidates:
        try:
            mod = __import__(mod_path, fromlist=['get_ai_reply_with_image'])
        except ImportError:
            continue
        fn = getattr(mod, 'get_ai_reply_with_image', None)
        if callable(fn):
            logger.info('competency_ocr: resolved AI client at %s', mod_path)
            return fn
    return None


def transcribe_competency_image(image_bytes: bytes, mime: str = 'image/jpeg') -> str:
    """
    Returns raw AI output (newline-separated competencies).
    Raises RuntimeError if the AI client or vision function is unavailable.
    """
    fn = _resolve_vision_fn()
    if fn is None:
        raise RuntimeError(
            'Vision OCR is not configured: add get_ai_reply_with_image() '
            'to your AI client module (expected at apps/reports/ai_drafting.py). '
            'You can still type competencies manually.'
        )
    raw = fn(_OCR_PROMPT, image_bytes, mime=mime)
    if raw is None:
        raise RuntimeError(
            'AI vision service is temporarily unavailable. '
            'Type the competencies manually or retry in about a minute.'
        )
    return raw.strip()


def parse_competency_lines(raw: str) -> list:
    """Turn raw OCR output into clean, de-duplicated, length-capped lines."""
    if not raw:
        return []
    out, seen = [], set()
    for line in raw.splitlines():
        cleaned = line.strip()
        for prefix in ('- ', '• ', '* '):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break
        i = 0
        while i < len(cleaned) and cleaned[i].isdigit():
            i += 1
        if i > 0 and i < len(cleaned) and cleaned[i] in '.)':
            cleaned = cleaned[i + 1:].lstrip()
        cleaned = cleaned.strip()
        if not cleaned:
            continue
        if len(cleaned) > 500:
            cleaned = cleaned[:500].rstrip()
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out