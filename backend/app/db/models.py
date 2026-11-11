from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Tables from plan 10.3 go here. Use JSON (not ARRAY) for list fields — SQLite.
