"""Small, authenticated Cloudflare origin. Does not expose legacy/admin/photo paths."""

import os
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.client_events import router as client_events_router
from app.api.firebase_auth import router as firebase_auth_router
from app.core.firebase_config import PROJECT_ROOT
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=False)


def allowed_origins():
    origins = []
    for value in os.getenv("FIREBASE_PHOTO_API_ORIGINS", "").split(","):
        value = value.strip().rstrip("/")
        if not value:
            continue
        parsed = urlsplit(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme not in ({"https", "http"} if local else {"https"})
                or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or "*" in value):
            raise ValueError("Use explicit HTTPS frontend origins; loopback HTTP is local-only.")
        origins.append(value)
    return origins


app = FastAPI(title="SM Photography private photo API", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins(), allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type"], expose_headers=["Content-Disposition"])


@app.middleware("http")
async def private_responses(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/health")
def health():
    return {"status": "healthy"}


app.include_router(firebase_auth_router)
app.include_router(client_events_router)
