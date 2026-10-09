def migrate(db):
    db.execute("ALTER TABLE jobs ADD COLUMN state TEXT")
