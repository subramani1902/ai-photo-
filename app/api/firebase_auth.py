"""Additive authentication probes. No gallery passwords or public account linking."""

from fastapi import APIRouter, Depends, Response

from app.core.firebase_tokens import FirebasePrincipal
from app.database.models import User
from app.dependencies.firebase_auth import get_current_firebase_admin, get_current_firebase_principal

router = APIRouter(prefix="/api/firebase/auth", tags=["Firebase Authentication"])


@router.get("/me")
def firebase_profile(response: Response,
                     principal: FirebasePrincipal = Depends(get_current_firebase_principal)):
    response.headers["Cache-Control"] = "private, no-store"
    return {"uid": principal.uid, "project_id": principal.project_id}


@router.get("/admin")
def firebase_admin_profile(response: Response, admin: User = Depends(get_current_firebase_admin)):
    response.headers["Cache-Control"] = "private, no-store"
    return {"id": admin.id, "email": admin.email, "is_active": admin.is_active}
