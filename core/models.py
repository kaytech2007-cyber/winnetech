from django.db import models, transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.conf import settings
from django.utils import timezone
import uuid
import random
import re
import string
import time
from datetime import date

from django.db import IntegrityError, OperationalError, connections, router
from django.db.models.deletion import ProtectedError


class HODStaffIDAllocationError(Exception):
    pass


class StaffIDAllocationError(Exception):
    pass


class InvalidProgrammeCodeError(Exception):
    pass


# ─── Subject / Course Choices ────────────────────────────────────────────────

ELECTIVE_SUBJECTS = [
    ('mathematics', 'Mathematics'),
    ('english_language', 'English Language'),
    ('social_studies', 'Social Studies'),
    ('ict', 'ICT'),
    ('science', 'Science'),
    ('est', 'EST (Entrepreneur Skills Training)'),
]

DEPARTMENT_SUBJECTS = [
    ('computer_system_servicing', 'Computer System & Servicing'),
    ('workshop_practice_power', 'Workshop Practice & Power Management'),
    ('networking_data_comm', 'Networking & Data Communication'),
    ('plumbing_general', 'Plumbing (General)'),
    ('plumbing_installation', 'Plumbing Installation'),
    ('plumbing_maintenance', 'Plumbing Maintenance'),
    ('electrical_installation', 'Electrical Installation'),
    ('electrical_maintenance', 'Electrical Maintenance'),
    ('electrical_wiring', 'Electrical Wiring'),
    ('welding_fabrication', 'Welding & Fabrication'),
    ('metal_work', 'Metal Work'),
    ('fashion_design', 'Fashion Design'),
    ('garment_construction', 'Garment Construction'),
    ('textile_studies', 'Textile Studies'),
    ('catering_general', 'Catering (General)'),
    ('food_beverage', 'Food & Beverage'),
    ('hospitality', 'Hospitality Management'),
    ('mechanical_engineering', 'Mechanical Engineering'),
    ('machine_maintenance', 'Machine Maintenance'),
    ('auto_mechanics', 'Auto Mechanics'),
    ('auto_electrical', 'Auto Electrical'),
    ('building_construction', 'Building & Construction'),
    ('masonry', 'Masonry'),
    ('carpentry', 'Carpentry'),
]

DEPARTMENT_CHOICES = [
    ('information_technology', 'Information Technology'),
    ('plumbing', 'Plumbing'),
    ('electricals', 'Electricals'),
    ('welding_fabrication', 'Welding & Fabrication'),
    ('fashion', 'Fashion'),
    ('catering', 'Catering'),
    ('mechanical_engineering', 'Mechanical Engineering'),
    ('auto_mechanics', 'Auto Mechanics'),
    ('building_construction', 'Building & Construction'),
]

COURSE_TYPE_CHOICES = [
    ('core', 'Core Subjects'),
    ('departmental', 'Departmental'),
]

GENDER_CHOICES = [
    ('male', 'Male'),
    ('female', 'Female'),
    ('other', 'Other'),
]

QUALIFICATION_CHOICES = [
    ('phd', 'PhD'),
    ('masters', "Master's Degree"),
    ('degree', "Bachelor's Degree"),
    ('hnd', 'HND'),
    ('diploma', 'Diploma'),
    ('certificate', 'Certificate'),
    ('other', 'Other'),
]


def generate_unique_staff_id():
    database = router.db_for_write(TeacherProfile)
    count = TeacherProfile.objects.using(database).count() + 1
    for attempt in range(count, count + 9999):
        candidate = f'WTI{str(attempt).zfill(4)}'
        if not _staff_id_is_reserved(candidate, using=database):
            return candidate
    while True:
        suffix = ''.join(random.choices(string.digits, k=6))
        candidate = f'WTI{suffix}'
        if not _staff_id_is_reserved(candidate, using=database):
            return candidate


# ─── Admin Profile ────────────────────────────────────────────────────────────

class AdminProfile(models.Model):
    ROLE_CHOICES = [
        ('superadmin', 'Super Admin'),
        ('admin', 'Admin'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='admin')
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_admins'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"

    @property
    def is_superadmin(self):
        return self.role == 'superadmin'


class StaffIDRegistry(models.Model):
    ROLE_CHOICES = [
        ('ADMIN', 'Admin'),
        ('FACILITATOR', 'Facilitator'),
    ]

    staff_id = models.CharField(max_length=100, unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    programme_code = models.CharField(max_length=50)
    year = models.PositiveSmallIntegerField()
    sequence = models.PositiveIntegerField()
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='staff_id_registry_entry',
    )
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['role', 'programme_code', 'year', 'sequence'],
                name='unique_staff_id_sequence',
            ),
        ]
        ordering = ['-year', '-sequence']


class StaffOnboardingToken(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='staff_onboarding',
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    delivery_failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['expires_at', 'used_at']),
        ]


# ─── Programme / Department / HOD / Facilitator management ────────────────


def add_years(date_value, years):
    if date_value is None:
        return None
    try:
        return date_value.replace(year=date_value.year + years)
    except ValueError:
        return date_value.replace(year=date_value.year + years, day=28)


class Programme(models.Model):
    CATEGORY_CHOICES = [
        ('GENERICS', 'Generics'),
        ('DEPARTMENTS', 'Departments'),
    ]

    name = models.CharField(max_length=200, unique=True)
    code = models.CharField(max_length=50, unique=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='DEPARTMENTS')
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    facilitators = models.ManyToManyField('TeacherProfile', blank=True, related_name='programmes')

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['code'], name='unique_programme_code'),
            models.UniqueConstraint(
                Lower('code'),
                name='unique_programme_code_case_insensitive',
            ),
        ]

    def clean(self):
        super().clean()
        self.code = (self.code or '').strip().upper()
        if self.category not in dict(self.CATEGORY_CHOICES):
            raise ValidationError({'category': 'Choose a valid programme category.'})
        if len(self.code) > 50:
            raise ValidationError({'code': 'Programme code must be 50 characters or fewer.'})
        if not re.fullmatch(r'[A-Z0-9_-]+', self.code):
            raise ValidationError({
                'code': 'Use letters, numbers, hyphens or underscores in the programme code.'
            })
        duplicate = type(self).objects.filter(
            code__iexact=self.code,
        ).exclude(pk=self.pk)
        if duplicate.exists():
            raise ValidationError({'code': 'A programme with this code already exists.'})

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if (
            HODProfile.objects.filter(programme=self).exists()
            or self.teachers.exists()
            or self.facilitators_detail.exists()
        ):
            raise ProtectedError(
                'Programmes with assigned HODs or facilitators cannot be deleted; deactivate the programme instead.',
                [self],
            )
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def active_hod(self):
        return HODProfile.objects.filter(
            programme_id=self.pk,
            is_active=True,
        ).first()


Program = Programme


class HODProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='hod_profile')
    staff_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    programme = models.OneToOneField(
        Programme,
        on_delete=models.PROTECT,
        related_name='hod_profile',
        null=True,
        blank=True,
    )
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.programme.code if self.programme else 'Unassigned'})"

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.username

    def save(self, *args, **kwargs):
        database = kwargs.get('using') or self._state.db or router.db_for_write(
            type(self),
            instance=self,
        )
        is_new = self._state.adding
        reserved_by_allocator = getattr(self, '_staff_id_reserved', False)

        if not is_new:
            existing_staff_id = type(self).objects.using(database).filter(
                pk=self.pk,
            ).values_list('staff_id', flat=True).first()
            if existing_staff_id:
                self.staff_id = existing_staff_id

        if not self.staff_id and self.programme_id:
            self.staff_id = generate_hod_staff_id(self.programme_id)
            reserved_by_allocator = True

        if self.staff_id:
            _reserve_hod_staff_id(
                self.staff_id,
                using=database,
                profile_id=None if is_new else self.pk,
                user_id=self.user_id,
                allow_preallocated=reserved_by_allocator,
            )

        with transaction.atomic(using=database):
            super().save(*args, **kwargs)
            if self.staff_id:
                registry = HODStaffIDRegistry.objects.using(database).filter(
                    staff_id=self.staff_id,
                )
                linked = registry.filter(hod_profile_id=self.pk).exists()
                if not linked and registry.filter(hod_profile__isnull=True).update(
                    hod_profile_id=self.pk
                ) != 1:
                    raise IntegrityError(
                        'HOD Staff ID is already reserved by another profile.'
                    )
        if reserved_by_allocator:
            self._staff_id_reserved = False


class HODStaffIDRegistry(models.Model):
    staff_id = models.CharField(max_length=100, unique=True)
    programme_code = models.CharField(max_length=50, null=True, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    sequence = models.PositiveIntegerField(null=True, blank=True)
    hod_profile = models.OneToOneField(
        HODProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='staff_id_registry',
    )
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['programme_code', 'year', 'sequence'],
                condition=(
                    Q(programme_code__isnull=False)
                    & Q(year__isnull=False)
                    & Q(sequence__isnull=False)
                ),
                name='unique_hod_staff_sequence',
            ),
        ]


def _staff_id_is_reserved(
    staff_id,
    *,
    using,
    exclude_user_id=None,
    exclude_hod_profile_id=None,
    allow_unlinked_hod_reservation=False,
):
    users = User.objects.using(using).filter(username__iexact=staff_id)
    if exclude_user_id is not None:
        users = users.exclude(pk=exclude_user_id)
    if users.exists():
        return True

    teachers = TeacherProfile.objects.using(using).filter(staff_id__iexact=staff_id)
    if teachers.exists():
        return True

    hods = HODProfile.objects.using(using).filter(staff_id__iexact=staff_id)
    if exclude_hod_profile_id is not None:
        hods = hods.exclude(pk=exclude_hod_profile_id)
    if hods.exists():
        return True

    registered_ids = StaffIDRegistry.objects.using(using).filter(
        staff_id__iexact=staff_id,
    )
    if registered_ids.exists():
        return True

    hod_registry_ids = HODStaffIDRegistry.objects.using(using).filter(
        staff_id__iexact=staff_id,
    )
    if exclude_hod_profile_id is not None:
        hod_registry_ids = hod_registry_ids.exclude(
            hod_profile_id=exclude_hod_profile_id,
        )
    if allow_unlinked_hod_reservation:
        hod_registry_ids = hod_registry_ids.exclude(hod_profile__isnull=True)
    return hod_registry_ids.exists()


def _staff_id_registry_values(staff_id):
    match = re.fullmatch(
        r'WTI-HOD-([A-Za-z0-9_-]+)/([0-9]{4})/([0-9]+)',
        staff_id,
    )
    if not match:
        return {
            'staff_id': staff_id,
            'programme_code': None,
            'year': None,
            'sequence': None,
        }
    return {
        'staff_id': staff_id,
        'programme_code': match.group(1),
        'year': int(match.group(2)),
        'sequence': int(match.group(3)),
    }


def _reserve_hod_staff_id(
    staff_id,
    *,
    using,
    profile_id=None,
    user_id=None,
    allow_preallocated=False,
):
    for attempt in range(5):
        try:
            with transaction.atomic(using=using):
                if _staff_id_is_reserved(
                    staff_id,
                    using=using,
                    exclude_user_id=user_id,
                    exclude_hod_profile_id=profile_id,
                    allow_unlinked_hod_reservation=allow_preallocated,
                ):
                    raise IntegrityError(
                        'Staff ID has already been issued in another role.'
                    )
                existing = HODStaffIDRegistry.objects.using(using).filter(
                    staff_id=staff_id,
                ).first()
                if existing:
                    if existing.hod_profile_id == profile_id and profile_id:
                        return
                    if allow_preallocated and existing.hod_profile_id is None:
                        return
                    raise IntegrityError(
                        'HOD Staff ID has already been issued and cannot be reused.'
                    )
                HODStaffIDRegistry.objects.using(using).create(
                    **_staff_id_registry_values(staff_id),
                    hod_profile_id=profile_id,
                )
                return
        except OperationalError as exc:
            message = str(exc).lower()
            is_sqlite_lock = (
                'sqlite' in connections[using].vendor
                and ('locked' in message or 'busy' in message)
            )
            if not is_sqlite_lock:
                raise
            if attempt == 4:
                raise HODStaffIDAllocationError(
                    'The database is busy and could not reserve the HOD Staff '
                    'ID. Try again.'
                ) from exc
        except IntegrityError:
            if attempt == 4:
                raise
            existing = HODStaffIDRegistry.objects.using(using).filter(
                staff_id=staff_id,
            ).first()
            if existing:
                if existing.hod_profile_id == profile_id and profile_id:
                    return
                if allow_preallocated and existing.hod_profile_id is None:
                    return
                raise
        time.sleep(0.02 * (attempt + 1))


def generate_hod_staff_id(programme_id):
    year = timezone.localdate().year
    database = router.db_for_write(HODStaffIDRegistry)

    for attempt in range(5):
        try:
            with transaction.atomic(using=database):
                programme = Programme.objects.using(database).select_for_update().get(
                    pk=programme_id
                )
                if not re.fullmatch(r'[A-Za-z0-9_-]+', programme.code):
                    raise InvalidProgrammeCodeError(
                        'Programme code must contain only letters, numbers, '
                        'hyphens, or underscores before an HOD Staff ID can '
                        'be generated.'
                    )

                prefix = f'WTI-HOD-{programme.code}/{year}/'
                issued_ids = HODStaffIDRegistry.objects.using(
                    database
                ).filter(staff_id__startswith=prefix).values_list(
                    'staff_id',
                    flat=True,
                )
                highest_sequence = 0
                for staff_id in issued_ids:
                    try:
                        highest_sequence = max(
                            highest_sequence,
                            int(staff_id.rsplit('/', 1)[1]),
                        )
                    except (IndexError, ValueError):
                        continue

                sequence = highest_sequence + 1
                staff_id = f'{prefix}{sequence:02d}{suffix}'
                while _staff_id_is_reserved(staff_id, using=database):
                    sequence += 1
                    staff_id = f'{prefix}{sequence:02d}'
                registry_values = _staff_id_registry_values(staff_id)
                registry_values.update(
                    programme_code=programme.code,
                    year=year,
                    sequence=sequence,
                )
                HODStaffIDRegistry.objects.using(database).create(
                    **registry_values,
                )
                return staff_id
        except InvalidProgrammeCodeError:
            raise
        except IntegrityError as exc:
            if attempt == 4:
                raise HODStaffIDAllocationError(
                    'Could not reserve a unique HOD Staff ID after several '
                    'attempts. Try again.'
                ) from exc
        except OperationalError as exc:
            message = str(exc).lower()
            is_sqlite_lock = (
                'sqlite' in connections[database].vendor
                and ('locked' in message or 'busy' in message)
            )
            if not is_sqlite_lock:
                raise
            if attempt == 4:
                raise HODStaffIDAllocationError(
                    'The database is busy and could not reserve an HOD Staff '
                    'ID. Try again.'
                ) from exc

        time.sleep(0.02 * (attempt + 1))


def generate_registered_staff_id(role, programme_code):
    role = role.upper()
    programme_code = programme_code.strip().upper()
    if role not in dict(StaffIDRegistry.ROLE_CHOICES):
        raise ValidationError('Unsupported Staff ID role.')
    if not re.fullmatch(r'[A-Z0-9_-]+', programme_code):
        raise InvalidProgrammeCodeError(
            'Programme code contains characters that cannot be used in a Staff ID.'
        )

    database = router.db_for_write(StaffIDRegistry)
    year = timezone.localdate().year
    prefix = f'WTI-{programme_code}/'
    suffix = f'-{year}'

    for attempt in range(5):
        try:
            with transaction.atomic(using=database):
                if role == 'FACILITATOR':
                    Programme.objects.using(database).select_for_update().get(
                        code=programme_code,
                        is_active=True,
                    )
                entries = StaffIDRegistry.objects.using(database).filter(
                    role=role,
                    programme_code=programme_code,
                    year=year,
                ).values_list('sequence', flat=True)
                sequence = max(entries, default=0) + 1
                while True:
                    staff_id = f'{prefix}{sequence:02d}'
                    if not _staff_id_is_reserved(staff_id, using=database):
                        break
                    sequence += 1
                StaffIDRegistry.objects.using(database).create(
                    staff_id=staff_id,
                    role=role,
                    programme_code=programme_code,
                    year=year,
                    sequence=sequence,
                )
                return staff_id
        except IntegrityError:
            if attempt == 4:
                raise StaffIDAllocationError(
                    'Could not reserve a unique Staff ID after several attempts. Try again.'
                )
        except OperationalError as exc:
            message = str(exc).lower()
            is_sqlite_lock = (
                'sqlite' in connections[database].vendor
                and ('locked' in message or 'busy' in message)
            )
            if not is_sqlite_lock:
                raise
            if attempt == 4:
                raise StaffIDAllocationError(
                    'The database is busy and could not reserve a Staff ID. Try again.'
                ) from exc
        time.sleep(0.02 * (attempt + 1))


HOD = HODProfile
HeadOfDepartment = HODProfile


class FacilitatorProfile(models.Model):
    EMPLOYMENT_TYPE_CHOICES = [
        ('PERMANENT', 'Permanent'),
        ('INTERNSHIP', 'Internship'),
    ]

    teacher = models.OneToOneField('TeacherProfile', on_delete=models.CASCADE, related_name='facilitator_profile')
    programme = models.ForeignKey(
        Programme,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='facilitators_detail',
    )
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE_CHOICES, default='PERMANENT')
    internship_start_date = models.DateField(null=True, blank=True)
    internship_end_date = models.DateField(null=True, blank=True)
    supervisor = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='interns',
        limit_choices_to={'employment_type': 'PERMANENT', 'is_active': True},
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['teacher__user__first_name', 'teacher__user__last_name']

    def clean(self):
        super().clean()
        if self.employment_type == 'INTERNSHIP':
            if self.internship_start_date and self.internship_end_date and self.internship_end_date <= self.internship_start_date:
                raise ValidationError({'internship_end_date': 'Internship end date must be later than the start date.'})
        if self.supervisor:
            if self.employment_type != 'INTERNSHIP':
                raise ValidationError({
                    'supervisor': 'Only internship facilitators may have a Permanent Facilitator supervisor.'
                })
            if self.supervisor.employment_type != 'PERMANENT':
                raise ValidationError({
                    'supervisor': 'Only a Permanent Facilitator may supervise an intern.'
                })
            if not self.programme_id or self.supervisor.programme_id != self.programme_id:
                raise ValidationError({
                    'supervisor': 'Supervisor and intern must belong to the same programme.'
                })

    def save(self, *args, **kwargs):
        if self.employment_type == 'INTERNSHIP' and self.internship_start_date and not self.internship_end_date:
            self.internship_end_date = add_years(self.internship_start_date, 1)
        if (
            self.employment_type == 'INTERNSHIP'
            and self.internship_end_date
            and self.internship_end_date < timezone.localdate()
        ):
            self.is_active = False
        self.clean()
        super().save(*args, **kwargs)

    @property
    def is_internship(self):
        return self.employment_type == 'INTERNSHIP'

    @property
    def is_expired(self):
        if self.employment_type != 'INTERNSHIP' or not self.internship_end_date:
            return False
        return self.internship_end_date < timezone.localdate()

    @property
    def remaining_days(self):
        if self.employment_type != 'INTERNSHIP' or not self.internship_end_date:
            return None
        return (self.internship_end_date - timezone.localdate()).days

    @property
    def full_name(self):
        return self.teacher.full_name

    def __str__(self):
        return f"{self.teacher.full_name} ({self.get_employment_type_display()})"


Facilitator = FacilitatorProfile


class InternshipSupervision(models.Model):
    internship = models.ForeignKey(
        FacilitatorProfile,
        on_delete=models.CASCADE,
        related_name='supervision_history',
        limit_choices_to={'employment_type': 'INTERNSHIP'},
    )
    supervisor = models.ForeignKey(
        FacilitatorProfile,
        on_delete=models.CASCADE,
        related_name='assigned_internships',
        limit_choices_to={'employment_type': 'PERMANENT'},
    )
    start_date = models.DateField(default=date.today)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='internship_assignments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']
        constraints = [
            models.UniqueConstraint(
                fields=['internship'],
                condition=Q(is_active=True),
                name='unique_active_supervisor_per_intern',
            )
        ]

    def clean(self):
        super().clean()
        if self.internship.employment_type != 'INTERNSHIP':
            raise ValidationError({'internship': 'Only Internship Facilitators can be assigned a supervisor.'})
        if self.supervisor.employment_type != 'PERMANENT':
            raise ValidationError({'supervisor': 'Only Permanent Facilitators can supervise interns.'})
        if self.internship.programme_id != self.supervisor.programme_id:
            raise ValidationError({'supervisor': 'The supervisor must belong to the same programme as the internship facilitator.'})
        if (
            not self.supervisor.is_active
            or not self.supervisor.teacher.is_approved
            or not self.supervisor.teacher.user.is_active
        ):
            raise ValidationError({
                'supervisor': 'Only active, approved Permanent Facilitators can supervise interns.'
            })
        if (
            not self.internship.is_active
            or self.internship.is_expired
            or not self.internship.teacher.is_approved
            or not self.internship.teacher.user.is_active
        ):
            raise ValidationError({
                'internship': 'Only active Internship Facilitators can be assigned a supervisor.'
            })

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.internship.teacher.full_name} -> {self.supervisor.teacher.full_name}"


class InternshipExtension(models.Model):
    STATUS_CHOICES = [
        ('REQUESTED', 'Requested'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    internship = models.ForeignKey(FacilitatorProfile, on_delete=models.CASCADE, related_name='extensions')
    old_end_date = models.DateField()
    new_end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default='APPROVED',
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requested_internship_extensions',
    )
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_extensions')
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['internship'],
                condition=Q(status='REQUESTED'),
                name='unique_pending_internship_extension',
            ),
        ]

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if (
            self.old_end_date
            and self.new_end_date
            and self.new_end_date <= self.old_end_date
        ):
            raise ValidationError({
                'new_end_date': 'The extended end date must be later than the current end date.'
            })

    def __str__(self):
        return f"{self.internship.teacher.full_name}: {self.old_end_date} -> {self.new_end_date}"


def expire_internships():
    today = timezone.localdate()
    with transaction.atomic():
        expired_ids = list(
            FacilitatorProfile.objects.select_for_update().filter(
                employment_type='INTERNSHIP',
                is_active=True,
                internship_end_date__lt=today,
            ).values_list('pk', flat=True)
        )
        if not expired_ids:
            return 0
        count = FacilitatorProfile.objects.filter(
            pk__in=expired_ids,
            is_active=True,
        ).update(is_active=False, updated_at=timezone.now())
        InternshipSupervision.objects.filter(
            internship_id__in=expired_ids,
            is_active=True,
        ).update(is_active=False, end_date=today)
        return count


# ─── Teacher Profile ─────────────────────────────────────────────────────────

class TeacherProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='teacher_profile')
    staff_id = models.CharField(max_length=100, unique=True, blank=True)
    phone = models.CharField(max_length=20)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.TextField()
    profile_photo = models.ImageField(upload_to='profiles/', null=True, blank=True)
    qualification = models.CharField(max_length=20, choices=QUALIFICATION_CHOICES)
    specialization = models.CharField(max_length=200)
    years_of_experience = models.PositiveIntegerField(default=0)
    previous_school = models.CharField(max_length=200, blank=True)
    bio = models.TextField(blank=True)
    course_type = models.CharField(max_length=20, choices=COURSE_TYPE_CHOICES)
    preferred_subject = models.CharField(max_length=100)
    department = models.CharField(max_length=100, blank=True)
    programme = models.ForeignKey(Programme, on_delete=models.PROTECT, null=True, blank=True, related_name='teachers')
    cv_document = models.FileField(upload_to='documents/cv/', null=True, blank=True)
    certificate_document = models.FileField(upload_to='documents/certificates/', null=True, blank=True)
    id_document = models.FileField(upload_to='documents/ids/', null=True, blank=True)
    is_approved = models.BooleanField(default=False)
    profile_complete = models.BooleanField(default=False)  # True after teacher fills details
    date_joined = models.DateTimeField(default=timezone.now)
    emergency_contact = models.CharField(max_length=100, blank=True)
    emergency_phone = models.CharField(max_length=20, blank=True)

    def save(self, *args, **kwargs):
        if not self.staff_id:
            self.staff_id = generate_unique_staff_id()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.staff_id})"

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.username

    @property
    def is_facilitator(self):
        return hasattr(self, 'facilitator_profile')


# ─── QR Code — Two types: school_attendance and lesson ───────────────────────

class QRCode(models.Model):
    QR_TYPE_CHOICES = [
        ('school_attendance', 'School Attendance'),
        ('lesson', 'Lesson'),
    ]
    code = models.UUIDField(default=uuid.uuid4, unique=True)
    qr_type = models.CharField(max_length=20, choices=QR_TYPE_CHOICES, default='school_attendance')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='qr_codes')
    created_at = models.DateTimeField(auto_now_add=True)
    # Valid for 5 years from creation
    valid_from = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    qr_image = models.ImageField(upload_to='qrcodes/', null=True, blank=True)
    label = models.CharField(max_length=100, default='School Attendance QR')

    def save(self, *args, **kwargs):
        if not self.valid_until and self.valid_from:
            try:
                self.valid_until = self.valid_from.replace(year=self.valid_from.year + 5)
            except ValueError:
                # Feb 29 edge case
                self.valid_until = self.valid_from.replace(year=self.valid_from.year + 5, day=28)
        super().save(*args, **kwargs)

    @property
    def is_valid_today(self):
        today = timezone.localdate()
        if not self.valid_until:
            return self.is_active
        return self.is_active and self.valid_from <= today <= self.valid_until

    def __str__(self):
        return f"{self.get_qr_type_display()} QR — {self.label}"

    class Meta:
        ordering = ['-created_at']


# ─── Attendance ───────────────────────────────────────────────────────────────

class Attendance(models.Model):
    STATUS_CHOICES = [
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('late', 'Late'),
        ('unverified', 'Unverified'),
    ]
    LESSON_STATUS_CHOICES = [
        ('done', 'Lesson Done'),
        ('not_done', 'Lesson Not Done'),
        ('pending', 'Pending'),
    ]

    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(default=timezone.localdate)

    # School attendance (scan school QR)
    school_qr = models.ForeignKey(
        QRCode, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='school_attendances'
    )
    school_status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='unverified',
    )
    check_in_time = models.TimeField(null=True, blank=True)

    # Lesson attendance (scan lesson QR)
    lesson_qr = models.ForeignKey(
        QRCode, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='lesson_attendances'
    )
    lesson_status = models.CharField(max_length=10, choices=LESSON_STATUS_CHOICES, default='pending')
    lesson_done_time = models.TimeField(null=True, blank=True)

    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ['teacher', 'date']
        ordering = ['-date', 'teacher']

    def __str__(self):
        return f"{self.teacher.full_name} — {self.date} — School:{self.school_status} Lesson:{self.lesson_status}"


# ─── Timetable ────────────────────────────────────────────────────────────────

class TimetableEntry(models.Model):
    DAY_CHOICES = [
        ('monday', 'Monday'),
        ('tuesday', 'Tuesday'),
        ('wednesday', 'Wednesday'),
        ('thursday', 'Thursday'),
        ('friday', 'Friday'),
    ]
    day = models.CharField(max_length=10, choices=DAY_CHOICES)
    subject = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField()
    teacher = models.ForeignKey(
        TeacherProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='timetable_entries'
    )
    is_break = models.BooleanField(default=False)
    room = models.CharField(max_length=50, blank=True)
    course_type = models.CharField(max_length=20, choices=COURSE_TYPE_CHOICES, blank=True)

    class Meta:
        ordering = ['day', 'start_time']

    def __str__(self):
        return f"{self.day} — {self.subject} ({self.start_time}–{self.end_time})"


# ─── Notifications ────────────────────────────────────────────────────────────

class Notification(models.Model):
    TYPE_CHOICES = [
        ('attendance', 'Attendance'),
        ('schedule', 'Schedule'),
        ('reminder', 'Class Reminder'),
        ('general', 'General'),
        ('admin', 'Admin Notice'),
    ]
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_notifications')
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='general')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    related_timetable = models.ForeignKey(TimetableEntry, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"To {self.recipient.username}: {self.title}"


# ─── Class Schedule ───────────────────────────────────────────────────────────

class ClassSchedule(models.Model):
    timetable_entry = models.ForeignKey(TimetableEntry, on_delete=models.CASCADE, related_name='schedules')
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='schedules')
    scheduled_date = models.DateField()
    reminder_sent = models.BooleanField(default=False)
    reminder_10min_sent = models.BooleanField(default=False)
    is_completed = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_schedules')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scheduled_date', 'timetable_entry__start_time']

    def __str__(self):
        return f"{self.teacher.full_name} — {self.timetable_entry.subject} on {self.scheduled_date}"


class SchedulingEvaluation(models.Model):
    STATUS_CHOICES = [
        ('ELIGIBLE', 'Eligible'),
        ('BLOCKED', 'Blocked'),
        ('UNVERIFIED', 'Unverified'),
    ]

    programme = models.ForeignKey(
        Programme,
        on_delete=models.PROTECT,
        related_name='scheduling_evaluations',
    )
    candidate = models.ForeignKey(
        TeacherProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scheduling_evaluations',
    )
    proposed_timetable_entry = models.ForeignKey(
        TimetableEntry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scheduling_evaluations',
    )
    proposed_class = models.CharField(max_length=200)
    scheduled_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES)
    rule_results = models.JSONField(default=list)
    weekly_scheduled_minutes = models.PositiveIntegerField(default=0)
    recurring_timetable_minutes = models.PositiveIntegerField(default=0)
    projected_weekly_minutes = models.PositiveIntegerField(default=0)
    attendance_status = models.CharField(max_length=20, default='UNVERIFIED')
    location_status = models.CharField(max_length=20, default='UNVERIFIED')
    availability_status = models.CharField(max_length=20, default='UNVERIFIED')
    recommendation_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    recommendation_rank = models.PositiveIntegerField(null=True, blank=True)
    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scheduling_evaluations',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        candidate_name = self.candidate.full_name if self.candidate else 'Deleted candidate'
        return f"{self.status}: {self.proposed_class} for {candidate_name} on {self.scheduled_date}"


class ClassConfirmationRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Facilitator response'),
        ('ACCEPTED', 'Accepted'),
        ('DECLINED', 'Declined'),
        ('WITHHELD', 'Withheld after re-evaluation'),
        ('CANCELLED', 'Cancelled'),
    ]

    programme = models.ForeignKey(
        Programme,
        on_delete=models.PROTECT,
        related_name='class_confirmation_requests',
    )
    candidate = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        related_name='class_confirmation_requests',
    )
    timetable_entry = models.ForeignKey(
        TimetableEntry,
        on_delete=models.PROTECT,
        related_name='class_confirmation_requests',
    )
    scheduled_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    evaluation = models.ForeignKey(
        SchedulingEvaluation,
        on_delete=models.PROTECT,
        related_name='confirmation_requests',
    )
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default='PENDING',
    )
    response_note = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='requested_class_confirmations',
    )
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='responded_class_confirmations',
    )
    confirmed_schedule = models.OneToOneField(
        ClassSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmation_request',
    )
    replaces = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replacement_confirmations',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['timetable_entry', 'scheduled_date'],
                condition=Q(status='PENDING'),
                name='unique_pending_confirmation_per_class_date',
            ),
        ]

    def __str__(self):
        return f"{self.status}: {self.timetable_entry.subject} for {self.candidate.full_name}"


class ClassReplacementRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending HOD approval'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    original_request = models.OneToOneField(
        ClassConfirmationRequest,
        on_delete=models.PROTECT,
        related_name='replacement_request',
    )
    programme = models.ForeignKey(
        Programme,
        on_delete=models.PROTECT,
        related_name='class_replacement_requests',
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='PENDING',
    )
    reason = models.TextField()
    recommendations = models.ManyToManyField(
        SchedulingEvaluation,
        blank=True,
        related_name='replacement_reviews',
    )
    selected_candidate = models.ForeignKey(
        TeacherProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='replacement_selections',
    )
    replacement_confirmation = models.OneToOneField(
        ClassConfirmationRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replacement_approval',
    )
    decision_note = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='decided_class_replacements',
    )

    class Meta:
        ordering = ['status', '-requested_at', '-pk']

    def __str__(self):
        return f"{self.status}: replacement for {self.original_request}"


# ─── School Location Settings ─────────────────────────────────────────────────

class SchoolLocation(models.Model):
    """Stores school location and allowed radius for QR code scanning."""
    name = models.CharField(max_length=200, default='Main Campus')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, help_text='School latitude coordinate')
    longitude = models.DecimalField(max_digits=9, decimal_places=6, help_text='School longitude coordinate')
    allowed_radius = models.PositiveIntegerField(default=100, help_text='Allowed radius in meters')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'School Location'
        verbose_name_plural = 'School Locations'

    def __str__(self):
        return f"{self.name} (Radius: {self.allowed_radius}m)"

    def is_within_radius(self, user_lat, user_lon):
        """Check if user coordinates are within allowed radius using Haversine formula."""
        from math import radians, cos, sin, sqrt, asin

        # Convert decimal degrees to radians
        lat1, lon1 = radians(float(self.latitude)), radians(float(self.longitude))
        lat2, lon2 = radians(float(user_lat)), radians(float(user_lon))

        # Haversine formula
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        
        # Radius of Earth in meters
        r = 6371000
        distance = c * r

        return distance <= self.allowed_radius
