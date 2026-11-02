import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth.models import User

from .models import AdminProfile, FacilitatorProfile, HODProfile, TeacherProfile


@database_sync_to_async
def user_can_receive_notifications(user_id):
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return False

    if not user.is_active:
        return False

    try:
        hod = user.hod_profile
    except HODProfile.DoesNotExist:
        hod = None
    if hod:
        return bool(
            hod.is_active
            and hod.programme_id
            and hod.programme.is_active
        )

    try:
        admin = user.admin_profile
    except AdminProfile.DoesNotExist:
        admin = None
    if user.is_staff and admin and admin.role in ('admin', 'superadmin'):
        return True
    if user.is_staff and user.is_superuser and admin is None:
        return True

    try:
        facilitator = user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return False
    if not facilitator.is_approved:
        return False
    try:
        return facilitator.facilitator_profile.is_active
    except FacilitatorProfile.DoesNotExist:
        return True


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if (
            not self.user.is_authenticated
            or not await user_can_receive_notifications(self.user.pk)
        ):
            await self.close()
            return

        self.group_name = f'notifications_{self.user.pk}'
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name,
        )
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )

    async def receive(self, text_data):
        return

    async def send_notification(self, event):
        await self.send(text_data=json.dumps({
            'type': 'notification',
            'title': event['title'],
            'message': event['message'],
            'notification_type': event.get('notification_type', 'general'),
            'timestamp': event.get('timestamp', ''),
        }))
