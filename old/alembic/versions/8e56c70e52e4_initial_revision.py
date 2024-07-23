"""Initial Revision

Revision ID: 8e56c70e52e4
Revises:
Create Date: 2020-10-26 16:57:54.817555

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8e56c70e52e4'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('guilds',
    sa.Column('id', sa.BIGINT(), nullable=False),
    sa.Column('prefix', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_guilds'))
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('guilds')
    # ### end Alembic commands ###