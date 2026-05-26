from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('login', '0020_remove_grupo_aula'),
    ]

    operations = [
        migrations.RenameField(
            model_name='calificacion',
            old_name='tipo',
            new_name='unidad',
        ),
        migrations.AlterField(
            model_name='calificacion',
            name='unidad',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RemoveConstraint(
            model_name='calificacion',
            name='uniq_calificacion_por_tipo',
        ),
        migrations.AddConstraint(
            model_name='calificacion',
            constraint=models.UniqueConstraint(fields=('id_inscripcion', 'id_asignacion_materia', 'unidad'), name='uniq_calificacion_por_unidad'),
        ),
    ]
