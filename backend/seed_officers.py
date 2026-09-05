import asyncio
from passlib.context import CryptContext
from sqlalchemy import select
from app.core.database import get_db
from app.models.schema import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def seed_officers():
    async for db in get_db():
        test_officers = [
            # --- System Administrators ---
            User(
                name="Atharv",
                email="atharv.admin@rajasthan.gov.in",
                officer_id="RJ-LM-ADMIN-001",
                gov_id_number="RAJ-GOV-A001",
                jurisdiction_circle="State Headquarters (Admin)",
                hashed_password=pwd_context.hash("Atharv@2026"),
                role="System Administrator"
            ),
            User(
                name="Rishita",
                email="rishita.admin@rajasthan.gov.in",
                officer_id="RJ-LM-ADMIN-002",
                gov_id_number="RAJ-GOV-A002",
                jurisdiction_circle="State Headquarters (Admin)",
                hashed_password=pwd_context.hash("Rishita@2026"),
                role="System Administrator"
            ),
            User(
                name="Mithil",
                email="mithil.admin@rajasthan.gov.in",
                officer_id="RJ-LM-ADMIN-003",
                gov_id_number="RAJ-GOV-A003",
                jurisdiction_circle="State Headquarters (Admin)",
                hashed_password=pwd_context.hash("Mithil@2026"),
                role="System Administrator"
            ),
            
            # --- Field Officers ---
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
        
        # Get existing emails to prevent duplicates
        result = await db.execute(select(User.email))
        existing_emails = {row[0] for row in result.all()}
        
        # Only add users whose emails aren't already in the database
        new_officers = [u for u in test_officers if u.email not in existing_emails]
        
        if new_officers:
            db.add_all(new_officers)
            await db.commit()
            print(f"Success! Seeded {len(new_officers)} new accounts.")
        else:
            print("All accounts already exist in the database. Nothing new to add.")
            
        break

if __name__ == "__main__":
    asyncio.run(seed_officers())
