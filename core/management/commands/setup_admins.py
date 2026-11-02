from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Disabled: use the interactive createsuperuser command for initial setup.'

    def handle(self, *args, **options):
        raise CommandError(
            'This command is disabled to protect existing accounts. '
            'For first-time setup, run "python manage.py createsuperuser" '
            'interactively against the intended database.'
        )
