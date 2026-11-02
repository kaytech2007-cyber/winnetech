from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_superadmin_profile(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    AdminProfile = apps.get_model('core', 'AdminProfile')
    for u in User.objects.filter(is_staff=True):
        if not AdminProfile.objects.filter(user=u).exists():
            role = 'superadmin' if u.is_superuser else 'admin'
            AdminProfile.objects.create(user=u, role=role)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0003_create_admin'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('superadmin', 'Super Admin'), ('admin', 'Admin')], default='admin', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_admins', to=settings.AUTH_USER_MODEL)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='admin_profile', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RunPython(create_superadmin_profile, migrations.RunPython.noop),
    ]
