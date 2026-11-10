from app.database.db import engine, Base
from app.database import models

print("Creating database tables...")

Base.metadata.create_all(bind=engine)

print("Database created successfully!")
print("Tables: events, photos, faces")