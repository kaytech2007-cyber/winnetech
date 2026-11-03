import os
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import AdminProfile


class Command(BaseCommand):
    help = "Bulletproof superadmin setup — prints all users, then forces superadmin."

    def handle(self, *args, **options):
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("ALL USERS IN DATABASE:")
        self.stdout.write("=" * 70)
        if not User.objects.exists():
            self.stdout.write("  (no users in database)")
        for u in User.objects.all().order_by("pk"):
            try:
                role = u.admin_profile.role
            except AdminProfile.DoesNotExist:
                role = "(no AdminProfile)"
            self.stdout.write(
                f"  id={u.pk:3} username='{u.username}' email='{u.email}' "
                f"staff={u.is_staff} super={u.is_superuser} "
                f"active={u.is_active} role={role}"
            )
        self.stdout.write("=" * 70 + "\n")

        username = os.environ.get("SUPERADMIN_USERNAME", "admin").strip()
        password = os.environ.get("SUPERADMIN_PASSWORD", "").strip()
        email = os.environ.get("SUPERADMIN_EMAIL", "kaytech2007@gmail.com").strip()

        self.stdout.write(f"ENV VARS:")
        self.stdout.write(f"  SUPERADMIN_USERNAME = '{username}'")
        self.stdout.write(f"  SUPERADMIN_PASSWORD = {'(set, ' + str(len(password)) + ' chars)' if password else '❌ NOT SET'}")
        self.stdout.write(f"  SUPERADMIN_EMAIL    = '{email}'")
        self.stdout.write("")

        if not password:
            self.stdout.write(self.style.ERROR(
                "❌ SUPERADMIN_PASSWORD is empty — cannot create superadmin.\n"
                "   Add it in Render → Environment, then redeploy."
            ))
            return

        # Nuclear option: DELETE all users, then recreate superadmin
        existing_superadmins = AdminProfile.objects.filter(role="superadmin")
        if existing_superadmins.exists():
            self.stdout.write(f"Found {existing_superadmins.count()} existing superadmin(s).")
            user = existing_superadmins.first().user
            self.stdout.write(f"Resetting: '{user.username}'")
        else:
            user = User.objects.filter(username=username).first()
            if not user:
                user = User.objects.filter(email__iexact=email).first()
            if user:
                self.stdout.write(f"Promoting existing user: '{user.username}'")
            else:
                self.stdout.write(f"Creating new user: '{username}'")
                user = User(username=username, email=email)

        user.username = username
        user.email = email
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save()

        profile, created = AdminProfile.objects.get_or_create(
            user=user,
            defaults={"role": "superadmin"},
        )
        if not created and profile.role != "superadmin":
            profile.role = "superadmin"
            profile.save()

        # Verify password actually works
        from django.contrib.auth import authenticate
        check = authenticate(username=username, password=password)
        if check and check.pk == user.pk:
            self.stdout.write(self.style.SUCCESS(
                f"\n✅ VERIFIED: '{username}' can log in with SUPERADMIN_PASSWORD"
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f"\n❌ VERIFICATION FAILED: '{username}' cannot authenticate!"
            ))