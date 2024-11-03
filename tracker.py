import os
from flask import Flask, request, jsonify
import requests
from datetime import datetime
import json
from sqlalchemy import create_engine, Column, String, Integer, ForeignKey, MetaData
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

# Database setup
DATABASE_URL = os.getenv('DATABASE_URL')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
Base = declarative_base() #base class for all models
INSIDERS_DISCORD = os.getenv('INSIDERS_DISCORD') #channels divided according to category
ALPHAS_DISCORD = os.getenv('ALPHAS_DISCORD')
KOLS_DISCORD = os.getenv('KOLS_DISCORD')
CABALS_DISCORD = os.getenv('CABALS_DISCORD')
GENERAL_DISCORD = os.getenv('GENERAL_DISCORD')

class Wallet(Base):
    __tablename__ = 'wallets'

    wallet_address = Column(String, primary_key=True)
    name = Column(String)
    category = Column(String)

class WalletToken(Base):
    __tablename__ = 'wallet_tokens'

    wallet_address = Column(String, ForeignKey('wallets.wallet_address'), primary_key=True)
    mint_address = Column(String, primary_key=True)
    tx_count = Column(Integer)

# Create tables in correct order
Base.metadata.create_all(engine)

# Function to get wallets from the database
def get_wallets():
    session = Session()
    wallets = session.query(Wallet.wallet_address).all()
    session.close()
    return [wallet[0] for wallet in wallets]

def get_names():
    session = Session()
    names = session.query(Wallet.name).all()
    session.close()
    return [name[0] for name in names]

def get_categories():
    session = Session()
    categories = session.query(Wallet.category).distinct().all()
    session.close()
    return [category[0] for category in categories]

# Add this new function to get wallet details
def get_wallet_details(wallet_address):
    session = Session()
    wallet = session.query(Wallet).filter(Wallet.wallet_address == wallet_address).first()
    session.close()
    if wallet:
        return {'name': wallet.name, 'category': wallet.category}
    return {'name': 'Unknown', 'category': 'Unknown'}

# this function checks with our databse to get the token trades for a wallet and if its not there we count that as new token trade and other sort of things like adding tx count
def get_and_update_token_history(wallet_address, mint_address):
    session = Session()
    try:
        # Get existing record
        history = session.query(WalletToken).filter(
            WalletToken.wallet_address == wallet_address,
            WalletToken.mint_address == mint_address
        ).first()
        
        if history:
            # Update existing record
            history.tx_count += 1
            session.commit()
            return {'exists': True, 'tx_count': history.tx_count}
        else:
            # Create new record with tx_count = 1
            new_history = WalletToken(
                wallet_address=wallet_address,
                mint_address=mint_address,
                tx_count=1
            )
            session.add(new_history)
            session.commit()
            return {'exists': False, 'tx_count': 1}
    except Exception as e:
        session.rollback()
        print(f"Error updating token history: {e}")
        return {'exists': False, 'tx_count': 0}
    finally:
        session.close()

# this parse the transaction we get from helius cuz its not that parsed well for us
def format_transaction(transaction):
    tx_type = transaction.get('type', 'UNKNOWN')
    description = transaction.get('description', '')
    signature = transaction.get('signature', '')

    wallets = get_wallets()
    results = []
    parsed_type = 'Native Transfer'
    mint = 'N/A'
    tokens = 0
    trade = 'No trade'
    wallet = None
    wallet_details = {'name': 'Unknown', 'category': 'Unknown'}
    
    # First pass: Find our wallet and its token changes
    for account in transaction.get('accountData', []):
        current_wallet = account.get('account')
        if current_wallet in wallets:
            wallet = account.get('account')
            wallet_details = get_wallet_details(wallet)
            balance_change = account.get('nativeBalanceChange', 0)
            
            # Handle SOL transfers
            if balance_change > 0:
                wallet_sol = f"{wallet_details['name']}({wallet}) got {balance_change / 1e9:.9f} SOL"
                trade = 'Receive'
            elif balance_change < 0:
                wallet_sol = f"{wallet_details['name']}({wallet}) used {abs(balance_change) / 1e9:.9f} SOL"
                trade = 'Send'
            else:
                wallet_sol = f"No SOL balance change for {wallet_details['name']}({wallet})"
            
            if balance_change != 0:
                results.append(wallet_sol)

    # Second pass: Look for token transfers in all accounts
    for account in transaction.get('accountData', []):
        token_changes = account.get('tokenBalanceChanges', [])
        for token_change in token_changes:
            user_account = token_change.get('userAccount')
            if user_account in wallets:
                wallet = user_account
                wallet_details = get_wallet_details(wallet)
                mint = token_change.get('mint', 'Unknown')
                raw_amount = token_change.get('rawTokenAmount', {})
                decimals = raw_amount.get('decimals', 0)
                token_amount = int(raw_amount.get('tokenAmount', '0')) / (10 ** decimals)
                
                if token_amount < 0:
                    wallet_token = f"{wallet_details['name']}({wallet}) sold {abs(token_amount)} {mint} tokens"
                    trade = 'Sell'
                elif token_amount > 0:
                    wallet_token = f"{wallet_details['name']}({wallet}) bought {token_amount} {mint} tokens"
                    trade = 'Buy'
                else:
                    wallet_token = f"No token balance change for {wallet_details['name']}({wallet})"
                
                parsed_type = 'SWAP'
                tokens = abs(token_amount)
                results.append(wallet_token)

    if not results:
        results.append('No relevant transaction data found')

    # Initialize Connection and tx_count at the start
    Connection = "No"
    tx_count = 0
    
    if mint != 'N/A':
        history_result = get_and_update_token_history(wallet, mint)
        Connection = "Yes" if history_result['exists'] else "No"
        tx_count = history_result['tx_count']

    json_message = {
        "wallet": wallet or "N/A",
        "category": wallet_details["category"] or "N/A",
        "name": wallet_details["name"] or "N/A",
        "type": tx_type or "N/A",
        "parsed_type": parsed_type or "N/A",
        "trade": trade or "N/A",
        "mint": mint or "N/A",
        "tx_no": tx_count or "N/A",
        "connection": Connection or "N/A",
        "token_amount": tokens or "N/A"
    }

    message = (f'Wallet: {wallet}\n'
               f'Category: {wallet_details["category"]}  Name: {wallet_details["name"]}\n'
               f'Type: {tx_type} Parsed Type: {parsed_type} \n'
               f'Trade: {trade} Token Amount: {tokens}\n'
               f'Mint: {mint}\n'
               f'GMGN: https://gmgn.ai/sol/token/{mint}\n'
               f'Connection: {Connection}\n'
               f'Tx_no: {tx_count}\n'
               f'Result: {" | ".join(results)}\n'
               f'Description: {description}\n'
               f'Timestamp: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
               f'Signature: [Solscan](https://solscan.io/tx/{signature}) [Beach](https://solanabeach.io/transaction/{signature}) [SolanaFm](https://solana.fm/tx/{signature}?cluster=mainnet-alpha)')
    return message, json_message

class GMGNTrader:
    def generate_trade_command(self, json_message):
        try:
            if (json_message["tx_count"] == 1 and 
                json_message["trade"] == "Buy" and 
                json_message["mint"] != "N/A" and 
                json_message["connection"] == "No"):
                
                return f"/buy {json_message['mint']} 0.01"
                
            elif (json_message["tx_count"] <= 2 and 
                  json_message["trade"] == "Sell" and 
                  json_message["mint"] != "N/A" and 
                  json_message["connection"] == "Yes"):
                
                return f"/sell {json_message['mint']} 100%"
            
            return None
            
        except KeyError as e:
            print(f"Error generating trade command: Missing key {e}")
            return None

@app.route('/', methods=['POST'])
def handle_request():
    if request.method == 'POST':
        request_body = request.json
        print(request_body)

        # Get wallets from the database
        wallets = get_wallets()

        messages = []
        for transaction in request_body:
            raw_data = json.dumps(transaction, indent=2)
            formatted_message = format_transaction(transaction)

            # Print formatted message to terminal
            print(f"\nFormatted Message:\n{formatted_message}")
            
            # Send the formatted message to both Telegram and Discord
            send_to_discord(formatted_message)

        return jsonify({'message': 'Logged and forwarded POST request body.'}), 200
    else:
        return jsonify({'message': 'Method not allowed.'}), 405

# this is to send the message to discord
def send_to_discord(message):
    try:
        # Extract information from the message
        lines = message.split('\n')
        info = {}
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                info[key.strip()] = value.strip()

        # Create a color based on the trade type
        color = 0x00ff00 if info.get('Trade') == 'Buy' else 0xff0000 if info.get('Trade') == 'Sell' else 0x808080

        # Create the embed with all information
        embed = {
            "embeds": [{
                "title": f"🔔 New {info.get('Parsed Type', 'Transaction')}",
                "description": f"**Wallet**: {info.get('Wallet', 'Unknown')}\n**Category**: {info.get('Category', 'Unknown')}",
                "color": color,
                "fields": [
                    {
                        "name": "Transaction Info",
                        "value": (
                            f"**Type**: {info.get('Type', 'Unknown')}\n"
                            f"**Trade**: {info.get('Trade', 'No trade')}\n"
                            f"**Token Amount**: {info.get('Token Amount', '0')}"
                        ),
                        "inline": True
                    },
                    {
                        "name": "Token Info",
                        "value": (
                            f"**Mint**: {info.get('Mint', 'N/A')}\n"
                            f"**Connection**: {info.get('Connection', 'No')}\n"
                            f"**Tx Count**: {info.get('Tx_no', '0')}"
                        ),
                        "inline": True
                    }
                ]
            }]
        }

        # Add result field if it exists
        if 'Result' in info:
            embed["embeds"][0]["fields"].append({
                "name": "Details",
                "value": info['Result'],
                "inline": False
            })

        # Add description if it exists and isn't empty
        if info.get('Description') and info['Description'].strip():
            embed["embeds"][0]["fields"].append({
                "name": "Description",
                "value": info['Description'],
                "inline": False
            })

        # Add signature links
        if 'Signature' in info:
            sig = info['Signature'].split('/')[-1] if 'https://' in info['Signature'] else info['Signature']
            embed["embeds"][0]["fields"].append({
                "name": "Transaction Links",
                "value": f"[Solscan](https://solscan.io/tx/{sig}) | [SolanaFM](https://solana.fm/tx/{sig}) | [Beach](https://solanabeach.io/transaction/{sig})",
                "inline": False
            })

        # Add GMGN link if mint exists and isn't N/A
        if info.get('Mint') and info.get('Mint') != 'N/A':
            embed["embeds"][0]["fields"].append({
                "name": "Token Links",
                "value": f"[GMGN](https://gmgn.ai/sol/token/{info['Mint']})",
                "inline": False
            })

        # Extract the category and determine webhook
        category = info.get('Category', '')
        webhook_url = None
        if 'Insider' in category:
            webhook_url = INSIDERS_DISCORD
            print("Sending to Insiders webhook")
        elif 'Alpha' in category:
            webhook_url = ALPHAS_DISCORD
            print("Sending to Alphas webhook")
        elif 'KOL' in category:
            webhook_url = KOLS_DISCORD
            print("Sending to KOLs webhook")
        elif 'Cabal' in category:
            webhook_url = CABALS_DISCORD
            print("Sending to Cabals webhook")

        # Send to specific webhook if category matches
        if webhook_url:
            try:
                response = requests.post(webhook_url, json=embed)
                response.raise_for_status()
                print(f"Successfully sent to {category} webhook")
            except Exception as e:
                print(f"Error sending to {category} webhook: {str(e)}")

        # Always send to general webhook
        try:
            response = requests.post(GENERAL_DISCORD, json=embed)
            response.raise_for_status()
            print("Successfully sent to general webhook")
        except Exception as e:
            print(f"Error sending to general webhook: {str(e)}")

    except Exception as e:
        print(f"Error in send_to_discord: {str(e)}")
        print("Original message:", message)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
