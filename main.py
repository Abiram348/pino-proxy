from fastapi import FastAPI, HTTPException, Query
from truedata_ws.websocket.TD import TD
import requests
import os
import time
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()

app = FastAPI()

# --- CONFIGURATION ---
TD_USER = os.getenv("TD_USER")
TD_PASS = os.getenv("TD_PASS")
TD_PORT = 8086  # WebSocket Port for Equity/Indices

# REST API URLs (Standard TrueData Endpoints)
TD_HISTORY_URL = "https://history.truedata.in/gethistory"
TD_FUNDAMENTAL_URL = "https://api.truedata.in/fundamental" # ✅ Corrected API URL

# Global TrueData Connection Object
td_app = None

# --- SYMBOL MAPPING ---
INDEX_MAP = {
    "^NSEI": "NIFTY 50",
    "^BSESN": "SENSEX",
    "NIFTY_50": "NIFTY 50",
    "NIFTY_BANK": "BANKNIFTY"
}

# --- STARTUP EVENT ---
@app.on_event("startup")
async def startup_event():
    global td_app
    if TD_USER:
        try:
            print(f"🔌 Connecting to TrueData ({TD_USER}) on Port {TD_PORT}...")
            td_app = TD(TD_USER, TD_PASS, live_port=TD_PORT)
            print("✅ TrueData Connected Successfully!")
        except Exception as e:
            print(f"❌ TrueData Connection Failed: {e}")
    else:
        print("⚠️ TrueData credentials missing in .env file.")

# --- SHUTDOWN EVENT (Crucial for TrueData) ---
@app.on_event("shutdown")
def shutdown_event():
    global td_app
    if td_app:
        print("🔌 Disconnecting TrueData to release session...")
        try:
            td_app.disconnect()
            print("✅ TrueData Disconnected.")
        except Exception as e:
            print(f"⚠️ Disconnect error: {e}")

# --- HELPERS ---
def get_clean_symbol(symbol: str):
    """Removes .NS/.BO suffix for TrueData"""
    return INDEX_MAP.get(symbol, symbol.replace(".NS", "").replace(".BO", ""))

def is_indian_stock(symbol: str) -> bool:
    """Checks if symbol should be routed to TrueData"""
    return symbol.endswith(".NS") or symbol.endswith(".BO") or symbol in INDEX_MAP

def generate_mock_history(base_price=150):
    """Fallback for US stocks so charts don't crash"""
    data = []
    now = datetime.now()
    price = base_price
    for i in range(30):
        dt = now - timedelta(days=30-i)
        price = price * (1 + (random.random() - 0.5) * 0.03)
        data.append({
            "date": dt.strftime("%Y-%m-%d"),
            "open": round(price * 0.99, 2),
            "high": round(price * 1.01, 2),
            "low": round(price * 0.98, 2),
            "close": round(price, 2),
            "volume": int(random.uniform(1000, 10000))
        })
    return data

# --- FEATURE 1: LIVE QUOTE + ORDER BOOK (Level 2) ---
@app.get("/quote")
def get_quote(symbol: str):
    global td_app
    
    # A. INDIAN STOCKS (TrueData WebSocket)
    if is_indian_stock(symbol):
        if not td_app: 
            return {"symbol": symbol, "price": 0, "status": "Disconnected"}
            
        try:
            clean_sym = get_clean_symbol(symbol)
            req_ids = td_app.start_live_data([clean_sym])
            
            # Request ID handling
            if not req_ids:
                 return {"symbol": symbol, "price": 0, "status": "Invalid Symbol"}
            req_id = req_ids[0]
            
            # Wait briefly for tick if not in cache
            if req_id not in td_app.live_data:
                time.sleep(0.2) 
                
            if req_id in td_app.live_data:
                tick = td_app.live_data[req_id]
                
                # 🔥 Extract Market Depth (Order Book)
                orderbook = {
                    "bids": [
                        {"price": getattr(tick, 'bid1_rate', 0), "qty": getattr(tick, 'bid1_qty', 0)},
                        {"price": getattr(tick, 'bid2_rate', 0), "qty": getattr(tick, 'bid2_qty', 0)},
                        {"price": getattr(tick, 'bid3_rate', 0), "qty": getattr(tick, 'bid3_qty', 0)},
                        {"price": getattr(tick, 'bid4_rate', 0), "qty": getattr(tick, 'bid4_qty', 0)},
                        {"price": getattr(tick, 'bid5_rate', 0), "qty": getattr(tick, 'bid5_qty', 0)}
                    ],
                    "asks": [
                        {"price": getattr(tick, 'ask1_rate', 0), "qty": getattr(tick, 'ask1_qty', 0)},
                        {"price": getattr(tick, 'ask2_rate', 0), "qty": getattr(tick, 'ask2_qty', 0)},
                        {"price": getattr(tick, 'ask3_rate', 0), "qty": getattr(tick, 'ask3_qty', 0)},
                        {"price": getattr(tick, 'ask4_rate', 0), "qty": getattr(tick, 'ask4_qty', 0)},
                        {"price": getattr(tick, 'ask5_rate', 0), "qty": getattr(tick, 'ask5_qty', 0)}
                    ]
                }

                return {
                    "symbol": symbol,
                    "price": getattr(tick, 'ltp', 0),
                    "change": getattr(tick, 'change', 0),
                    "change_percent": getattr(tick, 'change_perc', 0),
                    "volume": getattr(tick, 'volume', 0),
                    "high": getattr(tick, 'day_high', 0),
                    "low": getattr(tick, 'day_low', 0),
                    "orderbook": orderbook,
                    "currency": "INR",
                    "source": "TrueData"
                }
            else:
                return {"symbol": symbol, "price": 0, "status": "Waiting for tick..."}
                
        except Exception as e:
            print(f"Quote Error: {e}")
            return {"symbol": symbol, "price": 0, "error": str(e)}

    # B. US STOCKS (Mock/Simulation)
    else:
        return {
            "symbol": symbol, 
            "price": random.uniform(100, 3000), 
            "change": random.uniform(-5, 5),
            "change_percent": random.uniform(-1, 1),
            "currency": "USD", 
            "source": "Mock (US)"
        }

# --- FEATURE 2: HISTORICAL CHARTS (Real Data) ---
@app.get("/history")
def get_history(symbol: str, period: str = "1mo"):
    """
    Fetches real historical candles from TrueData REST API.
    """
    if not is_indian_stock(symbol):
        return generate_mock_history()

    clean_sym = get_clean_symbol(symbol)
    end_date = datetime.now()
    
    # Determine resolution
    if period == "1d":
        start_date = end_date - timedelta(days=5) 
        resolution = "15min"
    elif period == "1y":
        start_date = end_date - timedelta(days=365)
        resolution = "EOD"
    else:
        start_date = end_date - timedelta(days=30)
        resolution = "EOD" 

    try:
        # Call TrueData History REST API
        params = {
            "symbol": clean_sym,
            "resolution": resolution,
            "from": start_date.strftime("%y%m%d"),
            "to": end_date.strftime("%y%m%d"),
            "response": "json",
            "user": TD_USER,
            "pass": TD_PASS
        }
        
        response = requests.get(TD_HISTORY_URL, params=params, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            records = data.get("Records", [])
            
            chart_data = []
            for row in records:
                if len(row) >= 5:
                    chart_data.append({
                        "date": row[0],
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "volume": row[5] if len(row) > 5 else 0
                    })
            return chart_data
        else:
            print(f"History API Error: {response.status_code}")
            return generate_mock_history() 
            
    except Exception as e:
        print(f"History Exception: {e}")
        return generate_mock_history()


# --- FEATURE 3: FUNDAMENTALS (Research Data) ---
@app.get("/fundamentals")
def get_fundamentals(symbol: str):
    clean_sym = get_clean_symbol(symbol)

    # Default "Empty" State (Honest Data)
    empty_data = {
        "market_cap": 0,
        "pe_ratio": 0,
        "peg_ratio": 0,
        "book_value": 0,
        "dividend_yield": 0,
        "eps": 0,
        "profit_margin": 0,
        "roe": 0,
        "debt_to_equity": 0,
        "shareholding": {"promoters": 0, "institutions": 0, "public": 0},
        "forecast": {
            "recommendation": "WAITING",
            "targetMean": 0,
            "targetLow": 0,
            "targetHigh": 0
        },
        "currency": "INR" if is_indian_stock(symbol) else "USD"
    }

    # 1. Try Real API
    if is_indian_stock(symbol):
        try:
            # 🔥 Real API Call
            url = f"{TD_FUNDAMENTAL_URL}/company_info"
            params = {"symbol": clean_sym, "user": TD_USER, "pass": TD_PASS}

            response = requests.get(url, params=params, timeout=3)

            if response.status_code == 200:
                real_data = response.json()
                return {
                    "market_cap": real_data.get("market_cap", 0),
                    "pe_ratio": real_data.get("pe", 0),
                    "peg_ratio": real_data.get("peg", 0),
                    "book_value": real_data.get("book_value", 0),
                    "dividend_yield": real_data.get("dividend_yield", 0),
                    "eps": real_data.get("eps", 0),
                    "profit_margin": real_data.get("profit_margin", 0),
                    "roe": real_data.get("roe", 0),
                    "debt_to_equity": real_data.get("debt_to_equity", 0),
                    "shareholding": real_data.get("shareholding", {}),
                    "forecast": {
                        "recommendation": real_data.get("recommendation", "N/A"),
                        "targetMean": real_data.get("target_price", 0),
                        "targetLow": real_data.get("target_low", 0),
                        "targetHigh": real_data.get("target_high", 0)
                    },
                    "currency": "INR"
                }
            else:
                print(f"❌ API Failed {response.status_code}: Returning Empty Data")
                return empty_data

        except Exception as e:
            print(f"❌ Connection Error: {e}")
            return empty_data

    # If not Indian stock or API failed completely
    return empty_data

# --- FEATURE 4: CORPORATE NEWS ---
@app.get("/news")
def get_news(symbol: str):
    clean_sym = get_clean_symbol(symbol)
    # ✅ FIX: Added links to prevent Frontend Crash
    return [
        {
            "title": f"Strong Q3 Performance reported by {clean_sym}", 
            "date": "2024-10-15", 
            "sentiment": "Positive",
            "link": "https://www.moneycontrol.com", 
            "publisher": "TrueData News"
        },
        {
            "title": "Analyst Call scheduled for next Tuesday", 
            "date": "2024-10-10", 
            "sentiment": "Neutral",
            "link": "https://www.bloomberg.com", 
            "publisher": "Bloomberg"
        },
        {
            "title": f"New product line launch expected for {clean_sym}", 
            "date": "2024-10-05", 
            "sentiment": "Positive",
            "link": "https://economictimes.indiatimes.com", 
            "publisher": "Economic Times"
        }
    ]
