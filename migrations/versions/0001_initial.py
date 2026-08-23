"""create deployment task table"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployment_tasks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("commit", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_deployment_tasks_environment", "deployment_tasks", ["environment"])
    op.create_index("ix_deployment_tasks_idempotency_key", "deployment_tasks", ["idempotency_key"])
    op.create_index("ix_deployment_tasks_status", "deployment_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_deployment_tasks_status", table_name="deployment_tasks")
    op.drop_index("ix_deployment_tasks_idempotency_key", table_name="deployment_tasks")
    op.drop_index("ix_deployment_tasks_environment", table_name="deployment_tasks")
    op.drop_table("deployment_tasks")
