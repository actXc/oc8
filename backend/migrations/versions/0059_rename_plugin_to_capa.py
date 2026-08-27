"""rename plugin to capa

Revision ID: 0059
Revises: 0058
Create Date: 2026-08-18

"""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("plugin", "capa")
    op.rename_table("plugin_version", "capa_version")
    op.rename_table("plugin_installation", "capa_installation")

    op.alter_column("capa_version", "plugin_id", new_column_name="capa_id")
    op.alter_column("capa_installation", "plugin_id", new_column_name="capa_id")

    op.execute("ALTER INDEX ix_plugin_tenant_id RENAME TO ix_capa_tenant_id")
    op.execute("ALTER INDEX ix_plugin_name RENAME TO ix_capa_name")
    op.execute("ALTER INDEX ix_plugin_version_tenant_id RENAME TO ix_capa_version_tenant_id")
    op.execute("ALTER INDEX ix_plugin_version_plugin_id RENAME TO ix_capa_version_capa_id")
    op.execute(
        "ALTER INDEX ix_plugin_installation_tenant_id RENAME TO ix_capa_installation_tenant_id"
    )
    op.execute(
        "ALTER INDEX ix_plugin_installation_plugin_id RENAME TO ix_capa_installation_capa_id"
    )

    op.execute("ALTER TABLE capa RENAME CONSTRAINT ck_plugin_type TO ck_capa_type")
    op.execute("ALTER TABLE capa RENAME CONSTRAINT ck_plugin_origin TO ck_capa_origin")
    op.execute("ALTER TABLE capa RENAME CONSTRAINT ck_plugin_trust TO ck_capa_trust")
    op.execute(
        "ALTER TABLE capa RENAME CONSTRAINT uq_plugin_tenant_name TO uq_capa_tenant_name"
    )
    op.execute(
        "ALTER TABLE capa_version RENAME CONSTRAINT uq_plugin_version TO uq_capa_version"
    )
    op.execute(
        "ALTER TABLE capa_installation RENAME CONSTRAINT "
        "ck_plugin_installation_status TO ck_capa_installation_status"
    )
    op.execute(
        "ALTER TABLE capa_installation RENAME CONSTRAINT "
        "uq_plugin_installation TO uq_capa_installation"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE capa_installation RENAME CONSTRAINT "
        "uq_capa_installation TO uq_plugin_installation"
    )
    op.execute(
        "ALTER TABLE capa_installation RENAME CONSTRAINT "
        "ck_capa_installation_status TO ck_plugin_installation_status"
    )
    op.execute(
        "ALTER TABLE capa_version RENAME CONSTRAINT uq_capa_version TO uq_plugin_version"
    )
    op.execute(
        "ALTER TABLE capa RENAME CONSTRAINT uq_capa_tenant_name TO uq_plugin_tenant_name"
    )
    op.execute("ALTER TABLE capa RENAME CONSTRAINT ck_capa_trust TO ck_plugin_trust")
    op.execute("ALTER TABLE capa RENAME CONSTRAINT ck_capa_origin TO ck_plugin_origin")
    op.execute("ALTER TABLE capa RENAME CONSTRAINT ck_capa_type TO ck_plugin_type")

    op.execute(
        "ALTER INDEX ix_capa_installation_capa_id RENAME TO ix_plugin_installation_plugin_id"
    )
    op.execute(
        "ALTER INDEX ix_capa_installation_tenant_id RENAME TO ix_plugin_installation_tenant_id"
    )
    op.execute("ALTER INDEX ix_capa_version_capa_id RENAME TO ix_plugin_version_plugin_id")
    op.execute("ALTER INDEX ix_capa_version_tenant_id RENAME TO ix_plugin_version_tenant_id")
    op.execute("ALTER INDEX ix_capa_name RENAME TO ix_plugin_name")
    op.execute("ALTER INDEX ix_capa_tenant_id RENAME TO ix_plugin_tenant_id")

    op.alter_column("capa_installation", "capa_id", new_column_name="plugin_id")
    op.alter_column("capa_version", "capa_id", new_column_name="plugin_id")

    op.rename_table("capa_installation", "plugin_installation")
    op.rename_table("capa_version", "plugin_version")
    op.rename_table("capa", "plugin")
