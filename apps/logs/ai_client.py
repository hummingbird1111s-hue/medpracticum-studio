"""
Shared AI client with three-provider fallback:
- Gemini primary (quality, vision)
- Groq fallback (rate-limit relief, free tier, text-only)
- OpenRouter fallback (massive free model pool)
- Rate-limit memory: once Gemini 429s, skipped for the requested duration
- Per-model blacklist: 404'd models skipped for 5 minutes (avoids 6+ wasted API calls per request)

Attachment handling (✅ FIX v2):
- Gemini's Part.from_bytes only accepts image/* + application/pdf.
- get_ai_reply_with_image() refuses unsupported MIME types with a clear log.
- extract_text_from_attachment() handles .txt, .docx AND .pdf locally
  (via python-docx and pdfplumber). All three are free, instant, deterministic.
- Scanned PDFs (no text layer) fall back to Gemini vision automatically.
- get_ai_reply_with_attachment() is the unified entry point for views.

Hybrid transcription (✅ FIX v3):
- _tesseract_extract() extracts text via local Tesseract binary — no AI, no API.
- get_ai_reply_with_image_or_ocr() tries AI first (better on handwriting) then
  falls back to Tesseract. Used by the competency upload endpoint.
"""

import logging
import os
import time
import re

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------- Gemini ----------
try:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_errors = None
    genai_types = None

try:
    _gemini_key = getattr(settings, 'GEMINI_API_KEY', None)
    if _gemini_key and genai:
        try:
            ai_client = genai.Client(
                api_key=_gemini_key,
                http_options={'timeout': 20000},
            )
        except (TypeError, ValueError, AttributeError):
            ai_client = genai.Client(api_key=_gemini_key)
    else:
        ai_client = None
except Exception as e:
    logger.exception('Gemini Client Init Error: %s', e)
    ai_client = None


# ---------- Groq ----------
try:
    from groq import Groq
    _groq_key = os.environ.get('GROQ_API_KEY', '')
    groq_client = Groq(
        api_key=_groq_key,
        max_retries=0,
        timeout=15.0,
    ) if _groq_key else None
    if not groq_client:
        logger.info('Groq disabled (no GROQ_API_KEY in .env)')
except ImportError:
    groq_client = None
    logger.info('Groq SDK not installed (pip install groq)')
except Exception as e:
    groq_client = None
    logger.exception('Groq Client Init Error: %s', e)


# ---------- OpenRouter (Final Fallback) ----------
try:
    from openai import OpenAI
    _openrouter_key = os.environ.get('OPENROUTER_API_KEY', '')
    openrouter_client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=_openrouter_key,
        max_retries=0,
        timeout=15.0,
    ) if _openrouter_key else None
    if not openrouter_client:
        logger.info('OpenRouter disabled (no OPENROUTER_API_KEY in .env)')
except ImportError:
    openrouter_client = None
    logger.info('OpenRouter SDK not installed (pip install openai)')
except Exception as e:
    openrouter_client = None
    logger.exception('OpenRouter Client Init Error: %s', e)


# ---------- Model catalogs ----------
PREFERRED_MODELS = [
    'gemini-2.5-flash',
    'gemini-2.5-flash-lite',
    'gemini-2.5-pro',
    'gemini-2.0-flash',
    'gemini-2.0-flash-001',
    'gemini-2.0-flash-lite',
    'gemini-2.0-flash-exp',
    'gemini-1.5-flash',
    'gemini-1.5-flash-002',
    'gemini-1.5-pro',
    'gemini-1.5-pro-002',
]
GROQ_MODEL = 'openai/gpt-oss-120b'
GROQ_FALLBACK_MODEL = 'openai/gpt-oss-20b'
OPENROUTER_MODEL = 'openrouter/free'


# ---------- MIME whitelist for Gemini vision ----------
SUPPORTED_VISION_MIMES = {
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/heic',
    'image/heif',
    'application/pdf',
}

# Types we can extract locally then hand to a text model
TEXT_EXTRACTABLE_MIMES = {
    'text/plain',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # .docx
    'application/pdf',  # ✅ FIX v2: try text layer first
}


def _normalize_mime(mime):
    if not mime:
        return 'image/jpeg'
    m = mime.lower().split(';')[0].strip()
    if m in ('image/jpg', 'image/pjpeg'):
        return 'image/jpeg'
    return m


def _is_supported_vision_mime(mime):
    return _normalize_mime(mime) in SUPPORTED_VISION_MIMES


def _discover_live_models():
    if not ai_client:
        return list(PREFERRED_MODELS)
    try:
        live = set()
        for m in ai_client.models.list():
            name = (getattr(m, 'name', '') or '').replace('models/', '', 1)
            if 'generateContent' in (getattr(m, 'supported_actions', None) or []):
                live.add(name)
        usable = [m for m in PREFERRED_MODELS if m in live]
        if usable:
            logger.info('Gemini live models at startup: %s', usable)
            return usable
        else:
            logger.warning(
                'Gemini discovery found NO matching models. '
                'PREFERRED_MODELS=%s ; live models seen=%s',
                PREFERRED_MODELS, sorted(live)[:15],
            )
    except Exception as e:
        logger.warning('Gemini model discovery failed: %s', e)
    return list(PREFERRED_MODELS)


_AVAILABLE_MODELS = _discover_live_models()
DEFAULT_MODEL = getattr(settings, 'GEMINI_MODEL', _AVAILABLE_MODELS[0])


# ---------- Gemini rate-limit memory ----------
RATE_LIMIT_COOLDOWN = 60
_gemini_down_until = 0.0


def _gemini_available():
    return time.time() >= _gemini_down_until


def _mark_gemini_down(seconds=RATE_LIMIT_COOLDOWN):
    global _gemini_down_until
    _gemini_down_until = time.time() + seconds
    logger.warning('Gemini provider marked rate-limited for %ss', seconds)


# ---------- Per-model blacklist ----------
MODEL_COOLDOWN = 300
_model_down_until = {}


def _model_available(model_name):
    return time.time() >= _model_down_until.get(model_name, 0.0)


def _mark_model_down(model_name, seconds=MODEL_COOLDOWN):
    _model_down_until[model_name] = time.time() + seconds
    logger.warning('Gemini model "%s" blacklisted for %ss', model_name, seconds)


# ---------- Error classification ----------
def _exc_code(exc):
    if genai_errors and isinstance(exc, genai_errors.APIError):
        code = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
        if code:
            return int(code)
    msg = str(exc)
    for c in (429, 500, 502, 503, 504, 404, 403, 400):
        if str(c) in msg:
            return c
    return None


def _is_rate_limit(exc):
    return _exc_code(exc) == 429 or 'RESOURCE_EXHAUSTED' in str(exc).upper()


def _extract_retry_seconds(exc, default=60):
    msg = str(exc)
    m = re.search(r'retryDelay[^0-9]*(\d+)s', msg)
    if m:
        return min(int(m.group(1)), 86400)
    m = re.search(r'retry in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?', msg)
    if m and any(m.groups()):
        h, mn, s = (int(g) if g else 0 for g in m.groups())
        total = h * 3600 + mn * 60 + s
        if total > 0:
            return min(total, 86400)
    return default


def _is_missing_model(exc):
    msg = str(exc).upper()
    return _exc_code(exc) == 404 or 'NOT_FOUND' in msg or 'NO LONGER AVAILABLE' in msg


def _is_transient(exc):
    code = _exc_code(exc)
    if code in (500, 502, 503, 504):
        return True
    msg = str(exc).upper()
    return any(k in msg for k in ('500', '502', '503', '504', 'UNAVAILABLE', 'OVERLOADED'))


# ---------- Calls ----------
def _try_gemini(model_name, prompt):
    return ai_client.models.generate_content(model=model_name, contents=prompt)


def _try_groq(prompt, model=GROQ_MODEL):
    if not groq_client:
        return None
    try:
        response = groq_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024,
        )
        return (response.choices[0].message.content or '').strip() or None
    except Exception as exc:
        logger.warning('Groq %s failed: %s', model, str(exc)[:250])
        return None


def _try_openrouter(prompt, model=OPENROUTER_MODEL):
    if not openrouter_client:
        return None
    try:
        response = openrouter_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024,
        )
        return (response.choices[0].message.content or '').strip() or None
    except Exception as exc:
        logger.warning('OpenRouter %s failed: %s', model, str(exc)[:250])
        return None


def get_ai_reply(prompt, model=None):
    if not prompt or not prompt.strip():
        return None
    if not ai_client and not groq_client and not openrouter_client:
        logger.warning('get_ai_reply: no AI provider configured')
        return None

    primary = model or DEFAULT_MODEL
    candidates = [primary] + [m for m in _AVAILABLE_MODELS if m != primary]

    if ai_client and _gemini_available():
        tried_models = []
        for m in candidates:
            if len(tried_models) >= 3:
                break
            if not _model_available(m):
                logger.debug('Skipping %s (in blacklist)', m)
                continue
            tried_models.append(m)
            try:
                response = _try_gemini(m, prompt)
                text = (response.text or '').strip()
                if text:
                    logger.info('Gemini succeeded (model=%s)', m)
                    return text
            except Exception as exc:
                code = _exc_code(exc)
                snippet = str(exc)[:250]
                if _is_rate_limit(exc):
                    cooldown = _extract_retry_seconds(exc, RATE_LIMIT_COOLDOWN)
                    _mark_gemini_down(cooldown)
                    break
                if _is_missing_model(exc):
                    logger.warning('Gemini %s NOT FOUND (code=%s): %s', m, code, snippet)
                    _mark_model_down(m, MODEL_COOLDOWN)
                    continue
                if _is_transient(exc):
                    logger.warning('Gemini %s transient (code=%s): %s', m, code, snippet)
                    continue
                logger.error('Gemini %s hard error (code=%s): %s', m, code, snippet)
                break
        if tried_models:
            logger.info('Gemini failed (tried: %s) — falling back to Groq', tried_models)
        else:
            logger.info('Gemini skipped (all models blacklisted) — falling back to Groq')
    elif not _gemini_available():
        remaining = int(_gemini_down_until - time.time())
        logger.info('Skipping Gemini (cooling down for %ss) — using Groq', max(0, remaining))

    groq_text = _try_groq(prompt, GROQ_MODEL)
    if groq_text:
        logger.info('Groq succeeded (model=%s)', GROQ_MODEL)
        return groq_text

    groq_text = _try_groq(prompt, GROQ_FALLBACK_MODEL)
    if groq_text:
        logger.info('Groq fallback succeeded (%s)', GROQ_FALLBACK_MODEL)
        return groq_text

    logger.info('Groq failed — falling back to OpenRouter')
    openrouter_text = _try_openrouter(prompt, OPENROUTER_MODEL)
    if openrouter_text:
        logger.info('OpenRouter succeeded (model=%s)', OPENROUTER_MODEL)
        return openrouter_text

    logger.error('All AI providers failed for this prompt')
    return None


# ============================================================
# Vision OCR (Gemini only)
# ============================================================

def _try_gemini_vision(model_name, prompt, image_bytes, mime):
    if genai_types is not None:
        contents = [
            genai_types.Part.from_text(text=prompt),
            genai_types.Part.from_bytes(data=image_bytes, mime_type=mime),
        ]
    else:
        contents = [
            prompt,
            {'mime_type': mime, 'data': image_bytes},
        ]
    return ai_client.models.generate_content(model=model_name, contents=contents)


def get_ai_reply_with_image(prompt, image_bytes, mime='image/jpeg'):
    if not ai_client:
        logger.warning('get_ai_reply_with_image: no Gemini client configured')
        return None
    if not image_bytes:
        return None

    mime = _normalize_mime(mime)
    if mime not in SUPPORTED_VISION_MIMES:
        logger.warning(
            'get_ai_reply_with_image: unsupported MIME "%s" — refusing to send to Gemini. '
            'Supported: %s. Use extract_text_from_attachment() for text-based files.',
            mime, sorted(SUPPORTED_VISION_MIMES),
        )
        return None

    if not _gemini_available():
        remaining = int(_gemini_down_until - time.time())
        logger.warning('Gemini cooling down (%ss left) — vision unavailable', max(0, remaining))
        return None

    primary = DEFAULT_MODEL
    candidates = [primary] + [m for m in _AVAILABLE_MODELS if m != primary]

    for m in candidates:
        if not _model_available(m):
            logger.debug('Skipping vision model %s (in blacklist)', m)
            continue
        try:
            response = _try_gemini_vision(m, prompt, image_bytes, mime)
            text = (response.text or '').strip()
            if text:
                logger.info('Vision succeeded (model=%s, mime=%s)', m, mime)
                return text
        except Exception as exc:
            code = _exc_code(exc)
            snippet = str(exc)[:250]
            if _is_rate_limit(exc):
                cooldown = _extract_retry_seconds(exc, RATE_LIMIT_COOLDOWN)
                _mark_gemini_down(cooldown)
                break
            if _is_missing_model(exc):
                logger.warning('Vision %s NOT FOUND (code=%s): %s', m, code, snippet)
                _mark_model_down(m, MODEL_COOLDOWN)
                continue
            if _is_transient(exc):
                logger.warning('Vision %s transient (code=%s): %s', m, code, snippet)
                continue
            logger.error('Vision OCR hard error on %s (code=%s): %s', m, code, snippet)
            break

    logger.warning('Vision OCR failed across models (mime=%s)', mime)
    return None


# ============================================================
# Local text extraction (TXT, DOCX, PDF) + unified entry point
# ============================================================

MAX_EXTRACTED_CHARS = 20000
MAX_PDF_PAGES = 40


def _extract_txt(data):
    text = data.decode('utf-8', errors='replace').strip()
    if not text:
        return None, 'The text file appears to be empty.'
    return text, None


def _extract_docx(data):
    try:
        from io import BytesIO
        from docx import Document
    except ImportError:
        logger.error('extract_text_from_attachment: python-docx not installed')
        return None, 'Server cannot read .docx files (python-docx not installed). Please attach an image, PDF, or .txt instead.'
    try:
        doc = Document(BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text]
        # Also pull text from tables — most forms/competency docs live in tables
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text and cell.text.strip():
                        parts.append(cell.text.strip())
        text = '\n'.join(parts).strip()
        if not text:
            return None, 'The .docx file appears to be empty.'
        return text, None
    except Exception as exc:
        logger.warning('extract_text_from_attachment(docx): %s', exc)
        return None, 'Could not parse this .docx file. It may be corrupted or password-protected.'


def _extract_pdf(data):
    """
    ✅ FIX v2: try local pdfplumber extraction first — free and instant.
    Returns:
      (text, None)      → text found, use it
      (None, None)      → no text layer (scanned PDF) — caller should use vision
      (None, error_msg) → hard parse error
    """
    try:
        from io import BytesIO
        import pdfplumber
    except ImportError:
        logger.warning('extract_text_from_attachment(pdf): pdfplumber not installed — falling back to vision')
        return None, None  # no error — let vision path try
    try:
        text_parts = []
        with pdfplumber.open(BytesIO(data)) as pdf:
            pages = pdf.pages[:MAX_PDF_PAGES]
            for page in pages:
                t = page.extract_text() or ''
                if t.strip():
                    text_parts.append(t)
                if sum(len(p) for p in text_parts) >= MAX_EXTRACTED_CHARS:
                    break
        text = '\n\n'.join(text_parts).strip()
        if not text:
            # No text layer — scanned PDF, caller should try vision
            logger.info('PDF has no extractable text layer — will fall back to Gemini vision')
            return None, None
        return text, None
    except Exception as exc:
        logger.warning('extract_text_from_attachment(pdf): %s', exc)
        # Don't hard-fail — fall through to vision as a second chance
        return None, None


def extract_text_from_attachment(mime, data):
    """
    Pull plain text out of .txt / .docx / .pdf locally.

    Returns (text, error_message). Exactly one is non-None, OR both are None
    if the caller should use the vision path (images, scanned PDFs).
    """
    m = _normalize_mime(mime)

    # Images / non-PDF vision types → caller uses vision path
    if m in SUPPORTED_VISION_MIMES and m != 'application/pdf':
        return None, None

    if m == 'text/plain':
        text, err = _extract_txt(data)
        if err:
            return None, err
        if len(text) > MAX_EXTRACTED_CHARS:
            text = text[:MAX_EXTRACTED_CHARS] + '\n\n[... truncated to 20,000 characters ...]'
        return text, None

    if m == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        text, err = _extract_docx(data)
        if err:
            return None, err
        if len(text) > MAX_EXTRACTED_CHARS:
            text = text[:MAX_EXTRACTED_CHARS] + '\n\n[... truncated to 20,000 characters ...]'
        return text, None

    if m == 'application/pdf':
        return _extract_pdf(data)

    if m == 'application/msword':
        return None, 'Legacy .doc files are not supported. Please save as .docx or PDF and re-upload.'

    return None, f'Unsupported file type: {mime or "unknown"}. Please attach an image, PDF, .txt, or .docx.'


def get_ai_reply_with_attachment(prompt, attachment_bytes, mime, filename=''):
    """
    Unified entry point for AI chat and competency capture.

    Routing:
      - image/*                    → Gemini vision
      - application/pdf (text)     → pdfplumber local extract → text chain
      - application/pdf (scanned)  → Gemini vision (auto-fallback)
      - text/plain                 → decode → text chain
      - .docx                      → python-docx → text chain
      - .doc                       → clear error
      - unknown                    → clear error

    Returns (reply_text, error_message). Exactly one is non-None.
    """
    # No attachment — plain text prompt
    if not attachment_bytes:
        text = get_ai_reply(prompt)
        if text is None:
            return None, 'All AI providers are busy or unavailable. Please try again in a moment.'
        return text, None

    m = _normalize_mime(mime)

    # Images (not PDF) → straight to vision
    if m in SUPPORTED_VISION_MIMES and m != 'application/pdf':
        text = get_ai_reply_with_image(prompt, attachment_bytes, mime=m)
        if text is None:
            return None, (
                'The AI could not read this image. '
                'Try re-saving as JPG or PNG, or take a clearer photo.'
            )
        return text, None

    # Text-extractable (txt / docx / pdf)
    if m in TEXT_EXTRACTABLE_MIMES:
        extracted, err = extract_text_from_attachment(m, attachment_bytes)
        if err:
            logger.warning('get_ai_reply_with_attachment: %s (%s, %s)', err, m, filename)
            return None, err
        if extracted:
            combined = (
                f"{prompt}\n\n"
                f"---\n"
                f"ATTACHED FILE CONTENT ({filename or 'attachment'}):\n"
                f"{extracted}"
            )
            text = get_ai_reply(combined)
            if text is None:
                return None, 'All AI providers are busy or unavailable. Please try again in a moment.'
            return text, None

        # PDF with no text layer → vision fallback
        if m == 'application/pdf':
            logger.info('PDF has no text layer — trying Gemini vision')
            text = get_ai_reply_with_image(prompt, attachment_bytes, mime=m)
            if text is None:
                return None, (
                    'This PDF has no readable text layer and vision could not process it. '
                    'Try a clearer scan, or upload a .docx / .txt version.'
                )
            return text, None

    # Fall-through: unsupported
    return None, f'Unsupported file type: {mime or "unknown"}. Please attach an image, PDF, .txt, or .docx.'


# ============================================================
# ✅ FIX v3 — Hybrid transcription: AI first, Tesseract fallback
# Used by the competency-area upload endpoint.
# ============================================================

def _tesseract_extract(data, mime):
    """
    System OCR via Tesseract. No AI, no external API.
    Returns (text, error). Exactly one is non-None.
    """
    try:
        import pytesseract
        from PIL import Image
        from io import BytesIO
    except ImportError:
        return None, (
            'System OCR not available: pytesseract / Pillow not installed. '
            'Run: pip install pytesseract pillow'
        )

    # Auto-locate the Tesseract binary (Windows / Linux / macOS)
    if not (pytesseract.pytesseract.tesseract_cmd
            and os.path.exists(pytesseract.pytesseract.tesseract_cmd)):
        for candidate in (
            r'C:\Program Files\Tesseract-OCR\tesseract.exe',
            r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
            '/usr/bin/tesseract',
            '/usr/local/bin/tesseract',
            '/opt/homebrew/bin/tesseract',
        ):
            if os.path.exists(candidate):
                pytesseract.pytesseract.tesseract_cmd = candidate
                break

    try:
        m = _normalize_mime(mime)

        if m == 'application/pdf':
            import pdfplumber
            parts = []
            with pdfplumber.open(BytesIO(data)) as pdf:
                for page in pdf.pages[:10]:
                    try:
                        img = page.to_image(resolution=200).original
                    except Exception:
                        continue
                    t = pytesseract.image_to_string(img).strip()
                    if t:
                        parts.append(t)
                    if sum(len(p) for p in parts) > 5000:
                        break
            text = '\n\n'.join(parts).strip()
            return (text or None), (None if text else 'No text found in PDF.')

        img = Image.open(BytesIO(data))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        text = pytesseract.image_to_string(img).strip()
        return (text or None), (None if text else 'No text found in image.')

    except pytesseract.TesseractNotFoundError:
        return None, (
            'Tesseract binary not found. Install from '
            'https://github.com/UB-Mannheim/tesseract/wiki'
        )
    except Exception as e:
        logger.warning('Tesseract extraction failed (%s): %s', mime, e)
        return None, f'System OCR failed: {e}'


def get_ai_reply_with_image_or_ocr(prompt, data, mime='image/jpeg', *, ocr_only=False):
    """
    Hybrid transcription: AI first (better on handwriting), Tesseract fallback.

    ocr_only=True  → skip AI entirely, force system engine.
    Returns (text, error). Exactly one is non-None.
    """
    if not data:
        return None, 'No file provided.'

    mime = _normalize_mime(mime)

    # 1) Try AI first (only for types Gemini vision accepts)
    if (not ocr_only and ai_client and _gemini_available()
            and mime in SUPPORTED_VISION_MIMES):
        ai_text = get_ai_reply_with_image(prompt, data, mime=mime)
        if ai_text:
            logger.info('Transcription via Gemini vision (mime=%s)', mime)
            return ai_text, None
        logger.warning('Gemini vision failed (mime=%s) — falling back to Tesseract', mime)

    # 2) System Tesseract fallback
    tesseract_text, err = _tesseract_extract(data, mime)
    if tesseract_text:
        logger.info('Transcription via Tesseract (mime=%s)', mime)
        return tesseract_text, None

    return None, err or 'Could not transcribe the file.'