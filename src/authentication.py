import getpass
import database


def authenticate_user():
    """Authenticate the user before running the ESG pipeline."""

    print("\n" + "=" * 50)
    print("        ESG ANALYTICS PLATFORM")
    print("             LOGIN REQUIRED")
    print("=" * 50)

    email = input("Email: ")
    password = getpass.getpass("Password: ")
    user = database.authenticate(email, password)
    if user:
        print("\nLogin successful.\n")
        return user

    print("\nInvalid username or password.")
    print("Access denied.")

    return False
