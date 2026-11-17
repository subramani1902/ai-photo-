"""Additive local identity bindings. Existing event/photo/face schemas stay intact."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.database.db import Base


class FirebaseAdminLink(Base):
    __tablename__ = "firebase_admin_links"

    project_id = Column(String(63), primary_key=True)
    firebase_uid = Column(String(128), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
