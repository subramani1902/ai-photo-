"""Homepage management for explicitly approved Firebase studio administrators."""

from concurrent.futures import ThreadPoolExecutor
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.dependencies.firebase_auth import get_current_firebase_principal
from app.services.homepage_content import MAX_UPLOAD_BYTES, SLOTS, HomepageError, HomepageStore, public_admin_state, resolve_homepage_image
from app.services.homepage_publisher import publish_homepage

LOG = logging.getLogger(__name__)
publisher = ThreadPoolExecutor(max_workers=1, thread_name_prefix="homepage-publisher")


def authorize_request(request):
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    credentials = HTTPAuthorizationCredentials(scheme=scheme, credentials=token) if scheme and token else None
    principal = get_current_firebase_principal(credentials)
    try:
        store = HomepageStore()
        store.authorize(principal)
        request.state.homepage_store, request.state.homepage_principal = store, principal
    except HomepageError as error:
        raise HTTPException(error.status, str(error)) from None
    except Exception:
        raise HTTPException(503, "Homepage management is temporarily unavailable.") from None


class HomepageAdminRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def secured(request):
            # Reject ordinary clients before the multipart parser accepts any photo bytes.
            await run_in_threadpool(authorize_request, request)
            size = request.headers.get("Content-Length")
            if size and (not size.isdigit() or int(size) > MAX_UPLOAD_BYTES + 128 * 1024):
                raise HTTPException(413, "Choose an image up to 20 MB.")
            try:
                return await handler(request)
            except HomepageError as error:
                raise HTTPException(error.status, str(error)) from None
        return secured


router = APIRouter(prefix="/api/firebase/homepage", tags=["Homepage administration"], route_class=HomepageAdminRoute)


def store_for(request: Request):
    return request.state.homepage_store


@router.get("/admin")
def homepage_admin(store=Depends(store_for)):
    try:
        return public_admin_state(store.read())
    except HomepageError:
        raise
    except Exception:
        raise HTTPException(503, "The saved homepage settings are temporarily unavailable.") from None


@router.post("/photos/{slot_id}")
async def save_homepage_photo(request: Request, slot_id: str, revision: int = Form(ge=1),
                              title: str = Form(max_length=100), alt: str = Form(max_length=250),
                              instagram_url: str = Form(default="", max_length=500),
                              position: str = Form(default="50% 50%", max_length=12),
                              photo: UploadFile | None = File(default=None), store=Depends(store_for)):
    data = None
    if photo is not None:
        try:
            data = await photo.read(MAX_UPLOAD_BYTES + 1)
        finally:
            await photo.close()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HomepageError("Choose an image up to 20 MB.", 413)
    try:
        state = await run_in_threadpool(store.save, slot_id,
              {"title": title, "alt": alt, "instagram_url": instagram_url, "position": position},
              data, revision, request.state.homepage_principal.uid)
        return public_admin_state(state)
    except HomepageError:
        raise
    except Exception:
        raise HTTPException(503, "The photo could not be saved. Please try again.") from None


@router.get("/previews/{slot_id}")
def preview_homepage_photo(slot_id: str, store=Depends(store_for)):
    if slot_id not in SLOTS:
        raise HomepageError("Unknown homepage photo.", 404)
    photo = next(p for p in store.read()["draft"]["photos"] if p["id"] == slot_id)
    path = resolve_homepage_image(photo["image"])
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class PublishRequest(BaseModel):
    revision: int = Field(gt=0, strict=True)


def run_publish(store, job_id, config):
    try:
        publish_homepage(config, lambda message: store.progress(job_id, message))
        store.progress(job_id, "Your homepage photos are live.", status="published", config=config)
    except Exception as error:
        LOG.warning("Homepage publishing interrupted (%s)", type(error).__name__)
        try:
            message = str(error) if isinstance(error, HomepageError) else "Publishing was interrupted. Your saved photos are ready to publish again."
            store.progress(job_id, message, status="failed")
        except Exception:
            LOG.warning("Could not update publishing status; its lease permits retry")


@router.post("/publish", status_code=202)
def publish_saved_homepage(data: PublishRequest, request: Request, store=Depends(store_for)):
    try:
        job_id, config = store.begin_publish(data.revision, request.state.homepage_principal.uid)
        publisher.submit(run_publish, store, job_id, config)
        return public_admin_state(store.read())
    except HomepageError:
        raise
    except Exception:
        raise HTTPException(503, "The homepage publisher is temporarily unavailable.") from None
