from src.contracts import COPY


def migrate(db):
    db.execute(COPY)
    db.execute('DROP TABLE legacy')
