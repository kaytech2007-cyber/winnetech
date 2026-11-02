import qrcode
import io
import base64
from datetime import datetime, date, timedelta
import hashlib
import logging
import re
import secrets
import smtplib
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth.models import User
from django.contrib import messages
from django.conf import settings
from django.core.mail import send_mail
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.http import FileResponse
from django.views.decorators.http import require_POST, require_http_methods
from django.db.models import Count, Prefetch

from .models import (
    TeacherProfile, Attendance, QRCode, Notification,
    TimetableEntry, ClassSchedule, AdminProfile, SchoolLocation,
    DEPARTMENT_SUBJECTS, ELECTIVE_SUBJECTS,
    Programme, HODProfile, FacilitatorProfile, InternshipSupervision,
    InternshipExtension,
    ClassConfirmationRequest, ClassReplacementRequest,
    StaffIDRegistry, StaffOnboardingToken,
    add_years, generate_hod_staff_id, generate_registered_staff_id,
    HODStaffIDAllocationError, StaffIDAllocationError,
    InvalidProgrammeCodeError,
)
from .forms import (
    TeacherRegistrationForm,
    NotificationForm, TimetableEntryForm, ClassScheduleForm, QRCodeForm,
    SchoolLocationForm, HODFacilitatorManagementForm, HODManagementForm,
    ProgrammeForm, InternshipExtensionRequestForm, FacilitatorAccountForm,
    AdminAccountForm, SetStaffPasswordForm,
    DEPARTMENT_SUBJECTS_BY_DEPARTMENT,
)
from .services.scheduling import (
    ELIGIBLE as SCHEDULING_ELIGIBLE,
    evaluate_class_assignment,
    rank_class_candidates,
)

logger = logging.getLogger(__name__)

# Teacher profile forms — keep under DB limits (PostgreSQL errors otherwise).
_TEACHER_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def _dob_input_value(post_data, profile):
    raw = post_data.get('date_of_birth', '').strip()
    if raw:
        return raw
    if getattr(profile, 'date_of_birth', None):
        return profile.date_of_birth.isoformat()
    return ''


def _parse_dob_or_error(raw, errors, field='date_of_birth'):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors[field] = 'Please enter a valid date of birth.'
        return None


def _safe_years_experience(raw):
    s = (raw or '').strip()
    if not s.isdigit():
        return 0
    return min(int(s), 2147483647)


def _validate_teacher_profile_lengths(data, errors):
    """Clip or reject fields that exceed model max_length (avoids DB DataError → 500)."""
    limits = [
        ('first_name', 150),
        ('last_name', 150),
        ('phone', 20),
        ('emergency_phone', 20),
        ('emergency_contact', 100),
        ('specialization', 200),
        ('previous_school', 200),
    ]
    for key, max_len in limits:
        val = data.get(key, '') or ''
        if len(val) > max_len:
            errors[key] = f'This field must be at most {max_len} characters.'


def _validate_teacher_uploads(request_files, errors):
    for name in ('profile_photo', 'cv_document', 'certificate_document', 'id_document'):
        f = request_files.get(name)
        if f and getattr(f, 'size', 0) > _TEACHER_MAX_UPLOAD_BYTES:
            errors[name] = 'File is too large (maximum 10 MB per file).'


# ── Role checks ───────────────────────────────────────────────────────────────

def is_admin(user):
    if not user.is_authenticated or not user.is_staff or _has_hod_profile(user):
        return False
    try:
        return user.admin_profile.role == 'admin'
    except (AttributeError, AdminProfile.DoesNotExist):
        return False

def is_superadmin(user):
    if not (user.is_authenticated and user.is_staff) or _has_hod_profile(user):
        return False
    try:
        return user.admin_profile.role == 'superadmin'
    except (AttributeError, AdminProfile.DoesNotExist):
        return user.is_superuser


def is_management_admin(user):
    return is_admin(user) or is_superadmin(user)


def has_facilitator_account_access(user):
    if not user.is_authenticated or not user.is_active or user.is_staff:
        return False
    try:
        profile = user.teacher_profile
    except (AttributeError, TeacherProfile.DoesNotExist):
        return False
    try:
        facilitator_profile = profile.facilitator_profile
    except (AttributeError, FacilitatorProfile.DoesNotExist):
        return True
    return bool(
        facilitator_profile.is_active
        and not facilitator_profile.is_expired
    )


def is_facilitator(user):
    if not has_facilitator_account_access(user):
        return False
    profile = user.teacher_profile
    if not profile.is_approved:
        return False
    return True


def is_hod(user):
    if not user.is_authenticated or not user.is_active:
        return False
    try:
        hod = user.hod_profile
    except (AttributeError, HODProfile.DoesNotExist):
        return False
    return bool(hod.is_active and hod.programme_id and hod.programme.is_active)


def _has_hod_profile(user):
    if not user.is_authenticated:
        return False
    try:
        user.hod_profile
        return True
    except (AttributeError, HODProfile.DoesNotExist):
        return False


def _dashboard_redirect(user):
    if _has_hod_profile(user):
        return redirect('hod_dashboard') if is_hod(user) else redirect('welcome')
    if is_superadmin(user):
        return redirect('superadmin_dashboard')
    if is_admin(user):
        return redirect('admin_dashboard')
    return redirect('facilitator_dashboard')


def _hod_teacher_queryset(programme):
    return (
        TeacherProfile.objects.filter(
            Q(programme=programme, facilitator_profile__isnull=True) |
            Q(facilitator_profile__programme=programme)
        )
        .select_related(
            'user',
            'programme',
            'facilitator_profile',
            'facilitator_profile__programme',
            'facilitator_profile__supervisor__teacher__user',
        )
        .distinct()
        .order_by('user__last_name', 'user__first_name', 'staff_id')
    )


def _hod_facilitator_availability_rows(programme, today):
    teachers = list(_hod_teacher_queryset(programme))
    teacher_ids = [teacher.pk for teacher in teachers]
    weekly_entries = TimetableEntry.objects.filter(
        teacher_id__in=teacher_ids,
        is_break=False,
    ).select_related('teacher__user').order_by(
        'teacher_id',
        'day',
        'start_time',
    )
    entries_by_teacher = {}
    for entry in weekly_entries:
        entries_by_teacher.setdefault(entry.teacher_id, []).append(entry)

    attendance_by_teacher = {
        attendance.teacher_id: attendance
        for attendance in Attendance.objects.filter(
            teacher_id__in=teacher_ids,
            date=today,
        )
    }
    upcoming_schedules = ClassSchedule.objects.filter(
        teacher_id__in=teacher_ids,
        scheduled_date__gte=today,
        scheduled_date__lt=today + timedelta(days=7),
    ).select_related('timetable_entry')
    schedules_by_teacher = {}
    for schedule in upcoming_schedules:
        schedules_by_teacher.setdefault(schedule.teacher_id, []).append(schedule)

    active_supervisions = InternshipSupervision.objects.filter(
        internship__teacher_id__in=teacher_ids,
        is_active=True,
    ).select_related('internship', 'supervisor__teacher__user')
    supervision_by_intern = {}
    supervised_intern_counts = {}
    for assignment in active_supervisions:
        supervision_by_intern.setdefault(
            assignment.internship_id,
            [],
        ).append(assignment)
        supervised_intern_counts[assignment.supervisor_id] = (
            supervised_intern_counts.get(assignment.supervisor_id, 0) + 1
        )

    rows = []
    for teacher in teachers:
        entries = entries_by_teacher.get(teacher.pk, [])
        weekly_minutes = sum(
            int((
                datetime.combine(today, entry.end_time)
                - datetime.combine(today, entry.start_time)
            ).total_seconds() // 60)
            for entry in entries
        )
        conflicts = []
        entries_by_day = {}
        for entry in entries:
            entries_by_day.setdefault(entry.day, []).append(entry)
        for day, day_entries in entries_by_day.items():
            for index, first in enumerate(day_entries):
                for second in day_entries[index + 1:]:
                    if (
                        first.start_time < second.end_time
                        and second.start_time < first.end_time
                    ):
                        conflicts.append(
                            f'{day.title()}: {first.subject} '
                            f'({first.start_time:%H:%M}-{first.end_time:%H:%M}) '
                            f'overlaps {second.subject} '
                            f'({second.start_time:%H:%M}-{second.end_time:%H:%M})'
                        )

        attendance = attendance_by_teacher.get(teacher.pk)
        try:
            facilitator = teacher.facilitator_profile
        except FacilitatorProfile.DoesNotExist:
            facilitator = None
        assignments = schedules_by_teacher.get(teacher.pk, [])

        supervision = 'Not applicable'
        if facilitator and facilitator.employment_type == 'INTERNSHIP':
            history = supervision_by_intern.get(facilitator.pk, [])
            if len(history) > 1:
                supervision = 'Multiple active supervision records'
            elif not history:
                supervision = 'No active supervision record'
            elif history[0].supervisor_id != facilitator.supervisor_id:
                supervision = 'Profile and supervision history do not match'
            else:
                supervision = history[0].supervisor.full_name
        elif facilitator and facilitator.employment_type == 'PERMANENT':
            supervision = (
                f'{supervised_intern_counts.get(facilitator.pk, 0)} active interns'
            )

        rows.append({
            'teacher': teacher,
            'employment_type': (
                facilitator.get_employment_type_display()
                if facilitator
                else 'Legacy — type not recorded'
            ),
            'attendance_status': (
                attendance.get_school_status_display()
                if attendance
                else 'Unverified'
            ),
            'check_in_time': attendance.check_in_time if attendance else None,
            'availability_status': 'Unverified',
            'availability_reason': 'No class availability response is recorded.',
            'weekly_class_count': len(entries),
            'weekly_minutes': weekly_minutes,
            'upcoming_class_count': len(assignments),
            'conflicts': conflicts,
            'supervision': supervision,
        })
    return rows


def _send_hod_password_setup_email(request, hod):
    user = hod.user
    if not user.email or not _staff_account_eligible_for_setup(user):
        return False
    return _send_staff_onboarding_email(
        request,
        user,
        hod.staff_id or user.username,
        'hod_login',
    )


def _send_staff_onboarding_email(request, user, staff_id, login_route):
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
    expiry_seconds = settings.STAFF_ONBOARDING_TIMEOUT
    onboarding, _ = StaffOnboardingToken.objects.update_or_create(
        user=user,
        defaults={
            'token_hash': token_hash,
            'expires_at': timezone.now() + timedelta(seconds=expiry_seconds),
            'used_at': None,
            'delivery_failed_at': None,
        },
    )
    setup_url = (
        request.build_absolute_uri(reverse('staff_password_setup'))
        + f'#{raw_token}'
    )
    login_url = request.build_absolute_uri(reverse(login_route))
    recipient_name = user.get_full_name() or user.username
    expiry_hours = max(1, (expiry_seconds + 3599) // 3600)
    programme_line = ''
    if _has_hod_profile(user) and user.hod_profile.programme:
        programme = user.hod_profile.programme
        programme_line = f'Assigned programme: {programme.name} ({programme.code}).\n'
    body = (
        f'Hello {recipient_name},\n\n'
        f'Your Winnetech account has been created. Your Staff ID is {staff_id}.\n'
        f'Your account login ID is {user.username}.\n'
        f'{programme_line}'
        f'Set your password using this single-use link:\n{setup_url}\n\n'
        f'The link expires within {expiry_hours} hour(s). After setting your password, '
        f'sign in at {login_url} using your account login ID.\n'
        'If you did not expect this account, contact the school administrator.'
    )
    try:
        sent_count = send_mail(
            'Set up your Winnetech account',
            body,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except (
        OSError,
        smtplib.SMTPException,
        ValueError,
        ImproperlyConfigured,
    ):
        onboarding.delivery_failed_at = timezone.now()
        onboarding.save(update_fields=['delivery_failed_at'])
        logger.exception(
            'Staff onboarding email delivery failed for user id=%s',
            user.pk,
        )
        return False
    if sent_count != 1:
        onboarding.delivery_failed_at = timezone.now()
        onboarding.save(update_fields=['delivery_failed_at'])
        logger.error(
            'Staff onboarding email backend did not deliver a message for user id=%s',
            user.pk,
        )
        return False
    return True


def _valid_staff_onboarding(token):
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    onboarding = StaffOnboardingToken.objects.select_related('user').filter(
        token_hash=token_hash,
        used_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if not onboarding or not secrets.compare_digest(
        onboarding.token_hash,
        token_hash,
    ):
        return None
    return onboarding


def _staff_account_eligible_for_setup(user):
    if not user.is_active or user.has_usable_password():
        return False
    if _has_hod_profile(user):
        hod = user.hod_profile
        return bool(
            hod.is_active
            and hod.programme_id
            and hod.programme.is_active
        )
    if hasattr(user, 'teacher_profile'):
        teacher = user.teacher_profile
        try:
            facilitator = teacher.facilitator_profile
        except FacilitatorProfile.DoesNotExist:
            return False
        return bool(
            teacher.is_approved
            and facilitator.is_active
            and not facilitator.is_expired
            and facilitator.programme_id
            and facilitator.programme.is_active
        )
    try:
        admin = user.admin_profile
    except (AttributeError, AdminProfile.DoesNotExist):
        return False
    return bool(user.is_staff and admin.role == 'admin')


@require_http_methods(['GET', 'POST'])
def staff_password_setup(request):
    def setup_response(context, status=200):
        response = render(
            request,
            'auth/staff_password_setup.html',
            context,
            status=status,
        )
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response

    token = request.POST.get('token', '').strip() if request.method == 'POST' else ''
    onboarding = _valid_staff_onboarding(token) if token else None
    if request.method == 'POST' and (
        onboarding is None
        or not _staff_account_eligible_for_setup(onboarding.user)
    ):
        return setup_response({'invalid_link': True}, status=400)

    user = onboarding.user if onboarding else None
    form = (
        SetStaffPasswordForm(request.POST, user=user)
        if request.method == 'POST'
        else None
    )

    if form and form.is_valid():
        with transaction.atomic():
            onboarding = StaffOnboardingToken.objects.select_for_update().get(
                pk=onboarding.pk,
            )
            user = User.objects.select_for_update().get(pk=onboarding.user_id)
            if (
                onboarding.used_at
                or onboarding.expires_at <= timezone.now()
                or not _staff_account_eligible_for_setup(user)
                or user.has_usable_password()
            ):
                return setup_response({'invalid_link': True}, status=400)
            user.set_password(form.cleaned_data['new_password1'])
            user.save(update_fields=['password'])
            onboarding.used_at = timezone.now()
            onboarding.save(update_fields=['used_at'])

        if hasattr(user, 'hod_profile'):
            login_route = 'hod_login'
        elif hasattr(user, 'teacher_profile'):
            login_route = 'login'
        else:
            login_route = 'admin_login'
        return setup_response({
            'complete': True,
            'login_url': reverse(login_route),
        })

    return setup_response({
        'form': form or SetStaffPasswordForm(user=User()),
        'token_value': token,
    })


@login_required
@user_passes_test(is_management_admin)
@require_POST
def retry_staff_onboarding(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if is_admin(request.user):
        is_allowed_target = (
            _has_hod_profile(user)
            or hasattr(user, 'teacher_profile')
        )
    else:
        try:
            target_admin = user.admin_profile
        except (AttributeError, AdminProfile.DoesNotExist):
            target_admin = None
        is_allowed_target = (
            _has_hod_profile(user)
            or hasattr(user, 'teacher_profile')
            or bool(target_admin and target_admin.role == 'admin')
        )
    if not is_allowed_target:
        raise Http404

    onboarding = get_object_or_404(
        StaffOnboardingToken.objects.select_related('user'),
        user=user,
    )
    if onboarding.used_at or user.has_usable_password():
        messages.error(request, 'This account no longer needs onboarding.')
    elif not _staff_account_eligible_for_setup(user):
        messages.error(
            request,
            'This account is inactive or no longer eligible for onboarding.',
        )
    else:
        if _has_hod_profile(user):
            staff_id = user.hod_profile.staff_id or user.username
            login_route = 'hod_login'
        elif hasattr(user, 'teacher_profile'):
            staff_id = user.teacher_profile.staff_id
            login_route = 'login'
        else:
            staff_id = user.username
            login_route = 'admin_login'
        if _send_staff_onboarding_email(
            request,
            user,
            staff_id,
            login_route,
        ):
            messages.success(request, 'Password setup email sent.')
        else:
            messages.error(
                request,
                'Password setup email could not be delivered. Check email configuration and retry.',
            )
    if _has_hod_profile(user):
        destination = 'superadmin_hods' if is_superadmin(request.user) else 'admin_hods'
    elif hasattr(user, 'teacher_profile'):
        destination = (
            'superadmin_facilitator_records'
            if is_superadmin(request.user)
            else 'admin_facilitator_records'
        )
    else:
        destination = 'superadmin_admins'
    return redirect(destination)


def _hod_list_view(request, route_prefix, template_name):
    hods = HODProfile.objects.select_related(
        'user',
        'user__staff_onboarding',
        'programme',
    ).order_by(
        'programme__name', 'user__last_name', 'user__first_name'
    )
    search = request.GET.get('q', '').strip()
    programme_filter = request.GET.get('programme', '').strip()
    status_filter = request.GET.get('status', '').strip()
    if programme_filter.isdigit():
        hods = hods.filter(programme_id=int(programme_filter))
    elif programme_filter:
        hods = hods.none()
    if search:
        hods = hods.filter(
            Q(staff_id__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__username__icontains=search)
            | Q(user__email__icontains=search)
            | Q(programme__name__icontains=search)
        )
    if status_filter == 'active':
        hods = hods.filter(is_active=True, user__is_active=True)
    elif status_filter == 'inactive':
        hods = hods.filter(Q(is_active=False) | Q(user__is_active=False))
    hod_page = Paginator(hods, 25).get_page(request.GET.get('page'))
    query_parameters = request.GET.copy()
    query_parameters.pop('page', None)
    return render(
        request,
        template_name,
        {
            'hods': hod_page,
            'search': search,
            'programme_filter': programme_filter,
            'status_filter': status_filter,
            'programmes': Programme.objects.order_by('name'),
            'query_string': query_parameters.urlencode(),
            'route_prefix': route_prefix,
            'list_url_name': f'{route_prefix}_hods',
            'create_url_name': f'{route_prefix}_hod_create',
            'manage_url_name': f'{route_prefix}_hod_manage',
            'deactivate_url_name': f'{route_prefix}_hod_deactivate',
            'base_template': (
                'admin/base_admin.html'
                if route_prefix == 'admin'
                else 'superadmin/base_superadmin.html'
            ),
        },
    )


def _portal_base_for_role(role):
    return {
        'admin': 'admin/base_admin.html',
        'superadmin': 'superadmin/base_superadmin.html',
        'hod': 'hod/base_hod.html',
    }[role]


@login_required
@user_passes_test(is_hod)
def hod_profile(request):
    return render(request, 'hod/profile.html', {
        'hod': request.user.hod_profile,
        'programme': request.user.hod_profile.programme,
    })


@login_required
@user_passes_test(is_hod)
def hod_timetable(request):
    hod = request.user.hod_profile
    facilitators = _hod_teacher_queryset(request.user.hod_profile.programme)
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    timetable_by_day = [
        (
            day,
            list(
                TimetableEntry.objects.filter(
                    day=day,
                    teacher__in=facilitators,
                ).select_related('teacher__user').order_by('start_time')
            ),
        )
        for day in days
    ]
    return render(request, 'hod/timetable.html', {
        'hod': hod,
        'programme': hod.programme,
        'timetable_by_day': timetable_by_day,
        'days': days,
    })


@login_required
@user_passes_test(is_hod)
def hod_attendance(request):
    hod = request.user.hod_profile
    today = timezone.localdate()
    try:
        selected_date = date.fromisoformat(request.GET.get('date', str(today)))
    except ValueError:
        selected_date = today
    teachers = _hod_teacher_queryset(request.user.hod_profile.programme)
    attendance = Attendance.objects.filter(
        teacher__in=teachers,
        date=selected_date,
    ).select_related('teacher__user').order_by('teacher__user__last_name')
    verified_teacher_ids = attendance.exclude(
        school_status='unverified',
    ).values_list('teacher_id', flat=True)
    return render(request, 'hod/attendance.html', {
        'hod': hod,
        'programme': hod.programme,
        'attendance': attendance,
        'unverified_teachers': teachers.exclude(pk__in=verified_teacher_ids),
        'selected_date': selected_date,
        'today': today,
    })


@login_required
@user_passes_test(is_hod)
def hod_notifications(request):
    hod = request.user.hod_profile
    notifications = Notification.objects.filter(
        recipient=request.user,
    ).order_by('-created_at')
    return render(request, 'hod/notifications.html', {
        'hod': hod,
        'programme': hod.programme,
        'notifications': Paginator(
            notifications,
            25,
        ).get_page(request.GET.get('page')),
    })


@login_required
@user_passes_test(is_hod)
def hod_supervision(request):
    hod = request.user.hod_profile
    assignments = InternshipSupervision.objects.filter(
        internship__programme_id=request.user.hod_profile.programme_id,
    ).select_related(
        'internship__teacher__user',
        'supervisor__teacher__user',
        'changed_by',
    ).order_by('-start_date')
    return render(request, 'hod/supervision.html', {
        'hod': hod,
        'assignments': Paginator(
            assignments,
            25,
        ).get_page(request.GET.get('page')),
        'programme': request.user.hod_profile.programme,
    })


@login_required
@user_passes_test(is_facilitator)
def facilitator_attendance(request):
    profile = request.user.teacher_profile
    attendance = Attendance.objects.filter(
        teacher=profile,
    ).order_by('-date')
    return render(request, 'teacher/attendance.html', {
        'attendance': Paginator(
            attendance,
            25,
        ).get_page(request.GET.get('page')),
    })


@login_required
@user_passes_test(is_facilitator)
def facilitator_notifications(request):
    notifications = Notification.objects.filter(
        recipient=request.user,
    ).order_by('-created_at')
    return render(request, 'teacher/notifications.html', {
        'notifications': Paginator(
            notifications,
            25,
        ).get_page(request.GET.get('page')),
    })


@login_required
def programme_list(request):
    if is_hod(request.user):
        programmes = Programme.objects.filter(
            pk=request.user.hod_profile.programme_id,
        )
        role = 'hod'
    elif is_superadmin(request.user):
        programmes = Programme.objects.all()
        role = 'superadmin'
    elif is_admin(request.user):
        programmes = Programme.objects.all()
        role = 'admin'
    else:
        return redirect('login')

    programmes = programmes.order_by('name').annotate(
        permanent_count=(
            Count(
                'facilitators_detail',
                filter=Q(facilitators_detail__employment_type='PERMANENT'),
                distinct=True,
            )
            + Count(
                'teachers',
                filter=Q(teachers__facilitator_profile__isnull=True),
                distinct=True,
            )
        ),
        internship_count=Count(
            'facilitators_detail',
            filter=Q(facilitators_detail__employment_type='INTERNSHIP'),
            distinct=True,
        ),
    ).select_related('hod_profile__user')
    return render(request, 'programmes/list.html', {
        'programmes': programmes,
        'role': role,
        'base_template': _portal_base_for_role(role),
    })


@login_required
def programme_detail(request, programme_id=None):
    if is_hod(request.user):
        role = 'hod'
        programme_id = request.user.hod_profile.programme_id
    elif is_superadmin(request.user):
        role = 'superadmin'
    elif is_admin(request.user):
        role = 'admin'
    else:
        return redirect('login')

    programme = get_object_or_404(
        Programme.objects.select_related('hod_profile__user').prefetch_related(
            'facilitators_detail__teacher__user',
            'facilitators_detail__supervisor__teacher__user',
            'facilitators_detail__extensions',
        ),
        pk=programme_id,
    )
    if role == 'hod' and programme.pk != request.user.hod_profile.programme_id:
        raise Http404

    facilitators = FacilitatorProfile.objects.filter(
        programme=programme,
    ).select_related('teacher__user', 'supervisor__teacher__user').annotate(
        supervised_intern_count=Count(
            'interns',
            filter=Q(
                interns__employment_type='INTERNSHIP',
                interns__is_active=True,
            ),
            distinct=True,
        ),
    )
    legacy = TeacherProfile.objects.filter(
        programme=programme,
        facilitator_profile__isnull=True,
    ).select_related('user')
    return render(request, 'programmes/detail.html', {
        'programme': programme,
        'permanent_facilitators': facilitators.filter(
            employment_type='PERMANENT',
        ).order_by('teacher__user__last_name', 'teacher__user__first_name'),
        'internship_facilitators': facilitators.filter(
            employment_type='INTERNSHIP',
        ).order_by('teacher__user__last_name', 'teacher__user__first_name'),
        'legacy_facilitators': legacy,
        'active_count': facilitators.filter(
            is_active=True,
            teacher__user__is_active=True,
        ).count() + legacy.filter(user__is_active=True).count(),
        'inactive_count': facilitators.filter(
            Q(is_active=False) | Q(teacher__user__is_active=False),
        ).count() + legacy.filter(user__is_active=False).count(),
        'active_supervision_count': InternshipSupervision.objects.filter(
            internship__programme=programme,
            is_active=True,
        ).count(),
        'expiring_internships': facilitators.filter(
            employment_type='INTERNSHIP',
            is_active=True,
            internship_end_date__gte=timezone.localdate(),
            internship_end_date__lte=timezone.localdate() + timedelta(days=30),
        ).order_by('internship_end_date'),
        'role': role,
        'base_template': _portal_base_for_role(role),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def programme_manage(request, programme_id=None):
    if is_superadmin(request.user):
        role = 'superadmin'
    elif is_admin(request.user):
        role = 'admin'
    else:
        return redirect('login')

    programme = get_object_or_404(Programme, pk=programme_id) if programme_id else None
    form = ProgrammeForm(request.POST or None, instance=programme)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(
            request,
            'Programme updated.' if programme else 'Programme created.',
        )
        return redirect(
            'admin_programmes' if role == 'admin' else 'superadmin_programmes'
        )
    return render(request, 'programmes/form.html', {
        'form': form,
        'programme': programme,
        'role': role,
        'base_template': _portal_base_for_role(role),
    })


@login_required
def hod_record_detail(request, hod_id):
    if not (is_admin(request.user) or is_superadmin(request.user)):
        return redirect('login')
    role = 'admin' if is_admin(request.user) else 'superadmin'
    hod = get_object_or_404(
        HODProfile.objects.select_related('user', 'programme'),
        pk=hod_id,
    )
    return render(request, 'hod_management/detail.html', {
        'hod': hod,
        'base_template': _portal_base_for_role(role),
        'role': role,
    })


def _hod_manage_view(request, route_prefix, hod_id=None):
    hod = None
    if hod_id is not None:
        hod = get_object_or_404(
            HODProfile.objects.select_related('user', 'programme'),
            pk=hod_id,
        )
    form = HODManagementForm(request.POST or None, hod=hod)
    is_new = hod is None

    if request.method == 'POST' and request.POST.get('action') == 'send_setup_link':
        if not hod:
            raise Http404
        if _send_hod_password_setup_email(request, hod):
            messages.success(request, 'Password setup link sent to the HOD email address.')
        else:
            messages.error(request, 'Password setup email could not be delivered. Check email configuration and try again.')
        return redirect(f'{route_prefix}_hod_manage', hod_id=hod.pk)

    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        staff_id = None
        if is_new:
            try:
                staff_id = generate_hod_staff_id(data['programme'].pk)
            except InvalidProgrammeCodeError as exc:
                form.add_error('programme', str(exc))
            except HODStaffIDAllocationError as exc:
                form.add_error(None, str(exc))

        if not form.errors:
            try:
                with transaction.atomic():
                    programme = data['programme']
                    if programme:
                        programme = Programme.objects.select_for_update().get(pk=programme.pk)

                    if hod:
                        hod = HODProfile.objects.select_for_update().select_related('user').get(pk=hod.pk)
                        user = User.objects.select_for_update().get(pk=hod.user_id)
                    else:
                        user = User(
                            username=data['username'],
                            email=data['email'],
                            first_name=data['first_name'],
                            last_name=data['last_name'],
                            is_active=True,
                        )
                        user.set_unusable_password()

                    previous_email = user.email
                    if data['is_active'] and programme:
                        existing_assignment = HODProfile.objects.select_for_update().filter(
                            programme=programme
                        ).exclude(pk=hod.pk if hod else None).first()
                        if existing_assignment:
                            if existing_assignment.is_active:
                                form.add_error(
                                    'programme',
                                    'This programme already has an active HOD.',
                                )
                                raise ValidationError('Programme assignment conflict.')
                            existing_assignment.programme = None
                            existing_assignment.save(
                                update_fields=['programme', 'updated_at']
                            )

                    if form.errors:
                        raise ValidationError('Invalid HOD programme assignment.')

                    user.username = data['username']
                    user.email = data['email']
                    user.first_name = data['first_name']
                    user.last_name = data['last_name']
                    user.save()
                    if (
                        hod
                        and previous_email.casefold() != data['email'].casefold()
                    ):
                        StaffOnboardingToken.objects.filter(
                            user=user,
                            used_at__isnull=True,
                        ).update(used_at=timezone.now())

                    if hod is None:
                        hod = HODProfile(user=user, staff_id=staff_id)
                        hod._staff_id_reserved = True
                    hod.phone = data['phone']
                    hod.is_active = data['is_active']
                    hod.programme = programme if hod.is_active else None
                    hod.save()
            except ValidationError:
                pass
            except IntegrityError:
                form.add_error(
                    None,
                    'The HOD account could not be saved because its email, username, '
                    'Staff ID, or programme assignment is no longer available. '
                    'Refresh the form and try again.',
                )
            else:
                if is_new:
                    if _send_hod_password_setup_email(request, hod):
                        messages.success(request, 'HOD account created and password setup link sent.')
                    else:
                        messages.warning(request, 'HOD account created, but the password setup email could not be delivered. Check email settings and use the resend link option.')
                else:
                    messages.success(request, 'HOD details updated.')
                return redirect(f'{route_prefix}_hods')

    return render(request, 'hod_management/form.html', {
        'form': form,
        'hod': hod,
        'is_new': is_new,
        'route_prefix': route_prefix,
        'manage_url_name': f'{route_prefix}_hod_manage',
        'list_url_name': f'{route_prefix}_hods',
        'base_template': (
            'admin/base_admin.html'
            if route_prefix == 'admin'
            else 'superadmin/base_superadmin.html'
        ),
    })


def _hod_deactivate_view(request, route_prefix, hod_id):
    with transaction.atomic():
        hod = get_object_or_404(
            HODProfile.objects.select_for_update(),
            pk=hod_id,
        )
        hod.is_active = False
        hod.programme = None
        hod.save(update_fields=['is_active', 'programme', 'updated_at'])
    messages.success(request, 'HOD deactivated; the programme assignment has been released.')
    return redirect(f'{route_prefix}_hods')


# ── Welcome ───────────────────────────────────────────────────────────────────

def welcome(request):
    if request.user.is_authenticated:
        return _dashboard_redirect(request.user)
    return render(request, 'welcome.html')


def service_worker(request):
    import os
    from django.conf import settings
    from django.http import FileResponse, Http404
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'js', 'sw.js')
    if not os.path.exists(sw_path):
        raise Http404
    response = FileResponse(open(sw_path, 'rb'), content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response


def offline_page(request):
    return render(request, 'offline.html')


# ── Auth ──────────────────────────────────────────────────────────────────────

def teacher_register(request):
    """Kept for backward compatibility — redirects to login."""
    return redirect('login')


@ensure_csrf_cookie
def unified_login(request):
    """Staff ID login retained for facilitators and admins; HOD usernames also work."""
    if request.user.is_authenticated:
        return _dashboard_redirect(request.user)

    error = None
    if request.method == 'POST':
        staff_id = request.POST.get('staff_id', '').strip().upper()
        password = request.POST.get('password', '').strip()
        if not staff_id or not password:
            error = 'Please enter your Staff ID and password.'
        else:
            # Try teacher first
            try:
                profile = TeacherProfile.objects.select_related('user').get(staff_id=staff_id)
                user = authenticate(request, username=profile.user.username, password=password)
                if user is None:
                    error = 'Invalid Staff ID or password.'
                elif not profile.is_approved:
                    error = 'Your account is not yet activated. Contact your admin.'
                else:
                    login(request, user)
                    return _dashboard_redirect(user)
            except TeacherProfile.DoesNotExist:
                # HOD accounts use their username; admin accounts retain their existing login.
                user = authenticate(request, username=staff_id, password=password)
                if user is None:
                    error = 'Invalid Staff ID or password.'
                elif not user.is_active:
                    error = 'This account is disabled.'
                elif _has_hod_profile(user):
                    if is_hod(user):
                        login(request, user)
                        return redirect('hod_dashboard')
                    error = 'Your HOD account is inactive or has no active programme.'
                elif not user.is_staff:
                    error = 'Invalid Staff ID or password.'
                else:
                    try:
                        if user.admin_profile.role == 'superadmin':
                            error = 'Please use the Super Admin Portal.'
                        else:
                            login(request, user)
                            return _dashboard_redirect(user)
                    except Exception:
                        login(request, user)
                        return _dashboard_redirect(user)
    return render(request, 'auth/login.html', {'error': error})


@ensure_csrf_cookie
def hod_login(request):
    if request.user.is_authenticated and is_hod(request.user):
        return redirect('hod_dashboard')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)
        if user is None:
            error = 'Invalid username or password.'
        elif not is_hod(user):
            error = 'This account does not have access to the HOD Portal.'
        else:
            login(request, user)
            return redirect('hod_dashboard')
    return render(request, 'auth/hod_login.html', {'error': error})


def teacher_login(request):
    """Alias — redirects to unified login."""
    return unified_login(request)


def admin_login(request):
    """Alias — redirects to unified login."""
    return unified_login(request)


@ensure_csrf_cookie
def superadmin_login(request):
    if request.user.is_authenticated and is_superadmin(request.user):
        return redirect('superadmin_dashboard')
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)
        if user is None:
            error = 'Invalid username or password.'
        elif not user.is_staff:
            error = 'No admin privileges.'
        elif not is_superadmin(user):
            error = 'This is the Super Admin portal. Use Admin Portal for regular admin login.'
        else:
            login(request, user)
            return redirect('superadmin_dashboard')
    return render(request, 'auth/superadmin_login.html', {'error': error})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('welcome')


# ── Teacher Dashboard ─────────────────────────────────────────────────────────

@login_required
def teacher_dashboard(request):
    if request.user.is_staff:
        return redirect('admin_dashboard')
    try:
        profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        messages.error(request, 'Facilitator profile not found.')
        return redirect('welcome')
    if not is_facilitator(request.user):
        return redirect('login')


    today = timezone.localdate()
    today_att = Attendance.objects.filter(teacher=profile, date=today).first()
    all_notifications = Notification.objects.filter(recipient=request.user).order_by('-created_at')[:20]
    unread_notifications = Notification.objects.filter(recipient=request.user, is_read=False).count()
    day_name = today.strftime('%A').lower()
    today_schedule = TimetableEntry.objects.filter(day=day_name, is_break=False, teacher=profile)
    upcoming_schedules = ClassSchedule.objects.filter(
        teacher=profile, scheduled_date__gte=today, is_completed=False
    ).order_by('scheduled_date', 'timetable_entry__start_time')[:5]
    class_confirmation_requests = ClassConfirmationRequest.objects.filter(
        candidate=profile,
        status='PENDING',
    ).select_related(
        'programme',
        'timetable_entry',
    ).order_by('scheduled_date', 'start_time')

    # Active QR codes for scanning
    school_qr = QRCode.objects.filter(qr_type='school_attendance', is_active=True).first()
    lesson_qr = QRCode.objects.filter(qr_type='lesson', is_active=True).first()

    return render(request, 'teacher/dashboard.html', {
        'profile': profile,
        'today_att': today_att,
        'all_notifications': all_notifications,
        'unread_notifications': unread_notifications,
        'today_schedule': today_schedule,
        'upcoming_schedules': upcoming_schedules,
        'class_confirmation_requests': class_confirmation_requests,
        'today': today,
        'school_qr': school_qr,
        'lesson_qr': lesson_qr,
    })


@login_required
@user_passes_test(is_facilitator)
def facilitator_dashboard(request):
    return teacher_dashboard(request)


@login_required
@user_passes_test(is_hod)
def hod_dashboard(request):
    hod = request.user.hod_profile
    programme = hod.programme
    facilitators = _hod_teacher_queryset(programme)
    today = timezone.localdate()
    day_name = today.strftime('%A').lower()
    timetable = TimetableEntry.objects.filter(
        teacher__in=facilitators,
        day=day_name,
        is_break=False,
    ).select_related('teacher__user').order_by('start_time')
    upcoming_class_schedules = ClassSchedule.objects.filter(
        teacher__in=facilitators,
        scheduled_date__gte=today,
        scheduled_date__lt=today + timedelta(days=7),
    ).select_related(
        'teacher__user',
        'timetable_entry',
    ).order_by('scheduled_date', 'timetable_entry__start_time')
    programme_profiles = FacilitatorProfile.objects.filter(programme=programme)
    approved_staff = facilitators.filter(is_approved=True)
    return render(request, 'hod/dashboard.html', {
        'hod': hod,
        'programme': programme,
        'facilitators': facilitators,
        'permanent_count': programme_profiles.filter(
            programme=programme, employment_type='PERMANENT'
        ).count() + facilitators.filter(
            facilitator_profile__isnull=True
        ).count(),
        'internship_count': programme_profiles.filter(employment_type='INTERNSHIP').count(),
        'active_count': approved_staff.filter(user__is_active=True).filter(
            Q(facilitator_profile__isnull=True)
            | Q(facilitator_profile__is_active=True)
        ).count(),
        'inactive_count': approved_staff.filter(
            Q(user__is_active=False)
            | Q(facilitator_profile__is_active=False)
        ).count(),
        'pending_count': facilitators.filter(is_approved=False).count(),
        'active_supervision_count': InternshipSupervision.objects.filter(
            internship__programme=programme,
            is_active=True,
        ).count(),
        'near_expiry': programme_profiles.filter(
            employment_type='INTERNSHIP',
            is_active=True,
            internship_end_date__gte=today,
            internship_end_date__lte=today + timedelta(days=30),
        ).select_related('teacher__user').order_by('internship_end_date'),
        'expired_count': programme_profiles.filter(
            employment_type='INTERNSHIP',
            internship_end_date__lt=today,
        ).count(),
        'internships': programme_profiles.filter(
            employment_type='INTERNSHIP'
        ).select_related('teacher__user', 'supervisor__teacher__user'),
        'timetable': timetable,
        'upcoming_class_schedules': upcoming_class_schedules,
        'availability_rows': _hod_facilitator_availability_rows(
            programme,
            today,
        ),
        'notifications': Notification.objects.filter(
            recipient=request.user
        ).order_by('-created_at')[:8],
        'today': today,
    })


@login_required
@user_passes_test(is_hod)
def hod_facilitators(request):
    return facilitator_records(request)


def _facilitator_record_status_filter(queryset, status):
    if status == 'active':
        return queryset.filter(
            is_approved=True,
            user__is_active=True,
        ).filter(
            Q(facilitator_profile__isnull=True)
            | Q(facilitator_profile__is_active=True)
        )
    if status == 'pending':
        return queryset.filter(is_approved=False)
    if status == 'inactive':
        return queryset.filter(
            Q(user__is_active=False)
            | Q(facilitator_profile__is_active=False)
        )
    return queryset


def _facilitator_records_for_programme(programme, search, status):
    records = TeacherProfile.objects.select_related(
        'user',
        'user__staff_onboarding',
        'programme',
        'facilitator_profile',
        'facilitator_profile__programme',
        'facilitator_profile__supervisor__teacher__user',
    ).prefetch_related(
        Prefetch(
            'facilitator_profile__extensions',
            queryset=InternshipExtension.objects.order_by('-created_at'),
        )
    )
    if programme:
        records = records.filter(
            Q(facilitator_profile__isnull=True, programme_id=programme.pk)
            | Q(facilitator_profile__programme_id=programme.pk)
        )
    else:
        records = records.filter(
            Q(facilitator_profile__isnull=True, programme__isnull=True)
            | Q(
                facilitator_profile__isnull=False,
                facilitator_profile__programme__isnull=True,
            )
        )
    if search:
        records = records.filter(
            Q(staff_id__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__username__icontains=search)
            | Q(user__email__icontains=search)
        )
    records = _facilitator_record_status_filter(records, status).distinct()
    permanent = records.filter(
        Q(facilitator_profile__isnull=True)
        | Q(facilitator_profile__employment_type='PERMANENT')
    ).annotate(
        supervised_intern_count=Count(
            'facilitator_profile__interns',
            filter=Q(
                facilitator_profile__interns__employment_type='INTERNSHIP',
                facilitator_profile__interns__is_active=True,
            ),
            distinct=True,
        )
    ).order_by('user__last_name', 'user__first_name', 'staff_id')
    internship = records.filter(
        facilitator_profile__employment_type='INTERNSHIP'
    ).order_by('user__last_name', 'user__first_name', 'staff_id')
    return permanent, internship


@login_required
def facilitator_records(request):
    user = request.user
    if is_hod(user):
        role = 'hod'
        hod = user.hod_profile
        programmes = Programme.objects.filter(pk=hod.programme_id)
        programme_filter = str(hod.programme_id)
        if request.GET.get('programme') not in (None, '', programme_filter):
            raise Http404
    elif is_admin(user):
        role = 'admin'
        hod = None
        programmes = Programme.objects.all().order_by('name')
        programme_filter = request.GET.get('programme', '').strip()
    elif is_superadmin(user):
        role = 'superadmin'
        hod = None
        programmes = Programme.objects.all().order_by('name')
        programme_filter = request.GET.get('programme', '').strip()
    else:
        return redirect('login')

    if programme_filter:
        try:
            programme_id = int(programme_filter)
        except ValueError as exc:
            raise Http404 from exc
        programmes = programmes.filter(pk=programme_id)
        if not programmes.exists():
            raise Http404

    employment_type = request.GET.get('employment_type', '').strip()
    if employment_type not in ('', 'PERMANENT', 'INTERNSHIP'):
        employment_type = ''
    search = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    if status not in ('', 'active', 'pending', 'inactive'):
        status = ''
    page_number = request.GET.get('page', '1')
    grouped_records = []
    for programme in programmes:
        permanent, internship = _facilitator_records_for_programme(
            programme,
            search,
            status,
        )
        if employment_type == 'PERMANENT':
            internship = internship.none()
        elif employment_type == 'INTERNSHIP':
            permanent = permanent.none()
        grouped_records.append({
            'programme': programme,
            'label': programme.name,
            'permanent': Paginator(permanent, 10).get_page(page_number),
            'internship': Paginator(internship, 10).get_page(page_number),
        })
    if role != 'hod' and not programme_filter:
        permanent, internship = _facilitator_records_for_programme(
            None,
            search,
            status,
        )
        if employment_type == 'PERMANENT':
            internship = internship.none()
        elif employment_type == 'INTERNSHIP':
            permanent = permanent.none()
        grouped_records.append({
            'programme': None,
            'label': 'Unassigned programme',
            'permanent': Paginator(permanent, 10).get_page(page_number),
            'internship': Paginator(internship, 10).get_page(page_number),
        })

    query_parameters = request.GET.copy()
    query_parameters.pop('page', None)
    if role == 'hod':
        query_parameters['programme'] = programme_filter

    return render(request, 'records/facilitators.html', {
        'grouped_records': grouped_records,
        'programmes': Programme.objects.filter(
            pk=hod.programme_id
        ) if role == 'hod' else Programme.objects.all().order_by('name'),
        'selected_programme': programme_filter,
        'search': search,
        'status': status,
        'employment_type': employment_type,
        'query_string': query_parameters.urlencode(),
        'role': role,
        'hod': hod,
        'programme': hod.programme if hod else None,
        'base_template': {
            'admin': 'admin/base_admin.html',
            'superadmin': 'superadmin/base_superadmin.html',
            'hod': 'hod/base_hod.html',
        }[role],
    })


@login_required
@user_passes_test(is_hod)
def hod_permanent_facilitators(request):
    query = request.GET.copy()
    query['employment_type'] = 'PERMANENT'
    request.GET = query
    return facilitator_records(request)


@login_required
@user_passes_test(is_hod)
def hod_internship_facilitators(request):
    query = request.GET.copy()
    query['employment_type'] = 'INTERNSHIP'
    request.GET = query
    return facilitator_records(request)


@login_required
def facilitator_record_detail(request, facilitator_id):
    user = request.user
    if is_hod(user):
        teacher = get_object_or_404(
            _hod_teacher_queryset(user.hod_profile.programme),
            pk=facilitator_id,
        )
        role = 'hod'
        hod = user.hod_profile
    elif is_admin(user):
        teacher = get_object_or_404(
            TeacherProfile.objects.select_related(
                'user', 'programme', 'facilitator_profile__programme',
                'facilitator_profile__supervisor__teacher__user',
            ).prefetch_related('facilitator_profile__extensions'),
            pk=facilitator_id,
        )
        role = 'admin'
        hod = None
    elif is_superadmin(user):
        teacher = get_object_or_404(
            TeacherProfile.objects.select_related(
                'user', 'programme', 'facilitator_profile__programme',
                'facilitator_profile__supervisor__teacher__user',
            ).prefetch_related('facilitator_profile__extensions'),
            pk=facilitator_id,
        )
        role = 'superadmin'
        hod = None
    else:
        return redirect('login')

    try:
        facilitator = teacher.facilitator_profile
    except FacilitatorProfile.DoesNotExist:
        facilitator = None
    return render(request, 'records/facilitator_detail.html', {
        'teacher': teacher,
        'facilitator': facilitator,
        'hod': hod,
        'programme': (
            facilitator.programme if facilitator else teacher.programme
        ),
        'base_template': {
            'admin': 'admin/base_admin.html',
            'superadmin': 'superadmin/base_superadmin.html',
            'hod': 'hod/base_hod.html',
        }[role],
        'role': role,
    })


@login_required
def private_facilitator_document(request, facilitator_id, document_type):
    if not request.user.is_active:
        raise Http404
    document_fields = {
        'cv': 'cv_document',
        'certificate': 'certificate_document',
        'identification': 'id_document',
    }
    field_name = document_fields.get(document_type)
    if not field_name:
        raise Http404

    teacher = get_object_or_404(
        TeacherProfile.objects.select_related(
            'user',
            'programme',
            'facilitator_profile__programme',
        ),
        pk=facilitator_id,
    )
    permitted = request.user.pk == teacher.user_id or is_management_admin(
        request.user
    )
    if is_hod(request.user):
        programme_id = request.user.hod_profile.programme_id
        try:
            facilitator_programme_id = teacher.facilitator_profile.programme_id
        except FacilitatorProfile.DoesNotExist:
            facilitator_programme_id = None
        permitted = permitted or (
            teacher.programme_id == programme_id
            or facilitator_programme_id == programme_id
        )
    if not permitted:
        raise Http404

    document = getattr(teacher, field_name)
    if not document:
        raise Http404
    try:
        document.open('rb')
    except OSError as exc:
        raise Http404 from exc
    return FileResponse(
        document,
        as_attachment=True,
        filename=document.name.rsplit('/', 1)[-1],
        content_type='application/octet-stream',
    )


def protect_legacy_document_url(request, relative_path):
    requested_name = f'documents/{relative_path}'
    for field_name in ('cv_document', 'certificate_document', 'id_document'):
        teacher = TeacherProfile.objects.filter(
            **{f'{field_name}__exact': requested_name},
        ).only('pk').first()
        if teacher:
            return private_facilitator_document(
                request,
                teacher.pk,
                {
                    'cv_document': 'cv',
                    'certificate_document': 'certificate',
                    'id_document': 'identification',
                }[field_name],
            )
    raise Http404


@login_required
@user_passes_test(is_hod)
@require_http_methods(['GET', 'POST'])
def hod_facilitator_manage(request, teacher_id):
    hod = request.user.hod_profile
    teacher = get_object_or_404(
        _hod_teacher_queryset(hod.programme),
        pk=teacher_id,
    )

    try:
        facilitator = teacher.facilitator_profile
    except FacilitatorProfile.DoesNotExist:
        facilitator = FacilitatorProfile(
            teacher=teacher,
            programme=hod.programme,
        )

    if request.method == 'POST':
        form = HODFacilitatorManagementForm(
            request.POST,
            instance=facilitator,
            programme=hod.programme,
        )
        if form.is_valid():
            with transaction.atomic():
                old_supervisor_id = facilitator.supervisor_id
                updated_facilitator = form.save(commit=False)
                updated_facilitator.teacher = teacher
                updated_facilitator.programme = hod.programme
                teacher.programme = hod.programme
                if (
                    updated_facilitator.employment_type == 'INTERNSHIP'
                    and updated_facilitator.internship_start_date
                    and not updated_facilitator.internship_end_date
                ):
                    updated_facilitator.internship_end_date = add_years(
                        updated_facilitator.internship_start_date,
                        1,
                    )
                updated_facilitator.save()
                teacher.save(update_fields=['programme'])

                if old_supervisor_id != updated_facilitator.supervisor_id:
                    today = timezone.localdate()
                    InternshipSupervision.objects.filter(
                        internship=updated_facilitator,
                        is_active=True,
                    ).update(is_active=False, end_date=today)
                    if updated_facilitator.supervisor_id:
                        InternshipSupervision.objects.create(
                            internship=updated_facilitator,
                            supervisor=updated_facilitator.supervisor,
                            start_date=today,
                            changed_by=request.user,
                        )

            messages.success(request, 'Facilitator record updated.')
            return redirect('hod_facilitators')
    else:
        form = HODFacilitatorManagementForm(
            instance=facilitator,
            programme=hod.programme,
        )

    return render(request, 'hod/facilitator_manage.html', {
        'hod': hod,
        'programme': hod.programme,
        'teacher': teacher,
        'facilitator': facilitator,
        'form': form,
        'extension_form': (
            InternshipExtensionRequestForm(internship=facilitator)
            if facilitator.employment_type == 'INTERNSHIP'
            and facilitator.pk
            else None
        ),
        'supervision_history': facilitator.supervision_history.select_related(
            'supervisor__teacher__user',
            'changed_by',
        ) if facilitator.pk else [],
    })


@login_required
@user_passes_test(is_hod)
@require_POST
def hod_facilitator_status(request, teacher_id):
    hod = request.user.hod_profile
    teacher = get_object_or_404(
        _hod_teacher_queryset(hod.programme),
        pk=teacher_id,
    )
    action = request.POST.get('action')
    if action not in ('deactivate', 'reactivate'):
        messages.error(request, 'Invalid facilitator action.')
        return redirect('hod_facilitators')

    try:
        facilitator = teacher.facilitator_profile
    except FacilitatorProfile.DoesNotExist:
        facilitator = None

    if action == 'reactivate':
        if not teacher.is_approved:
            messages.error(request, 'Unapproved facilitator accounts cannot be reactivated.')
            return redirect('hod_facilitators')
        if facilitator and facilitator.is_expired:
            messages.error(request, 'Expired internship facilitators cannot be reactivated.')
            return redirect('hod_facilitators')

    if action == 'deactivate':
        _deactivate_facilitator(teacher)
    else:
        with transaction.atomic():
            if facilitator:
                facilitator.is_active = True
                facilitator.save(update_fields=['is_active', 'updated_at'])
            teacher.user.is_active = True
            teacher.user.save(update_fields=['is_active'])

    messages.success(
        request,
        f'{teacher.full_name} {action}d.',
    )
    return redirect('hod_facilitators')


@login_required
@user_passes_test(is_hod)
@require_POST
def hod_request_internship_extension(request, facilitator_id):
    hod = request.user.hod_profile
    internship = get_object_or_404(
        FacilitatorProfile.objects.select_related('teacher__user', 'programme'),
        pk=facilitator_id,
        programme_id=hod.programme_id,
        employment_type='INTERNSHIP',
    )
    form = InternshipExtensionRequestForm(
        request.POST,
        internship=internship,
    )
    if form.is_valid():
        with transaction.atomic():
            if InternshipExtension.objects.select_for_update().filter(
                internship=internship,
                status='REQUESTED',
            ).exists():
                messages.error(request, 'An extension request is already awaiting a decision.')
                return redirect('hod_facilitator_manage', teacher_id=internship.teacher_id)
            extension = form.save(commit=False)
            extension.requested_by = request.user
            extension.save()
        messages.success(request, 'Internship extension request submitted for approval.')
    else:
        messages.error(
            request,
            'Extension request could not be submitted: '
            + ' '.join(
                error for errors in form.errors.values() for error in errors
            ),
        )
    return redirect('hod_facilitator_manage', teacher_id=internship.teacher_id)


@login_required
def internship_extension_requests(request):
    if is_hod(request.user):
        role = 'hod'
        extensions = InternshipExtension.objects.filter(
            internship__programme_id=request.user.hod_profile.programme_id,
        )
    elif is_superadmin(request.user):
        role = 'superadmin'
        extensions = InternshipExtension.objects.all()
    elif is_admin(request.user):
        role = 'admin'
        extensions = InternshipExtension.objects.all()
    else:
        return redirect('login')
    extensions = extensions.select_related(
        'internship__teacher__user',
        'internship__programme',
        'requested_by',
        'approved_by',
    ).order_by('-created_at')
    context = {
        'extensions': Paginator(extensions, 25).get_page(request.GET.get('page')),
        'role': role,
        'base_template': _portal_base_for_role(role),
    }
    if role == 'hod':
        context.update({
            'hod': request.user.hod_profile,
            'programme': request.user.hod_profile.programme,
        })
    return render(request, 'internships/extensions.html', context)


@login_required
@require_POST
def decide_internship_extension(request, extension_id):
    if is_superadmin(request.user):
        role = 'superadmin'
    elif is_admin(request.user):
        role = 'admin'
    else:
        return redirect('login')
    list_url_name = {
        'admin': 'admin_extension_requests',
        'superadmin': 'superadmin_extension_requests',
    }[role]
    action = request.POST.get('action')
    if action not in ('approve', 'reject'):
        messages.error(request, 'Choose a valid extension decision.')
        return redirect(list_url_name)

    with transaction.atomic():
        extension = get_object_or_404(
            InternshipExtension.objects.select_for_update().select_related(
                'internship',
            ),
            pk=extension_id,
        )
        if extension.status != 'REQUESTED':
            messages.error(request, 'This extension request has already been decided.')
            return redirect(list_url_name)
        if action == 'approve':
            internship = FacilitatorProfile.objects.select_for_update().get(
                pk=extension.internship_id,
            )
            if internship.internship_end_date != extension.old_end_date:
                messages.error(
                    request,
                    'The internship end date changed after this request. The request cannot be approved.',
                )
                return redirect(list_url_name)
            extension.status = 'APPROVED'
            extension.approved_by = request.user
            extension.approved_at = timezone.now()
            internship.internship_end_date = extension.new_end_date
            internship.save(update_fields=['internship_end_date', 'updated_at'])
        else:
            extension.status = 'REJECTED'
            extension.approved_by = request.user
            extension.approved_at = timezone.now()
        extension.save(update_fields=['status', 'approved_by', 'approved_at'])

    messages.success(
        request,
        f'Internship extension request {action}d.',
    )
    return redirect(list_url_name)


@login_required
@user_passes_test(is_admin)
def admin_hods(request):
    return _hod_list_view(request, 'admin', 'hod_management/list.html')


@login_required
@user_passes_test(is_admin)
@require_http_methods(['GET', 'POST'])
def admin_hod_manage(request, hod_id=None):
    return _hod_manage_view(request, 'admin', hod_id)


@login_required
@user_passes_test(is_admin)
@require_POST
def admin_hod_deactivate(request, hod_id):
    return _hod_deactivate_view(request, 'admin', hod_id)


@login_required
@user_passes_test(is_superadmin)
def superadmin_hods(request):
    return _hod_list_view(request, 'superadmin', 'hod_management/list.html')


@login_required
@user_passes_test(is_superadmin)
@require_http_methods(['GET', 'POST'])
def superadmin_hod_manage(request, hod_id=None):
    return _hod_manage_view(request, 'superadmin', hod_id)


@login_required
@user_passes_test(is_superadmin)
@require_POST
def superadmin_hod_deactivate(request, hod_id):
    return _hod_deactivate_view(request, 'superadmin', hod_id)


# ── Teacher Profile Complete / Update / Change Password ───────────────────────


@login_required
@user_passes_test(has_facilitator_account_access)
def teacher_update_profile(request):
    """Teacher updates their profile details."""
    if request.user.is_staff:
        return redirect('admin_dashboard')
    try:
        profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return redirect('welcome')

    from .models import DEPARTMENT_CHOICES, COURSE_TYPE_CHOICES, GENDER_CHOICES, QUALIFICATION_CHOICES, ELECTIVE_SUBJECTS, DEPARTMENT_SUBJECTS
    errors = {}
    success = None
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        gender = request.POST.get('gender', '').strip()
        address = request.POST.get('address', '').strip()
        qualification = request.POST.get('qualification', '').strip()
        specialization = request.POST.get('specialization', '').strip()
        years_exp = request.POST.get('years_of_experience', '0').strip()
        course_type = request.POST.get('course_type', '').strip()
        preferred_subject = request.POST.get('preferred_subject', '').strip()[:100]
        department = request.POST.get('department', '').strip()[:100]
        previous_school = request.POST.get('previous_school', '').strip()
        bio = request.POST.get('bio', '').strip()
        emergency_contact = request.POST.get('emergency_contact', '').strip()
        emergency_phone = request.POST.get('emergency_phone', '').strip()
        email = request.POST.get('email', '').strip()
        dob_raw = request.POST.get('date_of_birth', '').strip()

        if not first_name: errors['first_name'] = 'First name is required.'
        if not last_name: errors['last_name'] = 'Last name is required.'
        if not phone: errors['phone'] = 'Phone is required.'
        if not gender: errors['gender'] = 'Gender is required.'
        if not address: errors['address'] = 'Address is required.'
        if not qualification: errors['qualification'] = 'Qualification is required.'
        if not specialization: errors['specialization'] = 'Specialization is required.'
        if not course_type: errors['course_type'] = 'Course type is required.'
        if not preferred_subject: errors['preferred_subject'] = 'Subject is required.'
        if course_type == 'departmental' and not department:
            errors['department'] = 'Please select your department.'

        _validate_teacher_profile_lengths({
            'first_name': first_name, 'last_name': last_name, 'phone': phone,
            'emergency_phone': emergency_phone, 'emergency_contact': emergency_contact,
            'specialization': specialization, 'previous_school': previous_school,
        }, errors)
        _validate_teacher_uploads(request.FILES, errors)
        date_of_birth = _parse_dob_or_error(dob_raw, errors)

        if not errors:
            user = request.user
            user.first_name = first_name[:150]
            user.last_name = last_name[:150]
            if email:
                user.email = email[:254]
            profile.phone = phone[:20]
            profile.gender = gender
            profile.date_of_birth = date_of_birth
            profile.address = address
            profile.qualification = qualification
            profile.specialization = specialization[:200]
            profile.years_of_experience = _safe_years_experience(years_exp)
            profile.course_type = course_type
            profile.preferred_subject = preferred_subject
            profile.department = department if course_type == 'departmental' else ''
            profile.previous_school = previous_school[:200]
            profile.bio = bio
            profile.emergency_contact = emergency_contact[:100]
            profile.emergency_phone = emergency_phone[:20]
            if request.FILES.get('profile_photo'):
                profile.profile_photo = request.FILES['profile_photo']
            if request.FILES.get('cv_document'):
                profile.cv_document = request.FILES['cv_document']
            if request.FILES.get('certificate_document'):
                profile.certificate_document = request.FILES['certificate_document']
            if request.FILES.get('id_document'):
                profile.id_document = request.FILES['id_document']
            try:
                with transaction.atomic():
                    user.save()
                    profile.save()
                success = 'Profile updated successfully!'
            except Exception:
                logger.exception('teacher_update_profile save failed')
                errors['save'] = (
                    'Could not save changes. Try smaller files (under 10 MB each) or remove optional uploads.'
                )
                profile.refresh_from_db()
                user.refresh_from_db()

    dob_input_value = _dob_input_value(request.POST, profile)
    return render(request, 'teacher/update_profile.html', {
        'profile': profile,
        'errors': errors,
        'success': success,
        'post': request.POST,
        'dob_input_value': dob_input_value,
        'dept_choices': DEPARTMENT_CHOICES,
        'course_types': COURSE_TYPE_CHOICES,
        'genders': GENDER_CHOICES,
        'qualifications': QUALIFICATION_CHOICES,
        'elective_subjects': ELECTIVE_SUBJECTS,
        'dept_subjects': DEPARTMENT_SUBJECTS,
    })


@login_required
@user_passes_test(has_facilitator_account_access)
def teacher_change_password(request):
    """Teacher changes their own password."""
    if request.user.is_staff:
        return redirect('admin_dashboard')
    error = None
    success = None
    if request.method == 'POST':
        current = request.POST.get('current_password', '').strip()
        new_pw = request.POST.get('new_password', '').strip()
        confirm = request.POST.get('confirm_password', '').strip()
        if not current:
            error = 'Please enter your current password.'
        elif not authenticate(request, username=request.user.username, password=current):
            error = 'Current password is incorrect.'
        elif not new_pw:
            error = 'New password is required.'
        elif new_pw != confirm:
            error = 'New passwords do not match.'
        else:
            try:
                validate_password(new_pw, user=request.user)
            except ValidationError as exc:
                error = ' '.join(exc.messages)
            else:
                request.user.set_password(new_pw)
                request.user.save()
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, request.user)
                success = 'Password changed successfully!'
    return render(request, 'teacher/change_password.html', {'error': error, 'success': success})


@login_required
@require_POST
def mark_notification_read(request, notif_id):
    notif = get_object_or_404(Notification, id=notif_id, recipient=request.user)
    notif.is_read = True
    notif.save()
    return JsonResponse({'status': 'ok'})


@login_required
@user_passes_test(is_facilitator)
def qr_scanner(request):
    return render(request, 'qr/scanner.html')


@login_required
@user_passes_test(is_facilitator)
def view_timetable(request):
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    profile = request.user.teacher_profile
    timetable_by_day = [
        (
            day,
            list(
                TimetableEntry.objects.filter(
                    day=day,
                    teacher=profile,
                ).order_by('start_time')
            ),
        )
        for day in days
    ]
    all_notifications = Notification.objects.filter(recipient=request.user).order_by('-created_at')[:20]
    unread_notifications = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return render(request, 'timetable.html', {
        'timetable_by_day': timetable_by_day, 'days': days,
        'profile': profile,
        'all_notifications': all_notifications,
        'unread_notifications': unread_notifications,
    })


# ── QR Scanning ───────────────────────────────────────────────────────────────

def _scan_attendance_qr(request, qr_uuid, qr_type):
    qr_obj = get_object_or_404(
        QRCode,
        code=qr_uuid,
        qr_type=qr_type,
        is_active=True,
    )
    if not qr_obj.is_valid_today:
        return render(request, 'qr/scan_result.html', {
            'success': False,
            'message': 'This QR code has expired.',
        })
    if request.method == 'GET':
        return render(request, 'qr/scan_result.html', {
            'success': False,
            'message': 'Attendance can only be submitted from the Facilitator QR scanner.',
            'retry': True,
        })
    if not is_facilitator(request.user):
        return render(request, 'qr/scan_result.html', {
            'success': False,
            'message': 'An active, approved Facilitator account is required.',
        }, status=403)

    profile = request.user.teacher_profile
    try:
        latitude = Decimal(request.POST.get('lat', ''))
        longitude = Decimal(request.POST.get('lon', ''))
    except InvalidOperation:
        latitude = longitude = None
    if (
        latitude is None
        or longitude is None
        or not latitude.is_finite()
        or not longitude.is_finite()
        or not Decimal('-90') <= latitude <= Decimal('90')
        or not Decimal('-180') <= longitude <= Decimal('180')
    ):
        return render(request, 'qr/scan_result.html', {
            'success': False,
            'message': 'A valid location is required. Please enable location services and retry.',
            'retry': True,
        }, status=400)

    school_location = SchoolLocation.objects.filter(is_active=True).first()
    if not school_location:
        return render(request, 'qr/scan_result.html', {
            'success': False,
            'message': 'School location is not configured. Please contact administration.',
        }, status=503)
    if not school_location.is_within_radius(latitude, longitude):
        return render(request, 'qr/scan_result.html', {
            'success': False,
            'message': (
                'You are outside the school attendance radius '
                f'({school_location.allowed_radius}m). Move closer and retry.'
            ),
            'retry': True,
        }, status=403)

    today = timezone.localdate()
    now = timezone.localtime().time()
    is_school_scan = qr_type == 'school_attendance'
    with transaction.atomic():
        attendance, created = Attendance.objects.get_or_create(
            teacher=profile,
            date=today,
            defaults=(
                {
                    'school_qr': qr_obj,
                    'check_in_time': now,
                    'school_status': 'present',
                }
                if is_school_scan
                else {
                    'lesson_qr': qr_obj,
                    'lesson_done_time': now,
                    'lesson_status': 'done',
                }
            ),
        )
        changed = created
        if not created and is_school_scan and attendance.school_status == 'absent':
            attendance.school_qr = qr_obj
            attendance.check_in_time = now
            attendance.school_status = 'present'
            attendance.save(update_fields=[
                'school_qr',
                'check_in_time',
                'school_status',
            ])
            changed = True
        elif (
            not created
            and not is_school_scan
            and attendance.lesson_status != 'done'
        ):
            attendance.lesson_qr = qr_obj
            attendance.lesson_done_time = now
            attendance.lesson_status = 'done'
            attendance.save(update_fields=[
                'lesson_qr',
                'lesson_done_time',
                'lesson_status',
            ])
            changed = True

    verb = (
        'checked in'
        if is_school_scan
        else 'completed lesson'
    )
    msg = (
        f'{profile.full_name} {verb} at {now.strftime("%H:%M")}.'
        if changed
        else f'{profile.full_name} has already recorded this attendance.'
    )
    if changed:
        for admin in User.objects.filter(is_staff=True):
            Notification.objects.create(
                recipient=admin,
                sender=request.user,
                title=f'Attendance: {profile.full_name}',
                message=msg,
                notification_type='attendance',
            )
    return render(request, 'qr/scan_result.html', {
        'success': True,
        'scan_type': 'school' if is_school_scan else 'lesson_done',
        'message': msg,
        'teacher': profile,
        'time': now.strftime('%H:%M'),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def scan_school_qr(request, qr_uuid):
    return _scan_attendance_qr(request, qr_uuid, 'school_attendance')


@login_required
@require_http_methods(['GET', 'POST'])
def scan_lesson_qr(request, qr_uuid):
    return _scan_attendance_qr(request, qr_uuid, 'lesson')


# ── Admin Dashboard ───────────────────────────────────────────────────────────

@login_required
@user_passes_test(is_admin)
def admin_dashboard(request):
    today = timezone.localdate()
    approved_teachers = TeacherProfile.objects.filter(is_approved=True).select_related('user')
    pending_teachers = TeacherProfile.objects.filter(is_approved=False)
    today_att = Attendance.objects.filter(date=today).select_related('teacher__user')
    present_count = today_att.filter(school_status='present').count()
    absent_count = today_att.filter(school_status='absent').count()
    verified_teacher_ids = today_att.exclude(
        school_status='unverified',
    ).values_list('teacher_id', flat=True)
    unverified_count = approved_teachers.exclude(
        pk__in=verified_teacher_ids,
    ).count()
    recent_notifs = Notification.objects.filter(recipient=request.user).order_by('-created_at')[:10]
    school_qr = QRCode.objects.filter(qr_type='school_attendance', is_active=True).first()
    lesson_qr = QRCode.objects.filter(qr_type='lesson', is_active=True).first()
    day_name = today.strftime('%A').lower()
    today_timetable = TimetableEntry.objects.filter(day=day_name).order_by('start_time')

    # Group teachers by department/course_type
    from .models import DEPARTMENT_CHOICES
    departments = {}
    for val, label in DEPARTMENT_CHOICES:
        dept_teachers = approved_teachers.filter(department=val)
        if dept_teachers.exists():
            departments[label] = dept_teachers
    core_teachers = approved_teachers.filter(course_type='core')
    if core_teachers.exists():
        departments['Core Subjects'] = core_teachers

    return render(request, 'admin/dashboard.html', {
        'approved_teachers': approved_teachers,
        'pending_teachers': pending_teachers,
        'today_att': today_att,
        'present_count': present_count,
        'absent_count': absent_count,
        'unverified_count': unverified_count,
        'total_teachers': approved_teachers.count(),
        'recent_notifs': recent_notifs,
        'school_qr': school_qr,
        'lesson_qr': lesson_qr,
        'today_timetable': today_timetable,
        'today': today,
        'departments': departments,
    })


@login_required
@user_passes_test(is_management_admin)
def admin_qr_view(request):
    school_qrs = QRCode.objects.filter(qr_type='school_attendance').order_by('-created_at')
    lesson_qrs = QRCode.objects.filter(qr_type='lesson').order_by('-created_at')
    return render(request, 'admin/qr_view.html', {'school_qrs': school_qrs, 'lesson_qrs': lesson_qrs})


# ── School Location Management ─────────────────────────────────────────────────

@login_required
@user_passes_test(is_superadmin)
def school_location_list(request):
    locations = SchoolLocation.objects.all().order_by('-created_at')
    return render(request, 'admin/school_location_list.html', {'locations': locations})


@login_required
@user_passes_test(is_superadmin)
def school_location_create(request):
    if request.method == 'POST':
        form = SchoolLocationForm(request.POST)
        if form.is_valid():
            # Deactivate all other locations if this one is set to active
            if form.cleaned_data.get('is_active'):
                SchoolLocation.objects.update(is_active=False)
            form.save()
            messages.success(request, 'School location created successfully.')
            return redirect('school_location_list')
    else:
        form = SchoolLocationForm()
    return render(request, 'admin/school_location_form.html', {'form': form, 'title': 'Add School Location'})


@login_required
@user_passes_test(is_superadmin)
def school_location_update(request, location_id):
    location = get_object_or_404(SchoolLocation, id=location_id)
    if request.method == 'POST':
        form = SchoolLocationForm(request.POST, instance=location)
        if form.is_valid():
            # Deactivate all other locations if this one is set to active
            if form.cleaned_data.get('is_active'):
                SchoolLocation.objects.exclude(id=location_id).update(is_active=False)
            form.save()
            messages.success(request, 'School location updated successfully.')
            return redirect('school_location_list')
    else:
        form = SchoolLocationForm(instance=location)
    return render(request, 'admin/school_location_form.html', {'form': form, 'title': 'Edit School Location', 'location': location})


@login_required
@user_passes_test(is_superadmin)
def school_location_delete(request, location_id):
    location = get_object_or_404(SchoolLocation, id=location_id)
    if request.method == 'POST':
        location.delete()
        messages.success(request, 'School location deleted successfully.')
        return redirect('school_location_list')
    return render(request, 'admin/school_location_confirm_delete.html', {'location': location})


@login_required
@user_passes_test(is_admin)
def teacher_detail(request, teacher_id):
    teacher = get_object_or_404(TeacherProfile, id=teacher_id)
    attendances = Attendance.objects.filter(teacher=teacher).order_by('-date')[:30]
    return render(request, 'admin/teacher_detail.html', {'teacher': teacher, 'attendances': attendances})


@login_required
@user_passes_test(is_admin)
def admin_department_view(request, dept_slug):
    """Show all teachers in a specific department with their attendance."""
    from .models import DEPARTMENT_CHOICES
    dept_map = dict(DEPARTMENT_CHOICES)
    dept_name = dept_map.get(dept_slug, dept_slug.replace('_', ' ').title())

    if dept_slug == 'core':
        teachers = TeacherProfile.objects.filter(
            is_approved=True, course_type='core'
        ).select_related('user').order_by('preferred_subject')
        dept_name = 'Core Subjects'
    else:
        teachers = TeacherProfile.objects.filter(
            is_approved=True, department=dept_slug
        ).select_related('user')

    today = timezone.localdate()
    # Get today's attendance for these teachers
    teacher_ids = teachers.values_list('id', flat=True)
    today_att = Attendance.objects.filter(
        date=today, teacher_id__in=teacher_ids
    ).select_related('teacher__user')
    att_map = {a.teacher_id: a for a in today_att}

    teacher_data = []
    for t in teachers:
        att = att_map.get(t.id)
        teacher_data.append({
            'teacher': t,
            'attendance': att,
            'school_status': att.school_status if att else 'unverified',
            'lesson_status': att.lesson_status if att else 'pending',
            'check_in': att.check_in_time if att else None,
            'lesson_done': att.lesson_done_time if att else None,
        })

    return render(request, 'admin/department_view.html', {
        'dept_name': dept_name,
        'dept_slug': dept_slug,
        'teacher_data': teacher_data,
        'today': today,
        'total': teachers.count(),
        'present': sum(1 for d in teacher_data if d['school_status'] == 'present'),
        'lesson_done': sum(1 for d in teacher_data if d['lesson_status'] == 'done'),
        'is_core': dept_slug == 'core',
    })


@login_required
@user_passes_test(is_superadmin)
def qr_detail_sa(request, qr_id):
    """Super admin QR detail view."""
    return qr_detail(request, qr_id)


@login_required
@user_passes_test(is_superadmin)
def delete_qr(request, qr_id):
    """Permanently remove a generated QR code (superadmin only)."""
    qr_obj = get_object_or_404(QRCode, id=qr_id)
    if request.method != 'POST':
        messages.error(request, 'To delete a QR code, use the delete action from the QR management page.')
        return redirect('qr_detail_sa', qr_id=qr_id)
    label = qr_obj.label
    pk = qr_obj.pk
    try:
        with transaction.atomic():
            # Clear FK refs first (handles any DB quirks; Attendance uses SET_NULL).
            Attendance.objects.filter(school_qr_id=pk).update(school_qr=None)
            Attendance.objects.filter(lesson_qr_id=pk).update(lesson_qr=None)

            img = qr_obj.qr_image
            if getattr(img, 'name', None):
                try:
                    img.delete(save=False)
                except Exception as exc:
                    logger.warning('QR image delete skipped (id=%s): %s', pk, exc)

            qr_obj.delete()
    except ProtectedError:
        messages.error(request, 'This QR code cannot be deleted because other records depend on it.')
        return redirect('qr_detail_sa', qr_id=qr_id)
    except Exception:
        logger.exception('delete_qr failed for id=%s', qr_id)
        messages.error(request, 'Deleting this QR code failed. Try again later.')
        return redirect('qr_detail_sa', qr_id=qr_id)

    messages.success(request, f'QR code "{label}" was permanently deleted.')
    return redirect('generate_qr')


@login_required
@user_passes_test(is_admin)
def admin_teachers(request):
    teachers = TeacherProfile.objects.select_related('user').all().order_by('-date_joined')
    return render(request, 'admin/teachers.html', {'teachers': teachers})


@login_required
@user_passes_test(is_admin)
@require_POST
def approve_teacher(request, teacher_id):
    teacher = get_object_or_404(TeacherProfile, id=teacher_id)
    teacher.is_approved = True
    teacher.save()
    Notification.objects.create(
        recipient=teacher.user, sender=request.user,
        title='Account Approved',
        message='Your facilitator account has been approved. You can now log in.',
        notification_type='admin'
    )
    messages.success(request, f'{teacher.full_name} approved.')
    return redirect('admin_teachers')


@login_required
@user_passes_test(is_admin)
@require_POST
def reject_teacher(request, teacher_id):
    teacher = get_object_or_404(TeacherProfile, id=teacher_id)
    name = teacher.full_name
    _deactivate_facilitator(teacher)
    messages.warning(request, f'{name} rejected; the account is disabled and records were retained.')
    return redirect('admin_teachers')


@login_required
@user_passes_test(is_admin)
@require_http_methods(['GET', 'POST'])
def delete_teacher(request, teacher_id):
    teacher = get_object_or_404(TeacherProfile, id=teacher_id)
    if request.method == 'POST':
        name = teacher.full_name
        _deactivate_facilitator(teacher)
        messages.success(request, f'{name} deactivated; account and history were retained.')
        return redirect('admin_teachers')
    return render(request, 'admin/confirm_delete.html', {'teacher': teacher})


def _deactivate_facilitator(teacher):
    with transaction.atomic():
        teacher.user.is_active = False
        teacher.user.save(update_fields=['is_active'])
        try:
            facilitator = teacher.facilitator_profile
        except FacilitatorProfile.DoesNotExist:
            return

        facilitator.is_active = False
        facilitator.save(update_fields=['is_active', 'updated_at'])
        today = timezone.localdate()
        InternshipSupervision.objects.filter(
            internship=facilitator,
            is_active=True,
        ).update(is_active=False, end_date=today)
        if facilitator.employment_type == 'PERMANENT':
            InternshipSupervision.objects.filter(
                supervisor=facilitator,
                is_active=True,
            ).update(is_active=False, end_date=today)
            FacilitatorProfile.objects.filter(
                supervisor=facilitator,
            ).update(supervisor=None)


@login_required
@user_passes_test(is_management_admin)
def admin_attendance(request):
    today = timezone.localdate()
    selected_date = request.GET.get('date', str(today))
    try:
        from datetime import date as dt
        selected_date = dt.fromisoformat(selected_date)
    except ValueError:
        selected_date = today
    attendances = Attendance.objects.filter(date=selected_date).select_related('teacher__user')
    approved_teachers = TeacherProfile.objects.filter(is_approved=True)
    absent_ids = attendances.filter(
        school_status='absent',
    ).values_list('teacher_id', flat=True)
    verified_ids = attendances.exclude(
        school_status='unverified',
    ).values_list('teacher_id', flat=True)
    absent_teachers = approved_teachers.filter(id__in=absent_ids)
    unverified_teachers = approved_teachers.exclude(id__in=verified_ids)
    lesson_done_ids = attendances.filter(lesson_status='done').values_list('teacher_id', flat=True)
    lesson_not_done = approved_teachers.exclude(id__in=lesson_done_ids)
    return render(request, 'admin/attendance.html', {
        'attendances': attendances,
        'absent_teachers': absent_teachers,
        'unverified_teachers': unverified_teachers,
        'lesson_not_done': lesson_not_done,
        'selected_date': selected_date,
        'today': today,
    })


@login_required
@user_passes_test(is_management_admin)
@require_http_methods(['GET', 'POST'])
def admin_timetable(request, entry_id=None):
    entry = get_object_or_404(TimetableEntry, pk=entry_id) if entry_id else None
    if request.method == 'POST':
        form = TimetableEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                'Timetable entry updated.' if entry else 'Timetable entry added.',
            )
            return redirect('admin_timetable')
    else:
        form = TimetableEntryForm(instance=entry)
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    timetable_by_day = [(d, list(TimetableEntry.objects.filter(day=d).order_by('start_time'))) for d in days]
    return render(request, 'admin/timetable.html', {
        'form': form,
        'timetable_by_day': timetable_by_day,
        'days': days,
        'editing_entry': entry,
    })


@login_required
@user_passes_test(is_management_admin)
@require_POST
def delete_timetable_entry(request, entry_id):
    get_object_or_404(TimetableEntry, id=entry_id).delete()
    messages.success(request, 'Entry deleted.')
    return redirect('admin_timetable')


@login_required
@user_passes_test(is_management_admin)
def admin_schedule(request):
    scheduling_evaluation = None
    if request.method == 'POST':
        form = ClassScheduleForm(request.POST)
        if form.is_valid():
            proposed = form.save(commit=False)
            if request.POST.get('action') != 'request_confirmation':
                form.add_error(
                    None,
                    'Direct scheduling is disabled. Use Evaluate & Request '
                    'Confirmation so the Facilitator can accept or decline.',
                )
            else:
                try:
                    facilitator = proposed.teacher.facilitator_profile
                except FacilitatorProfile.DoesNotExist:
                    facilitator = None
                programme = (
                    facilitator.programme
                    if facilitator
                    else proposed.teacher.programme
                )
                if programme is None:
                    form.add_error(
                        'teacher',
                        'This account has no assigned programme and cannot be evaluated.',
                    )
                else:
                    scheduling_evaluation = evaluate_class_assignment(
                        proposed_class=proposed.timetable_entry,
                        scheduled_date=proposed.scheduled_date,
                        start_time=proposed.timetable_entry.start_time,
                        end_time=proposed.timetable_entry.end_time,
                        programme=programme,
                        candidate=proposed.teacher,
                        evaluated_by=request.user,
                    )
                    if scheduling_evaluation.status != SCHEDULING_ELIGIBLE:
                        messages.warning(
                            request,
                            f'No confirmation request was sent. Evaluation: '
                            f'{scheduling_evaluation.get_status_display()}. '
                            'Review the recorded reasons below.',
                        )
                    else:
                        try:
                            with transaction.atomic():
                                confirmation = ClassConfirmationRequest.objects.create(
                                    programme=programme,
                                    candidate=proposed.teacher,
                                    timetable_entry=proposed.timetable_entry,
                                    scheduled_date=proposed.scheduled_date,
                                    start_time=proposed.timetable_entry.start_time,
                                    end_time=proposed.timetable_entry.end_time,
                                    evaluation=scheduling_evaluation,
                                    requested_by=request.user,
                                )
                                Notification.objects.create(
                                    recipient=proposed.teacher.user,
                                    sender=request.user,
                                    title='Class Confirmation Request',
                                    message=(
                                        f'Please respond to the request for '
                                        f'{proposed.timetable_entry.subject} on '
                                        f'{proposed.scheduled_date} from '
                                        f'{proposed.timetable_entry.start_time:%H:%M} '
                                        f'to {proposed.timetable_entry.end_time:%H:%M}. '
                                        'This is a request, not a confirmed class.'
                                    ),
                                    notification_type='schedule',
                                    related_timetable=proposed.timetable_entry,
                                )
                        except IntegrityError:
                            messages.warning(
                                request,
                                'A confirmation request for this class and '
                                'date was already created.',
                            )
                        else:
                            messages.success(
                                request,
                                f'Confirmation request sent to '
                                f'{proposed.teacher.full_name}. The class is '
                                'not assigned unless the Facilitator accepts.',
                            )
                            return redirect('admin_schedule')
    else:
        form = ClassScheduleForm()
    schedules = ClassSchedule.objects.select_related('teacher__user', 'timetable_entry').order_by('-scheduled_date')[:50]
    confirmation_requests = ClassConfirmationRequest.objects.select_related(
        'candidate__user',
        'timetable_entry',
    ).order_by('-created_at')[:50]
    return render(request, 'admin/schedule.html', {
        'form': form,
        'schedules': schedules,
        'confirmation_requests': confirmation_requests,
        'scheduling_evaluation': scheduling_evaluation,
    })


def _notify_programme_hod(programme, *, sender, title, message):
    try:
        hod = programme.hod_profile
    except HODProfile.DoesNotExist:
        return False
    if not hod.is_active or not hod.user.is_active:
        return False
    Notification.objects.create(
        recipient=hod.user,
        sender=sender,
        title=title,
        message=message,
        notification_type='schedule',
    )
    return True


def _open_class_replacement_review(confirmation, *, response_note, sender):
    review, created = ClassReplacementRequest.objects.get_or_create(
        original_request=confirmation,
        defaults={
            'programme': confirmation.programme,
            'reason': response_note or 'The assigned Facilitator declined the request.',
        },
    )
    if created:
        candidate_profiles = FacilitatorProfile.objects.filter(
            programme=confirmation.programme,
        ).exclude(
            teacher=confirmation.candidate,
        ).select_related('teacher__user')
        result = rank_class_candidates(
            proposed_class=confirmation.timetable_entry,
            scheduled_date=confirmation.scheduled_date,
            start_time=confirmation.start_time,
            end_time=confirmation.end_time,
            programme=confirmation.programme,
            candidates=candidate_profiles,
            evaluated_by=sender,
        )
        review.recommendations.set(result['evaluations'])
    notified = _notify_programme_hod(
        confirmation.programme,
        sender=sender,
        title='Class replacement requires HOD review',
        message=(
            f'{confirmation.candidate.full_name} declined '
            f'{confirmation.timetable_entry.subject} on '
            f'{confirmation.scheduled_date}. No replacement was assigned. '
            'Review candidate evaluations and approve any replacement before '
            'a new confirmation request can be sent.'
        ),
    )
    return review, notified


@login_required
@user_passes_test(is_facilitator)
@require_POST
def respond_to_class_confirmation(request, confirmation_id):
    with transaction.atomic():
        confirmation = get_object_or_404(
            ClassConfirmationRequest.objects.select_for_update().select_related(
                'programme',
                'candidate__user',
                'timetable_entry',
                'requested_by',
            ),
            pk=confirmation_id,
            candidate=request.user.teacher_profile,
        )
        if confirmation.status != 'PENDING':
            messages.error(request, 'This confirmation request is no longer pending.')
            return redirect('facilitator_dashboard')

        action = request.POST.get('action')
        if action not in {'accept', 'decline'}:
            messages.error(request, 'Choose whether to accept or decline the request.')
            return redirect('facilitator_dashboard')

        if action == 'accept':
            evaluation = evaluate_class_assignment(
                proposed_class=confirmation.timetable_entry,
                scheduled_date=confirmation.scheduled_date,
                start_time=confirmation.start_time,
                end_time=confirmation.end_time,
                programme=confirmation.programme,
                candidate=confirmation.candidate,
                evaluated_by=request.user,
                exclude_confirmation_request_id=confirmation.pk,
            )
            confirmation.evaluation = evaluation
            confirmation.responded_by = request.user
            confirmation.responded_at = timezone.now()
            if evaluation.status != SCHEDULING_ELIGIBLE:
                confirmation.status = 'WITHHELD'
                confirmation.response_note = (
                    'Acceptance was withheld because the latest scheduling '
                    f'evaluation is {evaluation.get_status_display()}.'
                )
                confirmation.save(update_fields=[
                    'evaluation',
                    'responded_by',
                    'responded_at',
                    'status',
                    'response_note',
                ])
                _notify_programme_hod(
                    confirmation.programme,
                    sender=request.user,
                    title='Class confirmation withheld',
                    message=(
                        f'{confirmation.timetable_entry.subject} for '
                        f'{confirmation.candidate.full_name} was not assigned '
                        f'after re-evaluation ({evaluation.get_status_display()}).'
                    ),
                )
                messages.warning(
                    request,
                    'The assignment was not created because a required rule '
                    'is blocked or unverified.',
                )
                return redirect('facilitator_dashboard')

            schedule = ClassSchedule.objects.create(
                timetable_entry=confirmation.timetable_entry,
                teacher=confirmation.candidate,
                scheduled_date=confirmation.scheduled_date,
                created_by=confirmation.requested_by,
            )
            confirmation.status = 'ACCEPTED'
            confirmation.response_note = request.POST.get('response_note', '').strip()
            confirmation.confirmed_schedule = schedule
            confirmation.save(update_fields=[
                'evaluation',
                'responded_by',
                'responded_at',
                'status',
                'response_note',
                'confirmed_schedule',
            ])
            if confirmation.requested_by_id:
                Notification.objects.create(
                    recipient=confirmation.requested_by,
                    sender=request.user,
                    title='Class confirmation accepted',
                    message=(
                        f'{confirmation.candidate.full_name} accepted '
                        f'{confirmation.timetable_entry.subject} on '
                        f'{confirmation.scheduled_date}.'
                    ),
                    notification_type='schedule',
                    related_timetable=confirmation.timetable_entry,
                )
            messages.success(request, 'Your acceptance was recorded; the class is now confirmed.')
            return redirect('facilitator_dashboard')

        confirmation.status = 'DECLINED'
        confirmation.response_note = request.POST.get('response_note', '').strip()
        confirmation.responded_by = request.user
        confirmation.responded_at = timezone.now()
        confirmation.save(update_fields=[
            'status',
            'response_note',
            'responded_by',
            'responded_at',
        ])
        _, notified = _open_class_replacement_review(
            confirmation,
            response_note=confirmation.response_note,
            sender=request.user,
        )
        if notified:
            messages.info(
                request,
                'Your decline was recorded and the replacement request was sent '
                'to your programme HOD for review.',
            )
        else:
            messages.warning(
                request,
                'Your decline was recorded, but no active HOD is assigned to '
                'this programme. No replacement was assigned.',
            )
    return redirect('facilitator_dashboard')


@login_required
@user_passes_test(is_hod)
def hod_class_replacements(request):
    programme_id = request.user.hod_profile.programme_id
    reviews = ClassReplacementRequest.objects.filter(
        programme_id=programme_id,
        status='PENDING',
    ).select_related(
        'original_request__candidate__user',
        'original_request__timetable_entry',
    ).prefetch_related(
        'recommendations__candidate__user',
    )
    return render(request, 'hod/replacement_reviews.html', {
        'hod': request.user.hod_profile,
        'programme': request.user.hod_profile.programme,
        'reviews': reviews,
    })


@login_required
@user_passes_test(is_hod)
@require_POST
def hod_class_replacement_decision(request, review_id):
    hod = request.user.hod_profile
    with transaction.atomic():
        review = get_object_or_404(
            ClassReplacementRequest.objects.select_for_update().select_related(
                'programme',
                'original_request__candidate__user',
                'original_request__timetable_entry',
            ),
            pk=review_id,
            programme_id=hod.programme_id,
        )
        if review.status != 'PENDING':
            messages.error(request, 'This replacement request has already been processed.')
            return redirect('hod_class_replacements')

        action = request.POST.get('action')
        decision_note = request.POST.get('decision_note', '').strip()
        if action == 'reject':
            review.status = 'REJECTED'
            review.decision_note = decision_note or 'Rejected by the programme HOD.'
            review.decided_by = request.user
            review.decided_at = timezone.now()
            review.save(update_fields=[
                'status',
                'decision_note',
                'decided_by',
                'decided_at',
            ])
            notified_users = {
                review.original_request.candidate.user_id:
                    review.original_request.candidate.user,
            }
            if review.original_request.requested_by_id:
                notified_users[
                    review.original_request.requested_by_id
                ] = review.original_request.requested_by
            for recipient in notified_users.values():
                Notification.objects.create(
                    recipient=recipient,
                    sender=request.user,
                    title='Class replacement request rejected',
                    message=(
                        f'The HOD rejected the replacement request for '
                        f'{review.original_request.timetable_entry.subject} '
                        f'on {review.original_request.scheduled_date}. '
                        f'{review.decision_note}'
                    ),
                    notification_type='schedule',
                    related_timetable=review.original_request.timetable_entry,
                )
            messages.success(request, 'The replacement request was rejected; no class was reassigned.')
            return redirect('hod_class_replacements')

        if action != 'approve':
            messages.error(request, 'Choose approve or reject.')
            return redirect('hod_class_replacements')

        try:
            candidate_id = int(request.POST.get('candidate_id', ''))
        except ValueError:
            messages.error(request, 'Select an evaluated replacement candidate.')
            return redirect('hod_class_replacements')

        recommendation = review.recommendations.filter(
            candidate_id=candidate_id,
            status=SCHEDULING_ELIGIBLE,
        ).select_related('candidate__user').first()
        if recommendation is None:
            messages.error(
                request,
                'Only a candidate currently marked ELIGIBLE by the scheduling '
                'engine can be selected.',
            )
            return redirect('hod_class_replacements')

        original = review.original_request
        if ClassConfirmationRequest.objects.filter(
            timetable_entry=original.timetable_entry,
            scheduled_date=original.scheduled_date,
            status='PENDING',
        ).exists():
            messages.error(
                request,
                'Another confirmation request for this class and date is '
                'already awaiting a response.',
            )
            return redirect('hod_class_replacements')

        evaluation = evaluate_class_assignment(
            proposed_class=original.timetable_entry,
            scheduled_date=original.scheduled_date,
            start_time=original.start_time,
            end_time=original.end_time,
            programme=review.programme,
            candidate=recommendation.candidate,
            evaluated_by=request.user,
        )
        if evaluation.status != SCHEDULING_ELIGIBLE:
            review.recommendations.add(evaluation)
            messages.error(
                request,
                'The candidate no longer passes the scheduling checks '
                f'({evaluation.get_status_display()}); no confirmation request was sent.',
            )
            return redirect('hod_class_replacements')

        replacement_confirmation = ClassConfirmationRequest.objects.create(
            programme=review.programme,
            candidate=recommendation.candidate,
            timetable_entry=original.timetable_entry,
            scheduled_date=original.scheduled_date,
            start_time=original.start_time,
            end_time=original.end_time,
            evaluation=evaluation,
            requested_by=request.user,
            replaces=original,
        )
        review.status = 'APPROVED'
        review.selected_candidate = recommendation.candidate
        review.replacement_confirmation = replacement_confirmation
        review.decision_note = decision_note
        review.decided_by = request.user
        review.decided_at = timezone.now()
        review.save(update_fields=[
            'status',
            'selected_candidate',
            'replacement_confirmation',
            'decision_note',
            'decided_by',
            'decided_at',
        ])
        notified_users = {
            original.candidate.user_id: original.candidate.user,
        }
        if original.requested_by_id:
            notified_users[original.requested_by_id] = original.requested_by
        for recipient in notified_users.values():
            Notification.objects.create(
                recipient=recipient,
                sender=request.user,
                title='HOD approved class replacement',
                message=(
                    f'The HOD approved {recommendation.candidate.full_name} '
                    f'to respond to a confirmation request for '
                    f'{original.timetable_entry.subject} on '
                    f'{original.scheduled_date}. No class is assigned until '
                    'the replacement Facilitator accepts.'
                ),
                notification_type='schedule',
                related_timetable=original.timetable_entry,
            )
        Notification.objects.create(
            recipient=recommendation.candidate.user,
            sender=request.user,
            title='HOD-approved class confirmation request',
            message=(
                f'The HOD approved you to respond to a confirmation request for '
                f'{original.timetable_entry.subject} on {original.scheduled_date}. '
                'This remains unconfirmed until you accept.'
            ),
            notification_type='schedule',
            related_timetable=original.timetable_entry,
        )
        messages.success(
            request,
            'Replacement approval recorded. A confirmation request was sent; '
            'the class is not reassigned unless the Facilitator accepts.',
        )
    return redirect('hod_class_replacements')


@login_required
@user_passes_test(is_management_admin)
def admin_notifications(request):
    if request.method == 'POST':
        form = NotificationForm(request.POST)
        if form.is_valid():
            notif = form.save(commit=False)
            notif.sender = request.user
            notif.save()
            messages.success(request, 'Notification sent.')
            return redirect('admin_notifications')
    else:
        form = NotificationForm()
    sent = Notification.objects.filter(sender=request.user).order_by('-created_at')[:30]
    return render(request, 'admin/notifications.html', {'form': form, 'sent_notifications': sent})


@login_required
@user_passes_test(is_management_admin)
@require_http_methods(['GET', 'POST'])
def admin_create_teacher(request):
    form = FacilitatorAccountForm(
        request.POST if request.method == 'POST' else None,
        request.FILES if request.method == 'POST' else None,
    )
    error = None
    success = None
    retry_user_id = None
    created_user = None

    if request.method == 'POST' and form.is_valid():
        _validate_teacher_uploads(request.FILES, form.errors)
        if not form.errors:
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    programme = Programme.objects.select_for_update().get(
                        pk=data['programme'].pk,
                        is_active=True,
                    )
                    supervisor = None
                    if data['employment_type'] == 'INTERNSHIP':
                        supervisor = FacilitatorProfile.objects.select_for_update().get(
                            pk=data['supervisor'].pk,
                            programme=programme,
                            employment_type='PERMANENT',
                            is_active=True,
                            teacher__is_approved=True,
                            teacher__user__is_active=True,
                        )
                    staff_id = generate_registered_staff_id(
                        'FACILITATOR', programme.code,
                    )
                    new_user = User(
                        username=staff_id,
                        email=data['email'],
                        first_name=data['first_name'],
                        last_name=data['last_name'],
                        is_active=True,
                    )
                    new_user.set_unusable_password()
                    new_user.save()
                    teacher = TeacherProfile.objects.create(
                        user=new_user,
                        staff_id=staff_id,
                        phone=data['phone'],
                        gender=data['gender'],
                        address='',
                        qualification=data['qualification'],
                        specialization='',
                        course_type=data['course_type'],
                        preferred_subject=data['preferred_subject'],
                        department=data['department'],
                        emergency_contact=data['emergency_contact'],
                        emergency_phone=data['emergency_phone'],
                        profile_photo=data.get('profile_photo'),
                        certificate_document=data.get('certificate_document'),
                        programme=programme,
                        is_approved=True,
                        profile_complete=True,
                    )
                    facilitator = FacilitatorProfile.objects.create(
                        teacher=teacher,
                        programme=programme,
                        employment_type=data['employment_type'],
                        internship_start_date=data['internship_start_date'],
                        internship_end_date=(
                            add_years(data['internship_start_date'], 1)
                            if data['employment_type'] == 'INTERNSHIP'
                            else None
                        ),
                        supervisor=supervisor,
                    )
                    if supervisor:
                        InternshipSupervision.objects.create(
                            internship=facilitator,
                            supervisor=supervisor,
                            start_date=data['internship_start_date'],
                            changed_by=request.user,
                        )
                    registry = StaffIDRegistry.objects.get(staff_id=staff_id)
                    registry.user = new_user
                    registry.save(update_fields=['user'])
                    created_user = new_user
            except (
                IntegrityError,
                StaffIDAllocationError,
                InvalidProgrammeCodeError,
                Programme.DoesNotExist,
                FacilitatorProfile.DoesNotExist,
            ) as exc:
                logger.warning(
                    'Facilitator creation failed for actor id=%s (%s)',
                    request.user.pk,
                    type(exc).__name__,
                )
                error = (
                    'Programme or supervisor availability changed. Refresh the form '
                    'and try again.'
                )
            except (ValidationError, OSError):
                logger.exception(
                    'Facilitator account creation failed for actor id=%s',
                    request.user.pk,
                )
                error = 'Facilitator account creation failed. Check the details and try again.'
            else:
                if _send_staff_onboarding_email(
                    request, created_user, created_user.username, 'login',
                ):
                    success = (
                        f'Account created. Staff ID: {created_user.username}. '
                        'A password setup email was sent.'
                    )
                else:
                    error = (
                        f'Account created with Staff ID {created_user.username}, '
                        'but the password setup email could not be delivered.'
                    )
                    retry_user_id = created_user.pk
                form = FacilitatorAccountForm()

    return render(request, 'admin/create_teacher.html', {
        'error': error,
        'success': success,
        'form': form,
        'created_user': created_user,
        'retry_user_id': retry_user_id,
        'base_template': (
            'admin/base_admin.html'
            if is_admin(request.user)
            else 'superadmin/base_superadmin.html'
        ),
        'role': 'admin' if is_admin(request.user) else 'superadmin',
        'elective_subjects': ELECTIVE_SUBJECTS,
        'department_subjects': DEPARTMENT_SUBJECTS,
        'department_subject_map': DEPARTMENT_SUBJECTS_BY_DEPARTMENT,
        'programmes': Programme.objects.filter(is_active=True).order_by('name'),
    })


@login_required
@user_passes_test(is_management_admin)
@require_http_methods(['GET'])
def programme_supervisors(request, programme_id):
    programme = get_object_or_404(Programme, pk=programme_id, is_active=True)
    hod = programme.active_hod
    supervisors = FacilitatorProfile.objects.filter(
        programme=programme,
        employment_type='PERMANENT',
        is_active=True,
        teacher__is_approved=True,
        teacher__user__is_active=True,
    ).select_related('teacher__user').order_by(
        'teacher__user__last_name',
        'teacher__user__first_name',
    )
    return JsonResponse({
        'hod': (
            {
                'name': hod.full_name,
                'staff_id': hod.staff_id,
            }
            if hod
            else None
        ),
        'supervisors': [
            {
                'id': person.pk,
                'name': person.full_name,
                'staff_id': person.teacher.staff_id,
            }
            for person in supervisors
        ],
    })


@login_required
@user_passes_test(is_management_admin)
def admin_qr_view(request):
    school_qrs = QRCode.objects.filter(qr_type='school_attendance').order_by('-created_at')
    lesson_qrs = QRCode.objects.filter(qr_type='lesson').order_by('-created_at')
    return render(request, 'admin/qr_view.html', {'school_qrs': school_qrs, 'lesson_qrs': lesson_qrs})


@login_required
@user_passes_test(is_superadmin)
def generate_qr(request):
    error = None
    if request.method == 'POST':
        form = QRCodeForm(request.POST)
        if form.is_valid():
            try:
                qr_obj = form.save(commit=False)
                qr_obj.created_by = request.user
                qr_obj.save()
                scan_path = f'/scan/school/{qr_obj.code}/' if qr_obj.qr_type == 'school_attendance' else f'/scan/lesson/{qr_obj.code}/'
                scan_url = request.build_absolute_uri(scan_path)
                img = qrcode.make(scan_url)
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)
                qr_obj.qr_image.save(f'qr_{qr_obj.code}.png', ContentFile(buf.read()), save=True)
                messages.success(request, f'{qr_obj.get_qr_type_display()} QR Code generated! Valid for 5 years.')
                return redirect('qr_detail_sa', qr_id=qr_obj.id)
            except Exception as exc:
                logger.exception('QR generation failed')
                error = 'QR code generation failed. Please try again or contact support.'
        else:
            error = 'Please correct the errors shown below.'
    else:
        form = QRCodeForm()
    school_qrs = QRCode.objects.filter(qr_type='school_attendance').order_by('-created_at')
    lesson_qrs = QRCode.objects.filter(qr_type='lesson').order_by('-created_at')
    return render(request, 'superadmin/qr_generate.html', {
        'form': form,
        'school_qrs': school_qrs,
        'lesson_qrs': lesson_qrs,
        'error': error,
    })


@login_required
def qr_detail(request, qr_id):
    if not is_management_admin(request.user):
        return redirect('welcome')
    qr_obj = get_object_or_404(QRCode, id=qr_id)
    scan_path = f'/scan/school/{qr_obj.code}/' if qr_obj.qr_type == 'school_attendance' else f'/scan/lesson/{qr_obj.code}/'
    scan_url = request.build_absolute_uri(scan_path)
    qr_b64 = None
    try:
        img = qrcode.make(scan_url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        logger.exception('Failed to generate QR detail image for QR id %s', qr_id)
    qr_list_url = reverse('generate_qr') if is_superadmin(request.user) else reverse('admin_qr_view')
    return render(request, 'admin/qr_detail.html', {
        'qr_obj': qr_obj,
        'scan_url': scan_url,
        'qr_b64': qr_b64,
        'show_qr_delete': is_superadmin(request.user),
        'qr_list_url': qr_list_url,
    })


# ── API ───────────────────────────────────────────────────────────────────────

@login_required
def get_notifications_api(request):
    notifs = Notification.objects.filter(recipient=request.user, is_read=False).order_by('-created_at')[:10]
    data = [{'id': n.id, 'title': n.title, 'message': n.message, 'type': n.notification_type, 'time': n.created_at.strftime('%H:%M')} for n in notifs]
    return JsonResponse({'notifications': data, 'count': len(data)})


@login_required
@user_passes_test(is_facilitator)
def get_schedule_reminders_api(request):
    profile = request.user.teacher_profile
    today = timezone.localdate()
    now = timezone.localtime().time()
    schedules = ClassSchedule.objects.filter(teacher=profile, scheduled_date=today, is_completed=False).select_related('timetable_entry')
    data = []
    for s in schedules:
        start = s.timetable_entry.start_time
        diff = (datetime.combine(today, start) - datetime.combine(today, now)).total_seconds() / 60
        data.append({'id': s.id, 'subject': s.timetable_entry.subject, 'start_time': start.strftime('%H:%M'), 'end_time': s.timetable_entry.end_time.strftime('%H:%M'), 'minutes_until': round(diff)})
    return JsonResponse({'schedules': data})


@login_required
@user_passes_test(is_facilitator)
@require_POST
def mark_schedule_complete(request, schedule_id):
    schedule = get_object_or_404(
        ClassSchedule,
        id=schedule_id,
        teacher=request.user.teacher_profile,
    )
    if not schedule.is_completed:
        schedule.is_completed = True
        schedule.save(update_fields=['is_completed'])
    return JsonResponse({'status': 'ok'})


# ── Super Admin ───────────────────────────────────────────────────────────────

@login_required
@user_passes_test(is_superadmin)
def superadmin_dashboard(request):
    admins = User.objects.filter(is_staff=True).select_related('admin_profile')
    teachers = TeacherProfile.objects.select_related('user').all()
    pending = teachers.filter(is_approved=False)
    today = timezone.localdate()
    today_att = Attendance.objects.filter(date=today)
    return render(request, 'superadmin/dashboard.html', {
        'admins': admins, 'teachers': teachers, 'pending': pending,
        'total_admins': admins.count(), 'total_teachers': teachers.count(),
        'pending_count': pending.count(),
        'present_today': today_att.filter(school_status='present').count(),
        'today': today,
    })


@login_required
@user_passes_test(is_superadmin)
def superadmin_admins(request):
    admins = User.objects.filter(is_staff=True).select_related(
        'admin_profile',
        'staff_onboarding',
    ).order_by('username')
    search = request.GET.get('q', '').strip()
    if search:
        admins = admins.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(username__icontains=search)
            | Q(email__icontains=search)
        )
    query_parameters = request.GET.copy()
    query_parameters.pop('page', None)
    return render(request, 'superadmin/admins.html', {
        'admins': Paginator(admins, 25).get_page(request.GET.get('page')),
        'search': search,
        'query_string': query_parameters.urlencode(),
    })


@login_required
@user_passes_test(is_superadmin)
@require_http_methods(['GET', 'POST'])
def superadmin_create_admin(request):
    form = AdminAccountForm(
        request.POST if request.method == 'POST' else None,
    )
    error = None
    success = None
    retry_user_id = None
    created_user = None
    staff_id = None

    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                staff_id = generate_registered_staff_id('ADMIN', 'ADM')
                new_user = User(
                    username=staff_id,
                    first_name=data['first_name'],
                    last_name=data['last_name'],
                    email=data['email'],
                    is_staff=True,
                    is_active=True,
                )
                new_user.set_unusable_password()
                new_user.save()
                AdminProfile.objects.create(
                    user=new_user,
                    role='admin',
                    created_by=request.user,
                )
                registry = StaffIDRegistry.objects.get(staff_id=staff_id)
                registry.user = new_user
                registry.save(update_fields=['user'])
                created_user = new_user
        except (IntegrityError, StaffIDAllocationError) as exc:
            logger.warning(
                'Admin account creation failed for actor id=%s (%s)',
                request.user.pk,
                type(exc).__name__,
            )
            error = 'Admin account creation failed. Refresh and try again.'
        else:
            if _send_staff_onboarding_email(
                request,
                created_user,
                staff_id,
                'admin_login',
            ):
                success = (
                    f'Admin account created. Staff ID: {staff_id}. '
                    'A password setup email was sent.'
                )
            else:
                error = (
                    f'Admin account created with Staff ID {staff_id}, '
                    'but the password setup email could not be delivered.'
                )
                retry_user_id = created_user.pk
            form = AdminAccountForm()

    return render(request, 'superadmin/create_admin.html', {
        'form': form,
        'error': error,
        'success': success,
        'created_user': created_user,
        'retry_user_id': retry_user_id,
        'staff_id': staff_id,
    })


@login_required
@user_passes_test(is_superadmin)
def superadmin_delete_admin(request, admin_id):
    admin_user = get_object_or_404(User, id=admin_id, is_staff=True)
    if admin_user == request.user:
        messages.error(request, 'Cannot delete your own account.')
        return redirect('superadmin_admins')
    try:
        if admin_user.admin_profile.role == 'superadmin':
            messages.error(request, 'Cannot delete another Super Admin.')
            return redirect('superadmin_admins')
    except Exception:
        pass
    if request.method == 'POST':
        name = admin_user.get_full_name() or admin_user.username
        admin_user.delete()
        messages.success(request, f'Admin "{name}" deleted.')
        return redirect('superadmin_admins')
    return render(request, 'superadmin/confirm_delete_admin.html', {'admin_user': admin_user})


@login_required
@user_passes_test(is_superadmin)
def superadmin_teachers(request):
    teachers = TeacherProfile.objects.select_related('user').all().order_by('-date_joined')
    return render(request, 'superadmin/teachers.html', {'teachers': teachers})


@login_required
@user_passes_test(is_superadmin)
@require_POST
def superadmin_approve_teacher(request, teacher_id):
    teacher = get_object_or_404(TeacherProfile, id=teacher_id)
    teacher.is_approved = True
    teacher.save()
    Notification.objects.create(
        recipient=teacher.user, sender=request.user,
        title='Account Approved',
        message='Your facilitator account has been approved by the Super Admin. You can now log in.',
        notification_type='admin'
    )
    messages.success(request, f'{teacher.full_name} approved.')
    return redirect('superadmin_teachers')


@login_required
@user_passes_test(is_superadmin)
@require_http_methods(['GET', 'POST'])
def superadmin_delete_teacher(request, teacher_id):
    teacher = get_object_or_404(TeacherProfile, id=teacher_id)
    if request.method == 'POST':
        name = teacher.full_name
        _deactivate_facilitator(teacher)
        messages.success(request, f'{name} deactivated; account and history were retained.')
        return redirect('superadmin_teachers')
    return render(request, 'superadmin/confirm_delete_teacher.html', {'teacher': teacher})


@login_required
@user_passes_test(is_superadmin)
def superadmin_change_credentials(request):
    """Super admin can change their own username and password."""
    error = None
    success = None
    if request.method == 'POST':
        new_username = request.POST.get('new_username', '').strip()
        current_password = request.POST.get('current_password', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if not current_password:
            error = 'Please enter your current password to confirm changes.'
        elif not authenticate(request, username=request.user.username, password=current_password):
            error = 'Current password is incorrect.'
        elif new_username and User.objects.filter(username=new_username).exclude(pk=request.user.pk).exists():
            error = f'Username "{new_username}" is already taken.'
        elif new_password and new_password != confirm_password:
            error = 'New passwords do not match.'
        else:
            if new_password:
                try:
                    validate_password(new_password, user=request.user)
                except ValidationError as exc:
                    error = ' '.join(exc.messages)
            if not error:
                user = request.user
                changed = []
                if new_username and new_username != user.username:
                    user.username = new_username
                    changed.append('username')
                if new_password:
                    user.set_password(new_password)
                    changed.append('password')
                if changed:
                    user.save()
                    from django.contrib.auth import update_session_auth_hash
                    update_session_auth_hash(request, user)
                    success = f'Successfully updated: {", ".join(changed)}.'
                else:
                    success = 'No changes made.'

    return render(request, 'superadmin/change_credentials.html', {
        'error': error,
        'success': success,
        'current_username': request.user.username,
    })


@login_required
@user_passes_test(is_superadmin)
def superadmin_staff_ids(request):
    """Registry of all teacher Staff IDs."""
    teachers = TeacherProfile.objects.select_related('user').all().order_by('staff_id')
    return render(request, 'superadmin/staff_ids.html', {'teachers': teachers})
