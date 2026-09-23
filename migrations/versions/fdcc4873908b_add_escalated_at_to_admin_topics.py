"""add escalated_at to admin_topics

Revision ID: fdcc4873908b
Revises: 2ea500df96f2
Create Date: 2026-09-23

ADR-0009. Колонка только ДОБАВЛЯЕТСЯ и допускает NULL: контейнер применяет
миграции при старте (alembic upgrade head && python main.py), и упавшая
миграция означает, что бот не поднимется. ADD COLUMN в SQLite не
перестраивает таблицу и существующие строки не трогает.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fdcc4873908b'
down_revision: Union[str, Sequence[str], None] = '2ea500df96f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'admin_topics',
        sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('admin_topics') as batch_op:
        batch_op.drop_column('escalated_at')
