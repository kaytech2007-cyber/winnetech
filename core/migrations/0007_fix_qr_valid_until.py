from django.db import migrations
from datetime import date


def fix_qr_valid_until(apps, schema_editor):
    QRCode = apps.get_model('core', 'QRCode')
    for qr in QRCode.objects.filter(valid_until__isnull=True):
        vf = qr.valid_from or date.today()
        try:
            qr.valid_until = vf.replace(year=vf.year + 5)
        except ValueError:
            qr.valid_until = vf.replace(year=vf.year + 5, day=28)
        qr.save()


class Migration(migrations.Migration):
    dependencies = [('core', '0006_fix_admin_credentials')]
    operations = [migrations.RunPython(fix_qr_valid_until, migrations.RunPython.noop)]
