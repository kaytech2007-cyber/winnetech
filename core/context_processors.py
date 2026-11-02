from .models import TeacherProfile, Notification


def admin_context(request):
    """Adds pending teacher count to all admin templates."""
    if request.user.is_authenticated and request.user.is_staff:
        pending_count = TeacherProfile.objects.filter(is_approved=False).count()
        return {'pending_count': pending_count}
    return {}


def teacher_portal_context(request):
    """Sidebar / header data for teacher pages (base_teacher.html)."""
    if not request.user.is_authenticated or request.user.is_staff:
        return {}
    try:
        profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return {}
    all_notifications = Notification.objects.filter(recipient=request.user).order_by('-created_at')[:20]
    unread_notifications = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return {
        'profile': profile,
        'all_notifications': all_notifications,
        'unread_notifications': unread_notifications,
    }
