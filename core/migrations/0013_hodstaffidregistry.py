import re

from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


STAFF_ID_PATTERN = re.compile(
    r'^WTI-HOD-([A-Za-z0-9_-]+)/([0-9]{4})/([0-9]+)$'
)


def register_existing_hod_staff_ids(apps, schema_editor):
    HODProfile = apps.get_model('core', 'HODProfile')
    HODStaffIDRegistry = apps.get_model('core', 'HODStaffIDRegistry')
    database = schema_editor.connection.alias
    seen_sequences = set()

    hods = HODProfile.objects.using(database).exclude(
        staff_id__isnull=True,
    ).exclude(
        staff_id='',
    ).order_by('pk')

    for hod in hods:
        match = STAFF_ID_PATTERN.fullmatch(hod.staff_id)
        programme_code = match.group(1) if match else None
        year = int(match.group(2)) if match else None
        sequence = int(match.group(3)) if match else None
        sequence_key = (programme_code, year, sequence)
        if not match or sequence_key in seen_sequences:
            programme_code = year = sequence = None
        else:
            seen_sequences.add(sequence_key)

        HODStaffIDRegistry.objects.using(database).create(
            staff_id=hod.staff_id,
            programme_code=programme_code,
            year=year,
            sequence=sequence,
            hod_profile_id=hod.pk,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_hodprofile_staff_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='HODStaffIDRegistry',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('staff_id', models.CharField(max_length=100, unique=True)),
                (
                    'programme_code',
                    models.CharField(blank=True, max_length=50, null=True),
                ),
                ('year', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('sequence', models.PositiveIntegerField(blank=True, null=True)),
                ('issued_at', models.DateTimeField(auto_now_add=True)),
                (
                    'hod_profile',
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='staff_id_registry',
                        to='core.hodprofile',
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name='hodstaffidregistry',
            constraint=models.UniqueConstraint(
                condition=(
                    Q(programme_code__isnull=False)
                    & Q(year__isnull=False)
                    & Q(sequence__isnull=False)
                ),
                fields=('programme_code', 'year', 'sequence'),
                name='unique_hod_staff_sequence',
            ),
        ),
        migrations.RunPython(
            register_existing_hod_staff_ids,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
