
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from app.api.guest import router as guest_router
from app.api.auth import router as auth_router
from app.api.search import router as search_router
from app.api.events import router as events_router
from app.api.photos import router as photos_router
from app.api.people import router as people_router
from app.services.person_groups import init_person_groups


app = FastAPI(
    title="Event AI Photo Finder",
    description="AI-powered event photography search system",
    version="2.0.0"
)


# --------------------------------------------------
# SERVE GUEST FRONTEND CSS AND JAVASCRIPT
# --------------------------------------------------

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static"
)


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "status": "success",
        "project": "Event AI Photo Finder",
        "message": "Backend is running successfully"
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}


# --------------------------------------------------
# REGISTER API ROUTERS
# --------------------------------------------------

app.include_router(auth_router)
app.include_router(search_router)
app.include_router(events_router)
app.include_router(photos_router)
app.include_router(guest_router)
app.include_router(people_router)


@app.on_event("startup")
def initialize_person_groups():
    init_person_groups()


# --------------------------------------------------
# CUSTOM OPENAPI SCHEMA
# PRESERVE SWAGGER FILE UPLOAD DISPLAY
# --------------------------------------------------

def convert_file_schema_to_binary(schema):

    if isinstance(schema, dict):

        if schema.get("contentMediaType") == "application/octet-stream":
            schema.pop("contentMediaType", None)
            schema["format"] = "binary"

        for value in schema.values():
            convert_file_schema_to_binary(value)

    elif isinstance(schema, list):

        for item in schema:
            convert_file_schema_to_binary(item)


def custom_openapi():

    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        openapi_version="3.0.3"
    )

    convert_file_schema_to_binary(openapi_schema)

    app.openapi_schema = openapi_schema

    return app.openapi_schema


app.openapi = custom_openapi
