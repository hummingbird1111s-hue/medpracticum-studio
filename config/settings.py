"""
Django settings for config project (MedPracticum Studio).
"""

import logging
import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()  # Must run before DATABASES is defined

# ===== Render free-tier memory guards =====
# These MUST be in os.environ before matplotlib or numpy is first imported.
# Without them, numpy spawns cpu_count() BLAS threads and matplotlib's
# default backend loads GUI libs — together adding ~180 MB of RSS that
# pushes the 512 MB free-tier worker into SIGKILL during chart rendering.
os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')

BASE_DIR = Path(__file__).resolve().parent.parent


# ===== API keys (Gemini + Groq + OpenRouter) =====
def _load_key_from_env_or_file(env_name):
    """Shared loader for keys from env vars or the .env file."""
    key = os.environ.get(env_name, '').strip()
    if key:
        return key
    env_file = BASE_DIR / '.env'
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line and line.split('=', 1)[0].strip() == env_name:
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return ''


GEMINI_API_KEY = _load_key_from_env_or_file('GEMINI_API_KEY')
GROQ_API_KEY = _load_key_from_env_or_file('GROQ_API_KEY')
OPENROUTER_API_KEY = _load_key_from_env_or_file('OPENROUTER_API_KEY')

if not GEMINI_API_KEY:
    logging.getLogger(__name__).warning(
        'GEMINI_API_KEY is not set. Gemini AI features will be disabled. '
        'Set the environment variable or create a .env file at %s',
        BASE_DIR / '.env',
    )

if not GROQ_API_KEY:
    logging.getLogger(__name__).info(
        'GROQ_API_KEY is not set. Groq fallback will be disabled. '
        'Add it to %s to enable the fallback provider.',
        BASE_DIR / '.env',
    )

if not OPENROUTER_API_KEY:
    logging.getLogger(__name__).info(
        'OPENROUTER_API_KEY is not set. OpenRouter final fallback will be disabled. '
        'Add it to %s to enable the ultimate fallback provider.',
        BASE_DIR / '.env',
    )

if GROQ_API_KEY and not os.environ.get('GROQ_API_KEY'):
    os.environ['GROQ_API_KEY'] = GROQ_API_KEY

if OPENROUTER_API_KEY and not os.environ.get('OPENROUTER_API_KEY'):
    os.environ['OPENROUTER_API_KEY'] = OPENROUTER_API_KEY


# Lets WeasyPrint find the Pango/GTK libraries installed with MSYS2 on Windows
_MSYS_BIN = r'C:\msys64\mingw64\bin'
if os.name == 'nt' and os.path.isdir(_MSYS_BIN):
    os.environ.setdefault('WEASYPRINT_DLL_DIRECTORIES', _MSYS_BIN)


# ═══════════════════════════════════════════════════════════════════════
# DEBUG — determined first so SECRET_KEY / security decisions can use it
# ═══════════════════════════════════════════════════════════════════════
# On Render → DEBUG=False. Locally (no RENDER env var) → DEBUG=True.
DEBUG = 'RENDER' not in os.environ

# One-shot override: set DJANGO_DEBUG=1 on Render to force DEBUG=True
# temporarily (for viewing yellow error page in the browser).
# ⚠️  Remove this env var immediately after debugging — DEBUG=True in prod
#     exposes source code + settings to anyone who can trigger an error.
if os.environ.get('DJANGO_DEBUG', '').lower() in ('1', 'true', 'yes'):
    DEBUG = True


# ═══════════════════════════════════════════════════════════════════════
# SECRET_KEY — required in production, safe fallback for local dev only
# ═══════════════════════════════════════════════════════════════════════
_INSECURE_LOCAL_FALLBACK = (
    'django-insecure-e+%5l=_a=tv=bq$6qjwp=vf@!7g8h$9f)4n6j^lw3obzs8vy_f'
)

SECRET_KEY = os.environ.get('SECRET_KEY', '').strip()
if not SECRET_KEY:
    if DEBUG:
        # Local dev only — triggers W009 warning in `check --deploy`,
        # which is correct and expected. Set SECRET_KEY in .env to silence.
        SECRET_KEY = _INSECURE_LOCAL_FALLBACK
    else:
        raise ImproperlyConfigured(
            '\n'
            '═══════════════════════════════════════════════════════════════════════════\n'
            '  SECRET_KEY is required in production (DEBUG=False).\n'
            '═══════════════════════════════════════════════════════════════════════════\n'
            '\n'
            '  Generate one locally:\n'
            '      python -c "import secrets; print(secrets.token_urlsafe(50))"\n'
            '\n'
            '  Then set it as an environment variable on your host:\n'
            '      Render → your service → Environment → Add Environment Variable\n'
            '          Key:   SECRET_KEY\n'
            '          Value: <paste the generated value>\n'
            '\n'
            '  Render will auto-redeploy once the variable is saved.\n'
            '═══════════════════════════════════════════════════════════════════════════\n'
        )


# ═══════════════════════════════════════════════════════════════════════
# ALLOWED_HOSTS + CSRF trusted origins
# ═══════════════════════════════════════════════════════════════════════
RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')

ALLOWED_HOSTS = ['localhost', '127.0.0.1']
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# Allow extra hosts via env var (comma-separated). Useful later for custom
# domains, e.g. ALLOWED_HOSTS_EXTRA=medpracticum.com,www.medpracticum.com
_extra_hosts = os.environ.get('ALLOWED_HOSTS_EXTRA', '').strip()
if _extra_hosts:
    ALLOWED_HOSTS.extend(h.strip() for h in _extra_hosts.split(',') if h.strip())

# Trusted origins for CSRF POSTs.
# Include the canonical Render URL explicitly, then add the hostname
# supplied by Render and any additional domains configured by the owner.
CSRF_TRUSTED_ORIGINS = [
    'https://medpracticum-studio.onrender.com',
]

if RENDER_EXTERNAL_HOSTNAME:
    _render_origin = f'https://{RENDER_EXTERNAL_HOSTNAME}'
    if _render_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_render_origin)

# Add any explicitly configured HTTPS domains.
for _host in _extra_hosts.split(','):
    _host = _host.strip()
    if _host:
        _origin = _host if _host.startswith('https://') else f'https://{_host}'
        if _origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(_origin)


# ═══════════════════════════════════════════════════════════════════════
# Production security headers
# ═══════════════════════════════════════════════════════════════════════
# These are safe to enable globally — they only affect HTTPS responses,
# so they're no-ops on local HTTP. Setting them globally (instead of only
# inside `if not DEBUG`) lets `manage.py check --deploy` see them and
# clears W004 (HSTS).

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'      # default in Django 5; explicit is clearer
X_FRAME_OPTIONS = 'DENY'

# HSTS: tell browsers to always use HTTPS for this domain.
# ONLY applies over HTTPS responses — HTTP localhost is unaffected.
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30     # 30 days; raise to 1 year once stable
SECURE_HSTS_INCLUDE_SUBDOMAINS = False      # keep off unless you own all subdomains
SECURE_HSTS_PRELOAD = False                 # keep off unless you've registered at hstspreload.org


# ═══════════════════════════════════════════════════════════════════════
# Production-only hardening (would break HTTP local dev — hence conditional)
# ═══════════════════════════════════════════════════════════════════════
if not DEBUG:
    # Force all HTTP → HTTPS
    SECURE_SSL_REDIRECT = True

    # Cookies only over HTTPS in production.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# ===== CSRF and session cookie settings =====
# Lax allows normal same-site navigation while protecting against many
# cross-site request scenarios. Do not set a CSRF_COOKIE_DOMAIN unless
# you intentionally serve the app across multiple subdomains.
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SAMESITE = 'Lax'

# Keep the CSRF token in the normal CSRF cookie. Django's csrf_token
# template tag (and ensure_csrf_cookie on form views) can then set it.
CSRF_USE_SESSIONS = False

# The Render proxy terminates HTTPS before forwarding requests to Django.
# This header lets Django correctly recognize the original HTTPS request.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# ===== Application definition =====
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Local apps
    'apps.accounts',
    'apps.facilities',
    'apps.logs',
    'apps.reports',
    'apps.payments',
    'apps.notifications',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.payments.context_processors.subscription_status',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# ===== Database =====
DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}
if DATABASES['default']['ENGINE'].endswith('postgresql'):
    DATABASES['default'].setdefault('OPTIONS', {})['sslmode'] = 'require'


AUTH_USER_MODEL = 'accounts.User'


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Accra'
USE_I18N = True
USE_TZ = True


# ===== Static and Media files =====
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [BASE_DIR / 'static']

if not DEBUG:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
else:
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# ===== Login redirects =====
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


SITE_URL = os.environ.get('SITE_URL', 'http://127.0.0.1:8000')


REPORT_ALLOW_CUSTOM_FORMAT = True


# ===== School identity (used in AI + local drafting) =====
# Used by apps.reports.ai_drafting and apps.reports.local_drafter when the
# generated prose refers to the student's institution. Override here if the
# college changes; both drafters pick it up automatically.
SCHOOL_NAME = 'the College of Health, Yamfo'


# ═══════════════════════════════════════════════════════════════════════
# AI DRAFTING — Phase A (serial + local fallback)
# ═══════════════════════════════════════════════════════════════════════
# Reports are drafted SERIAL (one AI call at a time) to stay under Groq's
# 8 000 TPM limit. Sections that fail to draft with AI are filled by the
# local template drafter — never by raw log copy.
#
# REPORT_AI_DRAFTING_ENABLED
#     Master switch. When False, ALL sections are drafted locally.
#
# REPORT_AI_CALLABLE
#     Dotted path to the shared AI client. This is the same client used by
#     the AI assistant. It already has its own provider chain
#     (Gemini → Groq → OpenRouter) and rate-limit handling.
#
# REPORT_AI_DRAFTING_MAX_SECONDS
#     Wall-clock budget for the whole report. When exceeded, remaining
#     sections are drafted locally so the user always gets a complete report.
#
# REPORT_AI_DRAFTING_INTER_CALL_DELAY
#     Seconds to sleep between serial AI calls. 0.8s keeps us at ~40 TPM,
#     well under Groq's 8 000 TPM.
#
# REPORT_AI_CALL_TIMEOUT
#     Per-request HTTP timeout (seconds). Applies to the underlying HTTP call
#     inside the AI client.
#
# REPORT_AI_REFINE_MAX_SECONDS
#     Wall-clock budget for "Edit Report Manually → Polish with AI". Uses a
#     parallel runner (unchanged); independent of the drafting budget.
#
# REPORT_AI_DRAFTING_WORKERS
#     Only used by the PPTX drafting path (still parallel until Phase B).
#     Reduced to 4 as an interim safety margin against rate limits.
REPORT_AI_DRAFTING_ENABLED = True
REPORT_AI_CALLABLE = 'apps.logs.ai_client.get_ai_reply'
REPORT_AI_DRAFTING_MAX_SECONDS = 120
REPORT_AI_DRAFTING_INTER_CALL_DELAY = 0.8
REPORT_AI_CALL_TIMEOUT = 60
REPORT_AI_REFINE_MAX_SECONDS = 80
REPORT_AI_DRAFTING_WORKERS = 4


# ===== Email (password reset) =====
EMAIL_HOST_USER = _load_key_from_env_or_file('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = _load_key_from_env_or_file('EMAIL_HOST_PASSWORD')

EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 465
EMAIL_USE_TLS = False
EMAIL_USE_SSL = True

if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = 'apps.accounts.email_backend.CustomEmailBackend'
    DEFAULT_FROM_EMAIL = f'MedPracticum Studio <{EMAIL_HOST_USER}>'
    logging.getLogger(__name__).info('Email: using Gmail SMTP as %s', EMAIL_HOST_USER)
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
    DEFAULT_FROM_EMAIL = 'MedPracticum Studio <noreply@medpracticum.local>'
    logging.getLogger(__name__).warning(
        'Email: EMAIL_HOST_USER / EMAIL_HOST_PASSWORD not set. '
        'Password-reset emails will print to the console, not be sent. '
        'Add them to .env when you\u2019re ready for real delivery.'
    )

PASSWORD_RESET_TIMEOUT = 60 * 60


# ═══════════════════════════════════════════════════════════════════════
# PAYSTACK
# ═══════════════════════════════════════════════════════════════════════
PAYSTACK_PUBLIC_KEY = os.environ.get('PAYSTACK_PUBLIC_KEY', '')
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '')
PAYSTACK_CURRENCY   = 'GHS'

SUBSCRIPTION_PRICE  = 50
SUBSCRIPTION_MONTHS = 6


# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {name} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'apps': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ═══════════════════════════════════════════════════════════════════════
# AI PROVIDER POOL (apps/reports/ai_gateway.py + ai_batch.py)
# ═══════════════════════════════════════════════════════════════════════
# One JSON request per report, spread over these endpoints. Lower 'priority'
# is tried first; equal priorities are shuffled. Rows with an empty api_key
# or model are ignored. If every endpoint fails, the local drafter fills in.
REPORT_AI_ENDPOINTS = [
    {'name': 'gemini-3.8-flash', 'priority': 0,
     'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai',
     'api_key': GEMINI_API_KEY,
     'model': 'gemini-3.8-flash'},
    {'name': 'gemini-3.6-flash', 'priority': 1,
     'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai',
     'api_key': GEMINI_API_KEY,
     'model': 'gemini-3.6-flash'},
    {'name': 'groq-120b', 'priority': 1,
     'base_url': 'https://api.groq.com/openai/v1',
     'api_key': GROQ_API_KEY,
     'model': 'openai/gpt-oss-120b',
     'extra': {'reasoning_effort': 'low'}},
    {'name': 'groq-20b', 'priority': 2,
     'base_url': 'https://api.groq.com/openai/v1',
     'api_key': GROQ_API_KEY,
     'model': 'openai/gpt-oss-20b',
     'extra': {'reasoning_effort': 'low'}},
    {'name': 'openrouter', 'priority': 3,
     'base_url': 'https://openrouter.ai/api/v1',
     'api_key': OPENROUTER_API_KEY,
     'model': 'openrouter/free',
     'json_mode': False},
]
REPORT_AI_BATCH_MAX_TOKENS = 4000    # keeps one request inside Groq's 8k TPM
REPORT_AI_GATEWAY_MAX_WAIT = 150
