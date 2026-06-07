from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('login', '0032_revert_grupo_clave_global'),
    ]

    operations = [
        migrations.AlterField(
            model_name='grupo',
            name='clave',
            field=models.CharField(max_length=20),
        ),
        migrations.AddConstraint(
            model_name='grupo',
            constraint=models.UniqueConstraint(
                fields=('clave', 'id_ciclo_escolar'),
                name='uniq_grupo_clave_ciclo',
            ),
        ),
    ]
