def find(db, name):
    query = 'SELECT name FROM users WHERE name = ?'
    return db.execute(query, (name,)).fetchall()
