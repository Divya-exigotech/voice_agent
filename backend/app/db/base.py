from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import models here so Alembic autogenerate can discover them.
from app import models  # noqa: E402,F401
