import random

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serializers import org_summaries
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import api_key_of, current_user
from app.core.errors import ApiError, conflict, too_many
from app.core.ratelimit import RateLimiter
from app.core.security import create_session_token, hash_password, verify_password
from app.models import User
from app.schemas.common import LoginIn, MeOut, SignupIn, UserEnvelope, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

login_limiter = RateLimiter(get_settings().login_attempts_per_15_min, 15 * 60)


def _set_cookie(response: Response, user: User) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie,
        create_session_token(user.id),
        max_age=s.session_days * 86400,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/signup", response_model=UserEnvelope, status_code=201)
async def signup(body: SignupIn, response: Response, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    if (await db.execute(select(User.id).where(func.lower(User.email) == email))).first():
        raise conflict("An account with that email already exists", "email_taken")
    user = User(
        email=email,
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        avatar_hue=random.randint(0, 359),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:  # a concurrent signup with the same email won
        raise conflict("An account with that email already exists", "email_taken") from exc
    _set_cookie(response, user)
    return {"user": UserOut.model_validate(user)}


@router.post("/login", response_model=UserEnvelope)
async def login(
    body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    limit_key = f"{request.client.host if request.client else '-'}|{body.email.lower()}"
    if wait := login_limiter.retry_after(limit_key):
        raise too_many(f"Too many sign-in attempts. Try again in {wait // 60 + 1} min.", wait)
    user = (
        await db.execute(select(User).where(func.lower(User.email) == body.email.lower()))
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise ApiError(401, "Email or password is incorrect", "invalid_credentials")
    login_limiter.reset(limit_key)
    _set_cookie(response, user)
    return {"user": UserOut.model_validate(user)}


@router.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(get_settings().session_cookie, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=MeOut)
async def me(
    request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    """The signed-in user and their organizations. With an API key, `orgs` holds only
    the key's own organization."""
    key = api_key_of(request)
    orgs = await org_summaries(db, user.id, [key.org_id] if key else None)
    return MeOut(user=UserOut.model_validate(user), orgs=orgs)
