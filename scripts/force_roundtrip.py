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
        # Check balance
        balance = await exchange.fetch_balance()
        usdt = balance['USDT']['free']
        bnb = balance.get('BNB', {}).get('free', 0.0)
        
        print(f"Balance: {usdt} USDT, {bnb} BNB")
        
        # We need BNB to sell. If we don't have BNB, buy some first (at market).
        if bnb < 1.0:
            print("Buying 1 BNB to get ammo...")
            res = await exchange.create_order('BNB/USDT', 'market', 'buy', 1.0)
            await asyncio.sleep(2)
            balance = await exchange.fetch_balance()
            bnb = balance.get('BNB', {}).get('free', 0.0)
            print(f"New BNB balance: {bnb}")
            
        sell_amount = 1.0
        print(f"Smashing bid with {sell_amount} BNB...")
        try:
            res = await exchange.create_order('BNB/USDT', 'market', 'sell', sell_amount)
            print("Market sell placed:", res['id'])
        except Exception as e:
            print("Error:", e)
    
        await asyncio.sleep(5)
        
        # Now pump the price to hit the grid SELL order that was just created!
        # The bot should have placed a sell order after the buy order was filled.
        if usdt > 1000:
            buy_amount = 1.0
            print(f"Smashing ask with {buy_amount} BNB...")
            try:
                res = await exchange.create_order('BNB/USDT', 'market', 'buy', buy_amount)
                print("Market buy placed:", res['id'])
            except Exception as e:
                print("Error:", e)
                
    finally:
        await exchange.close()

if __name__ == '__main__':
    asyncio.run(main())
