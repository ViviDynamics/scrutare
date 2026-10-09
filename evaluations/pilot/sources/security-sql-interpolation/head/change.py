def user_by_name(db, name):
    return db.execute("SELECT id FROM users WHERE name = '" + name + "'")
