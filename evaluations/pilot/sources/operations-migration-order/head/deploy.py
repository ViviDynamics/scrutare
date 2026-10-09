from schema import migrate


def rollout(db):
    db.execute("UPDATE jobs SET state = 'ready'")
    migrate(db)
