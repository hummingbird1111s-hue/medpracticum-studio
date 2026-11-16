#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

# Pre-build Matplotlib font cache so it doesn't block server startup
python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot" || true

python manage.py collectstatic --no-input
python manage.py migrate