"""Account signup."""


def normalise(email):
    """Trim and lowercase an address.

    Applied to new signups since release 2.3. Accounts created earlier keep the
    address exactly as it was typed.
    """
    return email.strip().lower()


def create_account(connection, email):
    connection.execute("INSERT INTO accounts (email) VALUES (?)", (normalise(email),))
    connection.commit()
