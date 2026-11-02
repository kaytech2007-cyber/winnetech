from django.db import migrations, models
from django.db.models import Count, Q
from django.db.models.functions import Lower


CONSTRAINT_NAME = 'unique_auth_user_email_case_insensitive'


def add_email_uniqueness_constraint(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    database = schema_editor.connection.alias
    duplicates = (
        User.objects.using(database)
        .exclude(email='')
        .annotate(normalized_email=Lower('email'))
        .values('normalized_email')
        .annotate(account_count=Count('pk'))
        .filter(account_count__gt=1)
    )
    if duplicates.exists():
        raise RuntimeError(
            'Cannot enforce case-insensitive email uniqueness: duplicate '
            'non-empty account emails must be reviewed and resolved first.'
        )

    schema_editor.add_constraint(
        User,
        models.UniqueConstraint(
            Lower('email'),
            condition=~Q(email=''),
            name=CONSTRAINT_NAME,
        ),
    )


def remove_email_uniqueness_constraint(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    schema_editor.remove_constraint(
        User,
        models.UniqueConstraint(
            Lower('email'),
            condition=~Q(email=''),
            name=CONSTRAINT_NAME,
        ),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_alter_teacherprofile_staff_id_staffidregistry_and_more'),
    ]

    operations = [
        migrations.RunPython(
            add_email_uniqueness_constraint,
            remove_email_uniqueness_constraint,
        ),
    ]
