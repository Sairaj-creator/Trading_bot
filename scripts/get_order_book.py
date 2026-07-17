import asyncio
import ccxt.async_support as ccxt

async def main():
    e = ccxt.binance()
    e.set_sandbox_mode(True)
    ob = await e.fetch_order_book('BNB/USDT', limit=20)
    print("Bids (buyers):")
    for b in ob['bids']:
        print(f"Price: {b[0]}, Amount: {b[1]}")
    print("\nAsks (sellers):")
    for a in ob['asks']:
        print(f"Price: {a[0]}, Amount: {a[1]}")
    await e.close()

asyncio.run(main())
