#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py render_bootstrap
python manage.py load_gmail_token
python manage.py create_superadmin