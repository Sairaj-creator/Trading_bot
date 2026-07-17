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
        # Get my open orders
        orders = await exchange.fetch_open_orders('BNB/USDT')
        buy_orders = [o for o in orders if o['side'] == 'buy']
        sell_orders = [o for o in orders if o['side'] == 'sell']
        
        ticker = await exchange.fetch_ticker('BNB/USDT')
        print(f"Current price: {ticker['last']}")
        
        if buy_orders:
            buy_orders.sort(key=lambda x: x['price'], reverse=True)
            top_buy = buy_orders[0]
            print(f"Top buy order: {top_buy['price']} for {top_buy['amount']}")
            
            # Place a limit sell exactly at the top buy price. 
            # Binance testnet might just match it with someone else, or reject if self-trade.
            print("Attempting to trigger buy order...")
            try:
                res = await exchange.create_order('BNB/USDT', 'market', 'sell', 1.0)
                print("Market sell placed:", res['id'])
            except Exception as e:
                print("Error placing market sell:", e)
                
        elif sell_orders:
            sell_orders.sort(key=lambda x: x['price'])
            bot_sell = sell_orders[0]
            print(f"Bottom sell order: {bot_sell['price']} for {bot_sell['amount']}")
            print("Attempting to trigger sell order...")
            try:
                res = await exchange.create_order('BNB/USDT', 'market', 'buy', bot_sell['amount'] * 1.5)
                print("Market buy placed:", res['id'])
            except Exception as e:
                print("Error placing market buy:", e)
        else:
            print("No open orders found.")
            
    finally:
        await exchange.close()

if __name__ == '__main__':
    asyncio.run(main())
