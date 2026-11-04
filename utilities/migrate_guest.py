
from app.database.db import Base, engine
from app.database import models


def migrate():

    print("Starting guest access migration...")

    GuestAccess = models.GuestAccess

    GuestAccess.__table__.create(
        bind=engine,
        checkfirst=True
    )

    print("Guest access table created or already exists.")
    print("Existing users, events, photos and faces are preserved.")
    print("Guest access migration completed.")


if __name__ == "__main__":
    migrate()
