from types import SimpleNamespace
from datetime import date, time, timedelta
from decimal import Decimal
from importlib import import_module
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models.query import QuerySet
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import views
from .forms import TimetableEntryForm
from .models import (
    AdminProfile, Attendance, ClassConfirmationRequest, ClassReplacementRequest,
    FacilitatorProfile, HODProfile, HODStaffIDRegistry,
    InternshipExtension, InternshipSupervision, Programme, QRCode,
    StaffIDRegistry, StaffOnboardingToken,
    ClassSchedule, Notification, SchoolLocation, TeacherProfile, TimetableEntry,
    SchedulingEvaluation, expire_internships,
    add_years, generate_hod_staff_id, generate_registered_staff_id,
    generate_unique_staff_id,
)
from .services.scheduling import (
    BLOCKED,
    ELIGIBLE,
    UNVERIFIED,
    SchedulingVerification,
    evaluate_class_assignment,
    rank_class_candidates,
)


class FacilitatorCompatibilityTests(SimpleTestCase):
    def test_legacy_teacher_profile_can_use_facilitator_portal(self):
        user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            is_staff=False,
            teacher_profile=SimpleNamespace(is_approved=True),
        )

        self.assertTrue(views.is_facilitator(user))

    def test_inactive_facilitator_profile_cannot_use_facilitator_portal(self):
        user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            is_staff=False,
            teacher_profile=SimpleNamespace(
                is_approved=True,
                facilitator_profile=SimpleNamespace(is_active=False),
            ),
        )

        self.assertFalse(views.is_facilitator(user))


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class LegacyFacilitatorDashboardAccessTests(TestCase):
    def setUp(self):
        self.programme = Programme.objects.create(
            name='Legacy Dashboard Programme',
            code='LEGACY',
        )
        self.url = reverse('teacher_dashboard')

    def make_teacher(self, username, *, approved=True):
        user = User.objects.create_user(
            username=username,
            password='test-password',
        )
        teacher = TeacherProfile.objects.create(
            user=user,
            staff_id=f'WTI-{username}',
            phone='0200000000',
            gender='male',
            address='',
            qualification='degree',
            specialization='',
            course_type='core',
            preferred_subject='ict',
            programme=self.programme,
            is_approved=approved,
            profile_complete=True,
        )
        return user, teacher

    def make_facilitator(self, username, employment_type='PERMANENT'):
        user, teacher = self.make_teacher(username)
        facilitator_fields = {
            'teacher': teacher,
            'programme': self.programme,
            'employment_type': employment_type,
        }
        if employment_type == 'INTERNSHIP':
            _, supervisor_teacher = self.make_teacher(f'{username}-supervisor')
            supervisor = FacilitatorProfile.objects.create(
                teacher=supervisor_teacher,
                programme=self.programme,
                employment_type='PERMANENT',
            )
            facilitator_fields.update({
                'internship_start_date': timezone.localdate(),
                'supervisor': supervisor,
            })
        facilitator = FacilitatorProfile.objects.create(**facilitator_fields)
        return user, teacher, facilitator

    def assert_dashboard_denies_access(self, user):
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, self.url)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/login/'))

    def test_legacy_dashboard_url_is_unchanged(self):
        self.assertEqual(self.url, '/teacher/dashboard/')

    def test_active_permanent_facilitator_keeps_dashboard_access(self):
        user, teacher, _ = self.make_facilitator('permanent-dashboard')
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'teacher/dashboard.html')
        self.assertEqual(response.context['profile'], teacher)

    def test_approved_legacy_teacher_profile_keeps_compatibility_access(self):
        user, teacher = self.make_teacher('legacy-teacher-dashboard')
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'teacher/dashboard.html')
        self.assertEqual(response.context['profile'], teacher)

    def test_active_internship_facilitator_keeps_dashboard_access(self):
        user, teacher, facilitator = self.make_facilitator(
            'intern-dashboard',
            employment_type='INTERNSHIP',
        )
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'teacher/dashboard.html')
        self.assertEqual(response.context['profile'], teacher)
        self.assertFalse(facilitator.is_expired)

    def test_expired_internship_facilitator_is_denied(self):
        user, _, facilitator = self.make_facilitator(
            'expired-dashboard',
            employment_type='INTERNSHIP',
        )
        end_date = timezone.localdate() - timedelta(days=1)
        facilitator.internship_start_date = end_date - timedelta(days=365)
        facilitator.internship_end_date = end_date
        facilitator.save(update_fields=[
            'internship_start_date',
            'internship_end_date',
            'is_active',
            'updated_at',
        ])

        self.assert_dashboard_denies_access(user)

    def test_inactive_facilitator_is_denied(self):
        user, _, facilitator = self.make_facilitator('inactive-dashboard')
        facilitator.is_active = False
        facilitator.save(update_fields=['is_active'])

        self.assert_dashboard_denies_access(user)

    def test_unapproved_facilitator_is_denied(self):
        user, teacher = self.make_teacher(
            'unapproved-dashboard',
            approved=False,
        )
        FacilitatorProfile.objects.create(
            teacher=teacher,
            programme=self.programme,
            employment_type='PERMANENT',
        )

        self.assert_dashboard_denies_access(user)

    def test_inactive_user_is_denied(self):
        user, _, _ = self.make_facilitator('inactive-user-dashboard')
        user.is_active = False
        user.save(update_fields=['is_active'])

        self.assert_dashboard_denies_access(user)

    def test_user_without_facilitator_profile_is_denied(self):
        user = User.objects.create_user(
            username='no-facilitator-profile',
            password='test-password',
        )

        self.assert_dashboard_denies_access(user)

    def test_admin_hod_and_superadmin_are_denied(self):
        admin = User.objects.create_user(
            username='legacy-dashboard-admin',
            password='test-password',
            is_staff=True,
        )
        AdminProfile.objects.create(user=admin, role='admin')

        superadmin = User.objects.create_superuser(
            username='legacy-dashboard-superadmin',
            email='superadmin@example.test',
            password='test-password',
        )
        AdminProfile.objects.create(user=superadmin, role='superadmin')

        hod = User.objects.create_user(
            username='legacy-dashboard-hod',
            password='test-password',
        )
        HODProfile.objects.create(user=hod, programme=self.programme)

        for user in (admin, hod, superadmin):
            with self.subTest(role=user.username):
                self.assert_dashboard_denies_access(user)


class GetStateChangeProtectionTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def assert_get_is_rejected(self, view, user_role='admin', **kwargs):
        request = self.factory.get('/')
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_staff=True,
            is_superuser=True,
            admin_profile=SimpleNamespace(role=user_role),
        )

        response = view(request, **kwargs)

        self.assertEqual(response.status_code, 405)

    def test_approval_and_rejection_actions_reject_get(self):
        for view in (views.approve_teacher, views.reject_teacher):
            with self.subTest(view=view.__name__):
                self.assert_get_is_rejected(view, teacher_id=1)

    def test_timetable_deletion_rejects_get(self):
        self.assert_get_is_rejected(views.delete_timetable_entry, entry_id=1)

    def test_superadmin_approval_rejects_get(self):
        self.assert_get_is_rejected(
            views.superadmin_approve_teacher,
            user_role='superadmin',
            teacher_id=1,
        )

    def test_logout_and_notification_read_reject_get(self):
        self.assert_get_is_rejected(views.logout_view)
        self.assert_get_is_rejected(
            views.mark_notification_read,
            notif_id=1,
        )


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class AttendanceEvidenceClassificationTests(TestCase):
    def setUp(self):
        self.programme = Programme.objects.create(
            name='Attendance Evidence Programme',
            code='ATTEND',
        )
        self.admin = User.objects.create_user(
            username='attendance-admin',
            password='test-password',
            is_staff=True,
        )
        AdminProfile.objects.create(user=self.admin, role='admin')

    def create_teacher(self, username, programme=None):
        user = User.objects.create_user(
            username=username,
            password='test-password',
        )
        return TeacherProfile.objects.create(
            user=user,
            staff_id=f'WTI-{username}',
            phone='0200000000',
            gender='male',
            address='',
            qualification='degree',
            specialization='',
            course_type='core',
            preferred_subject='ict',
            programme=programme or self.programme,
            is_approved=True,
            profile_complete=True,
        )

    def test_attendance_defaults_to_unverified(self):
        teacher = self.create_teacher('attendance-default')

        attendance = Attendance.objects.create(teacher=teacher)

        self.assertEqual(attendance.school_status, 'unverified')
        self.assertEqual(attendance.get_school_status_display(), 'Unverified')

    def test_admin_attendance_separates_explicit_absence_from_unverified(self):
        absent_teacher = self.create_teacher('confirmed-absent')
        unverified_teacher = self.create_teacher('no-attendance-evidence')
        Attendance.objects.create(
            teacher=absent_teacher,
            date=timezone.localdate(),
            school_status='absent',
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('admin_attendance'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['absent_teachers']),
            [absent_teacher],
        )
        self.assertEqual(
            list(response.context['unverified_teachers']),
            [unverified_teacher],
        )
        self.assertContains(response, 'Unverified')

    def test_admin_dashboard_counts_only_explicit_absence(self):
        absent_teacher = self.create_teacher('dashboard-absent')
        self.create_teacher('dashboard-unverified')
        Attendance.objects.create(
            teacher=absent_teacher,
            date=timezone.localdate(),
            school_status='absent',
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('admin_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['absent_count'], 1)
        self.assertEqual(response.context['unverified_count'], 1)

    def test_hod_attendance_shows_unverified_only_for_assigned_programme(self):
        hod_user = User.objects.create_user(
            username='attendance-hod',
            password='test-password',
        )
        HODProfile.objects.create(
            user=hod_user,
            programme=self.programme,
            is_active=True,
        )
        unverified_teacher = self.create_teacher('hod-unverified')
        absent_teacher = self.create_teacher('hod-absent')
        Attendance.objects.create(
            teacher=absent_teacher,
            date=timezone.localdate(),
            school_status='absent',
        )
        other_programme = Programme.objects.create(
            name='Other Attendance Programme',
            code='OTHERATT',
        )
        self.create_teacher('other-programme-unverified', other_programme)
        self.client.force_login(hod_user)

        response = self.client.get(reverse('hod_attendance'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['unverified_teachers']),
            [unverified_teacher],
        )
        self.assertEqual(
            list(response.context['attendance']),
            list(Attendance.objects.filter(teacher=absent_teacher)),
        )

    def test_hod_dashboard_reports_actual_attendance_workload_conflicts_and_supervision(self):
        hod_user = User.objects.create_user(
            username='availability-hod',
            password='test-password',
        )
        HODProfile.objects.create(
            user=hod_user,
            programme=self.programme,
            is_active=True,
        )
        other_programme = Programme.objects.create(
            name='Other Availability Programme',
            code='OTHERAVAIL',
        )
        outside_teacher = self.create_teacher(
            'availability-outside-programme',
            other_programme,
        )
        permanent_teacher = self.create_teacher('availability-permanent')
        permanent = FacilitatorProfile.objects.create(
            teacher=permanent_teacher,
            programme=self.programme,
            employment_type='PERMANENT',
        )
        internship_teacher = self.create_teacher('availability-intern')
        internship = FacilitatorProfile.objects.create(
            teacher=internship_teacher,
            programme=self.programme,
            employment_type='INTERNSHIP',
            internship_start_date=timezone.localdate(),
            supervisor=permanent,
        )
        InternshipSupervision.objects.create(
            internship=internship,
            supervisor=permanent,
            start_date=timezone.localdate(),
            changed_by=hod_user,
        )
        Attendance.objects.create(
            teacher=permanent_teacher,
            date=timezone.localdate(),
            school_status='present',
            check_in_time='08:05',
        )
        first_entry = TimetableEntry.objects.create(
            day='monday',
            subject='Workshop Practice',
            start_time='09:00',
            end_time='10:00',
            teacher=permanent_teacher,
        )
        TimetableEntry.objects.create(
            day='monday',
            subject='Networking',
            start_time='09:30',
            end_time='10:30',
            teacher=permanent_teacher,
        )
        ClassSchedule.objects.create(
            timetable_entry=first_entry,
            teacher=permanent_teacher,
            scheduled_date=timezone.localdate() + timedelta(days=1),
            created_by=hod_user,
        )
        self.client.force_login(hod_user)

        response = self.client.get(reverse('hod_dashboard'))

        self.assertEqual(response.status_code, 200)
        rows = {
            row['teacher'].pk: row
            for row in response.context['availability_rows']
        }
        permanent_row = rows[permanent_teacher.pk]
        internship_row = rows[internship_teacher.pk]
        self.assertNotIn(outside_teacher.pk, rows)
        self.assertEqual(permanent_row['attendance_status'], 'Present')
        self.assertEqual(permanent_row['check_in_time'].strftime('%H:%M'), '08:05')
        self.assertEqual(permanent_row['availability_status'], 'Unverified')
        self.assertEqual(permanent_row['weekly_class_count'], 2)
        self.assertEqual(permanent_row['weekly_minutes'], 120)
        self.assertEqual(len(permanent_row['conflicts']), 1)
        self.assertEqual(permanent_row['upcoming_class_count'], 1)
        self.assertEqual(permanent_row['supervision'], '1 active interns')
        self.assertEqual(internship_row['attendance_status'], 'Unverified')
        self.assertEqual(internship_row['supervision'], permanent.full_name)
        self.assertEqual(len(response.context['upcoming_class_schedules']), 1)
        self.assertContains(response, 'not confirmation')

    def test_admin_schedule_with_recurring_timetable_conflict_is_not_saved_or_notified(self):
        teacher = self.create_teacher('schedule-conflict')
        FacilitatorProfile.objects.create(
            teacher=teacher,
            programme=self.programme,
            employment_type='PERMANENT',
        )
        TimetableEntry.objects.create(
            day='monday',
            subject='Existing Weekly Class',
            start_time=time(9, 0),
            end_time=time(10, 0),
            teacher=teacher,
        )
        proposed_entry = TimetableEntry.objects.create(
            day='monday',
            subject='Proposed Class',
            start_time=time(9, 30),
            end_time=time(10, 30),
        )
        days_until_monday = (7 - timezone.localdate().weekday()) % 7 or 7
        scheduled_date = timezone.localdate() + timedelta(days=days_until_monday)
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('admin_schedule'),
            {
                'action': 'request_confirmation',
                'timetable_entry': proposed_entry.pk,
                'teacher': teacher.pk,
                'scheduled_date': scheduled_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ClassSchedule.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        evaluation = response.context['scheduling_evaluation']
        self.assertEqual(evaluation.status, BLOCKED)
        self.assertIn('Existing Weekly Class', str(evaluation.rule_results))
        self.assertEqual(SchedulingEvaluation.objects.count(), 1)

    def test_engine_rejects_overlap_with_saved_assignment(self):
        teacher = self.create_teacher('saved-schedule-conflict')
        scheduled_date = timezone.localdate() + timedelta(days=1)
        existing_entry = TimetableEntry.objects.create(
            day='monday',
            subject='Existing Assignment',
            start_time=time(9, 0),
            end_time=time(10, 0),
        )
        ClassSchedule.objects.create(
            timetable_entry=existing_entry,
            teacher=teacher,
            scheduled_date=scheduled_date,
        )
        proposed_entry = TimetableEntry.objects.create(
            day='monday',
            subject='Proposed Assignment',
            start_time=time(9, 30),
            end_time=time(10, 30),
        )

        evaluation = evaluate_class_assignment(
            proposed_class=proposed_entry,
            scheduled_date=scheduled_date,
            start_time=time(9, 30),
            end_time=time(10, 30),
            programme=self.programme,
            candidate=teacher,
            evaluated_by=self.admin,
        )

        self.assertEqual(evaluation.status, BLOCKED)
        self.assertIn('Existing Assignment', str(evaluation.rule_results))

    def test_engine_rejects_closed_day_and_out_of_hours(self):
        teacher = self.create_teacher('out-of-hours-schedule')
        sunday = date(2025, 1, 5)
        timetable_entry = TimetableEntry.objects.create(
            day='monday',
            subject='Closed Day Assignment',
            start_time=time(7, 0),
            end_time=time(8, 0),
        )

        evaluation = evaluate_class_assignment(
            proposed_class=timetable_entry,
            scheduled_date=sunday,
            start_time=time(7, 0),
            end_time=time(8, 0),
            programme=self.programme,
            candidate=teacher,
            evaluated_by=self.admin,
        )

        self.assertEqual(evaluation.status, BLOCKED)
        self.assertIn('operating_day', str(evaluation.rule_results))
        self.assertIn('operating_hours', str(evaluation.rule_results))

    def test_admin_schedule_rejects_unapproved_facilitator(self):
        teacher = self.create_teacher('unapproved-schedule')
        teacher.is_approved = False
        teacher.save(update_fields=['is_approved'])
        timetable_entry = TimetableEntry.objects.create(
            day='tuesday',
            subject='Eligible Time Slot',
            start_time='13:00',
            end_time='14:00',
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('admin_schedule'),
            {
                'timetable_entry': timetable_entry.pk,
                'teacher': teacher.pk,
                'scheduled_date': (
                    timezone.localdate() + timedelta(days=1)
                ).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ClassSchedule.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertContains(response, 'Direct scheduling is disabled')

    def test_admin_schedule_rejects_expired_internship_facilitator(self):
        teacher = self.create_teacher('expired-schedule')
        today = timezone.localdate()
        FacilitatorProfile.objects.create(
            teacher=teacher,
            programme=self.programme,
            employment_type='INTERNSHIP',
            internship_start_date=today - timedelta(days=365),
            internship_end_date=today - timedelta(days=1),
        )
        timetable_entry = TimetableEntry.objects.create(
            day='tuesday',
            subject='Eligible Time Slot',
            start_time='13:00',
            end_time='14:00',
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('admin_schedule'),
            {
                'timetable_entry': timetable_entry.pk,
                'teacher': teacher.pk,
                'scheduled_date': (
                    timezone.localdate() + timedelta(days=1)
                ).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ClassSchedule.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertContains(response, 'Direct scheduling is disabled')

    def test_admin_schedule_requests_confirmation_without_assigning_classes(self):
        permanent_teacher = self.create_teacher('eligible-permanent-schedule')
        FacilitatorProfile.objects.create(
            teacher=permanent_teacher,
            programme=self.programme,
            employment_type='PERMANENT',
        )
        supervisor_teacher = self.create_teacher('eligible-intern-supervisor')
        supervisor = FacilitatorProfile.objects.create(
            teacher=supervisor_teacher,
            programme=self.programme,
            employment_type='PERMANENT',
        )
        internship_teacher = self.create_teacher('eligible-intern-schedule')
        FacilitatorProfile.objects.create(
            teacher=internship_teacher,
            programme=self.programme,
            employment_type='INTERNSHIP',
            internship_start_date=timezone.localdate(),
            supervisor=supervisor,
        )
        internship_profile = FacilitatorProfile.objects.get(
            teacher=internship_teacher,
        )
        InternshipSupervision.objects.create(
            internship=internship_profile,
            supervisor=supervisor,
            start_date=timezone.localdate(),
            changed_by=self.admin,
        )
        scheduled_date = timezone.localdate() + timedelta(days=1)
        first_entry = TimetableEntry.objects.create(
            day='tuesday',
            subject='Permanent Class',
            start_time='09:00',
            end_time='10:00',
        )
        second_entry = TimetableEntry.objects.create(
            day='tuesday',
            subject='Internship Class',
            start_time='10:00',
            end_time='11:00',
        )
        self.client.force_login(self.admin)

        with override_settings(
            SCHEDULING_VERIFICATION_PROVIDER=self.trusted_verification,
        ):
            for timetable_entry, teacher in (
                (first_entry, permanent_teacher),
                (second_entry, internship_teacher),
            ):
                response = self.client.post(
                    reverse('admin_schedule'),
                    {
                        'action': 'request_confirmation',
                        'timetable_entry': timetable_entry.pk,
                        'teacher': teacher.pk,
                        'scheduled_date': scheduled_date.isoformat(),
                    },
                )
                self.assertRedirects(response, reverse('admin_schedule'))

        self.assertEqual(ClassConfirmationRequest.objects.count(), 2)
        self.assertEqual(ClassSchedule.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 2)
        self.assertSetEqual(
            set(Notification.objects.values_list('recipient_id', flat=True)),
            {permanent_teacher.user_id, internship_teacher.user_id},
        )

    def trusted_verification(self, teacher, scheduled_date):
        return SchedulingVerification(
            attendance_verified=True,
            location_verified=True,
            availability_confirmed=True,
            teaching_eligibility_verified=True,
            explanation='Verified by test-only independent evidence provider.',
        )


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class SchedulingDecisionEngineTests(TestCase):
    def setUp(self):
        self.programme = Programme.objects.create(
            name='Scheduling Engine Programme',
            code='SCHEDENGINE',
        )
        self.class_date = self.next_operating_day()

    def next_operating_day(self):
        current = timezone.localdate()
        for offset in range(1, 8):
            candidate = current + timedelta(days=offset)
            if candidate.weekday() < 6:
                return candidate
        self.fail('Could not find a school operating day.')

    def create_teacher(self, username, programme=None, approved=True, active=True):
        user = User.objects.create_user(
            username=username,
            password='test-password',
            is_active=active,
        )
        return TeacherProfile.objects.create(
            user=user,
            staff_id=f'WTI-{username}',
            phone='0200000000',
            gender='male',
            address='',
            qualification='degree',
            specialization='',
            course_type='core',
            preferred_subject='ict',
            programme=programme or self.programme,
            is_approved=approved,
            profile_complete=True,
        )

    def create_permanent(self, username, programme=None):
        teacher = self.create_teacher(username, programme=programme)
        facilitator = FacilitatorProfile.objects.create(
            teacher=teacher,
            programme=programme or self.programme,
            employment_type='PERMANENT',
        )
        return teacher, facilitator

    def create_internship(self, username, supervisor=None):
        teacher = self.create_teacher(username)
        today = timezone.localdate()
        internship = FacilitatorProfile.objects.create(
            teacher=teacher,
            programme=self.programme,
            employment_type='INTERNSHIP',
            internship_start_date=today - timedelta(days=30),
            internship_end_date=today + timedelta(days=180),
            supervisor=supervisor,
        )
        if supervisor:
            InternshipSupervision.objects.create(
                internship=internship,
                supervisor=supervisor,
                start_date=today - timedelta(days=30),
            )
        return teacher, internship

    def trusted_verification(self, teacher, scheduled_date):
        return SchedulingVerification(
            attendance_verified=True,
            location_verified=True,
            availability_confirmed=True,
            teaching_eligibility_verified=True,
            explanation='Verified by test-only independent evidence provider.',
        )

    def evaluate(self, candidate, **kwargs):
        values = {
            'proposed_class': 'Engine test class',
            'scheduled_date': self.class_date,
            'start_time': time(9, 0),
            'end_time': time(10, 0),
            'programme': self.programme,
            'candidate': candidate,
        }
        values.update(kwargs)
        return evaluate_class_assignment(**values)

    def test_eligible_permanent_facilitator_is_audited(self):
        teacher, facilitator = self.create_permanent('engine-permanent')

        evaluation = self.evaluate(
            facilitator,
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, ELIGIBLE)
        self.assertEqual(evaluation.candidate, teacher)
        self.assertEqual(evaluation.location_status, 'VERIFIED')
        self.assertTrue(evaluation.rule_results)
        self.assertEqual(SchedulingEvaluation.objects.count(), 1)

    def test_eligible_internship_requires_valid_supervision(self):
        _, supervisor = self.create_permanent('engine-supervisor')
        _, intern = self.create_internship('engine-intern', supervisor=supervisor)

        evaluation = self.evaluate(
            intern,
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, ELIGIBLE)
        self.assertEqual(
            next(
                rule for rule in evaluation.rule_results
                if rule['key'] == 'internship_supervision'
            )['status'],
            'PASS',
        )

    def test_expired_internship_is_blocked(self):
        teacher = self.create_teacher('engine-expired')
        today = timezone.localdate()
        internship = FacilitatorProfile.objects.create(
            teacher=teacher,
            programme=self.programme,
            employment_type='INTERNSHIP',
            internship_start_date=today - timedelta(days=400),
            internship_end_date=today - timedelta(days=1),
        )

        evaluation = self.evaluate(
            internship,
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, BLOCKED)
        self.assertIn('expires before', str(evaluation.rule_results))

    def test_inactive_and_unapproved_accounts_are_blocked(self):
        inactive = self.create_teacher('engine-inactive-account', active=False)
        inactive_profile = FacilitatorProfile.objects.create(
            teacher=inactive,
            programme=self.programme,
            employment_type='PERMANENT',
        )
        unapproved = self.create_teacher('engine-unapproved', approved=False)
        unapproved_profile = FacilitatorProfile.objects.create(
            teacher=unapproved,
            programme=self.programme,
            employment_type='PERMANENT',
        )

        for profile in (inactive_profile, unapproved_profile):
            with self.subTest(candidate=profile.teacher.user.username):
                result = self.evaluate(
                    profile,
                    verification_provider=self.trusted_verification,
                )
                self.assertEqual(result.status, BLOCKED)

    def test_invalid_internship_supervision_is_blocked(self):
        _, intern = self.create_internship('engine-no-supervisor')

        evaluation = self.evaluate(
            intern,
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, BLOCKED)
        self.assertIn('supervisor relationship', str(evaluation.rule_results))

    def test_wrong_programme_is_blocked(self):
        other_programme = Programme.objects.create(
            name='Other Scheduling Programme',
            code='OTHERENGINE',
        )
        _, facilitator = self.create_permanent('engine-wrong-programme', other_programme)

        evaluation = self.evaluate(
            facilitator,
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, BLOCKED)

    def test_sunday_is_blocked(self):
        sunday = self.class_date + timedelta(days=(6 - self.class_date.weekday()) % 7)
        _, facilitator = self.create_permanent('engine-sunday')

        evaluation = self.evaluate(
            facilitator,
            scheduled_date=sunday,
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, BLOCKED)
        self.assertIn('operating_day', str(evaluation.rule_results))

    def test_outside_operating_hours_is_blocked(self):
        _, facilitator = self.create_permanent('engine-outside-hours')

        evaluation = self.evaluate(
            facilitator,
            start_time=time(7, 30),
            end_time=time(8, 30),
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, BLOCKED)
        self.assertIn('operating_hours', str(evaluation.rule_results))

    def test_recurring_timetable_conflict_is_blocked(self):
        teacher, facilitator = self.create_permanent('engine-recurring-conflict')
        TimetableEntry.objects.create(
            day=self.class_date.strftime('%A').lower(),
            subject='Existing recurring class',
            start_time='09:30',
            end_time='10:30',
            teacher=teacher,
        )

        evaluation = self.evaluate(
            facilitator,
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, BLOCKED)
        self.assertIn('Existing recurring class', str(evaluation.rule_results))

    def test_same_date_class_conflict_is_blocked(self):
        teacher, facilitator = self.create_permanent('engine-date-conflict')
        existing_entry = TimetableEntry.objects.create(
            day=self.class_date.strftime('%A').lower(),
            subject='Saved same-date class',
            start_time='09:15',
            end_time='09:45',
        )
        ClassSchedule.objects.create(
            timetable_entry=existing_entry,
            teacher=teacher,
            scheduled_date=self.class_date,
        )

        evaluation = self.evaluate(
            facilitator,
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, BLOCKED)
        self.assertIn('Saved same-date class', str(evaluation.rule_results))

    def test_workload_is_measured_without_a_limit(self):
        teacher, facilitator = self.create_permanent('engine-workload')
        first = TimetableEntry.objects.create(
            day='monday',
            subject='Completed assignment one',
            start_time='11:00',
            end_time='12:00',
        )
        second = TimetableEntry.objects.create(
            day='monday',
            subject='Completed assignment two',
            start_time='12:00',
            end_time='12:30',
        )
        ClassSchedule.objects.create(
            timetable_entry=first,
            teacher=teacher,
            scheduled_date=self.class_date,
        )
        ClassSchedule.objects.create(
            timetable_entry=second,
            teacher=teacher,
            scheduled_date=self.class_date,
        )

        evaluation = self.evaluate(
            facilitator,
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(evaluation.status, ELIGIBLE)
        self.assertEqual(evaluation.weekly_scheduled_minutes, 90)
        self.assertEqual(evaluation.projected_weekly_minutes, 150)
        self.assertIn('No authorised maximum workload', str(evaluation.rule_results))

    def test_missing_attendance_evidence_is_unverified(self):
        _, facilitator = self.create_permanent('engine-missing-attendance')

        evaluation = self.evaluate(facilitator)

        self.assertEqual(evaluation.status, UNVERIFIED)
        self.assertEqual(evaluation.attendance_status, 'UNVERIFIED')
        self.assertEqual(evaluation.location_status, 'UNVERIFIED')
        self.assertEqual(evaluation.availability_status, 'UNVERIFIED')

    def test_browser_gps_does_not_verify_location_or_physical_attendance(self):
        teacher, facilitator = self.create_permanent('engine-browser-gps')
        Attendance.objects.create(
            teacher=teacher,
            date=self.class_date,
            school_status='present',
            check_in_time='08:15',
        )

        evaluation = self.evaluate(facilitator)

        self.assertEqual(evaluation.status, UNVERIFIED)
        self.assertEqual(evaluation.attendance_status, 'UNVERIFIED')
        self.assertEqual(evaluation.location_status, 'UNVERIFIED')
        self.assertIn('Browser GPS', str(evaluation.rule_results))

    def test_candidates_are_ranked_by_explainable_relative_workload(self):
        lower_workload_teacher, lower_workload = self.create_permanent('engine-lower-workload')
        higher_workload_teacher, higher_workload = self.create_permanent('engine-higher-workload')
        existing_entry = TimetableEntry.objects.create(
            day='monday',
            subject='Prior workload',
            start_time='11:00',
            end_time='12:00',
        )
        ClassSchedule.objects.create(
            timetable_entry=existing_entry,
            teacher=lower_workload_teacher,
            scheduled_date=self.class_date,
        )
        for index in range(2):
            entry = TimetableEntry.objects.create(
                day='monday',
                subject=f'Prior workload {index}',
                start_time=f'{12 + index}:00',
                end_time=f'{13 + index}:00',
            )
            ClassSchedule.objects.create(
                timetable_entry=entry,
                teacher=higher_workload_teacher,
                scheduled_date=self.class_date,
            )

        result = rank_class_candidates(
            proposed_class='Ranking test class',
            scheduled_date=self.class_date,
            start_time=time(9, 0),
            end_time=time(10, 0),
            programme=self.programme,
            candidates=[higher_workload, lower_workload],
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(result['ranked'][0].candidate, lower_workload_teacher)
        self.assertEqual(result['ranked'][0].recommendation_rank, 1)
        self.assertEqual(result['ranked'][0].recommendation_score, Decimal('100.00'))
        self.assertEqual(result['ranked'][1].recommendation_score, Decimal('0.00'))
        self.assertIn('not workload limits', result['explanation'])

    def test_blocked_candidate_is_never_ranked_regardless_of_score(self):
        blocked_teacher, blocked_candidate = self.create_permanent('engine-blocked-best-score')
        _, eligible_candidate = self.create_permanent('engine-eligible-ranking')
        TimetableEntry.objects.create(
            day=self.class_date.strftime('%A').lower(),
            subject='Blocking commitment',
            start_time='09:15',
            end_time='09:45',
            teacher=blocked_teacher,
        )

        result = rank_class_candidates(
            proposed_class='Ranking gate class',
            scheduled_date=self.class_date,
            start_time=time(9, 0),
            end_time=time(10, 0),
            programme=self.programme,
            candidates=[blocked_candidate, eligible_candidate],
            verification_provider=self.trusted_verification,
        )

        self.assertEqual(len(result['ranked']), 1)
        self.assertEqual(
            result['ranked'][0].candidate,
            eligible_candidate.teacher,
        )
        blocked_evaluation = next(
            evaluation for evaluation in result['evaluations']
            if evaluation.candidate == blocked_teacher
        )
        self.assertEqual(blocked_evaluation.status, BLOCKED)
        self.assertIsNone(blocked_evaluation.recommendation_score)
        self.assertIsNone(blocked_evaluation.recommendation_rank)

    def test_no_eligible_candidates_are_not_ranked(self):
        _, facilitator = self.create_permanent('engine-no-eligible-candidate')

        result = rank_class_candidates(
            proposed_class='No eligible candidate class',
            scheduled_date=self.class_date,
            start_time=time(9, 0),
            end_time=time(10, 0),
            programme=self.programme,
            candidates=[facilitator],
        )

        self.assertEqual(result['ranked'], [])
        self.assertEqual(result['evaluations'][0].status, UNVERIFIED)
        self.assertIn('No candidate passed', result['explanation'])


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class ClassConfirmationWorkflowTests(TestCase):
    def setUp(self):
        self.programme = Programme.objects.create(
            name='Confirmation Workflow Programme',
            code='CONFIRM',
        )
        self.class_date = self.next_weekday()
        self.admin = User.objects.create_user(
            username='confirmation-admin',
            password='test-password',
            is_staff=True,
        )
        AdminProfile.objects.create(user=self.admin, role='admin')
        self.timetable_entry = TimetableEntry.objects.create(
            day=self.class_date.strftime('%A').lower(),
            subject='Confirmation Workflow Class',
            start_time=time(9, 0),
            end_time=time(10, 0),
        )

    def next_weekday(self):
        today = timezone.localdate()
        for offset in range(1, 8):
            candidate_date = today + timedelta(days=offset)
            if candidate_date.weekday() < 5:
                return candidate_date
        self.fail('Could not find a weekday for the confirmation test.')

    def create_candidate(self, username):
        user = User.objects.create_user(
            username=username,
            password='test-password',
        )
        teacher = TeacherProfile.objects.create(
            user=user,
            staff_id=f'WTI-{username}',
            phone='0200000000',
            gender='male',
            address='',
            qualification='degree',
            specialization='',
            course_type='core',
            preferred_subject='ict',
            programme=self.programme,
            is_approved=True,
            profile_complete=True,
        )
        facilitator = FacilitatorProfile.objects.create(
            teacher=teacher,
            programme=self.programme,
            employment_type='PERMANENT',
        )
        return teacher, facilitator

    def create_hod(self, programme=None, username='confirmation-hod'):
        hod_user = User.objects.create_user(
            username=username,
            password='test-password',
        )
        HODProfile.objects.create(
            user=hod_user,
            programme=programme or self.programme,
            is_active=True,
        )
        return hod_user

    def trusted_verification(self, teacher, scheduled_date):
        return SchedulingVerification(
            attendance_verified=True,
            location_verified=True,
            availability_confirmed=True,
            teaching_eligibility_verified=True,
            explanation='Verified by test-only independent evidence provider.',
        )

    def create_confirmation(self, candidate, requested_by=None):
        evaluation = evaluate_class_assignment(
            proposed_class=self.timetable_entry,
            scheduled_date=self.class_date,
            start_time=self.timetable_entry.start_time,
            end_time=self.timetable_entry.end_time,
            programme=self.programme,
            candidate=candidate,
            evaluated_by=requested_by or self.admin,
            verification_provider=self.trusted_verification,
        )
        return ClassConfirmationRequest.objects.create(
            programme=self.programme,
            candidate=candidate,
            timetable_entry=self.timetable_entry,
            scheduled_date=self.class_date,
            start_time=self.timetable_entry.start_time,
            end_time=self.timetable_entry.end_time,
            evaluation=evaluation,
            requested_by=requested_by or self.admin,
        )

    def test_request_is_not_created_or_notified_when_evidence_is_unverified(self):
        candidate, _ = self.create_candidate('confirmation-no-evidence')
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('admin_schedule'),
            {
                'action': 'request_confirmation',
                'timetable_entry': self.timetable_entry.pk,
                'teacher': candidate.pk,
                'scheduled_date': self.class_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SchedulingEvaluation.objects.count(), 1)
        self.assertEqual(SchedulingEvaluation.objects.get().status, UNVERIFIED)
        self.assertEqual(ClassConfirmationRequest.objects.count(), 0)
        self.assertEqual(ClassSchedule.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertContains(response, 'No confirmation request was sent')

    def test_eligible_candidate_receives_request_but_no_class_assignment(self):
        candidate, _ = self.create_candidate('confirmation-eligible')
        self.client.force_login(self.admin)

        with override_settings(
            SCHEDULING_VERIFICATION_PROVIDER=self.trusted_verification,
        ):
            response = self.client.post(
                reverse('admin_schedule'),
                {
                    'action': 'request_confirmation',
                    'timetable_entry': self.timetable_entry.pk,
                    'teacher': candidate.pk,
                    'scheduled_date': self.class_date.isoformat(),
                },
            )

        self.assertRedirects(response, reverse('admin_schedule'))
        confirmation = ClassConfirmationRequest.objects.get()
        self.assertEqual(confirmation.status, 'PENDING')
        self.assertEqual(confirmation.evaluation.status, ELIGIBLE)
        self.assertEqual(ClassSchedule.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertIn('not a confirmed class', Notification.objects.get().message)

    def test_only_one_pending_confirmation_exists_per_class_and_date(self):
        first_candidate, _ = self.create_candidate('confirmation-pending-first')
        second_candidate, _ = self.create_candidate('confirmation-pending-second')
        self.client.force_login(self.admin)
        with override_settings(
            SCHEDULING_VERIFICATION_PROVIDER=self.trusted_verification,
        ):
            first_response = self.client.post(
                reverse('admin_schedule'),
                {
                    'action': 'request_confirmation',
                    'timetable_entry': self.timetable_entry.pk,
                    'teacher': first_candidate.pk,
                    'scheduled_date': self.class_date.isoformat(),
                },
            )
            duplicate_response = self.client.post(
                reverse('admin_schedule'),
                {
                    'action': 'request_confirmation',
                    'timetable_entry': self.timetable_entry.pk,
                    'teacher': second_candidate.pk,
                    'scheduled_date': self.class_date.isoformat(),
                },
            )

        self.assertRedirects(first_response, reverse('admin_schedule'))
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertContains(duplicate_response, 'confirmation request for this class and date is already pending')
        self.assertEqual(ClassConfirmationRequest.objects.count(), 1)
        self.assertEqual(SchedulingEvaluation.objects.count(), 2)
        duplicate_evaluation = SchedulingEvaluation.objects.order_by('-created_at').first()
        self.assertEqual(duplicate_evaluation.status, BLOCKED)
        self.assertIn('pending_confirmation', str(duplicate_evaluation.rule_results))
        self.assertEqual(Notification.objects.count(), 1)

    def test_legacy_direct_assignment_action_cannot_bypass_confirmation(self):
        candidate, _ = self.create_candidate('confirmation-no-direct-assignment')
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('admin_schedule'),
            {
                'action': 'assign',
                'timetable_entry': self.timetable_entry.pk,
                'teacher': candidate.pk,
                'scheduled_date': self.class_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Direct scheduling is disabled')
        self.assertEqual(ClassSchedule.objects.count(), 0)
        self.assertEqual(ClassConfirmationRequest.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_facilitator_acceptance_creates_confirmed_schedule(self):
        candidate, _ = self.create_candidate('confirmation-accept')
        confirmation = self.create_confirmation(candidate)
        self.client.force_login(candidate.user)

        with override_settings(
            SCHEDULING_VERIFICATION_PROVIDER=self.trusted_verification,
        ):
            response = self.client.post(
                reverse(
                    'facilitator_class_confirmation_response',
                    args=[confirmation.pk],
                ),
                {'action': 'accept'},
            )

        self.assertRedirects(response, reverse('facilitator_dashboard'))
        confirmation.refresh_from_db()
        self.assertEqual(confirmation.status, 'ACCEPTED')
        self.assertEqual(confirmation.confirmed_schedule.teacher, candidate)
        self.assertEqual(ClassSchedule.objects.count(), 1)

    def test_facilitator_acceptance_is_withheld_if_recheck_is_unverified(self):
        self.create_hod()
        candidate, _ = self.create_candidate('confirmation-recheck')
        confirmation = self.create_confirmation(candidate)
        self.client.force_login(candidate.user)

        response = self.client.post(
            reverse(
                'facilitator_class_confirmation_response',
                args=[confirmation.pk],
            ),
            {'action': 'accept'},
        )

        self.assertRedirects(response, reverse('facilitator_dashboard'))
        confirmation.refresh_from_db()
        self.assertEqual(confirmation.status, 'WITHHELD')
        self.assertEqual(ClassSchedule.objects.count(), 0)
        self.assertEqual(confirmation.evaluation.status, UNVERIFIED)

    def test_decline_creates_audited_replacement_review_for_hod(self):
        hod_user = self.create_hod()
        candidate, _ = self.create_candidate('confirmation-decline')
        self.create_candidate('confirmation-replacement')
        confirmation = self.create_confirmation(candidate)
        self.client.force_login(candidate.user)

        with override_settings(
            SCHEDULING_VERIFICATION_PROVIDER=self.trusted_verification,
        ):
            response = self.client.post(
                reverse(
                    'facilitator_class_confirmation_response',
                    args=[confirmation.pk],
                ),
                {'action': 'decline', 'response_note': 'Unavailable'},
            )

        self.assertRedirects(response, reverse('facilitator_dashboard'))
        confirmation.refresh_from_db()
        review = ClassReplacementRequest.objects.get()
        self.assertEqual(confirmation.status, 'DECLINED')
        self.assertEqual(review.status, 'PENDING')
        self.assertEqual(review.programme, self.programme)
        self.assertTrue(review.recommendations.filter(status=ELIGIBLE).exists())
        self.assertEqual(ClassSchedule.objects.count(), 0)
        self.assertTrue(
            Notification.objects.filter(
                recipient=hod_user,
                title='Class replacement requires HOD review',
            ).exists()
        )

    def test_hod_approval_sends_replacement_request_without_assigning_class(self):
        self.create_hod()
        declined_candidate, _ = self.create_candidate('confirmation-declined-hod')
        replacement_candidate, _ = self.create_candidate('confirmation-replacement-hod')
        confirmation = self.create_confirmation(declined_candidate)
        self.client.force_login(declined_candidate.user)

        with override_settings(
            SCHEDULING_VERIFICATION_PROVIDER=self.trusted_verification,
        ):
            self.client.post(
                reverse(
                    'facilitator_class_confirmation_response',
                    args=[confirmation.pk],
                ),
                {'action': 'decline'},
            )

        review = ClassReplacementRequest.objects.get()
        self.client.force_login(self.programme.hod_profile.user)
        with override_settings(
            SCHEDULING_VERIFICATION_PROVIDER=self.trusted_verification,
        ):
            response = self.client.post(
                reverse('hod_class_replacement_decision', args=[review.pk]),
                {
                    'action': 'approve',
                    'candidate_id': replacement_candidate.pk,
                },
            )

        self.assertRedirects(response, reverse('hod_class_replacements'))
        review.refresh_from_db()
        self.assertEqual(review.status, 'APPROVED')
        self.assertEqual(review.selected_candidate, replacement_candidate)
        self.assertEqual(review.replacement_confirmation.status, 'PENDING')
        self.assertEqual(review.replacement_confirmation.replaces, confirmation)
        self.assertEqual(ClassSchedule.objects.count(), 0)

        self.client.force_login(replacement_candidate.user)
        with override_settings(
            SCHEDULING_VERIFICATION_PROVIDER=self.trusted_verification,
        ):
            response = self.client.post(
                reverse(
                    'facilitator_class_confirmation_response',
                    args=[review.replacement_confirmation.pk],
                ),
                {'action': 'accept'},
            )
        self.assertRedirects(response, reverse('facilitator_dashboard'))
        review.replacement_confirmation.refresh_from_db()
        self.assertEqual(review.replacement_confirmation.status, 'ACCEPTED')
        self.assertEqual(ClassSchedule.objects.count(), 1)
        self.assertEqual(
            ClassSchedule.objects.get().teacher,
            replacement_candidate,
        )

    def test_non_hod_cannot_approve_replacement(self):
        self.create_hod()
        declined_candidate, _ = self.create_candidate('confirmation-declined-permission')
        self.create_candidate('confirmation-replacement-permission')
        confirmation = self.create_confirmation(declined_candidate)
        self.client.force_login(declined_candidate.user)

        with override_settings(
            SCHEDULING_VERIFICATION_PROVIDER=self.trusted_verification,
        ):
            self.client.post(
                reverse(
                    'facilitator_class_confirmation_response',
                    args=[confirmation.pk],
                ),
                {'action': 'decline'},
            )

        review = ClassReplacementRequest.objects.get()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('hod_class_replacement_decision', args=[review.pk]),
            {'action': 'reject'},
        )

        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.status, 'PENDING')

    def test_hod_cannot_approve_an_unverified_replacement_candidate(self):
        hod_user = self.create_hod()
        declined_candidate, _ = self.create_candidate('confirmation-declined-unverified')
        replacement_candidate, _ = self.create_candidate('confirmation-replacement-unverified')
        confirmation = self.create_confirmation(declined_candidate)
        self.client.force_login(declined_candidate.user)

        self.client.post(
            reverse(
                'facilitator_class_confirmation_response',
                args=[confirmation.pk],
            ),
            {'action': 'decline'},
        )

        review = ClassReplacementRequest.objects.get()
        self.client.force_login(hod_user)
        queue_response = self.client.get(reverse('hod_class_replacements'))
        self.assertEqual(queue_response.status_code, 200)
        self.assertContains(queue_response, 'No independently verified attendance evidence')
        response = self.client.post(
            reverse('hod_class_replacement_decision', args=[review.pk]),
            {
                'action': 'approve',
                'candidate_id': replacement_candidate.pk,
            },
        )

        self.assertRedirects(response, reverse('hod_class_replacements'))
        review.refresh_from_db()
        self.assertEqual(review.status, 'PENDING')
        self.assertIsNone(review.replacement_confirmation)
        self.assertEqual(ClassConfirmationRequest.objects.count(), 1)
        self.assertEqual(ClassSchedule.objects.count(), 0)

    def test_hod_replacement_queue_is_programme_scoped(self):
        other_programme = Programme.objects.create(
            name='Other HOD Replacement Programme',
            code='OTHERREPLACE',
        )
        other_hod = self.create_hod(
            programme=other_programme,
            username='other-replacement-hod',
        )
        declined_candidate, _ = self.create_candidate('confirmation-programme-scope')
        confirmation = self.create_confirmation(declined_candidate)
        review = ClassReplacementRequest.objects.create(
            original_request=confirmation,
            programme=self.programme,
            reason='Programme access test',
        )
        self.client.force_login(other_hod)

        response = self.client.post(
            reverse('hod_class_replacement_decision', args=[review.pk]),
            {'action': 'reject'},
        )

        self.assertEqual(response.status_code, 404)
        review.refresh_from_db()
        self.assertEqual(review.status, 'PENDING')

    def test_facilitator_cannot_respond_to_another_facilitators_request(self):
        candidate, _ = self.create_candidate('confirmation-owner')
        other_candidate, _ = self.create_candidate('confirmation-other')
        confirmation = self.create_confirmation(candidate)
        self.client.force_login(other_candidate.user)

        response = self.client.post(
            reverse(
                'facilitator_class_confirmation_response',
                args=[confirmation.pk],
            ),
            {'action': 'accept'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(ClassSchedule.objects.count(), 0)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class QRAttendanceSafetyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='qr-facilitator',
            password='test-password',
        )
        self.profile = TeacherProfile.objects.create(
            user=self.user,
            staff_id='WTI9001',
            phone='0200000000',
            gender='male',
            address='',
            qualification='degree',
            specialization='',
            course_type='core',
            preferred_subject='ict',
            is_approved=True,
            profile_complete=True,
        )
        self.qr = QRCode.objects.create(
            created_by=self.user,
            qr_type='school_attendance',
            valid_from=timezone.localdate(),
            valid_until=timezone.localdate(),
        )
        SchoolLocation.objects.create(
            name='Test campus',
            latitude=Decimal('0'),
            longitude=Decimal('0'),
            allowed_radius=100,
        )
        self.client.force_login(self.user)
        self.url = reverse('scan_school_qr', kwargs={'qr_uuid': self.qr.code})

    def test_get_does_not_write_attendance(self):
        response = self.client.get(self.url, {'lat': '0', 'lon': '0'})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Attendance.objects.filter(teacher=self.profile).exists())

    def test_post_rejects_out_of_range_coordinates(self):
        response = self.client.post(self.url, {'lat': '91', 'lon': '0'})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Attendance.objects.filter(teacher=self.profile).exists())

    def test_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(self.url, {'lat': '0', 'lon': '0'})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Attendance.objects.filter(teacher=self.profile).exists())

    def test_valid_post_is_idempotent(self):
        first = self.client.post(self.url, {'lat': '0', 'lon': '0'})
        second = self.client.post(self.url, {'lat': '0', 'lon': '0'})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            Attendance.objects.filter(teacher=self.profile).count(),
            1,
        )

    def test_inactive_facilitator_profile_cannot_submit_attendance(self):
        programme = Programme.objects.create(name='Inactive Test', code='INACTIVE')
        FacilitatorProfile.objects.create(
            teacher=self.profile,
            programme=programme,
            is_active=False,
        )

        response = self.client.post(self.url, {'lat': '0', 'lon': '0'})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Attendance.objects.filter(teacher=self.profile).exists())


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class PrivateDocumentAccessTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            STORAGES={
                'default': {
                    'BACKEND': 'django.core.files.storage.FileSystemStorage',
                },
                'staticfiles': {
                    'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
                },
            },
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.owner = User.objects.create_user(
            username='document-owner',
            password='test-password',
        )
        self.teacher = TeacherProfile.objects.create(
            user=self.owner,
            staff_id='WTI9002',
            phone='0200000000',
            gender='male',
            address='',
            qualification='degree',
            specialization='',
            course_type='core',
            preferred_subject='ict',
            is_approved=True,
            profile_complete=True,
        )
        self.teacher.cv_document.save(
            'resume.txt',
            SimpleUploadedFile('resume.txt', b'private document'),
        )

    def test_owner_can_download_private_document(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse(
            'private_facilitator_document',
            args=[self.teacher.pk, 'cv'],
        ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store, no-cache, max-age=0')
        self.assertEqual(b''.join(response.streaming_content), b'private document')
        response.close()

    def test_unrelated_user_cannot_download_private_document(self):
        unrelated = User.objects.create_user(
            username='unrelated-user',
            password='test-password',
        )
        self.client.force_login(unrelated)

        response = self.client.get(reverse(
            'private_facilitator_document',
            args=[self.teacher.pk, 'cv'],
        ))

        self.assertEqual(response.status_code, 404)

    def test_legacy_media_url_does_not_expose_document(self):
        unrelated = User.objects.create_user(
            username='legacy-url-unrelated-user',
            password='test-password',
        )
        self.client.force_login(unrelated)

        response = self.client.get(reverse(
            'protect_legacy_document',
            kwargs={'relative_path': 'cv/resume.txt'},
        ))

        self.assertEqual(response.status_code, 404)


class HODPortalAccessTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.programme = SimpleNamespace(pk=12, is_active=True)
        self.hod_profile = SimpleNamespace(
            is_active=True,
            programme=self.programme,
            programme_id=self.programme.pk,
        )
        self.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            hod_profile=self.hod_profile,
        )

    def test_hod_role_requires_active_account_profile_and_programme(self):
        self.assertTrue(views.is_hod(self.user))

        inactive_profile = SimpleNamespace(
            is_active=False,
            programme=self.programme,
            programme_id=self.programme.pk,
        )
        self.assertFalse(views.is_hod(SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            hod_profile=inactive_profile,
        )))

        inactive_programme = SimpleNamespace(pk=12, is_active=False)
        self.assertFalse(views.is_hod(SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            hod_profile=SimpleNamespace(
                is_active=True,
                programme=inactive_programme,
                programme_id=12,
            ),
        )))

    def test_non_hod_cannot_open_hod_dashboard(self):
        request = self.factory.get('/hod/dashboard/')
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
        )

        response = views.hod_dashboard(request)

        self.assertEqual(response.status_code, 302)

    def test_hod_urls_resolve_to_separate_portal_views(self):
        self.assertEqual(reverse('hod_login'), '/hod-portal/')
        self.assertEqual(reverse('hod_dashboard'), '/hod/dashboard/')
        self.assertEqual(reverse('hod_facilitators'), '/hod/facilitators/')
        self.assertEqual(
            reverse('hod_facilitator_manage', args=[4]),
            '/hod/facilitators/4/manage/',
        )

    def test_hod_login_authenticates_only_hod_accounts(self):
        request = self.factory.post(
            '/hod-portal/',
            {'username': 'hod-user', 'password': 'secret'},
        )
        request.user = SimpleNamespace(is_authenticated=False)
        with patch.object(views, 'authenticate', return_value=self.user), patch.object(
            views, 'login'
        ) as login_user:
            response = views.hod_login(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('hod_dashboard'))
        login_user.assert_called_once_with(request, self.user)

    def test_hod_facilitator_query_is_limited_to_assigned_programme(self):
        queryset = MagicMock()
        with patch.object(
            views.TeacherProfile.objects,
            'filter',
            return_value=queryset,
        ) as filter_facilitators:
            views._hod_teacher_queryset(self.programme)

        programme_filter = filter_facilitators.call_args.args[0]
        self.assertEqual(programme_filter.connector, 'OR')
        self.assertEqual(
            programme_filter.children[0].children,
            [
                ('facilitator_profile__isnull', True),
                ('programme', self.programme),
            ],
        )
        self.assertEqual(
            programme_filter.children[1],
            ('facilitator_profile__programme', self.programme),
        )

    def test_manipulated_teacher_id_is_rejected_outside_programme_scope(self):
        request = self.factory.post('/hod/facilitators/99/manage/', {})
        request.user = self.user
        scoped_queryset = object()

        with patch.object(
            views,
            '_hod_teacher_queryset',
            return_value=scoped_queryset,
        ), patch.object(
            views,
            'get_object_or_404',
            side_effect=Http404,
        ) as get_scoped_teacher:
            with self.assertRaises(Http404):
                views.hod_facilitator_manage(request, teacher_id=99)

        get_scoped_teacher.assert_called_once_with(scoped_queryset, pk=99)

    def test_hod_status_action_rejects_get(self):
        request = self.factory.get('/hod/facilitators/4/status/')
        request.user = self.user

        response = views.hod_facilitator_status(request, teacher_id=4)

        self.assertEqual(response.status_code, 405)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class HODProgrammeManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='portal-admin',
            password='test-password',
            is_staff=True,
        )
        AdminProfile.objects.create(user=self.admin, role='admin')
        self.client.force_login(self.admin)
        self.programme = Programme.objects.create(
            name='Information Technology',
            code='IT',
        )
        self.other_programme = Programme.objects.create(
            name='Welding',
            code='WELD',
        )

    def hod_payload(self, username, programme, **overrides):
        payload = {
            'username': username,
            'first_name': 'Test',
            'last_name': 'HOD',
            'email': f'{username}@example.edu',
            'phone': '0200000000',
            'programme': str(programme.pk) if programme else '',
            'is_active': 'on',
        }
        payload.update(overrides)
        return payload

    def make_teacher(self, username, staff_id, programme):
        user = User.objects.create_user(
            username=username,
            password='existing-password',
            first_name='Legacy',
            last_name='Facilitator',
        )
        return TeacherProfile.objects.create(
            user=user,
            staff_id=staff_id,
            phone='0200000000',
            gender='male',
            address='',
            qualification='degree',
            specialization='',
            course_type='core',
            preferred_subject='ict',
            programme=programme,
            is_approved=True,
            profile_complete=True,
        )

    def test_hod_creation_assigns_programme_and_sends_setup_link(self):
        response = self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('it-hod', self.programme),
        )

        self.assertRedirects(response, reverse('admin_hods'))
        hod = HODProfile.objects.get(user__username='it-hod')
        self.assertEqual(hod.programme, self.programme)
        self.assertTrue(hod.is_active)
        self.assertEqual(hod.user.email, 'it-hod@example.edu')
        self.assertEqual(
            hod.staff_id,
            f'WTI-HOD-IT/{timezone.localdate().year}/01',
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Test HOD', mail.outbox[0].body)
        self.assertIn(hod.staff_id, mail.outbox[0].body)
        self.assertIn('it-hod', mail.outbox[0].body)
        self.assertIn('Information Technology (IT)', mail.outbox[0].body)
        self.assertIn('http://testserver/hod-portal/', mail.outbox[0].body)
        self.assertIn('/accounts/staff-setup/', mail.outbox[0].body)
        self.assertIn('it-hod', mail.outbox[0].body)
        self.assertFalse(hod.user.has_usable_password())
        token_in_body = mail.outbox[0].body.split('/accounts/staff-setup/', 1)[1].split()[0]
        token = token_in_body.split('/', 1)[0]
        onboarding = StaffOnboardingToken.objects.get(user=hod.user)
        self.assertNotEqual(onboarding.token_hash, token)
        self.assertIn('Set up your Winnetech account', mail.outbox[0].subject)

    def test_programme_creation_normalizes_code(self):
        response = self.client.post(
            reverse('admin_programme_create'),
            {
                'name': 'New Generics Programme',
                'code': '  gen-01 ',
                'category': 'GENERICS',
                'description': 'General programme',
                'is_active': 'on',
            },
        )

        self.assertRedirects(response, reverse('admin_programmes'))
        programme = Programme.objects.get(name='New Generics Programme')
        self.assertEqual(programme.code, 'GEN-01')
        self.assertEqual(programme.category, 'GENERICS')

    def test_programme_creation_rejects_duplicate_normalized_code(self):
        response = self.client.post(
            reverse('admin_programme_create'),
            {
                'name': 'Duplicate Programme',
                'code': ' it ',
                'category': 'DEPARTMENTS',
                'is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A programme with this code already exists.')
        self.assertFalse(Programme.objects.filter(name='Duplicate Programme').exists())

    def test_hod_cannot_edit_programme(self):
        hod_user = User.objects.create_user(
            username='programme-viewer',
            password='test-password',
        )
        HODProfile.objects.create(user=hod_user, programme=self.programme)
        self.client.force_login(hod_user)

        response = self.client.post(
            reverse('admin_programme_manage', args=[self.programme.pk]),
            {
                'name': 'Changed Programme',
                'code': 'CHANGED',
                'category': 'DEPARTMENTS',
                'is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Programme.objects.get(pk=self.programme.pk).name,
            'Information Technology',
        )

    def test_staff_id_sequence_increments_per_programme_and_year(self):
        with patch('core.models.timezone.localdate', return_value=date(2026, 4, 1)):
            first_user = User.objects.create_user(username='it-hod-one')
            first = HODProfile.objects.create(
                user=first_user,
                programme=self.programme,
            )
            first.programme = None
            first.save(update_fields=['programme'])
            second_user = User.objects.create_user(username='it-hod-two')
            second = HODProfile.objects.create(
                user=second_user,
                programme=self.programme,
            )
            weld_user = User.objects.create_user(username='weld-hod')
            weld = HODProfile.objects.create(
                user=weld_user,
                programme=self.other_programme,
            )

        self.assertEqual(first.staff_id, 'WTI-HOD-IT/2026/01')
        self.assertEqual(second.staff_id, 'WTI-HOD-IT/2026/02')
        self.assertEqual(weld.staff_id, 'WTI-HOD-WELD/2026/01')

    def test_hod_staff_id_skips_id_already_used_by_facilitator(self):
        year = timezone.localdate().year
        self.make_teacher(
            'legacy-hod-id-collision',
            f'WTI-HOD-IT/{year}/01',
            self.programme,
        )

        staff_id = generate_hod_staff_id(self.programme.pk)

        self.assertEqual(staff_id, f'WTI-HOD-IT/{year}/02')

    def test_registered_staff_id_skips_existing_hod_id_and_username(self):
        year = timezone.localdate().year
        hod_user = User.objects.create_user(username='registered-id-hod')
        HODProfile.objects.create(
            user=hod_user,
            programme=self.programme,
            staff_id=f'WTI-IT/{year}-01',
        )
        User.objects.create_user(username=f'WTI-ADM/{year}-01')

        facilitator_id = generate_registered_staff_id('FACILITATOR', 'IT')
        admin_id = generate_registered_staff_id('ADMIN', 'ADM')

        self.assertEqual(facilitator_id, f'WTI-IT/{year}-02')
        self.assertEqual(admin_id, f'WTI-ADM/{year}-02')

    def test_legacy_staff_id_generator_skips_other_role_ids(self):
        User.objects.create_user(username='WTI0001')
        User.objects.create_user(username='WTI0002')
        hod_user = User.objects.create_user(username='legacy-generator-hod')
        HODProfile.objects.create(
            user=hod_user,
            programme=self.programme,
            staff_id='WTI0003',
        )

        self.assertEqual(generate_unique_staff_id(), 'WTI0004')

    def test_competing_allocation_retries_after_unique_conflict(self):
        original_create = QuerySet.create
        attempts = []

        def competing_create(queryset, **kwargs):
            attempts.append(kwargs['staff_id'])
            if len(attempts) == 1:
                raise IntegrityError('simulated concurrent unique conflict')
            return original_create(queryset, **kwargs)

        with patch.object(QuerySet, 'create', new=competing_create):
            staff_id = generate_hod_staff_id(self.programme.pk)

        self.assertEqual(staff_id, f'WTI-HOD-IT/{timezone.localdate().year}/01')
        self.assertEqual(attempts, [staff_id, staff_id])
        self.assertEqual(
            HODStaffIDRegistry.objects.filter(staff_id=staff_id).count(),
            1,
        )

    def test_sqlite_lock_retries_allocation(self):
        original_create = QuerySet.create
        attempts = []

        def locked_create(queryset, **kwargs):
            attempts.append(kwargs['staff_id'])
            if len(attempts) == 1:
                raise OperationalError('database is locked')
            return original_create(queryset, **kwargs)

        with patch.object(QuerySet, 'create', new=locked_create):
            staff_id = generate_hod_staff_id(self.programme.pk)

        self.assertEqual(staff_id, f'WTI-HOD-IT/{timezone.localdate().year}/01')
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            HODStaffIDRegistry.objects.filter(staff_id=staff_id).count(),
            1,
        )

    def test_invalid_programme_code_is_rejected_without_creating_hod(self):
        invalid_codes = ('IT/TECH', 'IT TECH', 'IT.TECH')
        for index, code in enumerate(invalid_codes):
            with self.subTest(code=code):
                invalid_programme = Programme.objects.create(
                    name=f'Invalid Code Programme {index}',
                    code=f'TEMP-{index}',
                )
                Programme.objects.filter(pk=invalid_programme.pk).update(
                    code=code,
                )
                invalid_programme.refresh_from_db()
                username = f'invalid-code-hod-{index}'
                response = self.client.post(
                    reverse('admin_hod_create'),
                    self.hod_payload(username, invalid_programme),
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    'Programme code must contain only letters',
                )
                self.assertFalse(User.objects.filter(username=username).exists())
                self.assertFalse(
                    HODStaffIDRegistry.objects.filter(
                        programme_code=code,
                    ).exists()
                )

    def test_staff_id_migration_backfill_rejects_invalid_programme_code(self):
        programme = Programme.objects.create(
            name='Invalid Backfill Code',
            code='BAD-CODE',
        )
        Programme.objects.filter(pk=programme.pk).update(code='BAD CODE')
        programme.refresh_from_db()
        user = User.objects.create_user(username='invalid-backfill-hod')
        hod = HODProfile.objects.create(user=user)
        HODProfile.objects.filter(pk=hod.pk).update(programme=programme)
        migration = import_module(
            'core.migrations.0012_hodprofile_staff_id'
        )
        schema_editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))

        with self.assertRaisesRegex(
            RuntimeError,
            'Invalid Backfill Code.*BAD CODE',
        ):
            migration.populate_hod_staff_ids(apps, schema_editor)

        hod.refresh_from_db()
        self.assertIsNone(hod.staff_id)

    def test_staff_id_migration_backfill_preserves_nonempty_existing_ids(self):
        user = User.objects.create_user(username='existing-id-migration-hod')
        hod = HODProfile.objects.create(
            user=user,
            staff_id='WTI-HOD-LEGACY/2025/07',
        )
        migration = import_module(
            'core.migrations.0012_hodprofile_staff_id'
        )
        schema_editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))

        migration.populate_hod_staff_ids(apps, schema_editor)

        hod.refresh_from_db()
        self.assertEqual(hod.staff_id, 'WTI-HOD-LEGACY/2025/07')

    def test_new_calendar_year_starts_sequence_at_one(self):
        first_user = User.objects.create_user(username='it-hod-2026')
        with patch('core.models.timezone.localdate', return_value=date(2026, 12, 31)):
            first = HODProfile.objects.create(
                user=first_user,
                programme=self.programme,
            )
        first.programme = None
        first.save(update_fields=['programme'])
        second_user = User.objects.create_user(username='it-hod-2027')
        with patch('core.models.timezone.localdate', return_value=date(2027, 1, 1)):
            second = HODProfile.objects.create(
                user=second_user,
                programme=self.programme,
            )

        self.assertEqual(first.staff_id, 'WTI-HOD-IT/2026/01')
        self.assertEqual(second.staff_id, 'WTI-HOD-IT/2027/01')

    def test_deactivated_hod_staff_id_is_not_reused(self):
        first_response = self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('former-it-hod', self.programme),
        )
        self.assertRedirects(first_response, reverse('admin_hods'))
        first = HODProfile.objects.get(user__username='former-it-hod')

        self.client.post(
            reverse('admin_hod_deactivate', args=[first.pk]),
        )
        second_response = self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('current-it-hod', self.programme),
        )

        self.assertRedirects(second_response, reverse('admin_hods'))
        second = HODProfile.objects.get(user__username='current-it-hod')
        year = timezone.localdate().year
        self.assertEqual(first.staff_id, f'WTI-HOD-IT/{year}/01')
        self.assertEqual(second.staff_id, f'WTI-HOD-IT/{year}/02')

    def test_reassignment_does_not_change_existing_staff_id(self):
        user = User.objects.create_user(
            username='it-hod',
            password='existing-password',
            email='it-hod@example.edu',
        )
        hod = HODProfile.objects.create(user=user, programme=self.programme)
        original_staff_id = hod.staff_id
        payload = self.hod_payload(
            user.username,
            self.other_programme,
            email=user.email,
        )

        response = self.client.post(
            reverse('admin_hod_manage', args=[hod.pk]),
            payload,
        )

        self.assertRedirects(response, reverse('admin_hods'))
        hod.refresh_from_db()
        self.assertEqual(hod.staff_id, original_staff_id)

    def test_issued_staff_id_stays_reserved_after_hod_profile_deletion(self):
        first_user = User.objects.create_user(username='deleted-profile-hod')
        first = HODProfile.objects.create(
            user=first_user,
            programme=self.programme,
        )
        original_staff_id = first.staff_id
        registry_entry = HODStaffIDRegistry.objects.get(staff_id=original_staff_id)

        first.delete()

        registry_entry.refresh_from_db()
        self.assertIsNone(registry_entry.hod_profile_id)
        next_user = User.objects.create_user(username='replacement-profile-hod')
        next_hod = HODProfile.objects.create(
            user=next_user,
            programme=self.programme,
        )
        self.assertNotEqual(next_hod.staff_id, original_staff_id)
        self.assertEqual(
            next_hod.staff_id,
            f'WTI-HOD-IT/{timezone.localdate().year}/02',
        )

    def test_existing_staff_id_is_not_changed_on_profile_save(self):
        user = User.objects.create_user(username='immutable-hod-id')
        hod = HODProfile.objects.create(
            user=user,
            programme=self.programme,
        )
        original_staff_id = hod.staff_id

        hod.staff_id = 'browser-supplied-id'
        hod.save(update_fields=['staff_id'])
        hod.refresh_from_db()

        self.assertEqual(hod.staff_id, original_staff_id)

    def test_direct_hod_profile_creation_reserves_supplied_staff_id(self):
        user = User.objects.create_user(username='direct-hod-id')

        hod = HODProfile.objects.create(
            user=user,
            staff_id='WTI-HOD-CUSTOM/2026/99',
        )

        registry = HODStaffIDRegistry.objects.get(
            staff_id='WTI-HOD-CUSTOM/2026/99',
        )
        self.assertEqual(registry.hod_profile_id, hod.pk)

    def test_direct_hod_creation_rejects_previously_reserved_staff_id(self):
        user = User.objects.create_user(username='reserved-hod-id')
        hod = HODProfile.objects.create(
            user=user,
            staff_id='WTI-HOD-IT/2026/77',
        )
        registry = HODStaffIDRegistry.objects.get(staff_id=hod.staff_id)
        other_user = User.objects.create_user(username='duplicate-direct-hod-id')

        with self.assertRaises(IntegrityError):
            HODProfile.objects.create(
                user=other_user,
                staff_id=hod.staff_id,
            )

        registry.refresh_from_db()
        self.assertEqual(registry.hod_profile_id, hod.pk)
        self.assertEqual(
            HODProfile.objects.filter(staff_id=hod.staff_id).count(),
            1,
        )

    def test_failed_profile_creation_keeps_new_registry_reservation(self):
        staff_id = 'WTI-HOD-IT/2026/88'

        with self.assertRaises(IntegrityError):
            HODProfile.objects.create(staff_id=staff_id)

        self.assertTrue(
            HODStaffIDRegistry.objects.filter(staff_id=staff_id).exists()
        )

    def test_existing_registry_record_survives_failed_duplicate_creation(self):
        user = User.objects.create_user(username='stable-registry-hod')
        hod = HODProfile.objects.create(
            user=user,
            staff_id='WTI-HOD-IT/2026/89',
        )
        original_registry = HODStaffIDRegistry.objects.get(staff_id=hod.staff_id)
        other_user = User.objects.create_user(username='failed-duplicate-hod')

        with self.assertRaises(IntegrityError):
            HODProfile.objects.create(
                user=other_user,
                staff_id=hod.staff_id,
            )

        original_registry.refresh_from_db()
        self.assertEqual(original_registry.hod_profile_id, hod.pk)

    def test_superadmin_can_create_hod_with_reserved_staff_id(self):
        superadmin = User.objects.create_superuser(
            username='hod-creation-superadmin',
            email='superadmin@example.edu',
            password='test-password',
        )
        self.client.force_login(superadmin)

        response = self.client.post(
            reverse('superadmin_hod_create'),
            self.hod_payload('superadmin-created-hod', self.programme),
        )

        self.assertRedirects(response, reverse('superadmin_hods'))
        hod = HODProfile.objects.get(
            user__username='superadmin-created-hod',
        )
        self.assertTrue(hod.staff_id)
        self.assertTrue(
            HODStaffIDRegistry.objects.filter(
                staff_id=hod.staff_id,
                hod_profile=hod,
            ).exists()
        )

    def test_duplicate_staff_ids_are_rejected(self):
        first_user = User.objects.create_user(username='duplicate-one')
        first = HODProfile.objects.create(
            user=first_user,
            programme=self.programme,
        )
        second_user = User.objects.create_user(username='duplicate-two')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HODProfile.objects.create(
                    user=second_user,
                    programme=self.other_programme,
                    staff_id=first.staff_id,
                )

    def test_failed_onboarding_email_keeps_created_account(self):
        with patch('core.views.send_mail', side_effect=OSError('mail unavailable')):
            response = self.client.post(
                reverse('admin_hod_create'),
                self.hod_payload('mail-failure-hod', self.programme),
                follow=True,
            )

        hod = HODProfile.objects.get(user__username='mail-failure-hod')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'password setup email could not be delivered')
        self.assertTrue(User.objects.filter(pk=hod.user_id).exists())
        self.assertTrue(hod.staff_id)
        self.assertFalse(hod.user.has_usable_password())
        self.assertTrue(
            StaffOnboardingToken.objects.filter(
                user=hod.user,
                delivery_failed_at__isnull=False,
            ).exists()
        )

    def test_resending_setup_email_does_not_create_another_account(self):
        self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('resend-hod', self.programme),
        )
        hod = HODProfile.objects.get(user__username='resend-hod')
        original_staff_id = hod.staff_id
        initial_user_count = User.objects.count()

        response = self.client.post(
            reverse('admin_hod_manage', args=[hod.pk]),
            {'action': 'send_setup_link'},
        )

        self.assertRedirects(
            response,
            reverse('admin_hod_manage', args=[hod.pk]),
        )
        self.assertEqual(HODProfile.objects.filter(user__username='resend-hod').count(), 1)
        self.assertEqual(User.objects.count(), initial_user_count)
        hod.refresh_from_db()
        self.assertEqual(hod.staff_id, original_staff_id)
        self.assertEqual(len(mail.outbox), 2)

    def test_setup_link_does_not_reset_an_established_hod_password(self):
        self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('established-password-hod', self.programme),
        )
        hod = HODProfile.objects.get(user__username='established-password-hod')
        onboarding = StaffOnboardingToken.objects.get(user=hod.user)
        original_token_hash = onboarding.token_hash
        hod.user.set_password('Existing-HOD-Password-2026!')
        hod.user.save(update_fields=['password'])

        response = self.client.post(
            reverse('admin_hod_manage', args=[hod.pk]),
            {'action': 'send_setup_link'},
        )

        self.assertRedirects(
            response,
            reverse('admin_hod_manage', args=[hod.pk]),
        )
        self.assertEqual(len(mail.outbox), 1)
        onboarding.refresh_from_db()
        self.assertEqual(onboarding.token_hash, original_token_hash)
        setup_response = self.client.post(
            reverse('staff_password_setup'),
            {
                'token': next(
                    line for line in mail.outbox[0].body.splitlines()
                    if '/accounts/staff-setup/' in line
                ).split('#', 1)[1],
                'new_password1': 'Unexpected-Password-2026!',
                'new_password2': 'Unexpected-Password-2026!',
            },
        )
        self.assertEqual(setup_response.status_code, 400)
        hod.user.refresh_from_db()
        self.assertTrue(hod.user.check_password('Existing-HOD-Password-2026!'))

    @override_settings(STAFF_ONBOARDING_TIMEOUT=90)
    def test_onboarding_token_uses_configured_timeout(self):
        self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('configured-timeout-hod', self.programme),
        )
        hod = HODProfile.objects.get(user__username='configured-timeout-hod')
        onboarding = StaffOnboardingToken.objects.get(user=hod.user)
        timeout = (onboarding.expires_at - onboarding.created_at).total_seconds()

        self.assertGreaterEqual(timeout, 85)
        self.assertLessEqual(timeout, 90)

    def test_account_email_is_unique_case_insensitively_in_database(self):
        User.objects.create_user(
            username='email-case-first',
            email='CaseSensitive@example.edu',
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username='email-case-second',
                    email='casesensitive@example.edu',
                )

    def test_admin_facilitator_creation_sets_both_programme_fields(self):
        response = self.client.post(
            reverse('admin_create_teacher'),
            {
                'first_name': 'New',
                'last_name': 'Facilitator',
                'email': 'new-facilitator@example.edu',
                'phone': '0200000000',
                'gender': 'male',
                'qualification': 'degree',
                'course_type': 'core',
                'programme': str(self.programme.pk),
                'employment_type': 'PERMANENT',
                'preferred_subject': 'mathematics',
                'department': '',
                'emergency_contact': '',
                'emergency_phone': '',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['error'])
        teacher = TeacherProfile.objects.get(staff_id='WTI-IT/2026-01')
        self.assertEqual(teacher.programme, self.programme)
        self.assertEqual(
            teacher.facilitator_profile.programme,
            self.programme,
        )
        self.assertEqual(teacher.user.email, 'new-facilitator@example.edu')
        self.assertFalse(teacher.user.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            StaffIDRegistry.objects.filter(
                staff_id=teacher.staff_id,
                user=teacher.user,
                role='FACILITATOR',
            ).exists()
        )

    def test_facilitator_staff_ids_are_generated_sequentially(self):
        staff_ids = []
        for index in range(2):
            response = self.client.post(
                reverse('admin_create_teacher'),
                {
                    'first_name': f'Facilitator{index}',
                    'last_name': 'Sequence',
                    'email': f'facilitator-sequence-{index}@example.edu',
                    'phone': '0200000000',
                    'gender': 'male',
                    'qualification': 'degree',
                    'course_type': 'core',
                    'programme': str(self.programme.pk),
                    'employment_type': 'PERMANENT',
                    'preferred_subject': 'mathematics',
                    'department': '',
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.context['error'])
            staff_ids.append(
                TeacherProfile.objects.get(
                    user__email=f'facilitator-sequence-{index}@example.edu',
                ).staff_id
            )
        year = timezone.localdate().year
        self.assertEqual(
            staff_ids,
            [f'WTI-IT/{year}-01', f'WTI-IT/{year}-02'],
        )

    def test_inactive_programmes_are_not_offered_for_facilitator_creation(self):
        inactive = Programme.objects.create(
            name='Inactive Programme',
            code='INACTIVE',
            is_active=False,
        )
        response = self.client.get(reverse('admin_create_teacher'))
        programme_queryset = response.context['form'].fields['programme'].queryset
        self.assertIn(self.programme, programme_queryset)
        self.assertNotIn(inactive, programme_queryset)

    def test_facilitator_type_is_required(self):
        response = self.client.post(
            reverse('admin_create_teacher'),
            {
                'first_name': 'Permanent',
                'last_name': 'Candidate',
                'email': 'permanent-candidate@example.edu',
                'phone': '0200000000',
                'gender': 'male',
                'qualification': 'degree',
                'course_type': 'core',
                'programme': str(self.programme.pk),
                'preferred_subject': 'mathematics',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required.')
        self.assertFalse(User.objects.filter(email='permanent-candidate@example.edu').exists())

    def test_internship_creation_requires_same_programme_supervisor_and_creates_history(self):
        supervisor_teacher = self.make_teacher(
            'active-permanent-profile',
            'WTI9201',
            self.programme,
        )
        supervisor = FacilitatorProfile.objects.create(
            teacher=supervisor_teacher,
            programme=self.programme,
            employment_type='PERMANENT',
        )
        start_date = timezone.localdate()
        response = self.client.post(
            reverse('admin_create_teacher'),
            {
                'first_name': 'Intern',
                'last_name': 'Facilitator',
                'email': 'intern@example.edu',
                'phone': '0200000001',
                'gender': 'female',
                'qualification': 'hnd',
                'programme': str(self.programme.pk),
                'employment_type': 'INTERNSHIP',
                'internship_start_date': start_date.isoformat(),
                'supervisor': str(supervisor.pk),
                'course_type': 'core',
                'preferred_subject': 'ict',
                'department': '',
                'emergency_contact': '',
                'emergency_phone': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['error'])
        intern = FacilitatorProfile.objects.get(
            teacher__user__email='intern@example.edu',
        )
        self.assertEqual(intern.employment_type, 'INTERNSHIP')
        self.assertEqual(intern.programme, self.programme)
        self.assertEqual(intern.supervisor, supervisor)
        self.assertEqual(intern.internship_end_date, add_years(start_date, 1))
        self.assertEqual(
            InternshipSupervision.objects.filter(
                internship=intern,
                supervisor=supervisor,
                is_active=True,
            ).count(),
            1,
        )

    def test_internship_rejects_supervisor_from_another_programme(self):
        supervisor_teacher = self.make_teacher(
            'other-programme-supervisor',
            'WTI9204',
            self.programme,
        )
        supervisor = FacilitatorProfile.objects.create(
            teacher=supervisor_teacher,
            programme=self.programme,
            employment_type='PERMANENT',
        )
        response = self.client.post(
            reverse('admin_create_teacher'),
            {
                'first_name': 'Cross',
                'last_name': 'Programme',
                'email': 'cross-programme@example.edu',
                'phone': '0200000002',
                'gender': 'female',
                'qualification': 'degree',
                'programme': str(self.other_programme.pk),
                'employment_type': 'INTERNSHIP',
                'internship_start_date': timezone.localdate().isoformat(),
                'supervisor': str(supervisor.pk),
                'course_type': 'core',
                'preferred_subject': 'ict',
                'department': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('supervisor', response.context['form'].errors)
        self.assertFalse(
            User.objects.filter(email='cross-programme@example.edu').exists()
        )

    def test_programme_supervisor_endpoint_only_returns_active_permanent_people(self):
        eligible_teacher = self.make_teacher(
            'eligible-supervisor',
            'WTI9202',
            self.programme,
        )
        eligible = FacilitatorProfile.objects.create(
            teacher=eligible_teacher,
            programme=self.programme,
            employment_type='PERMANENT',
        )
        inactive_teacher = self.make_teacher(
            'inactive-supervisor',
            'WTI9203',
            self.programme,
        )
        FacilitatorProfile.objects.create(
            teacher=inactive_teacher,
            programme=self.programme,
            employment_type='PERMANENT',
            is_active=False,
        )
        hod_user = User.objects.create_user(
            username='supervision-programme-hod',
            password='test-password',
            first_name='Programme',
            last_name='HOD',
        )
        HODProfile.objects.create(
            user=hod_user,
            programme=self.programme,
            is_active=True,
        )
        response = self.client.get(
            reverse('programme_supervisors', args=[self.programme.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['hod']['name'], 'Programme HOD')
        self.assertEqual(
            [entry['id'] for entry in response.json()['supervisors']],
            [eligible.pk],
        )

    def test_password_setup_token_is_single_use_and_sets_password(self):
        self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('password-setup-hod', self.programme),
        )
        hod = HODProfile.objects.get(user__username='password-setup-hod')
        setup_url = next(
            line for line in mail.outbox[-1].body.splitlines()
            if '/accounts/staff-setup/' in line
        )
        token = urlsplit(setup_url).fragment
        onboarding = StaffOnboardingToken.objects.get(user=hod.user)
        self.assertNotIn(token, onboarding.token_hash)
        setup_page = self.client.get(reverse('staff_password_setup'))
        self.assertEqual(setup_page.status_code, 200)
        self.assertIn('no-store', setup_page['Cache-Control'])
        self.assertEqual(setup_page['Referrer-Policy'], 'no-referrer')
        self.assertNotContains(setup_page, token)

        response = self.client.post(
            reverse('staff_password_setup'),
            {
                'token': token,
                'new_password1': 'Secure-Winnetech-2026!Setup',
                'new_password2': 'Secure-Winnetech-2026!Setup',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your account is ready')
        hod.user.refresh_from_db()
        onboarding.refresh_from_db()
        self.assertTrue(hod.user.has_usable_password())
        self.assertIsNotNone(onboarding.used_at)
        self.assertEqual(
            self.client.post(
                reverse('staff_password_setup'),
                {'token': token},
            ).status_code,
            400,
        )

    def test_expired_password_setup_token_is_rejected(self):
        self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('expired-setup-hod', self.programme),
        )
        setup_url = next(
            line for line in mail.outbox[-1].body.splitlines()
            if '/accounts/staff-setup/' in line
        )
        token = urlsplit(setup_url).fragment
        onboarding = StaffOnboardingToken.objects.get(
            user__username='expired-setup-hod',
        )
        onboarding.expires_at = timezone.now() - timedelta(seconds=1)
        onboarding.save(update_fields=['expires_at'])
        self.assertEqual(
            self.client.post(
                reverse('staff_password_setup'),
                {'token': token},
            ).status_code,
            400,
        )

    def test_password_setup_does_not_reactivate_deactivated_hod(self):
        self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('deactivated-before-setup-hod', self.programme),
        )
        hod = HODProfile.objects.get(
            user__username='deactivated-before-setup-hod',
        )
        setup_url = next(
            line for line in mail.outbox[-1].body.splitlines()
            if '/accounts/staff-setup/' in line
        )
        token = urlsplit(setup_url).fragment
        hod.is_active = False
        hod.save(update_fields=['is_active', 'updated_at'])
        response = self.client.post(
            reverse('staff_password_setup'),
            {
                'token': token,
                'new_password1': 'Secure-Winnetech-2026!Setup',
                'new_password2': 'Secure-Winnetech-2026!Setup',
            },
        )
        self.assertEqual(response.status_code, 400)
        hod.user.refresh_from_db()
        self.assertFalse(hod.user.has_usable_password())

    def test_superadmin_creates_admin_with_generated_id_and_pending_password(self):
        superadmin = User.objects.create_superuser(
            username='onboarding-superadmin',
            email='superadmin@example.edu',
            password='test-password',
        )
        self.client.force_login(superadmin)
        response = self.client.post(
            reverse('superadmin_create_admin'),
            {
                'first_name': 'New',
                'last_name': 'Administrator',
                'email': 'new-admin@example.edu',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['error'])
        new_admin = User.objects.get(email='new-admin@example.edu')
        self.assertEqual(
            new_admin.username,
            f'WTI-ADM/{timezone.localdate().year}-01',
        )
        self.assertTrue(new_admin.is_staff)
        self.assertFalse(new_admin.is_superuser)
        self.assertFalse(new_admin.has_usable_password())
        self.assertEqual(new_admin.admin_profile.role, 'admin')
        self.assertIn('/admin-portal/', mail.outbox[-1].body)

    def test_ordinary_admin_cannot_create_admin_accounts(self):
        initial_user_count = User.objects.count()
        response = self.client.get(reverse('superadmin_create_admin'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.count(), initial_user_count)

    def test_failed_facilitator_email_can_be_retried_without_duplicate_account(self):
        payload = {
            'first_name': 'Retry',
            'last_name': 'Facilitator',
            'email': 'retry-facilitator@example.edu',
            'phone': '0200000000',
            'gender': 'male',
            'qualification': 'degree',
            'course_type': 'core',
            'programme': str(self.programme.pk),
            'employment_type': 'PERMANENT',
            'preferred_subject': 'mathematics',
            'department': '',
        }
        with patch('core.views.send_mail', side_effect=[OSError('mail unavailable'), 1]):
            response = self.client.post(reverse('admin_create_teacher'), payload)
            self.assertContains(response, 'password setup email could not be delivered')
            user = User.objects.get(email='retry-facilitator@example.edu')
            initial_user_count = User.objects.count()
            previous_hash = user.staff_onboarding.token_hash
            retry_response = self.client.post(
                reverse('retry_staff_onboarding', args=[user.pk]),
            )

        self.assertRedirects(retry_response, reverse('admin_facilitator_records'))
        self.assertEqual(User.objects.count(), initial_user_count)
        user.refresh_from_db()
        self.assertNotEqual(user.staff_onboarding.token_hash, previous_hash)
        self.assertFalse(user.has_usable_password())

    def test_deactivation_preserves_user_and_releases_programme(self):
        user = User.objects.create_user(
            username='it-hod',
            password='existing-password',
            email='it-hod@example.edu',
        )
        hod = HODProfile.objects.create(
            user=user,
            programme=self.programme,
            is_active=True,
        )

        response = self.client.post(reverse('admin_hod_deactivate', args=[hod.pk]))

        self.assertRedirects(response, reverse('admin_hods'))
        hod.refresh_from_db()
        self.assertFalse(hod.is_active)
        self.assertIsNone(hod.programme)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertIsNone(self.programme.active_hod)

    def test_hod_programme_reassignment_releases_previous_assignment(self):
        user = User.objects.create_user(
            username='it-hod',
            password='existing-password',
            email='it-hod@example.edu',
        )
        hod = HODProfile.objects.create(
            user=user,
            programme=self.programme,
            is_active=True,
        )
        payload = self.hod_payload(
            user.username,
            self.other_programme,
            email=user.email,
        )

        response = self.client.post(
            reverse('admin_hod_manage', args=[hod.pk]),
            payload,
        )

        self.assertRedirects(response, reverse('admin_hods'))
        hod.refresh_from_db()
        self.assertEqual(hod.programme, self.other_programme)
        self.assertIsNone(self.programme.active_hod)
        self.assertEqual(self.other_programme.active_hod, hod)

    def test_second_active_hod_cannot_take_an_assigned_programme(self):
        first_user = User.objects.create_user(
            username='first-hod',
            password='existing-password',
            email='first@example.edu',
        )
        HODProfile.objects.create(
            user=first_user,
            programme=self.programme,
            is_active=True,
        )

        response = self.client.post(
            reverse('admin_hod_create'),
            self.hod_payload('second-hod', self.programme),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already has an active HOD')
        self.assertEqual(
            HODProfile.objects.filter(programme=self.programme, is_active=True).count(),
            1,
        )
        self.assertFalse(User.objects.filter(username='second-hod').exists())

    def test_hod_cannot_open_facilitator_from_another_programme(self):
        hod_user = User.objects.create_user(
            username='it-hod',
            password='existing-password',
        )
        HODProfile.objects.create(
            user=hod_user,
            programme=self.programme,
            is_active=True,
        )
        other_teacher = self.make_teacher(
            'weld-facilitator',
            'WTI-WELD/2026-01',
            self.other_programme,
        )
        FacilitatorProfile.objects.create(
            teacher=other_teacher,
            programme=self.other_programme,
        )
        self.client.force_login(hod_user)

        response = self.client.get(
            reverse('hod_facilitator_manage', args=[other_teacher.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_legacy_teacher_gets_authoritative_profile_on_hod_management(self):
        hod_user = User.objects.create_user(
            username='it-hod',
            password='existing-password',
        )
        HODProfile.objects.create(
            user=hod_user,
            programme=self.programme,
            is_active=True,
        )
        teacher = self.make_teacher(
            'legacy-facilitator',
            'WTI-IT/2026-01',
            self.programme,
        )
        self.client.force_login(hod_user)

        response = self.client.post(
            reverse('hod_facilitator_manage', args=[teacher.pk]),
            {
                'employment_type': 'PERMANENT',
                'internship_start_date': '',
                'supervisor': '',
            },
        )

        self.assertRedirects(response, reverse('hod_facilitators'))
        teacher.refresh_from_db()
        self.assertEqual(teacher.programme, self.programme)
        self.assertEqual(
            teacher.facilitator_profile.programme,
            self.programme,
        )

    def test_legacy_facilitator_without_profile_remains_visible(self):
        hod_user = User.objects.create_user(
            username='it-hod',
            password='existing-password',
        )
        HODProfile.objects.create(
            user=hod_user,
            programme=self.programme,
            is_active=True,
        )
        self.make_teacher(
            'legacy-facilitator',
            'WTI-IT/2026-01',
            self.programme,
        )
        self.client.force_login(hod_user)

        response = self.client.get(reverse('hod_facilitators'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Legacy Facilitator')

    def test_legacy_timetable_course_type_is_displayed_and_preserved(self):
        entry = TimetableEntry.objects.create(
            day='monday',
            subject='Legacy course',
            start_time='08:00',
            end_time='09:00',
            course_type='electives',
        )
        form = TimetableEntryForm(
            {
                'day': 'monday',
                'subject': entry.subject,
                'start_time': '08:00',
                'end_time': '09:00',
                'teacher': '',
                'is_break': '',
                'room': '',
                'course_type': 'electives',
            },
            instance=entry,
        )

        self.assertIn(
            ('electives', 'electives (legacy; preserved)'),
            form.fields['course_type'].choices,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        entry.refresh_from_db()
        self.assertEqual(entry.course_type, 'electives')
        self.assertEqual(entry.get_course_type_display(), 'electives')

        edit_url = reverse('admin_timetable_edit', args=[entry.pk])
        page = self.client.get(edit_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'electives (legacy; preserved)')
        response = self.client.post(
            edit_url,
            {
                'day': 'monday',
                'subject': 'Legacy course updated',
                'start_time': '08:00',
                'end_time': '09:00',
                'teacher': '',
                'is_break': '',
                'room': '',
                'course_type': '',
            },
        )
        self.assertRedirects(response, reverse('admin_timetable'))
        entry.refresh_from_db()
        self.assertEqual(entry.course_type, 'electives')

    def test_existing_admin_and_superadmin_dashboards_remain_accessible(self):
        self.assertEqual(
            self.client.get(reverse('admin_dashboard')).status_code,
            200,
        )
        superadmin = User.objects.create_superuser(
            username='portal-superadmin',
            email='superadmin@example.edu',
            password='test-password',
        )
        self.client.force_login(superadmin)
        self.assertEqual(
            self.client.get(reverse('superadmin_dashboard')).status_code,
            200,
        )

    def test_authorized_admin_can_access_admin_hod_management(self):
        response = self.client.get(reverse('admin_hods'))

        self.assertEqual(response.status_code, 200)

    def test_superadmin_retains_access_through_superadmin_authorization(self):
        superadmin = User.objects.create_superuser(
            username='hod-management-superadmin',
            email='superadmin@example.edu',
            password='test-password',
        )
        self.client.force_login(superadmin)

        self.assertEqual(
            self.client.get(reverse('superadmin_hods')).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('superadmin_hod_create')).status_code,
            200,
        )

    def test_hod_cannot_access_admin_hod_management(self):
        hod_user = User.objects.create_user(
            username='admin-route-hod',
            password='test-password',
            is_staff=True,
        )
        HODProfile.objects.create(
            user=hod_user,
            programme=self.programme,
            is_active=True,
        )
        self.client.force_login(hod_user)

        response = self.client.get(reverse('admin_hods'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_ordinary_staff_cannot_access_admin_hod_management(self):
        ordinary_staff = User.objects.create_user(
            username='ordinary-staff',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(ordinary_staff)

        response = self.client.get(reverse('admin_hods'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_unauthenticated_user_is_redirected_from_admin_hod_management(self):
        self.client.logout()

        response = self.client.get(reverse('admin_hods'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES={
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
)
class CentralRecordsAndSupervisionTests(TestCase):
    def setUp(self):
        self.programme = Programme.objects.create(
            name='Information Technology',
            code='IT',
        )
        self.other_programme = Programme.objects.create(
            name='Welding',
            code='WELD',
        )
        self.admin = User.objects.create_user(
            username='records-admin',
            password='test-password',
            is_staff=True,
        )
        AdminProfile.objects.create(user=self.admin, role='admin')
        self.teacher_sequence = 0
        self.client.force_login(self.admin)

    def create_teacher(self, username, programme, approved=True):
        self.teacher_sequence += 1
        user = User.objects.create_user(
            username=username,
            email=f'{username}@example.edu',
            password='test-password',
            first_name=username.title(),
            last_name='Facilitator',
        )
        return TeacherProfile.objects.create(
            user=user,
            staff_id=f'WTI{self.teacher_sequence:04d}',
            phone='0200000000',
            gender='male',
            address='',
            qualification='degree',
            specialization='',
            course_type='core',
            preferred_subject='ict',
            programme=programme,
            is_approved=approved,
            profile_complete=True,
        )

    def create_facilitator(
        self,
        username,
        programme,
        employment_type='PERMANENT',
        supervisor=None,
        approved=True,
    ):
        teacher = self.create_teacher(username, programme, approved)
        return FacilitatorProfile.objects.create(
            teacher=teacher,
            programme=programme,
            employment_type=employment_type,
            internship_start_date=(
                date(2026, 10, 23)
                if employment_type == 'INTERNSHIP'
                else None
            ),
            supervisor=supervisor,
        )

    def test_admin_and_superadmin_can_view_grouped_facilitator_records(self):
        permanent = self.create_facilitator('permanent-it', self.programme)
        intern = self.create_facilitator(
            'intern-it',
            self.programme,
            employment_type='INTERNSHIP',
            supervisor=permanent,
        )
        InternshipExtension.objects.create(
            internship=intern,
            old_end_date=date(2027, 10, 23),
            new_end_date=date(2027, 12, 23),
            reason='Approved extension',
        )

        response = self.client.get(reverse('admin_facilitator_records'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Permanent Facilitators')
        self.assertContains(response, 'Internship Facilitators')
        self.assertContains(response, 'permanent-it')
        self.assertContains(response, 'intern-it')
        self.assertContains(response, 'Approved extension')
        self.assertContains(response, permanent.full_name)

        superadmin = User.objects.create_superuser(
            username='records-superadmin',
            email='records-superadmin@example.edu',
            password='test-password',
        )
        self.client.force_login(superadmin)
        self.assertEqual(
            self.client.get(reverse('superadmin_facilitator_records')).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('superadmin_create_facilitator')).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('superadmin_admins')).status_code,
            200,
        )

    def test_admin_cannot_access_superadmin_admin_records(self):
        response = self.client.get(reverse('superadmin_admins'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_hod_records_and_detail_are_isolated_to_assigned_programme(self):
        self.create_facilitator('it-facilitator', self.programme)
        other_teacher = self.create_teacher('weld-facilitator', self.other_programme)
        hod_user = User.objects.create_user(
            username='it-records-hod',
            password='test-password',
        )
        HODProfile.objects.create(user=hod_user, programme=self.programme)
        self.client.force_login(hod_user)

        response = self.client.get(reverse('hod_facilitators'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'it-facilitator')
        self.assertNotContains(response, 'weld-facilitator')
        self.assertContains(response, 'Programme oversight')

        self.assertEqual(
            self.client.get(
                reverse('hod_facilitators'),
                {'programme': self.other_programme.pk},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse('facilitator_record_detail', args=[other_teacher.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse('superadmin_admins')).status_code,
            302,
        )

    def test_facilitator_records_require_an_authorized_portal_role(self):
        ordinary_user = User.objects.create_user(
            username='records-ordinary-user',
            password='test-password',
        )
        self.client.force_login(ordinary_user)

        for url_name in (
            'admin_facilitator_records',
            'superadmin_facilitator_records',
            'facilitator_record_detail',
        ):
            with self.subTest(url=url_name):
                url = (
                    reverse(url_name, args=[999])
                    if url_name == 'facilitator_record_detail'
                    else reverse(url_name)
                )
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)

    def test_record_search_and_programme_filter_use_real_records(self):
        self.create_facilitator('searchable-it', self.programme)
        self.create_facilitator('other-welding', self.other_programme)

        response = self.client.get(
            reverse('admin_facilitator_records'),
            {'q': 'searchable', 'programme': self.programme.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'searchable-it')
        self.assertNotContains(response, 'other-welding')

    def test_facilitator_records_pagination_and_status_filter(self):
        for index in range(11):
            self.create_facilitator(
                f'page-facilitator-{index:02d}',
                self.programme,
                approved=index != 10,
            )

        page_two = self.client.get(
            reverse('admin_facilitator_records'),
            {'programme': self.programme.pk, 'page': 2},
        )
        self.assertEqual(page_two.status_code, 200)
        self.assertContains(page_two, 'Page 2 of 2')
        self.assertContains(page_two, 'page-facilitator-10')

        pending = self.client.get(
            reverse('admin_facilitator_records'),
            {'programme': self.programme.pk, 'status': 'pending'},
        )
        self.assertEqual(pending.status_code, 200)
        self.assertContains(pending, 'page-facilitator-10')
        self.assertNotContains(pending, 'page-facilitator-00')

    def test_permanent_supervisor_and_internship_rules_are_enforced(self):
        permanent = self.create_facilitator('permanent-supervisor', self.programme)
        intern = self.create_facilitator(
            'supervised-intern',
            self.programme,
            employment_type='INTERNSHIP',
            supervisor=permanent,
        )
        other_programme_permanent = self.create_facilitator(
            'welding-supervisor',
            self.other_programme,
        )

        with self.assertRaises(ValidationError):
            self.create_facilitator(
                'intern-cannot-supervise',
                self.programme,
                employment_type='INTERNSHIP',
                supervisor=intern,
            )

        permanent.supervisor = intern
        with self.assertRaises(ValidationError):
            permanent.save()

        intern.supervisor = other_programme_permanent
        with self.assertRaises(ValidationError):
            intern.save()

        first_assignment = InternshipSupervision.objects.create(
            internship=intern,
            supervisor=permanent,
        )
        self.assertTrue(first_assignment.is_active)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InternshipSupervision.objects.create(
                    internship=intern,
                    supervisor=permanent,
                )

        non_intern = self.create_facilitator('second-permanent', self.programme)
        with self.assertRaises(ValidationError):
            InternshipSupervision.objects.create(
                internship=non_intern,
                supervisor=permanent,
            )

    def test_programme_with_staff_cannot_be_deleted_through_queryset(self):
        self.create_teacher('protected-programme-staff', self.programme)

        with self.assertRaises(ProtectedError):
            Programme.objects.filter(pk=self.programme.pk).delete()

    def test_facilitator_deactivation_preserves_account_and_attendance_history(self):
        facilitator = self.create_facilitator('retained-facilitator', self.programme)
        attendance = Attendance.objects.create(
            teacher=facilitator.teacher,
            date=timezone.localdate(),
            school_status='present',
        )

        response = self.client.post(reverse(
            'delete_teacher',
            args=[facilitator.teacher_id],
        ))

        self.assertRedirects(response, reverse('admin_teachers'))
        facilitator.teacher.user.refresh_from_db()
        facilitator.refresh_from_db()
        self.assertFalse(facilitator.teacher.user.is_active)
        self.assertFalse(facilitator.is_active)
        self.assertTrue(Attendance.objects.filter(pk=attendance.pk).exists())

    def test_internship_expiry_is_idempotent_and_closes_supervision_history(self):
        supervisor = self.create_facilitator(
            'expiry-supervisor',
            self.programme,
        )
        intern = self.create_facilitator(
            'expiry-intern',
            self.programme,
            employment_type='INTERNSHIP',
            supervisor=supervisor,
        )
        assignment = InternshipSupervision.objects.create(
            internship=intern,
            supervisor=supervisor,
        )
        yesterday = timezone.localdate() - timedelta(days=1)
        FacilitatorProfile.objects.filter(pk=intern.pk).update(
            internship_end_date=yesterday,
        )

        self.assertEqual(expire_internships(), 1)
        self.assertEqual(expire_internships(), 0)
        intern.refresh_from_db()
        assignment.refresh_from_db()
        self.assertFalse(intern.is_active)
        self.assertFalse(assignment.is_active)
        self.assertEqual(assignment.end_date, timezone.localdate())

    def test_hod_extension_request_requires_admin_approval_to_change_end_date(self):
        internship = self.create_facilitator(
            'extension-intern',
            self.programme,
            employment_type='INTERNSHIP',
        )
        original_end_date = internship.internship_end_date
        hod_user = User.objects.create_user(
            username='extension-hod',
            password='test-password',
        )
        HODProfile.objects.create(user=hod_user, programme=self.programme)
        self.client.force_login(hod_user)
        response = self.client.post(
            reverse('hod_extension_request', args=[internship.pk]),
            {
                'new_end_date': original_end_date + timedelta(days=30),
                'reason': 'Programme requires additional practical work.',
            },
        )

        self.assertEqual(response.status_code, 302)
        extension = InternshipExtension.objects.get(internship=internship)
        self.assertEqual(extension.status, 'REQUESTED')
        internship.refresh_from_db()
        self.assertEqual(internship.internship_end_date, original_end_date)

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('admin_extension_decision', args=[extension.pk]),
            {'action': 'approve'},
        )

        self.assertRedirects(response, reverse('admin_extension_requests'))
        extension.refresh_from_db()
        internship.refresh_from_db()
        self.assertEqual(extension.status, 'APPROVED')
        self.assertEqual(extension.approved_by, self.admin)
        self.assertEqual(internship.internship_end_date, extension.new_end_date)
