"""
One-JSON-call-per-report drafting (and per-presentation), on top of ai_gateway.

Flow: 1 call -> parse JSON -> validate every section with the existing
finalize_section / _quality_issues -> failed sections get ONE repair call
(only those sections) -> anything still failing uses the local drafter.
Return shapes match draft_sections_with_ai / draft_presentation_with_ai.
"""
import json
import logging
import re

from django.conf import settings

from . import ai_drafting as ai
from . import ai_gateway, local_drafter, local_pptx_drafter

logger = logging.getLogger(__name__)

_FENCE = chr(96) * 3                      # markdown code fence

_JSON_SYSTEM = ("You are a precise writing assistant. Output exactly one valid JSON "
                "object and nothing else: no markdown fences, no commentary.")


def parse_json_object(text):
    """Parse a JSON object; salvage complete string values from truncated output."""
    if not text:
        return {}
    t = re.sub(r'^\s*' + _FENCE + r'(?:json)?\s*|\s*' + _FENCE + r'\s*$', '',
               text.strip(), flags=re.I)
    start, end = t.find('{'), t.rfind('}')
    if start != -1 and end > start:
        try:
            obj = json.loads(t[start:end + 1], strict=False)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    out = {}
    for m in re.finditer(r'"([A-Za-z_]+)"\s*:\s*"((?:[^"\\]|\\.)*)"', t, flags=re.S):
        try:
            out[m.group(1)] = json.loads('"' + m.group(2) + '"', strict=False)
        except ValueError:
            continue
    return out


def _as_text(raw):
    if isinstance(raw, (list, tuple)):
        return '\n'.join(str(x) for x in raw)
    if isinstance(raw, dict):                       # e.g. speaker notes keyed by slide
        return '\n'.join(f'{k}: {v}' for k, v in raw.items())
    return raw if isinstance(raw, str) else ''


def _token_budget(keys):
    cap = int(getattr(settings, 'REPORT_AI_BATCH_MAX_TOKENS', 5000))
    need = sum(ai._SECTION_MAX_TOKENS.get(k, ai._DEFAULT_MAX_TOKENS) for k in keys) + 300
    return min(cap, need)


# ============================================================
# Report
# ============================================================

def _report_prompt(sections, context, extras, budgets):
    spec = []
    for key, title, instruction in sections:
        hint = ''
        if budgets.get(key):
            b = budgets[key]
            hint = f"\nLENGTH: roughly {int(b * 0.8)} to {int(b * 1.2)} words, no more than the source supports."
        spec.append(f'KEY "{key}" ({title}):\n{instruction}{hint}')
    keys = ', '.join(f'"{k}"' for k, _t, _i in sections)
    return (
        "You are an expert academic editor drafting a health-information practicum "
        "report for a Ghanaian college (COHY).\n\n"
        f"{ai._COMMON_RULES}\n{extras}"
        f"OUTPUT FORMAT: one JSON object with exactly these keys: {keys}. Every value "
        "is a plain string. Use \\n for line breaks inside a string: prose sections "
        "separate paragraphs with \\n\\n; skills_acquired has one skill per line; "
        "activities_performed uses a heading line, then one activity per line, with "
        "\\n\\n between units. Use the person and layout each section's instruction "
        "asks for (the abstract is third person, all others first person).\n\n"
        "SECTION INSTRUCTIONS (authoritative):\n\n" + '\n\n'.join(spec) +
        f"\n\n--- STUDENT DATA ---\n{context}\n-------------------\n"
    )


def _accept_section(key, title, raw, fallback_unit, log_ngrams):
    raw = _as_text(raw)
    if not raw.strip():
        return ''
    text = ai.finalize_section(key, raw, fallback_unit, title)
    if not text:
        return ''
    issues = [i for i in ai._quality_issues(key, text, log_ngrams,
                                            ai._SECTION_FLOORS.get(key, 0), 0)
              if i[0] != 'too_long']
    return '' if issues else text


def draft_report(session, keys, context, extras, budgets, log_ngrams,
                 fallback_unit, log_count):
    pending = [s for s in ai.DRAFT_SECTIONS if s[0] in keys]
    drafts, status = {}, {}

    for _attempt in range(2):                       # initial call + one repair call
        if not pending:
            break
        prompt = _report_prompt(pending, context, extras, budgets)
        reply = ai_gateway.chat(prompt, max_tokens=_token_budget([k for k, _t, _i in pending]),
                                temperature=0.7, json_mode=True, system=_JSON_SYSTEM)
        parsed = parse_json_object(reply)
        still = []
        for key, title, instr in pending:
            text = _accept_section(key, title, parsed.get(key), fallback_unit, log_ngrams)
            if text:
                drafts[key], status[key] = text, 'ai'
            else:
                still.append((key, title, instr))
        pending = still
        if not parsed:                              # whole pool failed: don't call twice
            break

    for key, _title, _instr in pending:
        local = local_drafter.draft_local_section(session, key)
        if local:
            drafts[key], status[key] = local, 'local'
        else:
            logger.error('Section %s could not be drafted at all', key)

    note = ai._status_note(status)
    if log_count < 5:
        note = f"Note: only {log_count} log(s) recorded. " + note
    out = dict(drafts)
    out['_ai_note'] = note
    out['_section_status'] = status
    out['_failed_keys'] = [k for k, v in status.items() if v == 'local']
    out['_source_hash'] = ai.source_fingerprint(session)
    return out


# ============================================================
# Presentation
# ============================================================

def _pptx_prompt(sections, context, extras):
    spec = '\n\n'.join(f'KEY "{k}" ({t}):\n{i}' for k, t, i in sections)
    keys = ', '.join(f'"{k}"' for k, _t, _i in sections)
    return (
        "You are preparing content for a PowerPoint presentation summarising a "
        "health-information practicum report for a Ghanaian college (COHY).\n\n"
        f"{extras}\n"
        f"OUTPUT FORMAT: one JSON object with exactly these keys: {keys}. Every value "
        "is a plain string with ONE bullet per line separated by \\n (no bullet "
        "symbols). For the speaker notes key, one slide per line as instructed.\n\n"
        f"SECTION INSTRUCTIONS (authoritative):\n\n{spec}\n\n"
        f"--- STUDENT DATA ---\n{context}\n-------------------\n"
    )


def draft_pptx(session, keys, context, extras, report_content):
    pending = [s for s in ai.PPTX_DRAFT_SECTIONS if s[0] in keys]
    drafts, status = {}, {}

    for _attempt in range(2):
        if not pending:
            break
        reply = ai_gateway.chat(_pptx_prompt(pending, context, extras),
                                max_tokens=min(2000, 350 * len(pending) + 300),
                                temperature=0.6, json_mode=True, system=_JSON_SYSTEM)
        parsed = parse_json_object(reply)
        still = []
        for key, title, instr in pending:
            text = ai._finalize_pptx(key, _as_text(parsed.get(key)))
            if text:
                drafts[key], status[key] = text, 'ai'
            else:
                still.append((key, title, instr))
        pending = still
        if not parsed:
            break

    for key, _t, _i in pending:
        local = local_pptx_drafter.draft_local_pptx_section(session, key, report_content)
        if local:
            drafts[key], status[key] = local, 'local'
        else:
            logger.error('Both AI and local PPTX drafting failed for %s', key)

    failed = [k for k, v in status.items() if v == 'local']
    ai_n, total = sum(1 for v in status.values() if v == 'ai'), len(status)
    if not failed:
        note = f'All {total} slides were drafted by AI.'
    elif ai_n == 0:
        note = (f'All {total} slides were drafted locally because the AI service was '
                f'unavailable. The presentation is complete. Retry later for AI-polished slides.')
    else:
        note = (f'{ai_n} of {total} slides were drafted by AI. {len(failed)} slide(s) '
                f'were drafted locally: {", ".join(failed)}. You can retry just those.')
    out = dict(drafts)
    out['_pptx_ai_note'] = note
    out['_pptx_section_status'] = status
    out['_pptx_failed_keys'] = failed
    out['_source_hash'] = ai.source_fingerprint(session)
    return out
