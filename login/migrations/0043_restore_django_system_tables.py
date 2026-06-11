# Restaura tablas de Django eliminadas en 0025 para que funcionen migrate/test.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('login', '0042_ciclo_periodo_coherente_mes'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE TABLE IF NOT EXISTS django_content_type (
                id SERIAL PRIMARY KEY,
                app_label VARCHAR(100) NOT NULL,
                model VARCHAR(100) NOT NULL,
                CONSTRAINT django_content_type_app_label_model_uniq
                    UNIQUE (app_label, model)
            );
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="""
            CREATE TABLE IF NOT EXISTS auth_permission (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                content_type_id INTEGER NOT NULL
                    REFERENCES django_content_type(id)
                    DEFERRABLE INITIALLY DEFERRED,
                codename VARCHAR(100) NOT NULL,
                CONSTRAINT auth_permission_content_type_id_codename_uniq
                    UNIQUE (content_type_id, codename)
            );
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
