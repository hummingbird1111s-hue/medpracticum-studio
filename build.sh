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

# 6. Auto-create admin accounts (unique index_number avoids the unique-constraint clash)
python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin', 'admin@medpracticum.com', 'AdminPass123!', index_number='ADMIN-001')"

python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(username='oxbee').exists() or User.objects.create_superuser('oxbee', 'oxbee@medpracticum.com', 'OxbeePass123!', index_number='ADMIN-002')"