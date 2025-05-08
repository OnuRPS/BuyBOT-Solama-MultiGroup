import os
import asyncio
import aiohttp
import json
from telegram import Bot
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from dotenv import load_dotenv

load_dotenv()

SOLANA_RPC = "https://rpc.helius.xyz/?api-key=4db5289f-5c8e-4e55-8478-dd1e73ee2eee"
MONITORED_WALLET = "D6FDaJjvRwBSm54rBP7ViRbF7KQxzpNw35TFWNWwpsbB"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_IDS = os.getenv("CHAT_IDS", "").split(",")
GIF_URL = os.getenv("GIF_URL")
WSOL_MINT = "So11111111111111111111111111111111111111112"
SOFTCAP_SOL = 50

bot = Bot(token=TELEGRAM_TOKEN)
last_sig = None
initial_run = True

async def get_sol_price():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd") as r:
                data = await r.json()
                return float(data["solana"]["usd"])
    except:
        return 0.0

async def get_wallet_balance():
    try:
        client = AsyncClient(SOLANA_RPC)
        wallet = Pubkey.from_string(MONITORED_WALLET)

        resp = await client.get_token_accounts_by_owner(
            wallet,
            Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
           )


        sol_total = 0.0
        for token_acc in resp.value:
            info = token_acc.account.data.parsed["info"]
            mint = info["mint"]
            if mint == WSOL_MINT:
                amount = float(info["tokenAmount"]["amount"]) / 1e9
                sol_total += amount

        await client.close()
        return sol_total
    except Exception as e:
        print(f"\u26a0\ufe0f Error getting WSOL balance (manual fallback): {e}")
        return 0.0

def generate_bullets(sol_amount):
    bullets_count = int(sol_amount / 0.1)
    return '🥇' * min(bullets_count, 100)

def send_telegram_message(text, gif_url=None):
    for chat_id in CHAT_IDS:
        try:
            if gif_url:
                bot.send_animation(chat_id=chat_id.strip(), animation=gif_url, caption=text, parse_mode="Markdown")
            else:
                bot.send_message(chat_id=chat_id.strip(), text=text, parse_mode="Markdown")
            print(f"✅ Message sent to chat {chat_id}")
        except Exception as e:
            print(f"❌ Failed to send message to {chat_id}: {e}")

def test_telegram_message():
    print("🧪 Sending test message to Telegram...")
    text = (
        "✅ Bot started and connected successfully!\n\n"
        "🟢 Solana BuyDetector™ is live.\n"
        "🔍 Waiting for first transaction..."
    )
    send_telegram_message(text, gif_url=GIF_URL)

async def check_transactions():
    global last_sig, initial_run
    client = AsyncClient(SOLANA_RPC)
    pubkey = Pubkey.from_string(MONITORED_WALLET)
    print("🟢 Solana BuyDetector™ activated.")

    while True:
        try:
            sigs_resp = await client.get_signatures_for_address(pubkey, limit=1)
            sig_info = sigs_resp.value[0]
            sig = sig_info.signature

            if sig != last_sig or initial_run:
                initial_run = False
                print(f"🔍 Checking TX: {sig}")
                tx_resp = await client.get_transaction(sig, encoding="jsonParsed", max_supported_transaction_version=0)
                if not tx_resp.value:
                    await asyncio.sleep(10)
                    continue

                tx_json = json.loads(tx_resp.value.to_json())
                transaction = tx_json.get("transaction", {})
                msg = transaction.get("message", {})
                instructions = msg.get("instructions", [])
                inner = tx_json.get("meta", {}).get("innerInstructions", [])
                meta = tx_json.get("meta", {})

                sol_amount = 0
                from_addr = "Unknown"
                to_addr = MONITORED_WALLET

                for instr_list in [instructions] + [x.get("instructions", []) for x in inner if isinstance(x, dict)]:
                    for instr in instr_list:
                        if instr.get("program") == "spl-token":
                            parsed = instr.get("parsed", {})
                            if parsed.get("type") == "transfer":
                                info = parsed.get("info", {})
                                if info.get("mint") == WSOL_MINT and info.get("destination") == MONITORED_WALLET:
                                    amount = int(info.get("amount", 0))
                                    sol_amount = amount / 1e9
                                    from_addr = info.get("source", "Unknown")
                                    to_addr = info.get("destination", MONITORED_WALLET)
                                    print(f"✅ WSOL transfer detected: {sol_amount} SOL")
                                    break
                    if sol_amount > 0:
                        break

                if sol_amount == 0:
                    for b in meta.get("postTokenBalances", []):
                        if b.get("owner") == MONITORED_WALLET and b.get("mint") == WSOL_MINT:
                            pre_amt = next((x for x in meta.get("preTokenBalances", []) if x.get("accountIndex") == b.get("accountIndex")), {})
                            old_val = float(pre_amt.get("uiTokenAmount", {}).get("uiAmount", 0))
                            new_val = float(b.get("uiTokenAmount", {}).get("uiAmount", 0))
                            diff = new_val - old_val
                            if diff > 0:
                                sol_amount = diff
                                print(f"✅ WSOL delta detected: {sol_amount} SOL")
                                break

                if sol_amount > 0:
                    sol_price = await get_sol_price()
                    usd_value = sol_amount * sol_price
                    bullets = generate_bullets(sol_amount)
                    wallet_balance = await get_wallet_balance()
                    wallet_usd = wallet_balance * sol_price

                    emoji = "💸" if usd_value < 10 else "🚀" if usd_value < 100 else "🔥"

                    progress_pct = min(wallet_balance / SOFTCAP_SOL * 100, 100)
                    filled = int(progress_pct // 5)
                    progress_bar = f"[{'█' * filled}{'░' * (20 - filled)}] {progress_pct:.1f}%"

                    softcap_status = f"🔴 *SoftCap:* {SOFTCAP_SOL} SOL"
                    if wallet_balance >= SOFTCAP_SOL:
                        softcap_status += "\n🥳 ✅ *SoftCap Passed!*"

                    msg_text = (
                        f"{emoji} *New $BabyGOV contribution detected!*\n\n"
                        f"🔁 *From:* `{from_addr}`\n"
                        f"📥 *To:* `{to_addr}`\n"
                        f"🟨 *Amount Received:*\n"
                        f"┌────────────────────────────┐\n"
                        f"│  {sol_amount:.4f} SOL (~${usd_value:,.2f})  │\n"
                        f"└────────────────────────────┘\n"
                        f"{bullets}\n\n"
                        f"💼 *Raised:*\n"
                        f"┌────────────────────────────┐\n"
                        f"│  {wallet_balance:.4f} SOL (~${wallet_usd:,.2f})  │\n"
                        f"└────────────────────────────┘\n\n"
                        f"{softcap_status}\n"
                        f"📊 *Progress:*\n{progress_bar}\n\n"
                        f"🔗 [View on Solscan](https://solscan.io/tx/{sig})\n\n"
                        f"───────────────\n"
                        f"🤖 *BuyDetector™ Solana*\n"
                        f"🔧 by [ReactLAB](https://t.me/PandaBaoOfficial)"
                    )

                    send_telegram_message(msg_text, gif_url=GIF_URL)
                    print(f"📬 TX posted: {sig}")
                    last_sig = sig
                else:
                    print("⚠️ No SOL or WSOL received in this transaction.")

        except Exception as e:
            print(f"⚠️ Outer error: {e}")

        await asyncio.sleep(10)

if __name__ == "__main__":
    test_telegram_message()
    asyncio.run(check_transactions())
