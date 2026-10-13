def store(db, value):
    with db:
        db.execute('INSERT INTO events VALUES (?)', (value,))
