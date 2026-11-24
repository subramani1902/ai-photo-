"""Published package information and Firebase-authorized studio administration."""

from threading import Lock
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies.firebase_auth import get_current_firebase_principal
from app.services.homepage_content import has_website_permission
from app.services.studio_management import (
    ClientUpdate, InvoiceIssue, InvoicePaymentLink, PACKAGE_KEYS, PackageUpdate, PaymentEntry, PublicPackages,
    RECORD_ID, STUDIO_ADMINS, STUDIO_PERMISSION,
    StudioError, StudioStore, search_term,
)

RecordId = Annotated[str, Path(pattern=f"^{RECORD_ID}$")]
_packages_lock = Lock()
_public_packages = None
_packages_expires = 0


class StudioRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > 128 * 1024):
                raise HTTPException(413, "The studio record is too large.")
            try:
                response = await handler(request)
            except StudioError as error:
                raise HTTPException(error.status, str(error), headers={"Cache-Control": "private, no-store"}) from None
            except RequestValidationError as error:
                issue = error.errors()[0]
                field = " ".join(str(part).replace("_paise", "").replace("_", " ") for part in issue["loc"][1:])
                message = issue["msg"].removeprefix("Value error, ")
                raise HTTPException(422, f"{field.title()} — {message}" if field else message) from None
            response.headers.setdefault("Cache-Control", "private, no-store")
            return response
        return handle


router = APIRouter(prefix="/api/studio", tags=["Studio management"], route_class=StudioRoute)


def store_for_request():
    try:
        return StudioStore()
    except Exception:
        raise HTTPException(503, "The studio connection is temporarily unavailable.") from None


def studio_only(principal=Depends(get_current_firebase_principal)):
    store = store_for_request()
    try:
        store.authorize(principal)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "Unable to confirm studio access.") from None
    return store, principal.uid


@router.get("/access")
def studio_access(principal=Depends(get_current_firebase_principal)):
    client = store_for_request().client
    try:
        photos = has_website_permission(client, principal, "website_homepage_admins", "manage_homepage")
        studio = has_website_permission(client, principal, STUDIO_ADMINS, STUDIO_PERMISSION)
    except Exception:
        raise HTTPException(503, "Unable to confirm studio access.") from None
    if not photos and not studio:
        raise StudioError("This account does not have Studio admin access.", 403)
    return {"photos": photos, "studio": studio}


@router.get("/packages", response_model=PublicPackages)
def get_public_packages(response: Response):
    global _public_packages, _packages_expires
    with _packages_lock:
        if monotonic() >= _packages_expires:
            try:
                details = store_for_request().read_packages()
                _public_packages = PublicPackages.model_validate({key: details[key] for key in PACKAGE_KEYS})
            except Exception:
                raise HTTPException(503, "Package information is temporarily unavailable.") from None
            _packages_expires = monotonic() + 30
        response.headers["Cache-Control"] = "public, max-age=30"
        return _public_packages


@router.get("/admin/packages")
def admin_packages(access=Depends(studio_only)):
    try:
        return access[0].read_packages()
    except Exception:
        raise HTTPException(503, "The saved packages are temporarily unavailable.") from None


@router.put("/admin/packages")
def update_packages(update: PackageUpdate, access=Depends(studio_only)):
    global _packages_expires
    store, uid = access
    try:
        result = store.save_packages(update, uid)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "The package settings could not be saved. Try again.") from None
    with _packages_lock:
        _packages_expires = 0
    return result


class ClientSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str = Field(default="", max_length=100)
    after: str | None = Field(default=None, pattern=f"^{RECORD_ID}$")


@router.post("/admin/clients/search")
def list_clients(query: ClientSearch, access=Depends(studio_only)):
    try:
        return access[0].list_clients(search_term(query.search)[:100], query.after)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "The client list is temporarily unavailable.") from None


@router.get("/admin/clients/{record_id}")
def read_client(record_id: RecordId, access=Depends(studio_only)):
    try:
        return access[0].read_client(record_id)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "The client record is temporarily unavailable.") from None


@router.put("/admin/clients/{record_id}")
def update_client(record_id: RecordId, update: ClientUpdate, access=Depends(studio_only)):
    store, uid = access
    try:
        return store.save_client(record_id, update, uid)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "The client record could not be saved. Try again.") from None


@router.get("/admin/billing")
def read_billing(access=Depends(studio_only)):
    try:
        return {"bookings": access[0].read_billing()}
    except Exception:
        raise HTTPException(503, "The billing ledger is temporarily unavailable.") from None


@router.post("/admin/clients/{record_id}/invoice")
def issue_invoice(record_id: RecordId, update: InvoiceIssue, access=Depends(studio_only)):
    store, uid = access
    try:
        return store.issue_invoice(record_id, update, uid)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "The invoice could not be issued. Try again.") from None


@router.post("/admin/clients/{record_id}/payments")
def record_payment(record_id: RecordId, update: PaymentEntry, access=Depends(studio_only)):
    store, uid = access
    try:
        return store.record_payment(record_id, update, uid)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "The payment could not be recorded. Try again.") from None


@router.post("/admin/clients/{record_id}/payment-link")
def issue_payment_link(record_id: RecordId, access=Depends(studio_only)):
    store, uid = access
    try:
        return store.issue_payment_link(record_id, uid)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "The payment link could not be created. Try again.") from None


@router.post("/admin/clients/{record_id}/payment-link/revoke")
def revoke_payment_link(record_id: RecordId, data: InvoicePaymentLink, access=Depends(studio_only)):
    try:
        access[0].revoke_payment_link(record_id, data.token)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "The payment link could not be revoked. Try again.") from None
    return {"revoked": True}


@router.post("/invoice-payment")
def view_payment_link(data: InvoicePaymentLink):
    try:
        return store_for_request().read_payment_link(data.token)
    except StudioError:
        raise
    except Exception:
        raise HTTPException(503, "The invoice balance is temporarily unavailable.") from None
