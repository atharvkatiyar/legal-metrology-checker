import asyncio
from passlib.context import CryptContext
from app.core.database import get_db  # We use get_db since we know your project has it
from app.models.schema import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def seed_officers():
    # Loop through the get_db() generator to extract the active database session
    async for db in get_db():
        test_officers = [
            User(
                name="Vikramaditya Rathore",
                email="v.rathore@rajasthan.gov.in",
                officer_id="RJ-LM-2024-0418",
                gov_id_number="RAJ-GOV-98712",
                jurisdiction_circle="Circle-II (Sanganer / Industrial Zone)",
                hashed_password=pwd_context.hash("SecureInspector@2026"),
                role="Senior Legal Metrology Officer"
            ),
            User(
                name="Ananya Sharma",
                email="ananya.sharma@rajasthan.gov.in",
                officer_id="RJ-LM-2023-1102",
                gov_id_number="RAJ-GOV-55410",
                jurisdiction_circle="Circle-IV (Jaipur Central)",
                hashed_password=pwd_context.hash("JaipurField@2026"),
                role="Field Enforcement Officer"
            ),
            User(
                name="Amit Patel",
                email="amit.p@rajasthan.gov.in",
                officer_id="RJ-LM-2025-0011",
                gov_id_number="RAJ-GOV-11223",
                jurisdiction_circle="Circle-I (Jaipur North)",
                hashed_password=pwd_context.hash("Amit@2026"),
                role="Field Enforcement Officer"
            ),
            User(
                name="Priya Singh",
                email="priya.s@rajasthan.gov.in",
                officer_id="RJ-LM-2025-0022",
                gov_id_number="RAJ-GOV-22334",
                jurisdiction_circle="Circle-III (Jaipur South)",
                hashed_password=pwd_context.hash("Priya@2026"),
                role="Field Enforcement Officer"
            ),
            User(
                name="Rahul Verma",
                email="rahul.v@rajasthan.gov.in",
                officer_id="RJ-LM-2025-0033",
                gov_id_number="RAJ-GOV-33445",
                jurisdiction_circle="Circle-V (Ajmer Road)",
                hashed_password=pwd_context.hash("Rahul@2026"),
                role="Field Enforcement Officer"
            ),
            User(
                name="Neha Gupta",
                email="neha.g@rajasthan.gov.in",
                officer_id="RJ-LM-2025-0044",
                gov_id_number="RAJ-GOV-44556",
                jurisdiction_circle="Circle-VI (Mansarovar)",
                hashed_password=pwd_context.hash("Neha@2026"),
                role="Field Enforcement Officer"
            ),
            User(
                name="Raj Kumar",
                email="raj.k@rajasthan.gov.in",
                officer_id="RJ-LM-2025-0055",
                gov_id_number="RAJ-GOV-55667",
                jurisdiction_circle="Circle-VII (Vaishali Nagar)",
                hashed_password=pwd_context.hash("Raj@2026"),
                role="Senior Legal Metrology Officer"
            )
        ]
        
        db.add_all(test_officers)
        await db.commit()
        print("Government enforcement accounts seeded successfully.")
        break  # We only need it to run once

if __name__ == "__main__":
    asyncio.run(seed_officers())