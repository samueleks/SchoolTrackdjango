from django.db import migrations, models
import django.db.models.deletion


def asignar_carrera_a_materias(apps, schema_editor):
    Materia = apps.get_model('login', 'Materia')
    Carrera = apps.get_model('login', 'Carrera')
    AsignacionMateria = apps.get_model('login', 'AsignacionMateria')
    Grupo = apps.get_model('login', 'Grupo')

    primera_carrera = Carrera.objects.order_by('id').first()
    if not primera_carrera:
        return

    for materia in Materia.objects.filter(id_carrera__isnull=True):
        carrera_id = None
        asignacion = (
            AsignacionMateria.objects.filter(id_materia_id=materia.id_materia)
            .order_by('id_asignacion_materia')
            .first()
        )
        if asignacion:
            grupo = Grupo.objects.filter(pk=asignacion.id_grupo_id).first()
            if grupo and grupo.id_carrera_id:
                carrera_id = grupo.id_carrera_id
        if not carrera_id:
            carrera_id = primera_carrera.id
        materia.id_carrera_id = carrera_id
        materia.save(update_fields=['id_carrera_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('login', '0033_grupo_clave_por_ciclo'),
    ]

    operations = [
        migrations.AddField(
            model_name='materia',
            name='id_carrera',
            field=models.ForeignKey(
                blank=True,
                db_column='id_carrera',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='materias',
                to='login.carrera',
            ),
        ),
        migrations.RunPython(asignar_carrera_a_materias, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='materia',
            name='id_carrera',
            field=models.ForeignKey(
                db_column='id_carrera',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='materias',
                to='login.carrera',
            ),
        ),
    ]
