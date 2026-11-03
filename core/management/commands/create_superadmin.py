import os
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import AdminProfile


class Command(BaseCommand):
    help = "Create or promote a Winnetech superadmin with AdminProfile."

    def handle(self, *args, **options):
        username = os.environ.get("SUPERADMIN_USERNAME", "superadmin")
        password = os.environ.get("SUPERADMIN_PASSWORD")
        email = os.environ.get("SUPERADMIN_EMAIL", "kaytech2007@gmail.com")

        if not password:
            self.stdout.write(
                self.style.WARNING("SUPERADMIN_PASSWORD not set — skipping.")
            )
            return

        # 1. Check if a superadmin already exists
        existing_sa = AdminProfile.objects.filter(role="superadmin").first()
        if existing_sa:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Superadmin already exists: {existing_sa.user.username} — skipping."
                )
            )
            return

        # 2. Find user by username OR email (case-insensitive)
        user = User.objects.filter(username=username).first()
        if not user:
            user = User.objects.filter(email__iexact=email).first()

        # 3. Promote existing user, or create new one
        if user:
            self.stdout.write(
                self.style.WARNING(
                    f"User already exists ({user.username} / {user.email}) — promoting."
                )
            )
            user.set_password(password)
            user.is_staff = True
            user.is_superuser = True
            user.save()
        else:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
            )
            user.is_staff = True
            user.is_superuser = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f"User created: {username}"))

        # 4. Create AdminProfile with superadmin role
        AdminProfile.objects.create(user=user, role="superadmin")

        self.stdout.write(
            self.style.SUCCESS(f"Superadmin ready: {username} ({email})")
        )