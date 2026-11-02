from django.core.management.base import BaseCommand

from core.models import expire_internships


class Command(BaseCommand):
    help = 'Deactivate internship facilitators whose contract end date has passed.'

    def handle(self, *args, **options):
        count = expire_internships()
        self.stdout.write(
            self.style.SUCCESS(f'{count} expired internship facilitator(s) deactivated.')
        )
