from src.contracts import COPY


def migrate(db):
    db.execute('DROP TABLE legacy')
    db.execute(COPY)
