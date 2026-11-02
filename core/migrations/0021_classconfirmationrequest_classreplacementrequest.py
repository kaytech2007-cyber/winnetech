import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_schedulingevaluation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ClassConfirmationRequest',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name='ID',
                )),
                ('scheduled_date', models.DateField()),
                ('start_time', models.TimeField()),
                ('end_time', models.TimeField()),
                ('status', models.CharField(
                    choices=[
                        ('PENDING', 'Pending Facilitator response'),
                        ('ACCEPTED', 'Accepted'),
                        ('DECLINED', 'Declined'),
                        ('WITHHELD', 'Withheld after re-evaluation'),
                        ('CANCELLED', 'Cancelled'),
                    ],
                    default='PENDING',
                    max_length=12,
                )),
                ('response_note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('candidate', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='class_confirmation_requests',
                    to='core.teacherprofile',
                )),
                ('confirmed_schedule', models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='confirmation_request',
                    to='core.classschedule',
                )),
                ('evaluation', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='confirmation_requests',
                    to='core.schedulingevaluation',
                )),
                ('programme', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='class_confirmation_requests',
                    to='core.programme',
                )),
                ('replaces', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='replacement_confirmations',
                    to='core.classconfirmationrequest',
                )),
                ('requested_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='requested_class_confirmations',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('responded_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='responded_class_confirmations',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('timetable_entry', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='class_confirmation_requests',
                    to='core.timetableentry',
                )),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='ClassReplacementRequest',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name='ID',
                )),
                ('status', models.CharField(
                    choices=[
                        ('PENDING', 'Pending HOD approval'),
                        ('APPROVED', 'Approved'),
                        ('REJECTED', 'Rejected'),
                    ],
                    default='PENDING',
                    max_length=10,
                )),
                ('reason', models.TextField()),
                ('decision_note', models.TextField(blank=True)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decided_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='decided_class_replacements',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('original_request', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='replacement_request',
                    to='core.classconfirmationrequest',
                )),
                ('programme', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='class_replacement_requests',
                    to='core.programme',
                )),
                ('recommendations', models.ManyToManyField(
                    blank=True,
                    related_name='replacement_reviews',
                    to='core.schedulingevaluation',
                )),
                ('replacement_confirmation', models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='replacement_approval',
                    to='core.classconfirmationrequest',
                )),
                ('selected_candidate', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='replacement_selections',
                    to='core.teacherprofile',
                )),
            ],
            options={
                'ordering': ['status', '-requested_at', '-pk'],
            },
        ),
    ]
