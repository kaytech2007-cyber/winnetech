import qrcode
import io
import base64
from datetime import datetime, date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.core.files.base import ContentFile

from .models import (
    TeacherProfile, Attendance, QRCode, Notification,
    TimetableEntry, ClassSchedule, AdminProfile
)
from .forms import (
    TeacherRegistrationForm,
    NotificationForm, TimetableEntryForm, ClassScheduleForm, QRCodeForm
)


def is_admin(user):
    return user.is_authenticated and user.is_staff

def is_superadmin(user):
    if not (user.is_authenticated and user.is_staff):
        return False
    try:
        return user.admin_profile.role == 'superadmin'
    except Exception:
        return user.is_superuser


def welcome(request):
    if request.user.is_authenticated:
        if request.user.is_staff:
            try:
                if request.user.admin_profile.role == 'superadmin':
                    return redirect('superadmin_dashboard')
            except Exception:
                if request.user.is_superuser:
                    return redirect('superadmin_dashboard')
            return redirect('admin_dashboard')
        return redirect('teacher_dashboard')
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


# ── Teacher login using Staff ID ──────────────────────────────────────────────

def teacher_register(request):
    if request.method == 'POST':
        form = TeacherRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                user = form.save()
                messages.success(request, f'Registration successful! Welcome {user.get_full_name()}. Awaiting admin approval.')
                return redirect('teacher_login')
            except Exception as e:
                messages.error(request, f'Registration failed: {str(e)}')
        else:
            errs = []
            for field, errors in form.errors.items():
                label = form.fields[field].label if field in form.fields else field
                for err in errors:
                    errs.append(f'{label}: {err}' if label else err)
            messages.error(request, 'Fix: ' + ' | '.join(errs) if errs else 'Please correct errors.')
    else:
        form = TeacherRegistrationForm()
    return render(request, 'auth/register.html', {'form': form})


def teacher_login(request):
    """Login using Staff ID (e.g. WTI0001) and password."""
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect('teacher_dashboard')
    error = None
    if request.method == 'POST':
        staff_id = request.POST.get('staff_id', '').strip().upper()
        password = request.POST.get('password', '').strip()
        if not staff_id or not password:
            error = 'Please enter your Staff ID and password.'
        else:
            # Find teacher by staff_id
            try:
                profile = TeacherProfile.objects.select_related('user').get(staff_id=staff_id)
                user = authenticate(request, username=profile.user.username, password=password)
                if user is None:
                    error = 'Invalid Staff ID or password.'
                elif user.is_staff:
                    error = 'Please use the Admin Portal to login as admin.'
                elif not profile.is_approved:
                    error = 'Your account is pending admin approval.'
                else:
                    login(request, user)
                    return redirect('teacher_dashboard')
            except TeacherProfile.DoesNotExist:
                error = 'Invalid Staff ID or password.'
    return render(request, 'auth/teacher_login.html', {'error': error})


def admin_login(request):
    if request.user.is_authenticated and request.user.is_staff:
        try:
            if request.user.admin_profile.role == 'superadmin':
                return redirect('superadmin_dashboard')
        except Exception:
            pass
        return redirect('admin_dashboard')
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)
        if user is None:
            error = 'Invalid username or password.'
        elif not user.is_staff:
            error = 'This account does not have admin privileges.'
        elif not user.is_active:
            error = 'This account is disabled.'
        else:
            try:
                if user.admin_profile.role == 'superadmin':
                    error = 'Please use the Super Admin Portal.'
                else:
                    login(request, user)
                    return redirect('admin_dashboard')
            except Exception:
                login(request, user)
                return redirect('admin_dashboard')
    return render(request, 'auth/admin_login.html', {'error': error})


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


def logout_view(request):
    logout(request)
    return redirect('welcome')

