def store(db, value):
    query = 'INSERT INTO events VALUES (?)'
    with db:
        db.execute(query, (value,))
