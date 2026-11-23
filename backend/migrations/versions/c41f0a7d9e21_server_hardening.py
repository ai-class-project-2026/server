"""server hardening: ai_enabled_at, message client_id, unique reports, null JSON repair

Revision ID: c41f0a7d9e21
Revises: bb92a3ac7aae
Create Date: 2026-11-24 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c41f0a7d9e21'
down_revision: Union[str, Sequence[str], None] = 'bb92a3ac7aae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1) JSON null이 저장된 일정 고치기 (이런 행이 있으면 캘린더 GET이 팀 전체에서 500이 난다)
    for col in ("attendees", "absent", "source_message_ids"):
        op.execute(f"UPDATE events SET {col} = '[]' WHERE {col} IS NULL OR {col} = 'null'")

    # 2) AI를 켠 시각. 이미 켜진 워크스페이스는 AI를 언제 켰는지 기록이 없다.
    #    예전 메시지가 동의보다 먼저일 수 있으니, 마이그레이션 시각(지금)으로 채워서 그 이전 메시지는 분석하지 않는다.
    with op.batch_alter_table('workspaces', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE workspaces SET ai_enabled_at = CURRENT_TIMESTAMP WHERE ai_schedule_on = 1")

    # 3) 메시지 재전송 방지용 client_id (null은 여러 개 가능)
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('client_id', sa.String(length=64), nullable=True))
    op.create_index('uq_messages_client', 'messages', ['channel_id', 'user_id', 'client_id'], unique=True)

    # 4) 신고 중복 제거(가장 오래된 것만 남김) 후 유일 제약
    op.execute(
        "DELETE FROM reports WHERE id NOT IN "
        "(SELECT MIN(id) FROM reports GROUP BY message_id, user_id, type)"
    )
    op.create_index('uq_reports_once', 'reports', ['message_id', 'user_id', 'type'], unique=True)


def downgrade() -> None:
    """Downgrade schema. (JSON null 복구와 중복 신고 삭제는 되돌리지 않는다)"""
    op.drop_index('uq_reports_once', table_name='reports')
    op.drop_index('uq_messages_client', table_name='messages')
    # batch 대신 바로 DROP COLUMN: batch는 테이블을 다시 만들면서 이름 없는 CHECK 제약(filter_level)을 잃는다
    op.drop_column('messages', 'client_id')
    op.drop_column('workspaces', 'ai_enabled_at')
