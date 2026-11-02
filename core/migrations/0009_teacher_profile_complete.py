from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0008_reset_credentials')]
    operations = [
        migrations.AddField(
            model_name='teacherprofile',
            name='profile_complete',
            field=models.BooleanField(default=False),
        ),
    ]
