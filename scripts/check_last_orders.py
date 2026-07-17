import asyncio
import os
import ccxt.async_support as ccxt
from dotenv import load_dotenv
import json

load_dotenv()

async def main():
    e = ccxt.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_SECRET'),
        'options': {'adjustForTimeDifference': True}
    })
    e.set_sandbox_mode(True)
    try:
        orders = await e.fetch_orders('BNB/USDT', limit=10)
        for o in orders:
            print(o.get('id'), o.get('side'), o.get('price'), o.get('status'), o.get('filled'))
    except Exception as e_err:
        print(e_err)
    await e.close()

asyncio.run(main())
