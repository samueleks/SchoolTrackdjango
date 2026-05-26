# Generated migration to remove Django default tables

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('login', '0024_remove_usuarios_last_login_usuarios_ultimo_acceso'),
    ]

    operations = [
        # Remove django.contrib.admin tables
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS django_admin_log CASCADE;",
            reverse_sql=""
        ),
        # Remove django.contrib.auth tables
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS auth_user_groups CASCADE;",
            reverse_sql=""
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS auth_user_user_permissions CASCADE;",
            reverse_sql=""
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS auth_group_permissions CASCADE;",
            reverse_sql=""
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS auth_permission CASCADE;",
            reverse_sql=""
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS auth_group CASCADE;",
            reverse_sql=""
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS auth_user CASCADE;",
            reverse_sql=""
        ),
        # Remove django.contrib.contenttypes tables
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS django_content_type CASCADE;",
            reverse_sql=""
        ),
    ]
