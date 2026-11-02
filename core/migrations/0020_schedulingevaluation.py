import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_attendance_unverified_default'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SchedulingEvaluation',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name='ID',
                )),
                ('proposed_class', models.CharField(max_length=200)),
                ('scheduled_date', models.DateField()),
                ('start_time', models.TimeField()),
                ('end_time', models.TimeField()),
                ('status', models.CharField(
                    choices=[
                        ('ELIGIBLE', 'Eligible'),
                        ('BLOCKED', 'Blocked'),
                        ('UNVERIFIED', 'Unverified'),
                    ],
                    max_length=12,
                )),
                ('rule_results', models.JSONField(default=list)),
                ('weekly_scheduled_minutes', models.PositiveIntegerField(default=0)),
                ('recurring_timetable_minutes', models.PositiveIntegerField(default=0)),
                ('projected_weekly_minutes', models.PositiveIntegerField(default=0)),
                ('attendance_status', models.CharField(
                    default='UNVERIFIED',
                    max_length=20,
                )),
                ('location_status', models.CharField(
                    default='UNVERIFIED',
                    max_length=20,
                )),
                ('availability_status', models.CharField(
                    default='UNVERIFIED',
                    max_length=20,
                )),
                ('recommendation_score', models.DecimalField(
                    blank=True,
                    decimal_places=2,
                    max_digits=5,
                    null=True,
                )),
                ('recommendation_rank', models.PositiveIntegerField(
                    blank=True,
                    null=True,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('candidate', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='scheduling_evaluations',
                    to='core.teacherprofile',
                )),
                ('evaluated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='scheduling_evaluations',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('programme', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='scheduling_evaluations',
                    to='core.programme',
                )),
                ('proposed_timetable_entry', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='scheduling_evaluations',
                    to='core.timetableentry',
                )),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
            },
        ),
    ]
