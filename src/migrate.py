from __future__ import annotations

from alembic import command
from alembic.config import Config

from .config import PROJECT_ROOT


def upgrade() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    upgrade()
