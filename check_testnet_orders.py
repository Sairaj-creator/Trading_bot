import asyncio
import ccxt.async_support as ccxt

async def main():
    exchange = ccxt.binance({
        'apiKey': 'REDACTED',
        'secret': 'yocNzUdO6WMrsvZ4OmbJWbaz7BfpGHrXlwyQD13Qyat07bAuwo3vNU0IJJaPHdqW',
        'enableRateLimit': True,
    })
    # Connect to Binance Testnet
    exchange.set_sandbox_mode(True)
    
    try:
        ticker = await exchange.fetch_ticker('BNB/USDT')
        print(f"Current Market Price of BNB: ${ticker['last']}")
        
        orders = await exchange.fetch_open_orders('BNB/USDT')
        print(f"Total open limit orders waiting: {len(orders)}")
        
        if orders:
            # Sort orders by price descending (highest buy price first)
            buy_orders = sorted([o for o in orders if o['side'] == 'buy'], key=lambda x: x['price'], reverse=True)
            if buy_orders:
                closest_buy = buy_orders[0]
                distance = ticker['last'] - closest_buy['price']
                print(f"The closest order is a buy at: ${closest_buy['price']}")
                print(f"The market price needs to drop by ${distance:.2f} for this order to execute and trigger the Telegram message.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
