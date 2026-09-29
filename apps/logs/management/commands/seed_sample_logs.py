import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.logs.department_config import get_department_config
from apps.logs.models import DailyLog

ACTIVITIES = {
    'HIM': [
        "Registered new and returning patients at the OPD records desk and created new folders.",
        "Retrieved and refiled patient folders for the consulting rooms throughout the morning.",
        "Assisted with ICD-10 coding of discharge summaries under the supervision of the coding officer.",
        "Traced and resolved misplaced folders by cross-checking the folder movement register.",
        "Compiled daily OPD attendance figures and entered them into the electronic health record system.",
        "Helped prepare NHIS claims batches and checked them for missing diagnosis codes.",
    ],
    'PA': [
        "Observed and assisted with history taking for patients presenting at the OPD.",
        "Performed supervised physical examinations and discussed findings with the physician assistant.",
        "Took vital signs and assisted with triage of patients in the emergency unit.",
        "Reviewed common presentations such as fever, cough and abdominal pain with the clinical team.",
        "Practised writing SOAP notes and discussed differential diagnoses with the supervisor.",
        "Assisted with dressing and minor procedures under supervision.",
    ],
}

CHALLENGES = [
    ("Long queues at peak hours slowed down service delivery.",
     "Worked with staff to prioritise urgent cases and organise patients by arrival time."),
    ("Some patient records were incomplete when retrieved.",
     "Cross-checked with the registers and asked staff to help complete the missing details."),
    ("Difficulty recalling unfamiliar terminology during the first weeks.",
     "Kept a personal glossary and asked the supervisor to explain terms after each shift."),
]


class Command(BaseCommand):
    help = "Create sample daily logs for a user's latest attachment (for testing reports)."

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--days', type=int, default=20)
        parser.add_argument('--clear', action='store_true', help='Delete previously created sample logs')

    def handle(self, *args, **opts):
        try:
            user = User.objects.get(username=opts['username'])
        except User.DoesNotExist:
            raise CommandError('No user with that username.')

        session = user.attachments.order_by('-start_date', '-id').first()
        if session is None:
            raise CommandError('That user has no attachment yet.')

        if opts['clear']:
            removed = 0
            for log in session.daily_logs.all():
                if '#SAMPLE' in (log.tags or []):
                    log.delete()
                    removed += 1
            self.stdout.write(f'Removed {removed} sample logs.')
            return

        dept = get_department_config(user.department)
        rng = random.Random(42)
        activities = ACTIVITIES.get(user.department)
        created = 0
        day = session.start_date

        while created < opts['days'] and day <= session.end_date:
            if day.weekday() < 5 and not session.daily_logs.filter(date=day).exists():
                week = (day - session.start_date).days // 7
                unit = dept['units'][week % len(dept['units'])]
                summary = rng.choice(activities) if activities else (
                    f"Worked in {unit}, assisting staff with routine tasks and observing procedures."
                )
                challenge, solution = ('', '')
                if created % 4 == 3:
                    challenge, solution = CHALLENGES[(created // 4) % len(CHALLENGES)]

                DailyLog.objects.create(
                    attachment=session,
                    date=day,
                    week_number=week + 1,
                    unit_sub_department=unit,
                    activity_summary=summary,
                    tags=rng.sample(dept['tags'], k=min(2, len(dept['tags']))) + ['#SAMPLE'],
                    numeric_tallies={t['key']: rng.randint(5, 60) for t in dept['tallies']},
                    department_details={d['key']: 'Sample entry (test data)' for d in dept['details']},
                    skills_acquired='Improved accuracy, speed and confidence with routine tasks.' if created % 3 == 0 else '',
                    challenges_faced=challenge,
                    solutions_applied=solution,
                )
                created += 1
            day += timedelta(days=1)

        self.stdout.write(self.style.SUCCESS(f'Created {created} sample logs.'))