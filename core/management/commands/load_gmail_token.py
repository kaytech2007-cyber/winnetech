import os
from django.core.management.base import BaseCommand
from gmail_oauth_backend.models import RefreshToken


class Command(BaseCommand):
    help = "Load Gmail refresh token from env var into database (Render)."

    def handle(self, *args, **options):
        token = os.environ.get("GMAIL_REFRESH_TOKEN")
        if not token:
            self.stdout.write(
                self.style.WARNING("GMAIL_REFRESH_TOKEN not set — skipping.")
            )
            return

        obj, created = RefreshToken.objects.get_or_create(
            key="PyLab",
            defaults={"value": token},
        )
        if not created:
            obj.value = token
            obj.save()

        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(f"{action} Gmail refresh token (key=PyLab)")
        )