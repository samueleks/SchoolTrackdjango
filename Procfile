web: python manage.py collectstatic --noinput && python manage.py migrate --noinput && gunicorn SchoolTrackdjango.wsgi:application --bind 0.0.0.0:$PORT --timeout 60
