from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Callable, Optional, Union

from django.contrib.auth.models import User
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django.utils.module_loading import import_string

from ..models import (
    Attendance,
    ClassConfirmationRequest,
    ClassSchedule,
    FacilitatorProfile,
    InternshipSupervision,
    Programme,
    SchedulingEvaluation,
    TeacherProfile,
    TimetableEntry,
)


ELIGIBLE = 'ELIGIBLE'
BLOCKED = 'BLOCKED'
UNVERIFIED = 'UNVERIFIED'

PASS = 'PASS'
BLOCKED_RULE = 'BLOCKED'
UNVERIFIED_RULE = 'UNVERIFIED'
INFO = 'INFO'

OPERATING_DAYS = frozenset({
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
})
OPENING_TIME = time(8, 0)
CLOSING_TIME = time(15, 30)


@dataclass(frozen=True)
class SchedulingVerification:
    attendance_verified: bool = False
    location_verified: bool = False
    availability_confirmed: bool = False
    teaching_eligibility_verified: bool = False
    explanation: str = ''


VerificationProvider = Callable[
    [TeacherProfile, date],
    SchedulingVerification,
]
Candidate = Union[FacilitatorProfile, TeacherProfile]
ClassProposal = Union[str, TimetableEntry]


def _rule(key, status, explanation):
    return {'key': key, 'status': status, 'explanation': explanation}


def _configured_verification_provider():
    provider = getattr(settings, 'SCHEDULING_VERIFICATION_PROVIDER', None)
    if provider is None:
        return None
    if isinstance(provider, str):
        provider = import_string(provider)
    if not callable(provider):
        raise ImproperlyConfigured(
            'SCHEDULING_VERIFICATION_PROVIDER must be a callable or dotted path.'
        )
    return provider


def _minutes(start_time, end_time):
    return int((
        datetime.combine(date.min, end_time)
        - datetime.combine(date.min, start_time)
    ).total_seconds() // 60)


def _profile_for(candidate):
    if isinstance(candidate, FacilitatorProfile):
        return candidate.teacher, candidate
    if isinstance(candidate, TeacherProfile):
        try:
            return candidate, candidate.facilitator_profile
        except FacilitatorProfile.DoesNotExist:
            return candidate, None
    raise TypeError('candidate must be a TeacherProfile or FacilitatorProfile')


def _evaluate_candidate(
    *,
    proposed_class,
    scheduled_date,
    start_time,
    end_time,
    programme,
    candidate,
    evaluated_by=None,
    verification_provider=None,
    exclude_confirmation_request_id=None,
):
    if not isinstance(scheduled_date, date) or isinstance(scheduled_date, datetime):
        raise TypeError('scheduled_date must be a date')
    if not isinstance(start_time, time) or not isinstance(end_time, time):
        raise TypeError('start_time and end_time must be times')
    if not isinstance(programme, Programme):
        raise TypeError('programme must be a Programme')

    teacher, facilitator = _profile_for(candidate)
    timetable_entry = proposed_class if isinstance(proposed_class, TimetableEntry) else None
    subject = (
        proposed_class.subject
        if timetable_entry
        else str(proposed_class).strip()
    )
    if not subject:
        raise ValueError('proposed_class must identify a class or subject')
    if len(subject) > 200:
        raise ValueError('proposed_class must be 200 characters or fewer')

    checks = []
    blocked = False
    uncertain = False

    def add_check(key, status, explanation):
        nonlocal blocked, uncertain
        checks.append(_rule(key, status, explanation))
        blocked = blocked or status == BLOCKED_RULE
        uncertain = uncertain or status == UNVERIFIED_RULE

    if teacher.user.is_active and not teacher.user.is_staff:
        add_check('account_active', PASS, 'The account is active and is not a staff-admin account.')
    else:
        add_check('account_active', BLOCKED_RULE, 'The account is inactive or is a staff-admin account.')

    if teacher.is_approved:
        add_check('facilitator_approved', PASS, 'The Facilitator profile is approved.')
    else:
        add_check('facilitator_approved', BLOCKED_RULE, 'The Facilitator profile has not been approved.')

    if facilitator is None:
        add_check(
            'facilitator_profile',
            BLOCKED_RULE,
            'No Permanent or Internship Facilitator profile exists for this account.',
        )
    elif not facilitator.is_active:
        if (
            facilitator.employment_type == 'INTERNSHIP'
            and facilitator.internship_end_date
            and facilitator.internship_end_date < scheduled_date
        ):
            explanation = (
                'The internship expires before the proposed class date '
                f'({facilitator.internship_end_date}).'
            )
        else:
            explanation = 'The Facilitator profile is inactive.'
        add_check('facilitator_profile', BLOCKED_RULE, explanation)
    elif facilitator.employment_type == 'PERMANENT':
        add_check('facilitator_profile', PASS, 'The active profile is a Permanent Facilitator.')
    elif facilitator.employment_type == 'INTERNSHIP':
        valid_dates = (
            facilitator.internship_start_date is not None
            and facilitator.internship_end_date is not None
            and facilitator.internship_start_date <= scheduled_date
            and facilitator.internship_end_date >= scheduled_date
        )
        if valid_dates:
            add_check('facilitator_profile', PASS, 'The active internship covers the proposed class date.')
        else:
            add_check(
                'facilitator_profile',
                BLOCKED_RULE,
                'The internship has missing dates or is not active for the proposed class date.',
            )
    else:
        add_check(
            'facilitator_profile',
            BLOCKED_RULE,
            'The profile employment type is not a supported Facilitator type.',
        )

    if programme.is_active and facilitator and facilitator.programme_id == programme.pk:
        add_check('programme', PASS, 'The Facilitator is assigned to the requested active programme.')
    elif not programme.is_active:
        add_check('programme', BLOCKED_RULE, 'The requested programme is inactive.')
    else:
        add_check(
            'programme',
            BLOCKED_RULE,
            'The Facilitator profile is not assigned to the requested programme.',
        )

    if facilitator and facilitator.employment_type == 'INTERNSHIP':
        history = InternshipSupervision.objects.filter(
            internship=facilitator,
            is_active=True,
            start_date__lte=scheduled_date,
        ).select_related('supervisor__teacher__user', 'supervisor__programme')
        history = [
            assignment for assignment in history
            if assignment.end_date is None or assignment.end_date >= scheduled_date
        ]
        supervisor = facilitator.supervisor
        valid_history = (
            len(history) == 1
            and supervisor is not None
            and history[0].supervisor_id == supervisor.pk
        )
        valid_supervisor = bool(
            valid_history
            and supervisor.employment_type == 'PERMANENT'
            and supervisor.is_active
            and supervisor.programme_id == programme.pk
            and supervisor.teacher.is_approved
            and supervisor.teacher.user.is_active
            and not supervisor.teacher.user.is_staff
        )
        if valid_supervisor:
            add_check(
                'internship_supervision',
                PASS,
                f'The internship has one active, approved same-programme Permanent supervisor: {supervisor.full_name}.',
            )
        else:
            add_check(
                'internship_supervision',
                BLOCKED_RULE,
                'The internship supervisor relationship is missing, mismatched, inactive, unapproved, or outside the programme.',
            )
    else:
        add_check(
            'internship_supervision',
            INFO,
            'Internship supervision is not required for this candidate.',
        )

    verification = (
        verification_provider(teacher, scheduled_date)
        if verification_provider
        else SchedulingVerification()
    )
    if not isinstance(verification, SchedulingVerification):
        raise TypeError('verification_provider must return SchedulingVerification')

    if verification.teaching_eligibility_verified:
        add_check(
            'teaching_eligibility',
            PASS,
            verification.explanation or 'A trusted source verified teaching eligibility for this class.',
        )
    else:
        add_check(
            'teaching_eligibility',
            UNVERIFIED_RULE,
            'The project has no authoritative subject-to-qualification eligibility mapping; no qualification has been inferred.',
        )

    day_name = scheduled_date.strftime('%A').lower()
    if day_name in OPERATING_DAYS:
        add_check('operating_day', PASS, f'{day_name.title()} is an approved operating day.')
    else:
        add_check('operating_day', BLOCKED_RULE, f'{day_name.title()} is outside Monday–Saturday operating days.')

    if start_time < end_time and start_time >= OPENING_TIME and end_time <= CLOSING_TIME:
        add_check('operating_hours', PASS, 'The proposed class is within 08:00–15:30.')
    else:
        add_check('operating_hours', BLOCKED_RULE, 'The proposed class must fall wholly within 08:00–15:30 and have positive duration.')

    recurring_conflicts = []
    if facilitator:
        entries = TimetableEntry.objects.filter(
            teacher=teacher,
            day=day_name,
            is_break=False,
        )
        if timetable_entry:
            entries = entries.exclude(pk=timetable_entry.pk)
        for entry in entries:
            if start_time < entry.end_time and entry.start_time < end_time:
                recurring_conflicts.append(
                    f'{entry.subject} ({entry.start_time:%H:%M}-{entry.end_time:%H:%M})'
                )
    if recurring_conflicts:
        add_check(
            'recurring_timetable',
            BLOCKED_RULE,
            'Overlaps recurring timetable entries: ' + ', '.join(recurring_conflicts) + '.',
        )
    else:
        add_check('recurring_timetable', PASS, 'No overlapping recurring timetable entry was found.')

    saved_schedules = ClassSchedule.objects.filter(
        teacher=teacher,
        scheduled_date=scheduled_date,
    ).select_related('timetable_entry')
    same_date_conflicts = [
        f'{schedule.timetable_entry.subject} '
        f'({schedule.timetable_entry.start_time:%H:%M}-{schedule.timetable_entry.end_time:%H:%M})'
        for schedule in saved_schedules
        if (
            start_time < schedule.timetable_entry.end_time
            and schedule.timetable_entry.start_time < end_time
        )
    ]
    if same_date_conflicts:
        add_check(
            'same_date_schedule',
            BLOCKED_RULE,
            'Overlaps already-scheduled classes: ' + ', '.join(same_date_conflicts) + '.',
        )
    else:
        add_check('same_date_schedule', PASS, 'No overlapping class is already scheduled for this date.')

    pending_confirmation = ClassConfirmationRequest.objects.filter(
        timetable_entry_id=timetable_entry.pk if timetable_entry else None,
        scheduled_date=scheduled_date,
        status='PENDING',
    ) if timetable_entry else ClassConfirmationRequest.objects.none()
    if exclude_confirmation_request_id:
        pending_confirmation = pending_confirmation.exclude(
            pk=exclude_confirmation_request_id,
        )
    existing_request = pending_confirmation.first()
    if existing_request:
        add_check(
            'pending_confirmation',
            BLOCKED_RULE,
            f'A confirmation request for this class and date is already pending '
            f'with {existing_request.candidate.full_name}.',
        )
    else:
        add_check(
            'pending_confirmation',
            PASS,
            'No other confirmation request is pending for this class and date.',
        )

    week_start = scheduled_date - timedelta(days=scheduled_date.weekday())
    week_end = week_start + timedelta(days=6)
    weekly_schedules = ClassSchedule.objects.filter(
        teacher=teacher,
        scheduled_date__gte=week_start,
        scheduled_date__lte=week_end,
    ).select_related('timetable_entry')
    weekly_minutes = sum(
        _minutes(
            schedule.timetable_entry.start_time,
            schedule.timetable_entry.end_time,
        )
        for schedule in weekly_schedules
    )
    recurring_entries = list(TimetableEntry.objects.filter(
        teacher=teacher,
        is_break=False,
    ))
    recurring_minutes = sum(
        _minutes(entry.start_time, entry.end_time)
        for entry in recurring_entries
    )
    proposed_minutes = max(_minutes(start_time, end_time), 0)
    additional_schedule_minutes = sum(
        _minutes(
            schedule.timetable_entry.start_time,
            schedule.timetable_entry.end_time,
        )
        for schedule in weekly_schedules
        if not (
            schedule.timetable_entry.teacher_id == teacher.pk
            and schedule.timetable_entry.day
            == schedule.scheduled_date.strftime('%A').lower()
        )
    )
    proposal_already_in_recurring_timetable = bool(
        timetable_entry
        and timetable_entry.teacher_id == teacher.pk
        and timetable_entry.day == day_name
        and timetable_entry.start_time == start_time
        and timetable_entry.end_time == end_time
        and not timetable_entry.is_break
    )
    projected_minutes = (
        recurring_minutes
        + additional_schedule_minutes
        + (
            0
            if proposal_already_in_recurring_timetable
            else proposed_minutes
        )
    )
    checks.append(_rule(
        'weekly_workload',
        INFO,
        f'Existing saved class assignments total {weekly_minutes} minutes this week; '
        f'recurring timetable entries total {recurring_minutes} minutes per week; '
        f'additional saved assignments not represented in the recurring timetable total '
        f'{additional_schedule_minutes} minutes. The proposal projects '
        f'{projected_minutes} teaching minutes for the week. '
        'No authorised maximum workload is configured, so workload is measured but not used as a block.',
    ))

    attendance = Attendance.objects.filter(
        teacher=teacher,
        date=scheduled_date,
    ).first()
    if attendance and attendance.school_status == 'absent':
        add_check(
            'attendance',
            BLOCKED_RULE,
            'An explicit absence record exists for the proposed class date.',
        )
        attendance_status = 'ABSENT'
    else:
        attendance_status = (
            attendance.school_status.upper()
            if attendance
            else 'UNVERIFIED'
        )
        if verification.attendance_verified:
            add_check(
                'attendance',
                PASS,
                verification.explanation or 'A trusted server-side source verified attendance.',
            )
            attendance_status = 'VERIFIED'
        else:
            explanation = verification.explanation or (
                'No independently verified attendance evidence is available.'
            )
            add_check('attendance', UNVERIFIED_RULE, explanation)
            attendance_status = 'UNVERIFIED'

    if verification_provider and verification.location_verified:
        add_check(
            'location',
            PASS,
            verification.explanation or 'A trusted server-side source verified location.',
        )
        location_status = 'VERIFIED'
    else:
        add_check(
            'location',
            UNVERIFIED_RULE,
            'Browser GPS is the only configured location signal and cannot independently prove physical presence.',
        )
        location_status = 'UNVERIFIED'

    if verification_provider and verification.availability_confirmed:
        add_check(
            'availability',
            PASS,
            verification.explanation or 'A trusted source confirmed the Facilitator is available.',
        )
        availability_status = 'CONFIRMED'
    else:
        add_check(
            'availability',
            UNVERIFIED_RULE,
            'The project has no Facilitator availability-response record for this proposed class.',
        )
        availability_status = 'UNVERIFIED'

    status = BLOCKED if blocked else UNVERIFIED if uncertain else ELIGIBLE
    return SchedulingEvaluation.objects.create(
        programme=programme,
        candidate=teacher,
        proposed_timetable_entry=timetable_entry,
        proposed_class=subject,
        scheduled_date=scheduled_date,
        start_time=start_time,
        end_time=end_time,
        status=status,
        rule_results=checks,
        weekly_scheduled_minutes=weekly_minutes,
        recurring_timetable_minutes=recurring_minutes,
        projected_weekly_minutes=projected_minutes,
        attendance_status=attendance_status,
        location_status=location_status,
        availability_status=availability_status,
        evaluated_by=evaluated_by,
    )


def evaluate_class_assignment(
    *,
    proposed_class: ClassProposal,
    scheduled_date: date,
    start_time: time,
    end_time: time,
    programme: Programme,
    candidate: Candidate,
    evaluated_by: Optional[User] = None,
    verification_provider: Optional[VerificationProvider] = None,
    exclude_confirmation_request_id: Optional[int] = None,
) -> SchedulingEvaluation:
    """Evaluate one candidate; only trusted server-side evidence may verify attendance, location, availability, or teaching eligibility."""
    if verification_provider is None:
        verification_provider = _configured_verification_provider()
    return _evaluate_candidate(
        proposed_class=proposed_class,
        scheduled_date=scheduled_date,
        start_time=start_time,
        end_time=end_time,
        programme=programme,
        candidate=candidate,
        evaluated_by=evaluated_by,
        verification_provider=verification_provider,
        exclude_confirmation_request_id=exclude_confirmation_request_id,
    )


def rank_class_candidates(
    *,
    proposed_class: ClassProposal,
    scheduled_date: date,
    start_time: time,
    end_time: time,
    programme: Programme,
    candidates: QuerySet,
    evaluated_by: Optional[User] = None,
    verification_provider: Optional[VerificationProvider] = None,
):
    """Evaluate and rank only ELIGIBLE candidates by their projected weekly minutes."""
    if verification_provider is None:
        verification_provider = _configured_verification_provider()
    evaluations = [
        evaluate_class_assignment(
            proposed_class=proposed_class,
            scheduled_date=scheduled_date,
            start_time=start_time,
            end_time=end_time,
            programme=programme,
            candidate=candidate,
            evaluated_by=evaluated_by,
            verification_provider=verification_provider,
        )
        for candidate in candidates
    ]
    eligible = sorted(
        (evaluation for evaluation in evaluations if evaluation.status == ELIGIBLE),
        key=lambda evaluation: (
            evaluation.projected_weekly_minutes,
            evaluation.candidate.full_name.casefold(),
            evaluation.pk,
        ),
    )
    if not eligible:
        return {'evaluations': evaluations, 'ranked': [], 'explanation': 'No candidate passed all mandatory and verification rules.'}

    minimum = eligible[0].projected_weekly_minutes
    maximum = eligible[-1].projected_weekly_minutes
    for rank, evaluation in enumerate(eligible, start=1):
        score = (
            Decimal('100.00')
            if minimum == maximum
            else (
                Decimal(maximum - evaluation.projected_weekly_minutes)
                * Decimal('100')
                / Decimal(maximum - minimum)
            ).quantize(Decimal('0.01'))
        )
        evaluation.recommendation_score = score
        evaluation.recommendation_rank = rank
        evaluation.save(update_fields=['recommendation_score', 'recommendation_rank'])
    return {
        'evaluations': evaluations,
        'ranked': eligible,
        'explanation': (
            'Eligible candidates are ranked by lower projected weekly teaching minutes, '
            'combining recurring timetable work with additional saved assignments without '
            "counting a saved occurrence of the candidate's own recurring entry twice. "
            'Scores are relative to this eligible candidate pool; '
            'they are not workload limits or assignment approvals.'
        ),
    }
