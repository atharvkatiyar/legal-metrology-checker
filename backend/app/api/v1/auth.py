from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class OfficerLoginRequest(BaseModel):
    login_identifier: str  # Allows logging in via Email OR Officer ID
    password: str

@router.post("/auth/login")
async def officer_login(body: OfficerLoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(
            (User.email == body.login_identifier) | (User.officer_id == body.login_identifier)
        )
    )
    officer = result.scalar_one_or_none()

    if not officer or not pwd_context.verify(body.password, officer.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Government Credentials. Access Denied."
        )

    return {
        "access_token": str(officer.id),  # Or generate a signed JWT
        "token_type": "bearer",
        "officer": {
            "id": str(officer.id),
            "name": officer.name,
            "officer_id": officer.officer_id,
            "gov_id": officer.gov_id_number,
            "jurisdiction": officer.jurisdiction_circle,
            "role": officer.role,
        }
    }