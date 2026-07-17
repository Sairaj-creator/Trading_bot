import asyncio
import sys
from sqlalchemy import select

# Fix sys.path to allow imports
sys.path.append('C:\\dev\\Trading_bot')

from database.session import async_session_factory
from database.models import Trade

async def main():
    async with async_session_factory() as session:
        result = await session.execute(select(Trade))
        trades = result.scalars().all()
        print(f"Found {len(trades)} trades")
        for t in trades:
            print(t.id, t.symbol, t.side, t.price, t.quantity, t.status)

asyncio.run(main())
