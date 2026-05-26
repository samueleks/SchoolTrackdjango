from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('login', '0021_alter_calificacion_tipo_to_unidad'),
    ]

    operations = [
        migrations.AddField(
            model_name='asistencia',
            name='unidad',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RemoveConstraint(
            model_name='asistencia',
            name='uniq_asistencia_por_clase_fecha',
        ),
        migrations.AddConstraint(
            model_name='asistencia',
            constraint=models.UniqueConstraint(fields=('id_inscripcion', 'id_horario', 'fecha_asistencia', 'unidad'), name='uniq_asistencia_por_clase_fecha_unidad'),
        ),
    ]
