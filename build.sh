#!/usr/bin/env bash
set -o errexit

# 1. Remove any legacy or conflicting Google packages that cause memory bloat
pip uninstall -y google google-generativeai google-ai-generativelanguage || true

# 2. Install the exact dependencies from requirements.txt
pip install -r requirements.txt

# 3. Pre-build Matplotlib font cache so it doesn't block server startup
python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot" || true

# 4. Collect static files (CSS, JS, images)
python manage.py collectstatic --no-input

# 5. Run database migrations
python manage.py migrate