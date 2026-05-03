from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app import auth
from app.database import get_db

router = APIRouter()


def _get_token(request: Request) -> str | None:
    return request.headers.get("X-Admin-Token") or request.cookies.get("admin_token")


@router.post("/login")
def login(body: dict, response: Response, db: Session = Depends(get_db)):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    from app.database import AdminUser
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not user or not auth.verify_password(password, user.password_hash):
        return {"ok": False, "error": "Usuário ou senha incorretos"}
    token = auth.create_session(db, user.username)
    response.set_cookie("admin_token", token, max_age=86400 * 30, httponly=False, samesite="lax")
    return {
        "ok": True,
        "token": token,
        "display_name": user.display_name,
        "must_change_password": user.must_change_password,
    }


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = _get_token(request)
    if token:
        auth.invalidate_session(db, token)
    response.delete_cookie("admin_token")
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    token = _get_token(request)
    user = auth.get_user_from_token(db, token)
    if not user:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "display_name": user.display_name,
        "username": user.username,
        "must_change_password": user.must_change_password,
    }


@router.post("/change-password")
def change_password(body: dict, request: Request, db: Session = Depends(get_db)):
    token = _get_token(request)
    user = auth.get_user_from_token(db, token)
    if not user:
        return {"ok": False, "error": "Não autenticado"}
    current = body.get("current_password") or ""
    new_pw = body.get("new_password") or ""
    if not auth.verify_password(current, user.password_hash):
        return {"ok": False, "error": "Senha atual incorreta"}
    if len(new_pw) < 6:
        return {"ok": False, "error": "Senha muito curta (mín. 6 caracteres)"}
    user.password_hash = auth.hash_password(new_pw)
    user.must_change_password = False
    db.commit()
    return {"ok": True}
