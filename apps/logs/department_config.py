"""Per-department log fields. Edit this file to change what each department records."""

COMMON_TEXT_FIELDS = [
    {'name': 'activity_summary', 'label': 'What did you do today?', 'required': True, 'rows': 5},
    {'name': 'skills_acquired', 'label': 'Skills you picked up', 'required': False, 'rows': 2},
    {'name': 'challenges_faced', 'label': 'Challenges faced', 'required': False, 'rows': 2},
    {'name': 'solutions_applied', 'label': 'How you handled them', 'required': False, 'rows': 2},
]

DEPARTMENT_CONFIG = {
    'HIM': {
        'units': ['Records Unit', 'OPD Registration', 'Medical Records Library', 'Coding Unit',
                  'NHIS Claims Unit', 'Statistics Unit'],
        'tallies': [
            {'key': 'folders_registered', 'label': 'Folders registered'},
            {'key': 'folders_retrieved', 'label': 'Folders retrieved'},
            {'key': 'icd10_codes_applied', 'label': 'ICD-10 codes applied'},
            {'key': 'missing_files_resolved', 'label': 'Missing files resolved'},
        ],
        'details': [
            {'key': 'ehr_system', 'label': 'Electronic Health Record system used', 'type': 'text'},
        ],
        'tags': ['#ICD10', '#Registration', '#Filing', '#Retrieval', '#NHIS', '#DataQuality'],
        'note': '',
    },
    'PA': {
        'units': ['OPD', 'Emergency', 'Maternity / ANC', 'Paediatrics', 'Male Ward', 'Female Ward',
                  'Surgery / Theatre'],
        'tallies': [
            {'key': 'patients_seen', 'label': 'Patients seen'},
            {'key': 'male_patients', 'label': 'Male patients'},
            {'key': 'female_patients', 'label': 'Female patients'},
            {'key': 'children_under_5', 'label': 'Children under 5'},
            {'key': 'elderly_60_plus', 'label': 'Patients 60+'},
            {'key': 'physical_exams_performed', 'label': 'Physical exams performed'},
        ],
        'details': [
            {'key': 'chief_complaints', 'label': 'Chief complaints observed', 'type': 'textarea'},
            {'key': 'differential_diagnoses', 'label': 'Differential diagnoses discussed', 'type': 'textarea'},
        ],
        'tags': ['#Triage', '#History', '#PhysicalExam', '#Diagnosis', '#Prescribing', '#Referral'],
        'note': 'Use anonymised case descriptions only (e.g. "45-year-old male").',
    },
    'MED_LAB': {
        'units': ['Hematology', 'Clinical Chemistry', 'Microbiology', 'Parasitology', 'Blood Bank',
                  'Histopathology', 'Sample Reception'],
        'tallies': [
            {'key': 'blood_samples', 'label': 'Blood samples'},
            {'key': 'urine_samples', 'label': 'Urine samples'},
            {'key': 'stool_samples', 'label': 'Stool samples'},
            {'key': 'other_specimens', 'label': 'Other specimens'},
            {'key': 'tests_run', 'label': 'Tests run'},
        ],
        'details': [
            {'key': 'tests_performed', 'label': 'Diagnostic tests performed', 'type': 'textarea'},
            {'key': 'equipment_operated', 'label': 'Lab equipment operated', 'type': 'textarea'},
            {'key': 'qc_activities', 'label': 'Quality control activities', 'type': 'textarea'},
        ],
        'tags': ['#FBC', '#Malaria', '#Urinalysis', '#QualityControl', '#Biosafety', '#Microscopy'],
        'note': '',
    },
    'DISEASE_CTRL': {
        'units': ['Surveillance Unit', 'Immunization Unit', 'Outreach / Community', 'Disease Control Office',
                  'Laboratory Liaison'],
        'tallies': [
            {'key': 'surveillance_cases_recorded', 'label': 'Surveillance cases recorded'},
            {'key': 'idsr_forms_entered', 'label': 'IDSR form entries'},
            {'key': 'immunizations_given', 'label': 'Immunizations given'},
            {'key': 'contact_tracing_runs', 'label': 'Contact tracing runs'},
        ],
        'details': [
            {'key': 'diseases_monitored', 'label': 'Diseases monitored / reported', 'type': 'textarea'},
        ],
        'tags': ['#IDSR', '#Surveillance', '#Immunization', '#ContactTracing', '#Outbreak', '#DHIMS2'],
        'note': '',
    },
    'MENTAL_HEALTH': {
        'units': ['Psychiatric OPD', 'Community Outreach', 'Counselling Unit', 'Ward', 'Support Group'],
        'tallies': [
            {'key': 'clients_seen', 'label': 'Clients seen'},
            {'key': 'counselling_sessions', 'label': 'Counselling sessions'},
            {'key': 'screenings_done', 'label': 'Screenings done'},
            {'key': 'community_talks', 'label': 'Community talks'},
        ],
        'details': [
            {'key': 'conditions_observed', 'label': 'Conditions observed (anonymised)', 'type': 'textarea'},
        ],
        'tags': ['#Counselling', '#Screening', '#Outreach', '#Psychoeducation', '#Referral'],
        'note': 'Never record client names or identifying details.',
    },
    'NUTRITION': {
        'units': ['Nutrition Clinic', 'Child Welfare Clinic', 'ANC', 'Community Outreach', 'Feeding Programme'],
        'tallies': [
            {'key': 'children_weighed', 'label': 'Children weighed'},
            {'key': 'mothers_counselled', 'label': 'Mothers counselled'},
            {'key': 'malnutrition_cases_identified', 'label': 'Malnutrition cases identified'},
            {'key': 'supplements_distributed', 'label': 'Supplements distributed'},
            {'key': 'demonstrations_held', 'label': 'Cooking demonstrations held'},
        ],
        'details': [
            {'key': 'nutrition_topics', 'label': 'Nutrition topics covered', 'type': 'textarea'},
        ],
        'tags': ['#GrowthMonitoring', '#Counselling', '#Supplementation', '#Anthropometry', '#Demonstration'],
        'note': '',
    },
    'HEALTH_PROM': {
        'units': ['Health Education Unit', 'Community Outreach', 'OPD Talks', 'Schools Programme',
                  'Community Durbar'],
        'tallies': [
            {'key': 'education_sessions', 'label': 'Education sessions held'},
            {'key': 'people_reached', 'label': 'People reached'},
            {'key': 'materials_distributed', 'label': 'IEC materials distributed'},
            {'key': 'outreach_visits', 'label': 'Outreach visits'},
        ],
        'details': [
            {'key': 'topics_covered', 'label': 'Topics covered', 'type': 'textarea'},
        ],
        'tags': ['#HealthEducation', '#Outreach', '#Campaign', '#IEC', '#Community'],
        'note': '',
    },
}

DEFAULT_CONFIG = {
    'units': ['OPD', 'Ward', 'Laboratory', 'Records Unit', 'Community Outreach'],
    'tallies': [{'key': 'clients_served', 'label': 'People served'}],
    'details': [],
    'tags': [],
    'note': '',
}


def get_department_config(code):
    return DEPARTMENT_CONFIG.get(code, DEFAULT_CONFIG)