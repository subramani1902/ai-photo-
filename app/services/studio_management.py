"""Server-only studio bookings, payment records, and public package settings."""

from datetime import date, datetime, timezone
from functools import cache
import re
from secrets import token_urlsafe
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.firebase import get_firestore_client
from app.core.firebase_config import PROJECT_ROOT
from app.services.homepage_content import has_website_permission

STUDIO_ADMINS = "website_studio_admins"
STUDIO_PERMISSION = "manage_studio"
RECORD_ID = r"[a-f0-9]{32}"
PAYMENT_LINK_TOKEN = r"[A-Za-z0-9_-]{43}"
UPI_ID = r"[A-Za-z0-9._-]{1,96}@[A-Za-z0-9.-]{1,64}"
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


class BillingChange(StudioModel):
    revision: int = Field(gt=0, strict=True)
    operation_id: str = Field(pattern=f"^{RECORD_ID}$")


class InvoiceIssue(BillingChange):
    description: str = Field(min_length=1, max_length=120)
    total_paise: Annotated[Amount, Field(gt=0, strict=True)]
    due_date: date | None = None


class PaymentEntry(BillingChange):
    payment: Payment


class InvoicePaymentLink(StudioModel):
    token: str = Field(pattern=f"^{PAYMENT_LINK_TOKEN}$")


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
        self.upi_settings = self.client.document("website_content/studio_upi")
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
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

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
            )}, "paid_paise": sum(payment["amount_paise"] for payment in details["payments"]),
                "invoice_total_paise": (data.get("invoice") or {}).get("total_paise")})
        return {"clients": clients, "next": docs[PAGE_SIZE - 1].id if len(docs) > PAGE_SIZE else None}

    def read_client(self, record_id):
        data = self.clients.document(record_id).get(timeout=8).to_dict()
        if data is None:
            raise StudioError("Client record was not found.", 404)
        return {"id": record_id, "revision": data["revision"], **data["details"], "invoice": data.get("invoice")}

    def save_client(self, record_id, update, uid):
        details = update.model_dump(mode="json", exclude={"revision"})
        revision = self.save(self.clients.document(record_id), details, update.revision, uid,
                             search=search_prefixes(details))
        return {"id": record_id, "revision": revision, **details}

    @staticmethod
    def billing_record(record_id, data):
        details = data["details"]
        return {"id": record_id, "revision": data["revision"], "invoice": data.get("invoice"),
                **{key: details.get(key) for key in ("name", "event_name", "event_date", "package", "quote_paise")},
                "payments": details.get("payments", [])}

    def read_billing(self):
        fields = ["revision", "invoice", "details.name", "details.event_name", "details.event_date",
                  "details.package", "details.quote_paise", "details.payments"]
        records = [self.billing_record(doc.id, doc.to_dict())
                   for doc in self.clients.select(fields).stream(timeout=15)]
        return sorted(records, key=lambda record: (record["name"].casefold(), record["id"]))

    def issue_invoice(self, record_id, update, uid):
        def issue(transaction, record):
            if record.get("invoice"):
                raise StudioError("This booking already has an invoice.", 409)
            today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
            counter = self.client.document(f"website_invoice_counters/{today.year}")
            current = counter.get(transaction=transaction).to_dict() or {}
            number = current.get("number", 0) + 1
            record["invoice"] = {
                "number": f"INV-{today.year}-{number:04d}", "issued_on": today.isoformat(),
                "due_date": update.due_date.isoformat() if update.due_date else None,
                "description": update.description, "total_paise": update.total_paise,
                "client_name": record["details"]["name"],
            }
            transaction.set(counter, {"number": number})
        return self.change_billing(record_id, update, uid, "invoice", issue)

    def record_payment(self, record_id, update, uid):
        def append(transaction, record):
            payments = record["details"]["payments"]
            if len(payments) >= 100:
                raise StudioError("This booking already has 100 recorded payments.", 409)
            payments.append(update.payment.model_dump(mode="json"))
        return self.change_billing(record_id, update, uid, "payment", append)

    @staticmethod
    def invoice_balance(record):
        return max(0, record["invoice"]["total_paise"] -
                   sum(payment["amount_paise"] for payment in record["details"]["payments"]))

    def save_upi_id(self, upi_id, uid):
        if not re.fullmatch(UPI_ID, upi_id, flags=re.ASCII):
            raise StudioError("Enter a valid studio UPI ID.")
        self.upi_settings.set({"upi_id": upi_id, "updated_by": uid,
                              "updated_at": datetime.now(timezone.utc)}, timeout=8)

    def issue_payment_link(self, record_id, uid):
        from google.cloud import firestore

        reference = self.clients.document(record_id)
        token = token_urlsafe(32)

        @firestore.transactional
        def issue(transaction):
            record = reference.get(transaction=transaction).to_dict() or {}
            if not record.get("invoice"):
                raise StudioError("Issue an invoice for this booking first.", 409)
            balance = self.invoice_balance(record)
            if not balance:
                raise StudioError("This invoice has no balance to pay.", 409)
            link = record.get("invoice_payment_link")
            if not link:
                config = self.upi_settings.get(transaction=transaction).to_dict() or {}
                upi_id = config.get("upi_id", "")
                if not isinstance(upi_id, str) or not re.fullmatch(UPI_ID, upi_id, flags=re.ASCII):
                    raise StudioError("The studio UPI account still needs to be configured.", 503)
                link = {"token": token, "upi_id": upi_id, "created_by": uid,
                        "created_at": datetime.now(timezone.utc)}
                transaction.update(reference, {"invoice_payment_link": link})
            return {"token": link["token"], "upi_id": link["upi_id"],
                    "invoice_number": record["invoice"]["number"], "balance_paise": balance}

        return issue(self.client.transaction())

    def revoke_payment_link(self, record_id, token):
        from google.cloud import firestore

        reference = self.clients.document(record_id)

        @firestore.transactional
        def revoke(transaction):
            record = reference.get(transaction=transaction).to_dict() or {}
            link = record.get("invoice_payment_link")
            if link and link.get("token") != token:
                raise StudioError("The link changed. Open Pay Balance to see the current link.", 409)
            if link:
                transaction.update(reference, {"invoice_payment_link": None})

        revoke(self.client.transaction())

    def read_payment_link(self, token):
        from google.cloud.firestore_v1.base_query import FieldFilter

        fields = ["invoice.number", "invoice.total_paise", "details.payments", "invoice_payment_link.upi_id"]
        matches = self.clients.where(filter=FieldFilter("invoice_payment_link.token", "==", token)).select(fields).limit(1).get(timeout=8)
        record = matches[0].to_dict() if matches else None
        if not record or not record.get("invoice"):
            raise StudioError("This payment link is unavailable. Ask the studio for a current link.", 404)
        balance = self.invoice_balance(record)
        return {"invoice_number": record["invoice"]["number"], "balance_paise": balance,
                "upi_id": record["invoice_payment_link"]["upi_id"] if balance else None}

    def change_billing(self, record_id, update, uid, kind, action):
        from google.cloud import firestore

        reference = self.clients.document(record_id)
        operation = {"id": update.operation_id, "kind": kind}

        @firestore.transactional
        def write(transaction):
            record = reference.get(transaction=transaction).to_dict()
            if record is None:
                raise StudioError("Client record was not found.", 404)
            if record.get("billing_operation") == operation:
                return self.billing_record(record_id, record)
            if record["revision"] != update.revision:
                raise StudioError("This booking changed. Reload it before saving again.", 409)
            action(transaction, record)
            record.update(revision=record["revision"] + 1, billing_operation=operation,
                          updated_at=datetime.now(timezone.utc), updated_by=uid)
            transaction.set(reference, record)
            return self.billing_record(record_id, record)

        return write(self.client.transaction())

    def save(self, reference, details, revision, uid, *, search=None):
        from google.cloud import firestore

        @firestore.transactional
        def write(transaction):
            previous = reference.get(transaction=transaction).to_dict() or {}
            current_revision = previous.get("revision", 0)
            if current_revision == revision + 1 and previous.get("details") == details:
                return current_revision
            if current_revision != revision:
                raise StudioError("This record changed. Reload it before saving again.", 409)
            now = datetime.now(timezone.utc)
            updated = {**previous, "details": details, "revision": revision + 1, "updated_at": now, "updated_by": uid,
                       "created_at": previous.get("created_at", now)}
            if search is not None:
                updated["search_prefixes"] = search
            transaction.set(reference, updated)
            return updated["revision"]

        return write(self.client.transaction())
