
from sqlalchemy import inspect, text

from app.database.db import engine, Base
from app.database import models


def migrate():

    print("Starting authentication database migration...")

    # Create the new users table if it does not exist.
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)

    event_columns = {
        column["name"]
        for column in inspector.get_columns("events")
    }

    if "owner_id" not in event_columns:

        print("Adding owner_id to events table...")

        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE events "
                    "ADD COLUMN owner_id INTEGER "
                    "REFERENCES users(id)"
                )
            )

        print("owner_id column added.")

    else:
        print("owner_id already exists.")

    # Create an index for event ownership lookups.
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_events_owner_id ON events(owner_id)"
            )
        )

    print("Authentication migration completed.")
    print("Existing event and photo records were preserved.")


if __name__ == "__main__":
    migrate()
