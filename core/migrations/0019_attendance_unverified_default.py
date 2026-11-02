from django.db import migrations, models


def mark_unsubstantiated_absences_unverified(apps, schema_editor):
    Attendance = apps.get_model('core', 'Attendance')
    database = schema_editor.connection.alias
    Attendance.objects.using(database).filter(
        school_status='absent',
        school_qr__isnull=True,
        check_in_time__isnull=True,
    ).update(school_status='unverified')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_auth_user_email_case_insensitive_unique'),
    ]

    operations = [
        migrations.RunPython(
            mark_unsubstantiated_absences_unverified,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='attendance',
            name='school_status',
            field=models.CharField(
                choices=[
                    ('present', 'Present'),
                    ('absent', 'Absent'),
                    ('late', 'Late'),
                    ('unverified', 'Unverified'),
                ],
                default='unverified',
                max_length=10,
            ),
        ),
    ]
