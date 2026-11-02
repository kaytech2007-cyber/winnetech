import re

from django.db import migrations, models
from django.utils import timezone


PROGRAMME_CODE_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')
STAFF_ID_PATTERN = re.compile(
    r'^WTI-HOD-([A-Za-z0-9_-]+)/([0-9]{4})/([0-9]+)$'
)


def populate_hod_staff_ids(apps, schema_editor):
    HODProfile = apps.get_model('core', 'HODProfile')
    database = schema_editor.connection.alias
    counters = {}

    assigned_programmes = {}
    for hod in HODProfile.objects.using(database).filter(
        programme__isnull=False,
    ).select_related('programme').order_by('programme__code', 'programme__pk'):
        programme = hod.programme
        assigned_programmes[programme.pk] = (programme.name, programme.code)

    for programme_name, programme_code in assigned_programmes.values():
        if not PROGRAMME_CODE_PATTERN.fullmatch(programme_code):
            raise RuntimeError(
                'Cannot backfill HOD Staff IDs: programme '
                f'"{programme_name}" has invalid code "{programme_code}". '
                'Programme codes must match [A-Za-z0-9_-]+. '
                'Correct the programme code through an approved data change '
                'before applying this migration.'
            )

    existing_hods = HODProfile.objects.using(database).filter(
        staff_id__isnull=False,
        programme__isnull=False,
    ).select_related('programme').order_by('pk')
    for hod in existing_hods:
        match = STAFF_ID_PATTERN.fullmatch(hod.staff_id)
        if match:
            key = (match.group(1), int(match.group(2)))
            counters[key] = max(counters.get(key, 0), int(match.group(3)))

    hods = HODProfile.objects.using(database).filter(
        staff_id__isnull=True,
        programme__isnull=False,
    ).select_related('programme').order_by(
        'programme__code', 'created_at', 'pk'
    )
    for hod in hods:
        created_at = hod.created_at
        year = timezone.localtime(created_at).year if timezone.is_aware(created_at) else created_at.year
        key = (hod.programme.code, year)
        counters[key] = counters.get(key, 0) + 1
        hod.staff_id = (
            f'WTI-HOD-{hod.programme.code}/{year}/{counters[key]:02d}'
        )
        hod.save(update_fields=['staff_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_programme_hodprofile_facilitatorprofile_internships'),
    ]

    operations = [
        migrations.AddField(
            model_name='hodprofile',
            name='staff_id',
            field=models.CharField(blank=True, max_length=100, null=True, unique=True),
        ),
        migrations.RunPython(
            populate_hod_staff_ids,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
