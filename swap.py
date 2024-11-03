from tracker import GMGNTrader
import os
from telethon import TelegramClient
import asyncio

api_id = os.getenv("API_ID") #telegram api id using https://my.telegram.org/apps
api_hash = os.getenv("API_HASH") #telegram api hash using https://my.telegram.org/apps
trader = GMGNTrader()
client = TelegramClient('session_name', api_id, api_hash)
bot_username = '@GMGN_sol_bot'

async def send_tx(json_message):
    await client.start()

    swap = trader.generate_trade_command(json_message)
    try:
        if swap:
            await client.send_message(bot_username, swap)
            print(f"Sent command: {swap}")
            
            await asyncio.sleep(2) #wait till reply appears
            response = await client.get_messages(bot_username, limit=1)
            if response and response[0]:  #get the reply 
                print(f"Bot response: {response[0].text}")
                return response[0].text
            else:
                print("No response received")
    
    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        await client.disconnect()
