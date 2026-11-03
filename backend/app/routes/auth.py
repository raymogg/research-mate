import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from .. import auth
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/api/auth")


class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class SignUp(Credentials):
    invite_code: str = Field(max_length=256)


def user_out(user: User) -> dict:
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}


def _sign_in(request: Request, response: Response, db: Session, user: User) -> dict:
    settings = request.app.state.settings
    token = auth.start_login(db, user, settings.login_days)
    response.set_cookie(
        auth.COOKIE,
        token,
        max_age=settings.login_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return user_out(user)


@router.post("/signup", status_code=201)
def sign_up(body: SignUp, request: Request, response: Response, db: Session = Depends(get_db)):
    code = request.app.state.settings.invite_code
    if not code:
        raise HTTPException(403, "Sign-up is closed")
    if not secrets.compare_digest(body.invite_code.strip().encode(), code.encode()):
        raise HTTPException(403, "That invite code isn't right")
    try:
        user = auth.create_user(db, body.username, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _sign_in(request, response, db, user)


@router.post("/login")
def log_in(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    user = auth.authenticate(db, body.username, body.password)
    if user is None:
        raise HTTPException(401, "Wrong username or password")
    return _sign_in(request, response, db, user)


@router.post("/logout", status_code=204)
def log_out(request: Request, response: Response, db: Session = Depends(get_db)):
    auth.end_login(db, request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE)


@router.get("/me")
def me(user: User = Depends(auth.current_user)):
    return user_out(user)
