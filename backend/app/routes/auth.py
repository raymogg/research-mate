"""Sign-in state. Signing in itself happens through Google or GitHub (oauth.py)."""

from fastapi import APIRouter, Depends, Request, Response
from sqlmodel import Session

from .. import auth
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/api/auth")


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


@router.post("/logout", status_code=204)
def log_out(request: Request, response: Response, db: Session = Depends(get_db)):
    auth.end_login(db, request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE)


@router.get("/me")
def me(user: User = Depends(auth.current_user)):
    return user_out(user)
