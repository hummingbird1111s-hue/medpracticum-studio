"""Standard wording used by the report. Edit freely."""

DECLARATION = (
    "I, {name} (Index Number: {index}), a Level {level} student of the Department of {dept} at "
    "Yamfo College of Health, declare that this report is the product of my own work, based on my "
    "practicum attachment at {facility} from {start} to {end}. Sources of information have been "
    "acknowledged, and no patient-identifiable information has been included. This report has not "
    "been submitted, in whole or in part, for any other award."
)

ACKNOWLEDGMENTS = [
    "I am thankful for the health, strength and opportunity to complete this practicum attachment.",
    "I am grateful to the management and staff of {facility} for welcoming me and allowing me to "
    "learn within the facility.{supervisor}",
    "My sincere appreciation goes to the lecturers and practicum coordinators of the Department of "
    "{dept}, Yamfo College of Health, for their guidance and preparation. I also thank my family and "
    "colleagues for their support.",
]

OBJECTIVES = [
    "apply classroom knowledge in a real working environment;",
    "develop practical skills relevant to {dept};",
    "understand the organisation, structure and daily operations of the facility;",
    "practise professional conduct, ethics, confidentiality and teamwork.",
]

METHODOLOGY = [
    "Information for this report was gathered through participant observation, supervised hands-on "
    "tasks, and informal discussions with staff of {facility}. Each working day, the student recorded "
    "a daily log using the MedPracticum Studio application, capturing the unit worked in, a summary of "
    "activities, numerical tallies, skills acquired, challenges faced and solutions applied.",
    "The daily logs were grouped by attachment week and analysed using simple descriptive statistics "
    "(totals, distributions and weekly trends). Confidentiality was maintained throughout: no patient "
    "names or identifying details were recorded.",
]

DEFAULT_RECS = {
    'facility': [
        "Provide a structured orientation programme for incoming students at the start of each attachment.",
        "Assign each student a named supervisor for every unit rotation to strengthen feedback.",
    ],
    'academic': [
        "Increase practical simulation sessions before students begin their attachment.",
        "Share the facility's common workflows with students during coursework.",
    ],
}

RECOMMENDATIONS = {
    'HIM': {
        'facility': [
            "Strengthen the folder tracking system (for example, a retrieval register or barcode tags) to reduce misplaced files.",
            "Provide regular refresher training on ICD-10 coding for records staff and students.",
        ],
        'academic': [
            "Give students more supervised practice in ICD-10 coding and health data flow mapping before attachment.",
            "Introduce hands-on electronic health record training within the curriculum.",
        ],
    },
    'PA': {
        'facility': [
            "Create more opportunities for students to take supervised histories and perform physical examinations.",
            "Hold short case-review sessions so students can discuss differential diagnoses with clinicians.",
        ],
        'academic': [
            "Expand clinical skills laboratory sessions on history taking and physical examination.",
            "Provide case-based learning that mirrors the conditions commonly seen at district facilities.",
        ],
    },
    'MED_LAB': {
        'facility': [
            "Set out a clear schedule for rotating students through all laboratory sections.",
            "Maintain visible standard operating procedures and quality control records at each bench.",
        ],
        'academic': [
            "Increase practical time on routine haematology, chemistry and microbiology equipment.",
            "Emphasise quality control and biosafety practice in laboratory courses.",
        ],
    },
    'DISEASE_CTRL': {
        'facility': [
            "Involve students in weekly surveillance data review meetings.",
            "Digitise IDSR reporting where possible to reduce delays and transcription errors.",
        ],
        'academic': [
            "Provide practice with IDSR forms, DHIMS2 and outbreak investigation exercises.",
            "Arrange field visits to surveillance and immunisation units during coursework.",
        ],
    },
    'MENTAL_HEALTH': {
        'facility': [
            "Provide a private, supervised space where students can observe counselling and psychoeducation.",
            "Include students in community mental health outreach planning.",
        ],
        'academic': [
            "Add role-play and supervised counselling practice before attachment.",
            "Train students on screening tools and referral pathways.",
        ],
    },
    'NUTRITION': {
        'facility': [
            "Give students more opportunities to run growth monitoring and cooking demonstration sessions.",
            "Keep anthropometric equipment calibrated and available for supervised student use.",
        ],
        'academic': [
            "Provide more hands-on anthropometry and nutrition counselling practice in class.",
            "Include community nutrition programme case studies in coursework.",
        ],
    },
    'HEALTH_PROM': {
        'facility': [
            "Involve students in planning as well as delivering health education sessions.",
            "Supply more IEC materials in local languages for community outreach.",
        ],
        'academic': [
            "Strengthen training in communication, community entry and programme evaluation.",
            "Give students practice designing health education materials before attachment.",
        ],
    },
}

# ============================================================
# HIM-format sections
# These are used by the linear (non-thesis) report layout that
# matches the COHY log book guidelines for Vacation Practicum
# reports. Students can edit every one of them via the
# "Write report text" page. Leave a list empty if the section
# should not appear by default.
# ============================================================

INTRODUCTION_EXTRA = [
    "The attachment was carried out in fulfilment of the practicum requirement for the award "
    "of the Higher National Diploma. It provided an opportunity to apply classroom knowledge "
    "in a real health facility, and to develop professional competence under the supervision "
    "of experienced staff.",
    "This report describes the activities I performed, the skills I acquired, the challenges "
    "I faced and the lessons I learnt during the period of attachment. It also offers "
    "recommendations to the facility and to the College based on my observations.",
]

LESSONS_LEARNT = [
    "The practicum taught me the importance of accuracy and confidentiality when handling "
    "patient records. Even small errors in filing or coding can have downstream effects on "
    "patient care, insurance claims and facility reporting.",
    "I learnt to work as part of a team and to communicate clearly with colleagues across "
    "different units. Supervisors and staff were patient and willing to explain processes.",
    "Time management and attention to detail are essential. Recording daily activities and "
    "reflecting on them helped me see my own progress over the attachment period.",
]

OBSERVATION = [
    "During the attachment I observed that the facility relies heavily on manual registers "
    "in some units while using digital systems in others. Staff were generally cooperative "
    "and willing to teach, though workloads often limited the time available for one-on-one "
    "instruction.",
    "I also observed the challenge of maintaining complete and accurate records when patient "
    "volumes are high. This highlighted why a well-organised health information system is "
    "central to quality care, and reinforced the value of the skills being taught in the "
    "classroom.",
]

WORKSHOPS = []  # Default empty. Students add entries if they attended any workshops.