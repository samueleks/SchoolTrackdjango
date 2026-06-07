from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('login', '0031_grupo_clave_por_ciclo'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='grupo',
            name='uniq_grupo_clave_ciclo',
        ),
        migrations.AlterField(
            model_name='grupo',
            name='clave',
            field=models.CharField(max_length=20, unique=True),
        ),
    ]
