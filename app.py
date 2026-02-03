import os
import logging
import asyncio
import threading
from flask import Flask
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN") 
START_SOL = 10.0

# --- FAKE WEB SERVER (For Koyeb Health Checks) ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "✅ Bot is Alive on Koyeb!"

def run_flask():
    # Koyeb expects the app to listen on port 8000 by default
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

# --- GLOBAL STATE ---
user_wallets = {}  
user_states = {}   
current_trade = {} 

# --- HELPER FUNCTIONS ---
def get_token_info(ca):
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
        data = requests.get(url, timeout=10).json()
        if not data.get('pairs'): return None
        pair = data['pairs'][0]
        return {
            'symbol': pair['baseToken']['symbol'],
            'name': pair['baseToken']['name'],
            'price_usd': float(pair['priceUsd']),
            'price_sol': float(pair['priceNative']),
            'ca': ca
        }
    except:
        return None

def get_sol_price():
    try:
        url = "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
        pairs = requests.get(url, timeout=10).json()['pairs']
        for p in pairs:
            if p['quoteToken']['symbol'] in ['USDC', 'USDT']:
                return float(p['priceUsd'])
        return float(pairs[0]['priceUsd'])
    except:
        return 0.0

# --- BOT HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    if uid not in user_wallets: user_wallets[uid] = {'sol': START_SOL, 'tokens': {}}
    await show_main_menu(update, uid)

async def show_main_menu(update, uid):
    keyboard = [
        [InlineKeyboardButton("🟢 Buy", callback_data='buy_step1'), InlineKeyboardButton("🔴 Sell", callback_data='sell_menu')],
        [InlineKeyboardButton("💼 Positions", callback_data='portfolio'), InlineKeyboardButton("⏱ Limit Orders", callback_data='limit_menu')],
        [InlineKeyboardButton("📅 DCA Orders", callback_data='dca_menu'), InlineKeyboardButton("👥 Copy Trade", callback_data='copy_menu')],
        [InlineKeyboardButton("🔄 Refresh / Start", callback_data='main_menu')]
    ]
    if uid not in user_wallets: user_wallets[uid] = {'sol': START_SOL, 'tokens': {}}
    bal = user_wallets[uid]['sol']
    sol_price = get_sol_price()
    usd_val = bal * sol_price
    text = f"⚡ **Solana Paper Terminal**\n💳 **Wallet:** `{uid}`\n💰 **Balance:** {bal:.4f} SOL (`${usd_val:,.2f}`)"
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        try: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except: pass 

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    await query.answer()
    if uid not in user_wallets: user_wallets[uid] = {'sol': START_SOL, 'tokens': {}}

    if query.data == 'main_menu': await show_main_menu(update, uid)
    elif query.data == 'portfolio':
        wallet = user_wallets[uid]
        sol_price = get_sol_price()
        total_val = wallet['sol'] * sol_price
        msg = f"💼 **Your Positions**\n🪙 **SOL:** {wallet['sol']:.4f} (`${wallet['sol']*sol_price:,.2f}`)\n"
        if wallet['tokens']:
            msg += "\n──────────────\n"
            for sym, data in wallet['tokens'].items():
                live = get_token_info(data['ca'])
                if live:
                    val = data['qty'] * live['price_usd']
                    total_val += val
                    msg += f"**{sym}**\nqty: {data['qty']:,.2f} • val: `${val:,.2f}`\n"
        msg += f"\n──────────────\n🚀 **Total Net Worth:** `${total_val:,.2f}`"
        kb = [[InlineKeyboardButton("🔙 Back to Home", callback_data='main_menu')]]
        await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif query.data == 'buy_step1':
        user_states[uid] = 'WAITING_FOR_CA'
        kb = [[InlineKeyboardButton("🔙 Cancel", callback_data='main_menu')]]
        await query.edit_message_text("🟢 **Buy Token**\n\nPaste the **Contract Address (CA)** below:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif query.data == 'sell_menu':
        tokens = user_wallets[uid]['tokens']
        if not tokens:
            kb = [[InlineKeyboardButton("🔙 Back", callback_data='main_menu')]]
            await query.edit_message_text("🤷‍♂️ **No positions.**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            return
        kb = []
        row = []
        for sym in tokens:
            row.append(InlineKeyboardButton(f"Sell {sym}", callback_data=f'sell_select_{sym}'))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("🔙 Back", callback_data='main_menu')])
        await query.edit_message_text("🔴 **Select Position to Sell:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif query.data in ['limit_menu', 'dca_menu', 'copy_menu']:
        kb = [[InlineKeyboardButton("🔙 Back", callback_data='main_menu')]]
        await query.edit_message_text(f"🚧 **Feature coming soon!**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif query.data.startswith('sell_select_'):
        symbol = query.data.replace('sell_select_', '')
        current_trade[uid] = {'selling_symbol': symbol}
        kb = [
            [InlineKeyboardButton("50%", callback_data=f'sell_exec_50'), InlineKeyboardButton("100%", callback_data=f'sell_exec_100')],
            [InlineKeyboardButton("🔙 Cancel", callback_data='sell_menu')]
        ]
        await query.edit_message_text(f"🔴 **Selling {symbol}**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

    elif query.data.startswith('sell_exec_'):
        percent = int(query.data.replace('sell_exec_', ''))
        symbol = current_trade[uid]['selling_symbol']
        wallet = user_wallets[uid]
        if symbol in wallet['tokens']:
            token_data = wallet['tokens'][symbol]
            live_info = get_token_info(token_data['ca'])
            if live_info:
                qty_to_sell = token_data['qty'] * (percent / 100)
                sol_received = qty_to_sell * live_info['price_sol']
                wallet['sol'] += sol_received
                token_data['qty'] -= qty_to_sell
                if token_data['qty'] <= 0 or percent == 100: del wallet['tokens'][symbol]
                kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data='main_menu')]]
                await query.edit_message_text(f"✅ **SOLD!**\n\n🔻 Sold: `{qty_to_sell:,.2f} {symbol}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    state = user_states.get(uid)

    if state == 'WAITING_FOR_CA':
        token = get_token_info(text)
        if not token:
            await update.message.reply_text("❌ **Token not found.**")
            return
        current_trade[uid] = {'ca': text, 'token': token}
        user_states[uid] = 'WAITING_FOR_AMOUNT'
        await update.message.reply_text(f"✅ **Found:** {token['symbol']}\n💲 Price: `${token['price_usd']:.6f}`\n\nEnter SOL Amount:")

    elif state == 'WAITING_FOR_AMOUNT':
        try:
            amount = float(text)
            wallet = user_wallets[uid]
            if amount > wallet['sol']:
                await update.message.reply_text("❌ **Insufficient SOL.**")
                return
            trade_data = current_trade[uid]
            token_info = trade_data['token']
            tokens_received = amount / token_info['price_sol']
            wallet['sol'] -= amount
            sym = token_info['symbol']
            if sym not in wallet['tokens']: wallet['tokens'][sym] = {'qty': 0, 'ca': trade_data['ca']}
            wallet['tokens'][sym]['qty'] += tokens_received
            user_states[uid] = None
            kb = [[InlineKeyboardButton("💼 View Positions", callback_data='portfolio')]]
            await update.message.reply_text(f"✅ **BOUGHT!**\n🔫 `{tokens_received:,.2f} {sym}`", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        except:
            await update.message.reply_text("❌ Invalid number.")

# --- LAUNCHER ---
if __name__ == '__main__':
    # 1. Start Bot in Background Thread
    def run_bot_loop():
        async def runner():
            if not BOT_TOKEN:
                print("❌ BOT_TOKEN missing")
                return
            app = ApplicationBuilder().token(BOT_TOKEN).build()
            app.add_handler(CommandHandler('start', start))
            app.add_handler(CallbackQueryHandler(menu_handler))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
            print("🤖 Bot Started!")
            await app.updater.start_polling(drop_pending_updates=True)
            while True: await asyncio.sleep(3600)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner())

    threading.Thread(target=run_bot_loop, daemon=True).start()

    # 2. Start Fake Web Server (Main Thread)
    print("🌍 Starting Web Server on Port 8000...")
    run_flask()
