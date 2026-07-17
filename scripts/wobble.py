import asyncio
import ccxt.async_support as ccxt
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    exchange = ccxt.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_SECRET'),
        'enableRateLimit': True,
        'options': {'adjustForTimeDifference': True}
    })
    exchange.set_sandbox_mode(True)
    
    try:
        print("Wobbling price...")
        for i in range(5):
            print(f"Cycle {i+1} - sell")
            try:
                await exchange.create_order('BNB/USDT', 'market', 'sell', 1.0)
            except Exception as e:
                print("Sell error:", e)
            await asyncio.sleep(1)
            
            print(f"Cycle {i+1} - buy")
            try:
                await exchange.create_order('BNB/USDT', 'market', 'buy', 1.0)
            except Exception as e:
                print("Buy error:", e)
            await asyncio.sleep(1)
            
    finally:
        await exchange.close()

if __name__ == '__main__':
    asyncio.run(main())
