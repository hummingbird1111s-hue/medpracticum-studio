"""
Provider pool: OpenAI-compatible endpoints with cooldown-aware failover.

Any endpoint that speaks POST {base_url}/chat/completions works (Groq,
Cerebras, SambaNova, Gemini's OpenAI-compat URL, a self-hosted router...).

Cooldowns live in the Django cache, so the cache MUST be shared between
processes (Redis), otherwise each Celery worker learns about 429s separately.
"""
import logging
import random
import re
import time

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r'<think>.*?</think>', re.S | re.I)


def endpoints():
    """Usable endpoints from settings.REPORT_AI_ENDPOINTS (those with a key)."""
    out = []
    for e in getattr(settings, 'REPORT_AI_ENDPOINTS', None) or []:
        if e.get('name') and e.get('base_url') and e.get('api_key') and e.get('model'):
            out.append(e)
    return out


def configured():
    return bool(endpoints())


# ---------------- cooldowns ----------------

def _ck(name):
    return f'ai_pool:cool:{name}'


def _cooldown(name, seconds):
    cache.set(_ck(name), time.time() + seconds, int(seconds) + 5)
    logger.warning('AI pool: %s cooling down for %ds', name, seconds)


def _remaining(name):
    return max(0.0, (cache.get(_ck(name)) or 0) - time.time())


def _retry_after(resp):
    try:
        v = float(resp.headers.get('retry-after', ''))
    except ValueError:
        v = 60.0
    return min(max(v, 5.0), 300.0)


# ---------------- one HTTP call ----------------

def _post(ep, prompt, system, max_tokens, temperature, json_mode, timeout):
    name = ep['name']
    url = ep['base_url'].rstrip('/') + '/chat/completions'
    messages = ([{'role': 'system', 'content': system}] if system else [])
    messages.append({'role': 'user', 'content': prompt})
    payload = {
        'model': ep['model'],
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
    }
    payload.update(ep.get('extra') or {})        # e.g. {'reasoning_effort': 'low'}
    if json_mode and ep.get('json_mode', True):
        payload['response_format'] = {'type': 'json_object'}
    headers = {'Authorization': f"Bearer {ep['api_key']}",
               'Content-Type': 'application/json'}

    for _ in range(2):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            logger.warning('AI pool: %s network error: %s', name, exc)
            _cooldown(name, 20)
            return None
        code = resp.status_code
        if code == 400 and 'response_format' in payload:
            payload.pop('response_format')      # provider lacks JSON mode: retry plain
            continue
        if code == 429:
            _cooldown(name, _retry_after(resp))
            return None
        if code in (401, 403):
            logger.error('AI pool: %s rejected the API key (HTTP %s)', name, code)
            _cooldown(name, 600)
            return None
        if code >= 500:
            _cooldown(name, 20)
            return None
        if code >= 400:
            logger.error('AI pool: %s HTTP %s: %s', name, code, resp.text[:300])
            return None
        try:
            text = resp.json()['choices'][0]['message']['content'] or ''
        except (ValueError, KeyError, IndexError, TypeError):
            logger.error('AI pool: %s returned an unexpected body', name)
            _cooldown(name, 20)
            return None
        text = _THINK_RE.sub('', text).strip()
        return text or None
    return None


# ---------------- public API ----------------

def chat(prompt, max_tokens=1000, temperature=0.65, json_mode=False, system=None):
    """
    Try each available endpoint (random order, skipping cooled-down ones).
    If every endpoint is cooling for <= 15s, wait once and try again.
    Returns reply text, or None so the caller can use the local drafter.
    """
    eps = endpoints()
    if not eps:
        return None
    timeout = float(getattr(settings, 'REPORT_AI_CALL_TIMEOUT', 90))
    deadline = time.time() + float(getattr(settings, 'REPORT_AI_GATEWAY_MAX_WAIT', 150))
    tried = set()

    for round_no in range(2):
        candidates = [e for e in eps if e['name'] not in tried and _remaining(e['name']) <= 0]
        # lower 'priority' number first; random order within the same priority
        candidates.sort(key=lambda e: (e.get('priority', 0), random.random()))
        for ep in candidates:
            if time.time() >= deadline:
                return None
            tried.add(ep['name'])
            text = _post(ep, prompt, system, max_tokens, temperature, json_mode, timeout)
            if text:
                logger.info('AI pool: answered by %s', ep['name'])
                return text
        wait = min(_remaining(e['name']) for e in eps)
        if round_no == 0 and 0 < wait <= 15 and time.time() + wait < deadline:
            time.sleep(wait + 0.5)
            tried.clear()
            continue
        break
    return None
