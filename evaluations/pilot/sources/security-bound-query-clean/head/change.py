def user_by_name(db, name):
    query = "SELECT id FROM users WHERE name = ?"
    return db.execute(query, (name,))
