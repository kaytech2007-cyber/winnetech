
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from .models import (
    TeacherProfile, ELECTIVE_SUBJECTS, DEPARTMENT_SUBJECTS,
    DEPARTMENT_CHOICES, COURSE_TYPE_CHOICES, GENDER_CHOICES,
    QUALIFICATION_CHOICES, TimetableEntry, Notification,
    QRCode, ClassSchedule, SchoolLocation, FacilitatorProfile,
    HODProfile, Programme, InternshipExtension, add_years,
)

# All valid subject values for server-side validation
ALL_SUBJECT_VALUES = (
    [v for v, _ in ELECTIVE_SUBJECTS] +
    [v for v, _ in DEPARTMENT_SUBJECTS]
)

DEPARTMENT_SUBJECTS_BY_DEPARTMENT = {
    'information_technology': [
        'computer_system_servicing',
        'workshop_practice_power',
        'networking_data_comm',
    ],
    'plumbing': [
        'plumbing_general',
        'plumbing_installation',
        'plumbing_maintenance',
    ],
    'electricals': [
        'electrical_installation',
        'electrical_maintenance',
        'electrical_wiring',
    ],
    'welding_fabrication': ['welding_fabrication', 'metal_work'],
    'fashion': [
        'fashion_design',
        'garment_construction',
        'textile_studies',
    ],
    'catering': ['catering_general', 'food_beverage', 'hospitality'],
    'mechanical_engineering': [
        'mechanical_engineering',
        'machine_maintenance',
    ],
    'auto_mechanics': ['auto_mechanics', 'auto_electrical'],
    'building_construction': [
        'building_construction',
        'masonry',
        'carpentry',
    ],
}


class HODFacilitatorManagementForm(forms.ModelForm):
    class Meta:
        model = FacilitatorProfile
        fields = [
            'employment_type',
            'internship_start_date',
            'supervisor',
        ]
        widgets = {
            'employment_type': forms.Select(attrs={'class': 'form-select'}),
            'internship_start_date': forms.DateInput(
                attrs={'class': 'form-control', 'type': 'date'}
            ),
            'supervisor': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, programme, **kwargs):
        super().__init__(*args, **kwargs)
        self.programme = programme
        supervisor_queryset = FacilitatorProfile.objects.filter(
            programme=programme,
            employment_type='PERMANENT',
            is_active=True,
            teacher__is_approved=True,
            teacher__user__is_active=True,
        ).select_related('teacher__user')
        self.fields['supervisor'].queryset = supervisor_queryset

        if self.instance.pk:
            self.fields['employment_type'].disabled = True
            self.fields['internship_start_date'].disabled = True
        else:
            self.fields['internship_start_date'].required = False

    def clean(self):
        cleaned_data = super().clean()
        employment_type = cleaned_data.get('employment_type')
        supervisor = cleaned_data.get('supervisor')

        if supervisor and supervisor.programme_id != self.programme.pk:
            self.add_error('supervisor', 'Choose a permanent facilitator in your programme.')
        if supervisor and supervisor.employment_type != 'PERMANENT':
            self.add_error('supervisor', 'Only permanent facilitators can supervise interns.')
        if supervisor and (
            not supervisor.is_active
            or not supervisor.teacher.is_approved
            or not supervisor.teacher.user.is_active
        ):
            self.add_error('supervisor', 'Choose an active, approved permanent facilitator.')

        if employment_type == 'INTERNSHIP':
            start_date = cleaned_data.get('internship_start_date')
            if not self.instance.pk and not start_date:
                self.add_error('internship_start_date', 'Enter the internship start date.')
            if start_date and add_years(start_date, 1) < timezone.localdate():
                self.add_error(
                    'internship_start_date',
                    'This internship period has already expired.',
                )
            if supervisor and (
                not self.instance.is_active or self.instance.is_expired
            ):
                self.add_error(
                    'supervisor',
                    'Inactive or expired internship facilitators cannot be assigned.',
                )
        else:
            cleaned_data['internship_start_date'] = None
            cleaned_data['supervisor'] = None

        return cleaned_data


class HODManagementForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(
        attrs={'class': 'form-control', 'autocomplete': 'username'}
    ))
    first_name = forms.CharField(max_length=150, widget=forms.TextInput(
        attrs={'class': 'form-control', 'autocomplete': 'given-name'}
    ))
    last_name = forms.CharField(max_length=150, widget=forms.TextInput(
        attrs={'class': 'form-control', 'autocomplete': 'family-name'}
    ))
    email = forms.EmailField(widget=forms.EmailInput(
        attrs={'class': 'form-control', 'autocomplete': 'email'}
    ))
    phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'tel'}),
    )
    programme = forms.ModelChoiceField(
        queryset=Programme.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=False,
    )
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def __init__(self, *args, hod=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hod = hod
        if hod:
            self.fields['username'].initial = hod.user.username
            self.fields['first_name'].initial = hod.user.first_name
            self.fields['last_name'].initial = hod.user.last_name
            self.fields['email'].initial = hod.user.email
            self.fields['phone'].initial = hod.phone
            self.fields['programme'].initial = hod.programme_id
            self.fields['is_active'].initial = hod.is_active
            if hod.programme_id:
                self.fields['programme'].queryset = Programme.objects.filter(
                    Q(is_active=True) | Q(pk=hod.programme_id)
                )

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        users = User.objects.filter(username__iexact=username)
        if self.hod:
            users = users.exclude(pk=self.hod.user_id)
        if users.exists():
            raise ValidationError('This username is already in use.')
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        users = User.objects.filter(email__iexact=email)
        if self.hod:
            users = users.exclude(pk=self.hod.user_id)
        if users.exists():
            raise ValidationError('This email address is already in use.')
        return email

    def clean(self):
        cleaned_data = super().clean()
        if (
            (cleaned_data.get('is_active') or self.hod is None)
            and not cleaned_data.get('programme')
        ):
            self.add_error(
                'programme',
                'A new or active HOD must be assigned to a programme.',
            )
        return cleaned_data


class ActiveProgrammeChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.name} ({obj.code}) — {obj.get_category_display()}'


class FacilitatorAccountForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20)
    gender = forms.ChoiceField(choices=GENDER_CHOICES)
    qualification = forms.ChoiceField(choices=QUALIFICATION_CHOICES)
    programme = ActiveProgrammeChoiceField(
        queryset=Programme.objects.filter(is_active=True),
        empty_label='-- Select Programme --',
    )
    employment_type = forms.ChoiceField(
        choices=FacilitatorProfile.EMPLOYMENT_TYPE_CHOICES,
        widget=forms.RadioSelect,
    )
    internship_start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    supervisor = forms.ModelChoiceField(
        queryset=FacilitatorProfile.objects.none(),
        required=False,
        empty_label='-- Select Permanent Supervisor --',
    )
    course_type = forms.ChoiceField(choices=COURSE_TYPE_CHOICES)
    preferred_subject = forms.ChoiceField(choices=[])
    department = forms.ChoiceField(
        choices=[('', '-- Select Department --')] + DEPARTMENT_CHOICES,
        required=False,
    )
    emergency_contact = forms.CharField(max_length=100, required=False)
    emergency_phone = forms.CharField(max_length=20, required=False)
    profile_photo = forms.ImageField(required=False)
    certificate_document = forms.FileField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['programme'].queryset = Programme.objects.filter(
            is_active=True,
        ).order_by('name')
        self.fields['programme'].widget.attrs.update({
            'class': 'form-select',
            'id': 'id_programme',
        })
        self.fields['employment_type'].widget.attrs.update({
            'class': 'form-check-input',
        })
        for name, field in self.fields.items():
            if name in ('employment_type',):
                continue
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
        self.fields['preferred_subject'].choices = (
            ELECTIVE_SUBJECTS + DEPARTMENT_SUBJECTS
        )
        self.fields['internship_start_date'].widget.attrs['class'] = 'form-control'
        programme_id = self.data.get('programme') if self.is_bound else None
        if programme_id and str(programme_id).isdigit():
            self.fields['supervisor'].queryset = FacilitatorProfile.objects.filter(
                programme_id=int(programme_id),
                employment_type='PERMANENT',
                is_active=True,
                teacher__is_approved=True,
                teacher__user__is_active=True,
            ).select_related('teacher__user').order_by(
                'teacher__user__last_name',
                'teacher__user__first_name',
            )

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account already uses this email address.')
        return email

    def clean(self):
        cleaned_data = super().clean()
        programme = cleaned_data.get('programme')
        employment_type = cleaned_data.get('employment_type')
        supervisor = cleaned_data.get('supervisor')
        start_date = cleaned_data.get('internship_start_date')

        if employment_type == 'INTERNSHIP':
            if not start_date:
                self.add_error(
                    'internship_start_date',
                    'Enter the internship start date.',
                )
            elif start_date < timezone.localdate():
                self.add_error(
                    'internship_start_date',
                    'The internship start date cannot be in the past.',
                )
            if not supervisor:
                self.add_error('supervisor', 'Select an active Permanent Supervisor.')
            elif programme and (
                supervisor.programme_id != programme.pk
                or supervisor.employment_type != 'PERMANENT'
                or not supervisor.is_active
                or not supervisor.teacher.is_approved
                or not supervisor.teacher.user.is_active
            ):
                self.add_error(
                    'supervisor',
                    'Choose an active Permanent Facilitator in the selected programme.',
                )
        else:
            cleaned_data['internship_start_date'] = None
            cleaned_data['supervisor'] = None

        course_type = cleaned_data.get('course_type')
        subject_choices = (
            ELECTIVE_SUBJECTS if course_type == 'core' else DEPARTMENT_SUBJECTS
        )
        self.fields['preferred_subject'].choices = subject_choices
        subject = cleaned_data.get('preferred_subject')
        department = cleaned_data.get('department')
        if course_type == 'core' and subject and subject not in dict(ELECTIVE_SUBJECTS):
            self.add_error('preferred_subject', 'Choose a valid core subject.')
        elif course_type == 'departmental':
            if department not in dict(DEPARTMENT_CHOICES):
                self.add_error('department', 'Choose a valid department.')
            elif subject and subject not in DEPARTMENT_SUBJECTS_BY_DEPARTMENT.get(
                department,
                [],
            ):
                self.add_error(
                    'preferred_subject',
                    'Choose a subject offered by the selected department.',
                )
        if course_type == 'core':
            cleaned_data['department'] = ''
        return cleaned_data


class AdminAccountForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs['class'] = (
                'form-control'
                if not isinstance(field.widget, forms.Select)
                else 'form-select'
            )

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account already uses this email address.')
        return email


class SetStaffPasswordForm(forms.Form):
    new_password1 = forms.CharField(
        label='New password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    new_password2 = forms.CharField(
        label='Confirm new password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('new_password1')
        confirmation = cleaned_data.get('new_password2')
        if password and confirmation and password != confirmation:
            self.add_error('new_password2', 'The passwords do not match.')
        if password:
            from django.contrib.auth.password_validation import validate_password
            try:
                validate_password(password, user=self.user)
            except ValidationError as exc:
                self.add_error('new_password1', exc)
        return cleaned_data


class ProgrammeForm(forms.ModelForm):
    class Meta:
        model = Programme
        fields = ['name', 'code', 'category', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput({
                'class': 'form-control',
                'autocomplete': 'off',
                'style': 'text-transform:uppercase',
            }),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


class InternshipExtensionRequestForm(forms.ModelForm):
    class Meta:
        model = InternshipExtension
        fields = ['new_end_date', 'reason']
        widgets = {
            'new_end_date': forms.DateInput(
                attrs={'class': 'form-control', 'type': 'date'}
            ),
            'reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, internship, **kwargs):
        super().__init__(*args, **kwargs)
        self.internship = internship

    def clean_new_end_date(self):
        new_end_date = self.cleaned_data['new_end_date']
        current_end_date = self.internship.internship_end_date
        if new_end_date <= timezone.localdate():
            raise ValidationError('The requested end date must be in the future.')
        if current_end_date and new_end_date <= current_end_date:
            raise ValidationError(
                'The requested end date must be later than the current end date.'
            )
        return new_end_date

    def save(self, commit=True):
        extension = super().save(commit=False)
        extension.internship = self.internship
        extension.old_end_date = (
            self.internship.internship_end_date or timezone.localdate()
        )
        extension.status = 'REQUESTED'
        if commit:
            extension.save()
        return extension


class TeacherRegistrationForm(UserCreationForm):
    # ── Staff ID (chosen by teacher, used for login) ───────────────────────
    staff_id = forms.CharField(
        max_length=20, required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. WTI0042',
            'style': 'text-transform:uppercase;font-weight:600;letter-spacing:1px',
        }),
        help_text='This will be your login ID. Must be unique (e.g. WTI0042).'
    )

    # ── Personal ──────────────────────────────────────────────
    first_name = forms.CharField(
        max_length=100, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'}))
    last_name = forms.CharField(
        max_length=100, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}))
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email Address'}))
    phone = forms.CharField(
        max_length=20, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone Number'}))
    gender = forms.ChoiceField(
        choices=[('', '-- Select Gender --')] + list(GENDER_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}))

    # ── Professional ──────────────────────────────────────────
    qualification = forms.ChoiceField(
        choices=[('', '-- Select Qualification --')] + list(QUALIFICATION_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}))
    # ── Course Preference ─────────────────────────────────────
    course_type = forms.ChoiceField(
        choices=[('', '-- Select Course Type --')] + list(COURSE_TYPE_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}))
    preferred_subject = forms.CharField(
        max_length=100, required=True,
        widget=forms.HiddenInput())
    department = forms.CharField(
        max_length=100, required=False,
        widget=forms.HiddenInput())

    # ── Documents ─────────────────────────────────────────────
    profile_photo = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}))
    certificate_document = forms.FileField(
        required=False,
        widget=forms.FileInput(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        # No username field — we generate it from staff_id
        fields = ['first_name', 'last_name', 'email', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({
            'class': 'form-control', 'placeholder': 'Create a strong password'})
        self.fields['password2'].widget.attrs.update({
            'class': 'form-control', 'placeholder': 'Confirm your password'})

    def clean_staff_id(self):
        sid = self.cleaned_data.get('staff_id', '').strip().upper()
        if not sid:
            raise ValidationError('Staff ID is required.')
        if len(sid) < 4:
            raise ValidationError('Staff ID must be at least 4 characters.')
        # Check uniqueness
        if TeacherProfile.objects.filter(staff_id=sid).exists():
            raise ValidationError(f'Staff ID "{sid}" is already taken. Choose a different one.')
        # Also check no user with this username
        if User.objects.filter(username=sid).exists():
            raise ValidationError(f'Staff ID "{sid}" is already in use.')
        return sid

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise ValidationError('A user with this email address already exists.')
        return email

    def clean_gender(self):
        gender = self.cleaned_data.get('gender', '')
        if not gender:
            raise ValidationError('Please select your gender.')
        return gender

    def clean_qualification(self):
        qual = self.cleaned_data.get('qualification', '')
        if not qual:
            raise ValidationError('Please select your qualification.')
        return qual

    def clean_course_type(self):
        ct = self.cleaned_data.get('course_type', '')
        if not ct:
            raise ValidationError('Please select a course type (Core Subjects or Departmental).')
        return ct

    def clean_preferred_subject(self):
        subject = self.cleaned_data.get('preferred_subject', '').strip()
        if not subject:
            raise ValidationError('Please select a preferred subject.')
        return subject

    def clean_years_of_experience(self):
        yoe = self.cleaned_data.get('years_of_experience')
        if yoe is None or yoe < 0:
            return 0
        return yoe

    def save(self, commit=True):
        user = super().save(commit=False)
        staff_id = self.cleaned_data['staff_id']
        # Use staff_id as the username so login works
        user.username = staff_id
        user.first_name = self.cleaned_data['first_name'].strip()
        user.last_name = self.cleaned_data['last_name'].strip()
        user.email = self.cleaned_data['email'].strip().lower()
        if commit:
            user.save()
            TeacherProfile.objects.create(
                user=user,
                staff_id=staff_id,  # explicitly set — no auto-generation needed
                phone=self.cleaned_data.get('phone', '').strip(),
                gender=self.cleaned_data.get('gender', ''),
                address='',
                qualification=self.cleaned_data.get('qualification', ''),
                specialization='',
                years_of_experience=0,
                bio='',
                course_type=self.cleaned_data.get('course_type', ''),
                preferred_subject=self.cleaned_data.get('preferred_subject', '').strip(),
                department=self.cleaned_data.get('department', '').strip(),
                profile_photo=self.cleaned_data.get('profile_photo'),
                certificate_document=self.cleaned_data.get('certificate_document'),
            )
        return user


# ── Auth Forms ────────────────────────────────────────────────────────────────

class TeacherLoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'}))
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password'}))


class AdminLoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Admin Username'}))
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Admin Password'}))


# ── Admin Forms ───────────────────────────────────────────────────────────────

class NotificationForm(forms.ModelForm):
    recipient = forms.ModelChoiceField(
        queryset=User.objects.filter(is_staff=False),
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label='-- Select Facilitator --')

    class Meta:
        model = Notification
        fields = ['recipient', 'title', 'message', 'notification_type']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Notification Title'}),
            'message': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'notification_type': forms.Select(attrs={'class': 'form-select'}),
        }


class TimetableEntryForm(forms.ModelForm):
    class Meta:
        model = TimetableEntry
        fields = ['day', 'subject', 'start_time', 'end_time', 'teacher', 'is_break', 'room', 'course_type']
        widgets = {
            'day': forms.Select(attrs={'class': 'form-select'}),
            'subject': forms.TextInput(attrs={'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'teacher': forms.Select(attrs={'class': 'form-select'}),
            'is_break': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'room': forms.TextInput(attrs={'class': 'form-control'}),
            'course_type': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_value = self.instance.course_type if self.instance.pk else ''
        allowed_values = {value for value, _ in COURSE_TYPE_CHOICES}
        if current_value and current_value not in allowed_values:
            self.fields['course_type'].choices = list(
                self.fields['course_type'].choices
            ) + [(current_value, f'{current_value} (legacy; preserved)')]

    def clean_course_type(self):
        value = self.cleaned_data.get('course_type', '')
        current_value = self.instance.course_type if self.instance.pk else ''
        allowed_values = {choice for choice, _ in COURSE_TYPE_CHOICES}
        if not value and current_value and current_value not in allowed_values:
            return current_value
        return value

    def _get_validation_exclusions(self):
        exclude = super()._get_validation_exclusions()
        current_value = self.instance.course_type if self.instance.pk else ''
        allowed_values = {choice for choice, _ in COURSE_TYPE_CHOICES}
        if (
            current_value
            and current_value not in allowed_values
            and self.cleaned_data.get('course_type') == current_value
        ):
            exclude.add('course_type')
        return exclude


class ClassScheduleForm(forms.ModelForm):
    class Meta:
        model = ClassSchedule
        fields = ['timetable_entry', 'teacher', 'scheduled_date']
        widgets = {
            'timetable_entry': forms.Select(attrs={'class': 'form-select'}),
            'teacher': forms.Select(attrs={'class': 'form-select'}),
            'scheduled_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }


class QRCodeForm(forms.ModelForm):
    class Meta:
        model = QRCode
        fields = ['qr_type', 'label']
        widgets = {
            'qr_type': forms.Select(attrs={'class': 'form-select'}),
            'label': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Main Gate QR / Classroom QR'}),
        }


class SchoolLocationForm(forms.ModelForm):
    class Meta:
        model = SchoolLocation
        fields = ['name', 'latitude', 'longitude', 'allowed_radius', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Main Campus'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'placeholder': 'e.g. 5.6037'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001', 'placeholder': 'e.g. -0.1870'}),
            'allowed_radius': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Radius in meters'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
