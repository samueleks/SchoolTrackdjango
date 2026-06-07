from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('login', '0034_materia_carrera'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE TABLE IF NOT EXISTS django_cache_table (
                    cache_key varchar(255) NOT NULL PRIMARY KEY,
                    value text NOT NULL,
                    expires timestamp with time zone NOT NULL
                );
                CREATE INDEX IF NOT EXISTS django_cache_table_expires
                    ON django_cache_table (expires);
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
