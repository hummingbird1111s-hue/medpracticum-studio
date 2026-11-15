"""
Seed script for Ghana health facilities.
Run with: python manage.py shell < seed_facilities.py
"""

from apps.facilities.models import Facility

# A comprehensive list of major, well-known Ghanaian health facilities suitable for clinical practicum placements.
# This list prioritizes Teaching Hospitals, Regional Hospitals, and major CHAG facilities.
FACILITIES_DATA = [
    # --- Teaching Hospitals (The "Golden 6") ---
    {
        'name': 'Korle Bu Teaching Hospital',
        'region': 'Greater Accra',
        'district': 'Accra Metropolitan',
        'facility_type': 'Teaching Hospital',
        'history_and_background': 'Established in 1923, it is the third largest hospital in Africa and the leading national referral centre in Ghana. It is the primary teaching hospital for the University of Ghana Medical School and other health training institutions.',
        'vision_mission': 'To be a centre of excellence in healthcare delivery, training, and research in Africa.',
        'organogram_description': 'Headed by a Chief Executive Officer (CEO), supported by Directorates of Medical Affairs, Nursing, Pharmacy, Administration, Finance, and Allied Health Sciences.',
    },
    {
        'name': 'Komfo Anokye Teaching Hospital',
        'region': 'Ashanti',
        'district': 'Kumasi Metropolitan',
        'facility_type': 'Teaching Hospital',
        'history_and_background': 'Komfo Anokye Teaching Hospital (KATH) is the second largest hospital in Ghana and the main referral centre for the northern half of the country. It serves as a teaching hospital for the Kwame Nkrumah University of Science and Technology (KNUST) School of Medicine.',
        'vision_mission': 'To provide excellent tertiary healthcare services, training, and research of international standards.',
        'organogram_description': 'Headed by a Chief Executive Officer, supported by Directorates of Medical Affairs, Nursing, Pharmacy, Administration, Finance, and allied health units.',
    },
    {
        'name': 'Tamale Teaching Hospital',
        'region': 'Northern',
        'district': 'Tamale Metropolitan',
        'facility_type': 'Teaching Hospital',
        'history_and_background': 'Tamale Teaching Hospital is the referral hospital for the northern regions of Ghana and a teaching site for the School of Medicine and Health Sciences, University for Development Studies (UDS).',
        'vision_mission': 'To deliver specialised and quality healthcare to the people of Northern Ghana and beyond.',
        'organogram_description': 'Chief Executive Officer oversees Medical, Nursing, Pharmacy, Administration, and Finance directorates.',
    },
    {
        'name': 'Cape Coast Teaching Hospital',
        'region': 'Central',
        'district': 'Cape Coast Metropolitan',
        'facility_type': 'Teaching Hospital',
        'history_and_background': 'Cape Coast Teaching Hospital is a tertiary referral centre for the Central Region and serves as a teaching hospital for the University of Cape Coast School of Medical Sciences.',
        'vision_mission': 'To provide quality specialist healthcare services, training, and research.',
        'organogram_description': 'Managed by a CEO with Directorates of Medical Affairs, Nursing, Pharmacy, and Administration.',
    },
    {
        'name': 'Ho Teaching Hospital',
        'region': 'Volta',
        'district': 'Ho Municipal',
        'facility_type': 'Teaching Hospital',
        'history_and_background': 'Formerly the Volta Regional Hospital, it was upgraded to a teaching hospital to serve the University of Health and Allied Sciences (UHAS) and the people of the Volta Region.',
        'vision_mission': 'To be a leading centre for specialist healthcare, medical training, and research in the Volta Region.',
        'organogram_description': 'Headed by a CEO, supported by Directorates of Medical Affairs, Nursing, Pharmacy, and Administration.',
    },
    {
        'name': 'Sunyani Teaching Hospital',
        'region': 'Bono',
        'district': 'Sunyani Municipal',
        'facility_type': 'Teaching Hospital',
        'history_and_background': 'The Sunyani Teaching Hospital is a major referral facility in the Bono Region, serving as a teaching hospital for various health training institutions.',
        'vision_mission': 'To provide high-quality tertiary healthcare services and training.',
        'organogram_description': 'Managed by a CEO with directorates for Medical Affairs, Nursing, Pharmacy, and Administration.',
    },

    # --- Regional Hospitals ---
    {
        'name': 'Greater Accra Regional Hospital (Ridge)',
        'region': 'Greater Accra',
        'district': 'Accra Metropolitan',
        'facility_type': 'Regional Hospital',
        'history_and_background': 'The Greater Accra Regional Hospital, located at Ridge, is a major referral hospital in the capital city, serving as a secondary level referral facility.',
        'vision_mission': 'To provide accessible and quality secondary healthcare services to the people of the Greater Accra Region.',
        'organogram_description': 'Headed by a Medical Director, with departments for various clinical and administrative functions.',
    },
    {
        'name': 'Effia Nkwanta Regional Hospital',
        'region': 'Western',
        'district': 'Sekondi-Takoradi Metropolitan',
        'facility_type': 'Regional Hospital',
        'history_and_background': 'Effia Nkwanta Regional Hospital is the main referral hospital for the Western Region of Ghana.',
        'vision_mission': 'To provide quality and accessible specialist healthcare services to the people of the Western Region.',
        'organogram_description': 'Managed by a Medical Director with clinical and administrative departments.',
    },
    {
        'name': 'Bolgatanga Regional Hospital',
        'region': 'Upper East',
        'district': 'Bolgatanga Municipal',
        'facility_type': 'Regional Hospital',
        'history_and_background': 'Bolgatanga Regional Hospital serves as the main referral centre for the Upper East Region.',
        'vision_mission': 'To provide quality secondary healthcare services to the people of the Upper East Region.',
        'organogram_description': 'Headed by a Medical Director with departmental heads for clinical and administrative units.',
    },
    {
        'name': 'Wa Regional Hospital',
        'region': 'Upper West',
        'district': 'Wa Municipal',
        'facility_type': 'Regional Hospital',
        'history_and_background': 'Wa Regional Hospital is the primary referral facility for the Upper West Region.',
        'vision_mission': 'To provide quality and accessible healthcare services to the people of the Upper West Region.',
        'organogram_description': 'Managed by a Medical Director with clinical and administrative support units.',
    },
    {
        'name': 'Koforidua Regional Hospital',
        'region': 'Eastern',
        'district': 'New Juaben Municipal',
        'facility_type': 'Regional Hospital',
        'history_and_background': 'Koforidua Regional Hospital is the main referral hospital for the Eastern Region of Ghana.',
        'vision_mission': 'To provide quality secondary healthcare services to the people of the Eastern Region.',
        'organogram_description': 'Headed by a Medical Director, with various clinical and administrative departments.',
    },

    # --- Major CHAG / Faith-Based Hospitals ---
    {
        'name': 'Holy Family Hospital, Techiman',
        'region': 'Bono East',
        'district': 'Techiman Municipal',
        'facility_type': 'CHAG Hospital',
        'history_and_background': 'Holy Family Hospital in Techiman is a major referral hospital in the Bono East Region, managed by the Catholic Diocese of Techiman under CHAG.',
        'vision_mission': 'To provide holistic, quality healthcare services to all, especially the poor and vulnerable.',
        'organogram_description': 'Headed by a Hospital Administrator/CEO, with a Medical Director and various departmental heads.',
    },
    {
        'name': 'Presbyterian Hospital, Agogo',
        'region': 'Ashanti',
        'district': 'Asante Akim North Municipal',
        'facility_type': 'CHAG Hospital',
        'history_and_background': 'Presbyterian Hospital, Agogo, is a leading CHAG hospital in the Ashanti Region, known for its comprehensive healthcare services and training of health professionals.',
        'vision_mission': 'To provide holistic healthcare and training services in a Christian environment.',
        'organogram_description': 'Managed by a Hospital Administrator, with a Medical Director and departmental heads.',
    },
    {
        'name': 'Battor Catholic Hospital',
        'region': 'Volta',
        'district': 'North Tongu District',
        'facility_type': 'CHAG Hospital',
        'history_and_background': 'Battor Catholic Hospital is a major referral facility in the Volta Region, operated by the Catholic Diocese of Keta-Akatsi.',
        'vision_mission': 'To provide quality and compassionate healthcare services, especially to the poor and marginalized.',
        'organogram_description': 'Headed by an Administrator, with a Medical Director and various clinical and support staff.',
    },
    {
        'name': 'St. Dominic Hospital, Akwatia',
        'region': 'Eastern',
        'district': 'Denkyembour District',
        'facility_type': 'CHAG Hospital',
        'history_and_background': 'St. Dominic Hospital in Akwatia is a prominent CHAG hospital in the Eastern Region, offering a wide range of medical services.',
        'vision_mission': 'To provide quality healthcare services in a spirit of Christian love and compassion.',
        'organogram_description': 'Managed by a Hospital Administrator and Medical Director, with departmental heads.',
    },
    {
        'name': 'Wenchi Methodist Hospital',
        'region': 'Bono',
        'district': 'Wenchi Municipal',
        'facility_type': 'CHAG Hospital',
        'history_and_background': 'Wenchi Methodist Hospital is a significant CHAG facility in the Bono Region, providing healthcare to the Wenchi municipality and surrounding areas.',
        'vision_mission': 'To provide holistic and quality healthcare services to the community.',
        'organogram_description': 'Headed by a Hospital Administrator and Medical Director.',
    },

    # --- Major District Hospitals (High Volume) ---
    {
        'name': 'Obuasi Government Hospital',
        'region': 'Ashanti',
        'district': 'Obuasi Municipal',
        'facility_type': 'District Hospital',
        'history_and_background': 'Obuasi Government Hospital is a key public hospital serving the Obuasi municipality and its environs, with a high patient turnover.',
        'vision_mission': 'To provide accessible and quality healthcare services to the people of Obuasi and beyond.',
        'organogram_description': 'Headed by a Medical Superintendent, with various clinical and administrative departments.',
    },
    {
        'name': 'Dunkwa District Hospital',
        'region': 'Central',
        'district': 'Dunkwa-On-Offin District',
        'facility_type': 'District Hospital',
        'history_and_background': 'Dunkwa District Hospital serves as the main referral facility for the Dunkwa-On-Offin district and surrounding communities.',
        'vision_mission': 'To provide quality primary and secondary healthcare services.',
        'organogram_description': 'Managed by a Medical Superintendent with departmental heads.',
    },
    {
        'name': 'Adidome Government Hospital',
        'region': 'Volta',
        'district': 'Central Tongu District',
        'facility_type': 'District Hospital',
        'history_and_background': 'Adidome Government Hospital is a major public hospital in the Central Tongu District of the Volta Region.',
        'vision_mission': 'To provide accessible and quality healthcare services to the people of Central Tongu and beyond.',
        'organogram_description': 'Headed by a Medical Superintendent.',
    },
]

def run():
    print(f"Starting seeding process for {len(FACILITIES_DATA)} facilities...")
    created_count = 0
    updated_count = 0
    
    for data in FACILITIES_DATA:
        facility, created = Facility.objects.update_or_create(
            name=data['name'],
            defaults=data
        )
        if created:
            created_count += 1
        else:
            updated_count += 1

    print("="*50)
    print(f"Seeding complete!")
    print(f"Facilities created: {created_count}")
    print(f"Facilities updated: {updated_count}")
    print(f"Total facilities in DB: {Facility.objects.count()}")
    print(f"Verified facilities: {Facility.objects.filter(is_verified=True).count()}")
    print("="*50)

if __name__ == "__main__":
    run()