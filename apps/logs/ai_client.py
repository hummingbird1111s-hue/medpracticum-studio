"""
Shared AI client with three-provider fallback:
- Gemini primary (quality, vision)
- Groq fallback (rate-limit relief, free tier, text-only)
- OpenRouter fallback (massive free model pool)
- Rate-limit memory: once Gemini 429s, skipped for the requested duration
- Per-model blacklist: 404'd models skipped for 5 minutes (avoids 6+ wasted API calls per request)
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
        # Try to set HTTP timeout (SDK-version-dependent). Fall back gracefully.
        try:
            ai_client = genai.Client(
                api_key=_gemini_key,
                http_options={'timeout': 20000},  # 20 seconds in ms
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
        max_retries=0,   # FIX: SDK default is 2 → sleeps 20-60s between failed calls
        timeout=15.0,    # FIX: fail fast
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
        max_retries=0,   # FIX
        timeout=15.0,    # FIX
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
# Broad list so at least one works regardless of Google's model rotation.
# Order matters: first usable is tried first.
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

# Use the OpenRouter auto-router so it always picks an active free model
OPENROUTER_MODEL = 'openrouter/free'


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


# ---------- Gemini rate-limit memory (provider-wide) ----------
RATE_LIMIT_COOLDOWN = 60
_gemini_down_until = 0.0


def _gemini_available():
    return time.time() >= _gemini_down_until


def _mark_gemini_down(seconds=RATE_LIMIT_COOLDOWN):
    global _gemini_down_until
    _gemini_down_until = time.time() + seconds
    logger.warning('Gemini provider marked rate-limited for %ss', seconds)


# ---------- Per-model blacklist (skip 404'd models for 5 minutes) ----------
MODEL_COOLDOWN = 300  # 5 minutes
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
    """Parse 'retry in 7h10m17s' or 'retryDelay: 25817s' from the Gemini error."""
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
    """
    Three-provider strategy:
    1. If Gemini available: try up to 3 non-blacklisted models.
    2. Gemini 429 -> mark provider down for requested duration, go to Groq.
    3. Groq primary model; if it fails, Groq fallback model.
    4. If both fail, go to OpenRouter (auto-routed to a working free model).
    """
    if not prompt or not prompt.strip():
        return None
    if not ai_client and not groq_client and not openrouter_client:
        logger.warning('get_ai_reply: no AI provider configured')
        return None

    primary = model or DEFAULT_MODEL
    candidates = [primary] + [m for m in _AVAILABLE_MODELS if m != primary]

    # --- Phase 1: Gemini ---
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
                    # FIX: show actual Google error, not just "retired"
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

    # --- Phase 2: Groq primary ---
    groq_text = _try_groq(prompt, GROQ_MODEL)
    if groq_text:
        logger.info('Groq succeeded (model=%s)', GROQ_MODEL)
        return groq_text

    # --- Phase 2b: Groq fallback model ---
    groq_text = _try_groq(prompt, GROQ_FALLBACK_MODEL)
    if groq_text:
        logger.info('Groq fallback succeeded (%s)', GROQ_FALLBACK_MODEL)
        return groq_text

    # --- Phase 3: OpenRouter (Final Safety Net) ---
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
                logger.info('Vision succeeded (model=%s)', m)
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

    logger.warning('Vision OCR failed across models')
    return None