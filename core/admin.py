from django.contrib import admin
from .models import (
    TeacherProfile, Attendance, QRCode, Notification,
    TimetableEntry, ClassSchedule, AdminProfile,
    SchedulingEvaluation, ClassConfirmationRequest, ClassReplacementRequest,
)


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'staff_id', 'course_type', 'preferred_subject', 'is_approved', 'date_joined']
    list_filter = ['is_approved', 'course_type', 'gender', 'qualification']
    search_fields = ['user__first_name', 'user__last_name', 'staff_id', 'phone']
    actions = ['approve_teachers']

    def approve_teachers(self, request, queryset):
        queryset.update(is_approved=True)
    approve_teachers.short_description = 'Approve selected facilitators'


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['teacher', 'date', 'school_status', 'lesson_status', 'check_in_time', 'lesson_done_time']
    list_filter = ['school_status', 'lesson_status', 'date']
    search_fields = ['teacher__user__first_name', 'teacher__user__last_name']


@admin.register(QRCode)
class QRCodeAdmin(admin.ModelAdmin):
    list_display = ['label', 'qr_type', 'valid_from', 'valid_until', 'is_active', 'created_by', 'created_at']
    list_filter = ['is_active', 'qr_type']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['recipient', 'title', 'notification_type', 'is_read', 'created_at']
    list_filter = ['notification_type', 'is_read']


@admin.register(TimetableEntry)
class TimetableEntryAdmin(admin.ModelAdmin):
    list_display = ['day', 'subject', 'start_time', 'end_time', 'teacher', 'is_break']
    list_filter = ['day', 'is_break', 'course_type']


@admin.register(ClassSchedule)
class ClassScheduleAdmin(admin.ModelAdmin):
    list_display = ['teacher', 'timetable_entry', 'scheduled_date', 'is_completed']
    list_filter = ['is_completed', 'scheduled_date']


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'created_at']
    list_filter = ['role']


@admin.register(SchedulingEvaluation)
class SchedulingEvaluationAdmin(admin.ModelAdmin):
    list_display = [
        'created_at',
        'programme',
        'candidate',
        'proposed_class',
        'scheduled_date',
        'status',
        'recommendation_rank',
        'recommendation_score',
    ]
    list_filter = ['status', 'programme', 'scheduled_date']
    search_fields = [
        'candidate__user__first_name',
        'candidate__user__last_name',
        'candidate__staff_id',
        'proposed_class',
    ]
    readonly_fields = [field.name for field in SchedulingEvaluation._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ClassConfirmationRequest)
class ClassConfirmationRequestAdmin(admin.ModelAdmin):
    list_display = [
        'created_at',
        'programme',
        'candidate',
        'timetable_entry',
        'scheduled_date',
        'status',
    ]
    list_filter = ['status', 'programme', 'scheduled_date']
    search_fields = [
        'candidate__user__first_name',
        'candidate__user__last_name',
        'candidate__staff_id',
        'timetable_entry__subject',
    ]
    readonly_fields = [field.name for field in ClassConfirmationRequest._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ClassReplacementRequest)
class ClassReplacementRequestAdmin(admin.ModelAdmin):
    list_display = [
        'requested_at',
        'programme',
        'original_request',
        'status',
        'selected_candidate',
        'decided_by',
    ]
    list_filter = ['status', 'programme', 'requested_at']
    search_fields = [
        'original_request__candidate__user__first_name',
        'original_request__candidate__user__last_name',
        'reason',
    ]
    readonly_fields = [field.name for field in ClassReplacementRequest._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
