"""report text: keep the reported message text on the report

Revision ID: e7a3c5d91b42
Revises: c41f0a7d9e21
Create Date: 2026-11-27 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a3c5d91b42'
down_revision: Union[str, Sequence[str], None] = 'c41f0a7d9e21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 신고한 때의 메시지 글. 이미 있는 신고는 지금 메시지 글로 채운다.
    op.add_column('reports', sa.Column('text', sa.String(), nullable=True))
    op.execute("UPDATE reports SET text = (SELECT text FROM messages WHERE messages.id = reports.message_id)")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reports', 'text')
