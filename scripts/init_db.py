import asyncio
import sys
sys.path.append('C:\\dev\\Trading_bot')

from database.session import engine
from database.models import Base

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("Database initialized.")

asyncio.run(init_db())
