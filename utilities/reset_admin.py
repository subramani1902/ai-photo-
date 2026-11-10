
from getpass import getpass

from app.database.db import SessionLocal
from app.database.models import User
from app.core.security import hash_password


def reset_admin():

    db = SessionLocal()

    try:
        admin = db.query(User).filter(User.id == 1).first()

        if admin is None:
            print("Admin account not found.")
            return

        print("Existing admin:", admin.email)

        new_email = input("Enter new admin email: ").strip().lower()

        if not new_email or "@" not in new_email:
            print("Invalid email address.")
            return

        existing_user = (
            db.query(User)
            .filter(
                User.email == new_email,
                User.id != admin.id
            )
            .first()
        )

        if existing_user:
            print("This email is already registered.")
            return

        new_password = getpass("Enter new password (minimum 12 characters): ")

        if len(new_password) < 12:
            print("Password must contain at least 12 characters.")
            return

        confirm_password = getpass("Confirm new password: ")

        if new_password != confirm_password:
            print("Passwords do not match.")
            return

        admin.email = new_email
        admin.password_hash = hash_password(new_password)
        admin.is_active = True

        db.commit()

        print("\nAdmin account updated successfully.")
        print("Admin email:", admin.email)
        print("Admin ID:", admin.id)

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    reset_admin()
