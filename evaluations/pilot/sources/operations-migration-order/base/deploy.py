from schema import migrate


def rollout(db):
    migrate(db)
    db.execute("UPDATE jobs SET state = 'ready'")
