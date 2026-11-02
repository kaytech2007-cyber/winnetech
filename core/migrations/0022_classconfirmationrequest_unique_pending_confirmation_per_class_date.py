from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_classconfirmationrequest_classreplacementrequest'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='classconfirmationrequest',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status', 'PENDING')),
                fields=('timetable_entry', 'scheduled_date'),
                name='unique_pending_confirmation_per_class_date',
            ),
        ),
    ]
