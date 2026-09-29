from collections import Counter, OrderedDict, defaultdict

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify

from apps.logs.department_config import get_department_config

from . import report_text as T
from .charts import bar_chart, line_chart, qr_data_uri
from .models import GeneratedReport


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else round(number, 1)


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


class PracticumReportBuilder:
    def __init__(self, attachment):
        self.attachment = attachment
        self.user = attachment.user
        self.facility = attachment.facility
        self.dept = get_department_config(self.user.department)
        self.logs = list(attachment.daily_logs.order_by('date'))
        days = (attachment.end_date - attachment.start_date).days
        self.total_weeks = max(1, min(days // 7 + 1, 16))
        self.weeks_range = list(range(1, self.total_weeks + 1))
        self._figure_no = 0

    # ---------- helpers ----------

    def _figure(self, title, intro, uri):
        self._figure_no += 1
        return {'number': self._figure_no, 'title': title, 'intro': intro, 'uri': uri}

    def _weeks(self):
        detail_labels = {d['key']: d['label'] for d in self.dept['details']}
        tally_labels = {t['key']: t['label'] for t in self.dept['tallies']}
        grouped = OrderedDict()
        for log in self.logs:
            grouped.setdefault(log.week_number, []).append(log)

        weeks = []
        for number, items in grouped.items():
            units = sorted({log.unit_sub_department for log in items})
            weeks.append({
                'number': number,
                'start': items[0].date,
                'end': items[-1].date,
                'days': len(items),
                'units': units,
                'logs': [{
                    'date': log.date,
                    'unit': log.unit_sub_department,
                    'summary': log.activity_summary,
                    'details': [
                        (detail_labels.get(k, k.replace('_', ' ').capitalize()), v)
                        for k, v in (log.department_details or {}).items()
                    ],
                } for log in items],
            })
        self._tally_labels = tally_labels
        return weeks

    def _tally_stats(self):
        labels = {t['key']: t['label'] for t in self.dept['tallies']}
        totals = Counter()
        by_week = defaultdict(Counter)
        for log in self.logs:
            for key, value in (log.numeric_tallies or {}).items():
                try:
                    n = float(value)
                except (TypeError, ValueError):
                    continue
                totals[key] += n
                by_week[key][log.week_number] += n
                labels.setdefault(key, key.replace('_', ' ').capitalize())
        keys = [t['key'] for t in self.dept['tallies'] if totals.get(t['key'])]
        keys += [k for k in totals if k not in keys and totals[k]]
        return labels, totals, by_week, keys

    def _charts(self, unit_counter, tag_counter, labels, totals, by_week, keys):
        charts = []
        if unit_counter:
            items = unit_counter.most_common(10)
            charts.append(self._figure(
                'Number of days logged per unit',
                'The distribution of logged days across units is shown in Figure {n}.',
                bar_chart([u for u, _ in items], [c for _, c in items], 'Days logged'),
            ))
        if keys:
            charts.append(self._figure(
                'Total recorded activity measures',
                'Figure {n} summarises the totals recorded for each measure over the attachment.',
                bar_chart([labels[k] for k in keys], [_num(totals[k]) for k in keys], 'Total', horizontal=True),
            ))
            for key in sorted(keys, key=lambda k: totals[k], reverse=True)[:2]:
                weekly = [_num(by_week[key].get(w, 0)) for w in self.weeks_range]
                charts.append(self._figure(
                    f'Weekly trend: {labels[key]}',
                    f'The weekly pattern for "{labels[key]}" is presented in Figure {{n}}.',
                    line_chart([f'Wk {w}' for w in self.weeks_range], weekly, labels[key]),
                ))
        if tag_counter:
            items = tag_counter.most_common(10)
            charts.append(self._figure(
                'Most frequent activity tags',
                'Figure {n} shows the tags used most often when describing daily activities.',
                bar_chart([t for t, _ in items], [c for _, c in items], 'Times used', horizontal=True),
            ))
        for chart in charts:
            chart['intro'] = chart['intro'].format(n=chart['number'])
        return charts

    # ---------- context ----------

    def build_context(self):
        s = self.attachment
        user = self.user
        dept_name = user.get_department_display()
        facility_name = self.facility.name if self.facility else 'the attachment facility'
        start = s.start_date.strftime('%d %B %Y').lstrip('0')
        end = s.end_date.strftime('%d %B %Y').lstrip('0')

        weeks = self._weeks()
        labels, totals, by_week, keys = self._tally_stats()
        unit_counter = Counter(log.unit_sub_department for log in self.logs)
        tag_counter = Counter(t for log in self.logs for t in (log.tags or []))
        charts = self._charts(unit_counter, tag_counter, labels, totals, by_week, keys)

        total_days = len(self.logs)
        unit_rows = [
            {'unit': unit, 'days': days, 'pct': round(100 * days / total_days, 1)}
            for unit, days in unit_counter.most_common()
        ] if total_days else []

        tally_table = {
            'weeks': self.weeks_range,
            'rows': [{
                'label': labels[k],
                'values': [_num(by_week[k].get(w, 0)) for w in self.weeks_range],
                'total': _num(totals[k]),
            } for k in keys],
        }

        results = []
        results.append(
            f"The student recorded {_plural(total_days, 'daily log')} across "
            f"{len(weeks)} of the {self.total_weeks} attachment weeks, working in "
            f"{_plural(len(unit_counter), 'unit')}."
        )
        if unit_counter:
            top_unit, top_days = unit_counter.most_common(1)[0]
            results.append(f"Most logged days were spent in {top_unit} ({_plural(top_days, 'day')}).")
        if keys:
            best = max(keys, key=lambda k: totals[k])
            results.append(
                f"Among the numerical measures recorded, {labels[best]} was highest, "
                f"with a total of {_num(totals[best])}."
            )

        skills = [(log.date, log.skills_acquired) for log in self.logs if log.skills_acquired.strip()]
        challenges = [
            (log.date, log.challenges_faced, log.solutions_applied)
            for log in self.logs if log.challenges_faced.strip()
        ]

        recs = T.RECOMMENDATIONS.get(user.department, T.DEFAULT_RECS)
        conclusion = [
            f"The practicum attachment at {facility_name} ran from {start} to {end} and gave the student "
            f"practical exposure to the work of {dept_name}. Over {_plural(total_days, 'logged day')}, the "
            f"student took part in routine activities across {_plural(len(unit_counter), 'unit')}.",
            f"Skills acquired were documented on {_plural(len(skills), 'day')}, and challenges were "
            f"recorded on {_plural(len(challenges), 'day')}. Each challenge, together with the action "
            f"taken, is presented in Chapter 4.",
            "Overall, the attachment strengthened the link between classroom learning and professional "
            "practice, and prepared the student for future work in the health sector.",
        ]

        supervisor_line = (
            f" I especially thank my supervisor, {s.assigned_supervisor_name}, for patient guidance and feedback."
            if s.assigned_supervisor_name else ''
        )

        verify_url = f"{settings.SITE_URL.rstrip('/')}/verify/sign-off/{s.verification_token}/"
        report = GeneratedReport.objects.filter(attachment=s).first()
        signed_off = bool(report and report.is_signed_off)

        fmt = dict(
            name=user.get_full_name() or user.username, index=user.index_number, level=user.level,
            dept=dept_name, facility=facility_name, start=start, end=end,
        )

        return {
            's': s, 'student': user, 'facility': self.facility, 'facility_name': facility_name,
            'student_name': fmt['name'], 'dept_name': dept_name,
            'generated_on': timezone.localdate(),
            'declaration': T.DECLARATION.format(**fmt),
            'acknowledgments': [t.format(**fmt, supervisor=supervisor_line) for t in T.ACKNOWLEDGMENTS],
            'objectives': [t.format(**fmt) for t in T.OBJECTIVES],
            'methodology': [t.format(**fmt) for t in T.METHODOLOGY],
            'weeks': weeks, 'table_base': len(weeks),
            'unit_rows': unit_rows, 'tally_table': tally_table,
            'charts': charts, 'results': results,
            'skills': skills, 'challenges': challenges,
            'conclusion': conclusion,
            'facility_recs': recs['facility'], 'academic_recs': recs['academic'],
            'all_logs': self.logs, 'total_days': total_days,
            'qr_uri': qr_data_uri(verify_url), 'verify_url': verify_url,
            'signed_off': signed_off,
            'signed_date': report.supervisor_signature_date if report else None,
        }

    # ---------- output ----------

    def render_html(self):
        return render_to_string('reports/report.html', self.build_context())

    def build_pdf(self):
        from weasyprint import HTML

        return HTML(string=self.render_html(), base_url=str(settings.BASE_DIR)).write_pdf()

    def save(self):
        pdf_bytes = self.build_pdf()
        report, _ = GeneratedReport.objects.get_or_create(attachment=self.attachment)
        if report.pdf_file:
            report.pdf_file.delete(save=False)
        filename = f"practicum-report-{slugify(self.user.index_number)}.pdf"
        report.pdf_file.save(filename, ContentFile(pdf_bytes), save=True)
        return report