"""add releases approvals audit events and release state"""

from alembic import op
import sqlalchemy as sa

revision = "0002_release_control_plane"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "releases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("commit", sa.String(length=64), nullable=False),
        sa.Column("image", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_releases_environment", "releases", ["environment"])
    op.create_index("ix_releases_status", "releases", ["status"])
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("release_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["release_id"], ["releases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("release_id"),
    )
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("operator", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=True),
        sa.Column("image", sa.String(length=512), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_environment", "audit_events", ["environment"])
    op.create_table(
        "release_states",
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("current_image", sa.String(length=512), nullable=True),
        sa.Column("previous_image", sa.String(length=512), nullable=True),
        sa.Column("history", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("environment", "engine"),
    )
    op.add_column("deployment_tasks", sa.Column("release_id", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_deployment_tasks_release_id", "deployment_tasks", "releases", ["release_id"], ["id"]
    )
    op.create_index("ix_deployment_tasks_release_id", "deployment_tasks", ["release_id"])


def downgrade() -> None:
    op.drop_index("ix_deployment_tasks_release_id", table_name="deployment_tasks")
    op.drop_constraint("fk_deployment_tasks_release_id", "deployment_tasks", type_="foreignkey")
    op.drop_column("deployment_tasks", "release_id")
    op.drop_table("release_states")
    op.drop_index("ix_audit_events_environment", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index("ix_releases_status", table_name="releases")
    op.drop_index("ix_releases_environment", table_name="releases")
    op.drop_table("releases")
