import os
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import AdminProfile


class Command(BaseCommand):
    help = "Create a Winnetech superadmin (with AdminProfile) if none exists."

    def handle(self, *args, **options):
        username = os.environ.get("SUPERADMIN_USERNAME", "superadmin")
        password = os.environ.get("SUPERADMIN_PASSWORD")
        email = os.environ.get("SUPERADMIN_EMAIL", "kaytech2007@gmail.com")

        if not password:
            self.stdout.write(
                self.style.WARNING(
                    "SUPERADMIN_PASSWORD not set — skipping superadmin creation."
                )
            )
            return

        # Check if a superadmin already exists
        existing = AdminProfile.objects.filter(role="superadmin").first()
        if existing:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Superadmin already exists: {existing.user.username} — skipping."
                )
            )
            return

        # Create user
        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
        else:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
            )

        user.is_staff = True
        user.is_superuser = True
        user.save()

        # Create AdminProfile with superadmin role
        AdminProfile.objects.create(
            user=user,
            role="superadmin",
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Superadmin created: {username} ({email})"
            )
        )