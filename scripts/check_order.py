import asyncio
import os
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

async def main():
    e = ccxt.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_SECRET'),
        'options': {'adjustForTimeDifference': True}
    })
    e.set_sandbox_mode(True)
    try:
        order = await e.fetch_order('6747282', 'BNB/USDT')
        print("STATUS:", order.get('status'))
        print("FILLED:", order.get('filled'))
        print("AVERAGE:", order.get('average'))
    except Exception as e_err:
        print(e_err)
    await e.close()

asyncio.run(main())
