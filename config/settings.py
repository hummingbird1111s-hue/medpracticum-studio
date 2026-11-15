"""
Django settings for config project (MedPracticum Studio).
"""

import logging
import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
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

# Push keys into os.environ so apps/logs/ai_client.py can see them seamlessly.
if GROQ_API_KEY and not os.environ.get('GROQ_API_KEY'):
    os.environ['GROQ_API_KEY'] = GROQ_API_KEY

if OPENROUTER_API_KEY and not os.environ.get('OPENROUTER_API_KEY'):
    os.environ['OPENROUTER_API_KEY'] = OPENROUTER_API_KEY


# Lets WeasyPrint find the Pango/GTK libraries installed with MSYS2 on Windows
_MSYS_BIN = r'C:\msys64\mingw64\bin'
if os.name == 'nt' and os.path.isdir(_MSYS_BIN):
    os.environ.setdefault('WEASYPRINT_DLL_DIRECTORIES', _MSYS_BIN)


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-e+%5l=_a=tv=bq$6qjwp=vf@!7g8h$9f)4n6j^lw3obzs8vy_f'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []


# Application definition
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
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Custom user model
AUTH_USER_MODEL = 'accounts.User'


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Africa/Accra'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

# Media files (uploaded and generated reports)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Login redirects
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'


# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Used inside the QR code so supervisors can open the verification page
SITE_URL = 'http://127.0.0.1:8000'


# Set to False to force every report into the official COHY format
REPORT_ALLOW_CUSTOM_FORMAT = True


# ===== AI DRAFTING =====
REPORT_AI_DRAFTING_ENABLED = True
REPORT_AI_CALLABLE = 'apps.logs.ai_client.get_ai_reply'


# ===== Email (password reset) =====
EMAIL_HOST_USER = _load_key_from_env_or_file('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = _load_key_from_env_or_file('EMAIL_HOST_PASSWORD')

EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 465                # Changed to 465 (SSL) to bypass firewall blocks
EMAIL_USE_TLS = False           # Turned off TLS
EMAIL_USE_SSL = True            # Turned on SSL

if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
    # Use our custom backend to fix Gmail's local_hostname rejection
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

# How long a password reset link stays valid (in seconds).
PASSWORD_RESET_TIMEOUT = 60 * 60  # 1 hour