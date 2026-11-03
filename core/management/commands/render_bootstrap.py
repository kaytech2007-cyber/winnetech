"""
Bootstrap admin user on Render free tier (no shell access).
Runs during build via build.sh. Idempotent — safe to run every deploy.
"""
import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Create the initial superuser if none exists."

    def handle(self, *args, **options):
        User = get_user_model()

        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write(
                self.style.SUCCESS("Superuser already exists — skipping.")
            )
            return

        email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@winnetech.local")
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
        username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin")

        if not password:
            self.stdout.write(
                self.style.WARNING(
                    "BOOTSTRAP_ADMIN_PASSWORD not set — skipping admin creation.\n"
                    "Set it in Render Environment variables to auto-create."
                )
            )
            return

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )
        self.stdout.write(
            self.style.SUCCESS(f"Superuser created: {username} ({email})")
        )