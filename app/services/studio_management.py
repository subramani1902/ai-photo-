"""Server-only studio bookings, payment records, and public package settings."""

from datetime import date, datetime, timezone
from functools import cache
import re
from typing import Annotated, Literal

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.firebase import get_firestore_client
from app.core.firebase_config import PROJECT_ROOT
from app.services.homepage_content import has_website_permission

STUDIO_ADMINS = "website_studio_admins"
STUDIO_PERMISSION = "manage_studio"
RECORD_ID = r"[a-f0-9]{32}"
PAGE_SIZE = 30
PACKAGE_KEYS = ("silver", "gold", "platinum")
Amount = Annotated[int, Field(ge=0, le=10_000_000_000)]


class StudioError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


class StudioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def clean_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if any(ord(char) < 32 and char not in "\r\n\t" for char in value):
                raise ValueError("Remove control characters.")
        return value


class Payment(StudioModel):
    date: date
    amount_paise: Annotated[Amount, Field(gt=0, strict=True)]
    method: Literal["upi", "bank_transfer", "card", "cash", "other"]
    reference: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=300)


class ClientDetails(StudioModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(default="", max_length=30)
    email: str = Field(default="", max_length=254)
    instagram: str = Field(default="", max_length=31)
    source: Literal["instagram", "website", "whatsapp", "referral", "other"] = "other"
    status: Literal["enquiry", "quoted", "booked", "completed", "archived"] = "enquiry"
    package: Literal["", "Wedding & reception", "Pre-wedding", "Celebration", "Silver", "Gold", "Platinum", "Custom"] = ""
    event_name: str = Field(default="", max_length=120)
    venue: str = Field(default="", max_length=160)
    event_date: date | None = None
    follow_up_date: date | None = None
    quote_paise: Annotated[Amount, Field(strict=True)] | None = None
    payments: list[Payment] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=2000)

    @field_validator("phone", "email", "instagram")
    @classmethod
    def check_contact(cls, value, info):
        if value:
            patterns = {"phone": r"\+?[0-9 ()-]+", "email": r"[^\s@]+@[^\s@]+\.[^\s@]+",
                        "instagram": r"@?[A-Za-z0-9_.]{1,30}"}
            if not re.fullmatch(patterns[info.field_name], value):
                raise ValueError("Enter a valid " + info.field_name + ".")
            if info.field_name == "phone" and not 7 <= len(phone_digits(value)) <= 15:
                raise ValueError("Enter a valid phone number.")
        return value.lstrip("@") if info.field_name == "instagram" else value


class PackageDetails(StudioModel):
    description: str = Field(default="", max_length=350)
    inclusions: str = Field(default="", max_length=600)
    starting_paise: Annotated[Amount, Field(strict=True)] | None = None
    deposit_paise: Annotated[Amount, Field(strict=True)] | None = None
    payment_terms: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def check_deposit(self):
        if self.starting_paise is not None and self.deposit_paise is not None and self.deposit_paise > self.starting_paise:
            raise ValueError("The booking deposit must be at most the starting price.")
        return self


class PublicPackages(StudioModel):
    silver: PackageDetails
    gold: PackageDetails
    platinum: PackageDetails


class PackageUpdate(PublicPackages):
    revision: int = Field(ge=0, strict=True)


class ClientUpdate(ClientDetails):
    revision: int = Field(ge=0, strict=True)


@cache
def initial_packages():
    return PublicPackages.model_validate_json((PROJECT_ROOT / "online/studio-packages.json").read_bytes())


def phone_digits(value):
    return re.sub(r"[^0-9]", "", value)


def search_term(value):
    value = " ".join(value.casefold().split()).lstrip("@")
    if re.fullmatch(r"\+?[0-9 ()-]+", value):
        return phone_digits(value)
    return value


def search_prefixes(details):
    name = search_term(details["name"])
    phone = phone_digits(details["phone"])
    values = [name, *name.split(), phone, phone[-10:], search_term(details["email"]),
              search_term(details["instagram"])]
    return sorted({value[:length] for value in values for length in range(1, min(len(value), 100) + 1)})


class StudioStore:
    def __init__(self, client=None):
        self.client = client if client is not None else get_firestore_client()
        self.packages = self.client.document("website_content/studio_packages")
        self.clients = self.client.collection("website_studio_clients")

    def authorize(self, principal):
        if not has_website_permission(self.client, principal, STUDIO_ADMINS, STUDIO_PERMISSION):
            raise StudioError("This account cannot manage studio clients or packages.", 403)

    def read_packages(self):
        data = self.packages.get(timeout=8).to_dict() or {}
        packages = PublicPackages.model_validate(data["details"]) if "details" in data else initial_packages()
        return {"revision": data.get("revision", 0), **packages.model_dump()}

    def save_packages(self, update, uid):
        details = update.model_dump(mode="json", exclude={"revision"})
        return {"revision": self.save(self.packages, details, update.revision, uid), **details}

    def list_clients(self, term, after):
        query = self.clients
        if term:
            query = query.where(filter=FieldFilter("search_prefixes", "array_contains", term))
        else:
            query = query.order_by("created_at", direction=firestore.Query.DESCENDING)
        if after:
            cursor = self.clients.document(after).get(timeout=8)
            if not cursor.exists:
                raise StudioError("This list changed. Refresh and try again.", 409)
            query = query.start_after(cursor)
        docs = query.limit(PAGE_SIZE + 1).get(timeout=8)
        clients = []
        for doc in docs[:PAGE_SIZE]:
            data = doc.to_dict()
            details = data["details"]
            clients.append({"id": doc.id, **{key: details[key] for key in (
                "name", "status", "source", "event_date", "follow_up_date", "quote_paise", "package",
            )}, "paid_paise": sum(payment["amount_paise"] for payment in details["payments"])})
        return {"clients": clients, "next": docs[PAGE_SIZE - 1].id if len(docs) > PAGE_SIZE else None}

    def read_client(self, record_id):
        data = self.clients.document(record_id).get(timeout=8).to_dict()
        if data is None:
            raise StudioError("Client record was not found.", 404)
        return {"id": record_id, "revision": data["revision"], **data["details"]}

    def save_client(self, record_id, update, uid):
        details = update.model_dump(mode="json", exclude={"revision"})
        revision = self.save(self.clients.document(record_id), details, update.revision, uid,
                             search=search_prefixes(details))
        return {"id": record_id, "revision": revision, **details}

    def save(self, reference, details, revision, uid, *, search=None):
        @firestore.transactional
        def write(transaction):
            previous = reference.get(transaction=transaction).to_dict() or {}
            current_revision = previous.get("revision", 0)
            if current_revision == revision + 1 and previous.get("details") == details:
                return current_revision
            if current_revision != revision:
                raise StudioError("This record changed. Reload it before saving again.", 409)
            now = datetime.now(timezone.utc)
            updated = {"details": details, "revision": revision + 1, "updated_at": now, "updated_by": uid,
                       "created_at": previous.get("created_at", now)}
            if search is not None:
                updated["search_prefixes"] = search
            transaction.set(reference, updated)
            return updated["revision"]

        return write(self.client.transaction())
