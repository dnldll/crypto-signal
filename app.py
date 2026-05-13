from flask import Flask, jsonify, render_template
import requests
import pandas as pd
import numpy as np
from datetime import datetime

app = Flask(__name__)

BINANCE_BASE = "https://api.binance.com/api/v3"
PAIRS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
TIMEFRAMES = {"1h": "1h", "4h": "4h", "1d": "1d"}


# ── Indicadores manuais (sem pandas-ta) ──

def calc_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def calc_sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length).mean()

def calc_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_loss = loss.ewm(com=length - 1, min_periods=length).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def fetch_klines(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    url = f"{BINANCE_BASE}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    raw = resp.json()
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df[["open_time", "open", "high", "low", "close", "volume"]]


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = calc_rsi(df["close"], 14)
    df["ema9"] = calc_ema(df["close"], 9)
    df["ema21"] = calc_ema(df["close"], 21)
    df["ema50"] = calc_ema(df["close"], 50)
    df["ema200"] = calc_ema(df["close"], 200)
    df["macd"], df["macd_signal"], df["macd_hist"] = calc_macd(df["close"])
    df["vol_ma20"] = calc_sma(df["volume"], 20)
    return df


def generate_signal(df: pd.DataFrame) -> dict:
    row = df.iloc[-1]
    prev = df.iloc[-2]
    score = 0
    reasons = []
    warnings = []

    rsi = row["rsi"]
    if rsi < 30:
        score += 3
        reasons.append(f"RSI em zona de sobrevenda extrema ({rsi:.1f})")
    elif rsi < 40:
        score += 2
        reasons.append(f"RSI em zona de sobrevenda ({rsi:.1f})")
    elif rsi < 50:
        score += 1
        reasons.append(f"RSI neutro-baixo ({rsi:.1f})")
    elif rsi > 70:
        score -= 2
        warnings.append(f"RSI sobrecomprado ({rsi:.1f}) — evitar entrada")

    price = row["close"]
    if price > row["ema200"]:
        score += 1
        reasons.append("Preço acima da EMA 200 (tendência de alta)")
    else:
        score -= 1
        warnings.append("Preço abaixo da EMA 200 (tendência de baixa)")

    if row["ema9"] > row["ema21"] and prev["ema9"] <= prev["ema21"]:
        score += 2
        reasons.append("Cruzamento de alta: EMA 9 cruzou acima da EMA 21")
    elif row["ema9"] > row["ema21"]:
        score += 1
        reasons.append("EMA 9 acima da EMA 21 (momentum positivo)")
    else:
        score -= 1
        warnings.append("EMA 9 abaixo da EMA 21 (momentum negativo)")

    if price > row["ema50"] and prev["close"] <= prev["ema50"]:
        score += 2
        reasons.append("Rompimento acima da EMA 50")

    macd = row["macd"]
    macd_sig = row["macd_signal"]
    macd_hist = row["macd_hist"]
    prev_hist = prev["macd_hist"]

    if macd > macd_sig and prev["macd"] <= prev["macd_signal"]:
        score += 2
        reasons.append("Cruzamento de alta no MACD")
    elif macd > macd_sig:
        score += 1
        reasons.append("MACD positivo (bull)")
    else:
        score -= 1
        warnings.append("MACD negativo (bear)")

    if macd_hist > 0 and prev_hist < 0:
        score += 1
        reasons.append("Histograma MACD virou positivo")
    elif macd_hist > prev_hist and macd_hist < 0:
        score += 1
        reasons.append("Histograma MACD em recuperação")

    vol = row["volume"]
    vol_ma = row["vol_ma20"]
    if vol > vol_ma * 1.5:
        score += 1
        reasons.append(f"Volume {vol/vol_ma:.1f}x acima da média — confirmação forte")
    elif vol > vol_ma:
        score += 0.5
        reasons.append("Volume acima da média")

    if score >= 6:
        signal = "FORTE COMPRA"
    elif score >= 3:
        signal = "COMPRA"
    elif score >= 1:
        signal = "NEUTRO"
    elif score >= -1:
        signal = "AGUARDAR"
    else:
        signal = "NÃO COMPRAR"

    return {
        "signal": signal,
        "score": round(score, 1),
        "rsi": round(rsi, 2),
        "price": round(price, 2),
        "ema9": round(row["ema9"], 2),
        "ema21": round(row["ema21"], 2),
        "ema50": round(row["ema50"], 2),
        "ema200": round(row["ema200"], 2),
        "macd": round(macd, 4),
        "macd_signal": round(macd_sig, 4),
        "volume": round(vol, 2),
        "vol_ma20": round(vol_ma, 2),
        "reasons": reasons,
        "warnings": warnings,
    }


def build_chart_data(df: pd.DataFrame) -> list:
    candles = []
    for _, row in df.tail(60).iterrows():
        def safe(v):
            return round(float(v), 6) if pd.notna(v) and not np.isinf(v) else None
        candles.append({
            "time": int(row["open_time"].timestamp()),
            "open": safe(row["open"]),
            "high": safe(row["high"]),
            "low": safe(row["low"]),
            "close": safe(row["close"]),
            "volume": safe(row["volume"]),
            "ema9": safe(row["ema9"]),
            "ema21": safe(row["ema21"]),
            "ema50": safe(row["ema50"]),
            "ema200": safe(row["ema200"]),
            "macd": safe(row["macd"]),
            "macd_signal": safe(row["macd_signal"]),
            "macd_hist": safe(row["macd_hist"]),
            "rsi": safe(row["rsi"]),
        })
    return candles


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analysis/<timeframe>")
def analysis(timeframe):
    if timeframe not in TIMEFRAMES:
        return jsonify({"error": "Timeframe inválido"}), 400

    result = {
        "timeframe": timeframe,
        "updated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "assets": {}
    }

    for asset, symbol in PAIRS.items():
        try:
            df = fetch_klines(symbol, TIMEFRAMES[timeframe], limit=250)
            df = compute_indicators(df)
            result["assets"][asset] = {
                "symbol": symbol,
                "signal": generate_signal(df),
                "chart": build_chart_data(df),
            }
        except Exception as e:
            result["assets"][asset] = {"error": str(e)}

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
