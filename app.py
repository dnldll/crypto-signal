from flask import Flask, jsonify, render_template
import requests
import pandas as pd
import pandas_ta as ta
import numpy as np
from datetime import datetime

app = Flask(__name__)

BINANCE_BASE = "https://api.binance.com/api/v3"
PAIRS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
TIMEFRAMES = {"1h": "1h", "4h": "4h", "1d": "1d"}


def fetch_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
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
    df["rsi"] = ta.rsi(df["close"], length=14)
    df["ema9"] = ta.ema(df["close"], length=9)
    df["ema21"] = ta.ema(df["close"], length=21)
    df["ema50"] = ta.ema(df["close"], length=50)
    df["ema200"] = ta.ema(df["close"], length=200)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    df["macd_hist"] = macd["MACDh_12_26_9"]
    df["vol_ma20"] = ta.sma(df["volume"], length=20)
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
    ema200 = row["ema200"]
    ema50 = row["ema50"]
    ema21 = row["ema21"]
    ema9 = row["ema9"]

    if price > ema200:
        score += 1
        reasons.append("Preço acima da EMA 200 (tendência de alta)")
    else:
        score -= 1
        warnings.append("Preço abaixo da EMA 200 (tendência de baixa)")

    if ema9 > ema21 and prev["ema9"] <= prev["ema21"]:
        score += 2
        reasons.append("Cruzamento de alta: EMA 9 cruzou acima da EMA 21")
    elif ema9 > ema21:
        score += 1
        reasons.append("EMA 9 acima da EMA 21 (momentum positivo)")
    elif ema9 < ema21:
        score -= 1
        warnings.append("EMA 9 abaixo da EMA 21 (momentum negativo)")

    if price > ema50 and prev["close"] <= prev["ema50"]:
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
    elif macd < macd_sig:
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
        color = "#00e676"
        emoji = "🟢🟢"
    elif score >= 3:
        signal = "COMPRA"
        color = "#69f0ae"
        emoji = "🟢"
    elif score >= 1:
        signal = "NEUTRO"
        color = "#ffd740"
        emoji = "🟡"
    elif score >= -1:
        signal = "AGUARDAR"
        color = "#ff9100"
        emoji = "🟠"
    else:
        signal = "NÃO COMPRAR"
        color = "#ff5252"
        emoji = "🔴"

    return {
        "signal": signal,
        "color": color,
        "emoji": emoji,
        "score": round(score, 1),
        "rsi": round(rsi, 2),
        "price": round(price, 2),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "macd": round(macd, 4),
        "macd_signal": round(macd_sig, 4),
        "volume": round(vol, 2),
        "vol_ma20": round(vol_ma, 2),
        "reasons": reasons,
        "warnings": warnings,
    }


def build_chart_data(df: pd.DataFrame) -> list:
    last60 = df.tail(60)
    candles = []
    for _, row in last60.iterrows():
        candles.append({
            "time": int(row["open_time"].timestamp()),
            "open": round(row["open"], 4),
            "high": round(row["high"], 4),
            "low": round(row["low"], 4),
            "close": round(row["close"], 4),
            "volume": round(row["volume"], 4),
            "ema9": round(row["ema9"], 4) if not np.isnan(row["ema9"]) else None,
            "ema21": round(row["ema21"], 4) if not np.isnan(row["ema21"]) else None,
            "ema50": round(row["ema50"], 4) if not np.isnan(row["ema50"]) else None,
            "ema200": round(row["ema200"], 4) if not np.isnan(row["ema200"]) else None,
            "macd": round(row["macd"], 6) if not np.isnan(row["macd"]) else None,
            "macd_signal": round(row["macd_signal"], 6) if not np.isnan(row["macd_signal"]) else None,
            "macd_hist": round(row["macd_hist"], 6) if not np.isnan(row["macd_hist"]) else None,
            "rsi": round(row["rsi"], 2) if not np.isnan(row["rsi"]) else None,
        })
    return candles


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analysis/<timeframe>")
def analysis(timeframe):
    if timeframe not in TIMEFRAMES:
        return jsonify({"error": "Timeframe inválido"}), 400

    interval = TIMEFRAMES[timeframe]
    result = {"timeframe": timeframe, "updated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "assets": {}}

    for asset, symbol in PAIRS.items():
        try:
            df = fetch_klines(symbol, interval, limit=250)
            df = compute_indicators(df)
            signal = generate_signal(df)
            chart = build_chart_data(df)
            result["assets"][asset] = {"symbol": symbol, "signal": signal, "chart": chart}
        except Exception as e:
            result["assets"][asset] = {"error": str(e)}

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
