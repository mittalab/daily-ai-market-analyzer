from datetime import date
import pandas as pd
from database.client import get_client
from indicators.technical import compute_stock_indicators

def validate_indicators_vs_manual(symbol: str, date_str: str | None = None) -> dict:
    """
    Computes system indicators for a given symbol up to a specific date
    and formats them for manual comparison against TradingView.
    """
    symbol = symbol.strip().upper()
    
    if not date_str:
        # Default to the latest date available in price history
        resp = (
            get_client()
            .table("price_history")
            .select("date")
            .eq("symbol", symbol)
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            date_str = resp.data[0]["date"]
        else:
            date_str = str(date.today())

    # Fetch 250 rows up to date_str to ensure indicators have enough lookback
    resp = (
        get_client()
        .table("price_history")
        .select("date,open,high,low,close,volume")
        .eq("symbol", symbol)
        .lte("date", date_str)
        .order("date", desc=True)
        .limit(250)
        .execute()
    )
    rows = list(reversed(resp.data))
    
    if not rows:
        raise ValueError(f"No price history found for {symbol} up to {date_str}")
        
    df = pd.DataFrame(rows)
    
    # Compute indicators
    res = compute_stock_indicators(df)
    
    # Map to the specific 8 indicators requested by the spec
    indicators_map = {
        "EMA20":          res.get("ema20"),
        "EMA50":          res.get("ema50"),
        "EMA180":         res.get("ema180"),
        "RSI14":          res.get("rsi14"),
        "MACD_LINE":      res.get("macd_line"),
        "MACD_SIGNAL":    res.get("macd_signal"),
        "MACD_HISTOGRAM": res.get("macd_histogram"),
        "ATR14":          res.get("atr14")
    }
    
    formatted_indicators = {}
    for key, val in indicators_map.items():
        formatted_indicators[key] = {
            "system": val,
            "tradingview": None,
            "diff_pct": None
        }
        
    return {
        "symbol": symbol,
        "date": date_str,
        "indicators": formatted_indicators,
        "computation_method": res.get("computation_method"),
        "warnings": res.get("warnings"),
        "note": "Enter TradingView values manually for comparison"
    }
