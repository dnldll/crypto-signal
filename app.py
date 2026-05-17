import os
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from flask import Flask, jsonify, render_template, request as flask_request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

CC_BASE            = "https://min-api.cryptocompare.com/data/v2"
PAIRS              = {"BTC": "BTC", "ETH": "ETH"}
TIMEFRAMES         = {"1h": "histohour", "4h": "histohour", "1d": "histoday"}

SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY       = os.environ.get("SUPABASE_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

BUY_SIGNALS = {1: ["COMPRA", "FORTE COMPRA"], 2: ["FORTE COMPRA"]}


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _sb_headers(prefer_return=False):
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer_return:
        h["Prefer"] = "return=minimal"
    return h

def sb_insert(table: str, data: dict):
    if not SUPABASE_URL:
        return
    requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(prefer_return=True),
        json=data,
        timeout=5,
    )

def sb_select(table: str, params: dict) -> list:
    if not SUPABASE_URL:
        return []
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(),
        params=params,
        timeout=5,
    )
    return resp.json() if resp.ok else []

def sb_patch(table: str, match: dict, data: dict):
    if not SUPABASE_URL:
        return
    params = {k: f"eq.{v}" for k, v in match.items()}
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_sb_headers(prefer_return=True),
        params=params,
        json=data,
        timeout=5,
    )


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(asset: str, timeframe: str, signal: dict):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    emoji = "🟢🟢" if signal["signal"] == "FORTE COMPRA" else "🟢"
    reasons_txt = "\n".join(f"  ✓ {r}" for r in signal["reasons"])
    text = (
        f"{emoji} *{signal['signal']} — {asset}/USDT*\n\n"
        f"💰 Preço: `${signal['price']:,.2f}`\n"
        f"📊 Score: `{signal['score']}/10`\n"
        f"📈 RSI: `{signal['rsi']}` — timeframe `{timeframe.upper()}`\n\n"
        f"*Motivos:*\n{reasons_txt}\n\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=8,
    )


# ── Indicadores ───────────────────────────────────────────────────────────────

def calc_ema(s, n): return s.ewm(span=n, adjust=False).mean()
def calc_sma(s, n): return s.rolling(window=n).mean()

def calc_rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(com=n-1, min_periods=n).mean()
    l = (-d.clip(upper=0)).ewm(com=n-1, min_periods=n).mean()
    return 100 - 100 / (1 + g / l)

def calc_macd(s, fast=12, slow=26, sig=9):
    m = calc_ema(s, fast) - calc_ema(s, slow)
    sl = m.ewm(span=sig, adjust=False).mean()
    return m, sl, m - sl


# ── Dados ─────────────────────────────────────────────────────────────────────

def fetch_klines(symbol: str, timeframe: str) -> pd.DataFrame:
    endpoint = TIMEFRAMES[timeframe]
    limit = 1000 if timeframe == "4h" else 250
    resp = requests.get(
        f"{CC_BASE}/{endpoint}",
        params={"fsym": symbol, "tsym": "USDT", "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()
    if raw.get("Response") == "Error":
        raise ValueError(raw.get("Message", "CryptoCompare error"))

    df = pd.DataFrame(raw["Data"]["Data"]).rename(
        columns={"time": "open_time", "volumefrom": "volume"}
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="s")
    df = df[["open_time", "open", "high", "low", "close", "volume"]].astype(float,errors="ignore")
    df["open_time"] = pd.to_datetime(df["open_time"])

    if timeframe == "4h":
        df = (
            df.set_index("open_time")
            .resample("4h")
            .agg(open=("open","first"), high=("high","max"),
                 low=("low","min"), close=("close","last"), volume=("volume","sum"))
            .dropna()
            .reset_index()
        )
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"]        = calc_rsi(df["close"])
    df["ema9"]       = calc_ema(df["close"], 9)
    df["ema21"]      = calc_ema(df["close"], 21)
    df["ema50"]      = calc_ema(df["close"], 50)
    df["ema200"]     = calc_ema(df["close"], 200)
    df["macd"], df["macd_signal"], df["macd_hist"] = calc_macd(df["close"])
    df["vol_ma20"]   = calc_sma(df["volume"], 20)
    return df


def generate_signal(df: pd.DataFrame) -> dict:
    row, prev = df.iloc[-1], df.iloc[-2]
    score, reasons, warnings = 0, [], []

    rsi = row["rsi"]
    if   rsi < 30: score += 3; reasons.append(f"RSI em sobrevenda extrema ({rsi:.1f})")
    elif rsi < 40: score += 2; reasons.append(f"RSI em sobrevenda ({rsi:.1f})")
    elif rsi < 50: score += 1; reasons.append(f"RSI neutro-baixo ({rsi:.1f})")
    elif rsi > 70: score -= 2; warnings.append(f"RSI sobrecomprado ({rsi:.1f})")

    price = row["close"]
    if price > row["ema200"]:
        score += 1; reasons.append("Preço acima da EMA 200 (tendência de alta)")
    else:
        score -= 1; warnings.append("Preço abaixo da EMA 200 (tendência de baixa)")

    if row["ema9"] > row["ema21"] and prev["ema9"] <= prev["ema21"]:
        score += 2; reasons.append("Cruzamento de alta: EMA 9 × EMA 21")
    elif row["ema9"] > row["ema21"]:
        score += 1; reasons.append("EMA 9 acima da EMA 21 (momentum positivo)")
    else:
        score -= 1; warnings.append("EMA 9 abaixo da EMA 21 (momentum negativo)")

    if price > row["ema50"] and prev["close"] <= prev["ema50"]:
        score += 2; reasons.append("Rompimento acima da EMA 50")

    macd, macd_sig = row["macd"], row["macd_signal"]
    if macd > macd_sig and prev["macd"] <= prev["macd_signal"]:
        score += 2; reasons.append("Cruzamento de alta no MACD")
    elif macd > macd_sig:
        score += 1; reasons.append("MACD positivo (bull)")
    else:
        score -= 1; warnings.append("MACD negativo (bear)")

    if row["macd_hist"] > 0 and prev["macd_hist"] < 0:
        score += 1; reasons.append("Histograma MACD virou positivo")
    elif row["macd_hist"] > prev["macd_hist"] and row["macd_hist"] < 0:
        score += 1; reasons.append("Histograma MACD em recuperação")

    vol, vol_ma = row["volume"], row["vol_ma20"]
    if vol > vol_ma * 1.5:
        score += 1; reasons.append(f"Volume {vol/vol_ma:.1f}x acima da média")
    elif vol > vol_ma:
        score += 0.5; reasons.append("Volume acima da média")

    label = ("FORTE COMPRA" if score >= 6 else "COMPRA" if score >= 3
             else "NEUTRO" if score >= 1 else "AGUARDAR" if score >= -1
             else "NÃO COMPRAR")

    return {
        "signal": label, "score": round(score, 1),
        "rsi": round(rsi, 2), "price": round(price, 2),
        "ema9": round(row["ema9"], 2), "ema21": round(row["ema21"], 2),
        "ema50": round(row["ema50"], 2), "ema200": round(row["ema200"], 2),
        "macd": round(macd, 4), "macd_signal": round(macd_sig, 4),
        "volume": round(vol, 2), "vol_ma20": round(vol_ma, 2),
        "reasons": reasons, "warnings": warnings,
    }


def build_chart_data(df: pd.DataFrame) -> list:
    def safe(v):
        return round(float(v), 6) if pd.notna(v) and not np.isinf(v) else None
    return [
        {
            "time": int(r["open_time"].timestamp()),
            **{k: safe(r[k]) for k in ["open","high","low","close","volume",
                                        "ema9","ema21","ema50","ema200",
                                        "macd","macd_signal","macd_hist","rsi"]}
        }
        for _, r in df.tail(60).iterrows()
    ]


# ── Alert logic ───────────────────────────────────────────────────────────────

def get_alert_config() -> dict:
    rows = sb_select("alert_config", {"id": "eq.1"})
    return rows[0] if rows else {"min_level": 1}

def get_last_signal(asset: str, timeframe: str) -> str | None:
    rows = sb_select("analyses", {
        "asset": f"eq.{asset}",
        "timeframe": f"eq.{timeframe}",
        "order": "created_at.desc",
        "limit": "1",
        "select": "signal",
    })
    return rows[0]["signal"] if rows else None

def save_and_alert(asset: str, timeframe: str, signal: dict, config: dict):
    # 1. Lê o último sinal ANTES de salvar o novo
    min_level = config.get("min_level", 1)
    buy_set   = BUY_SIGNALS.get(min_level, BUY_SIGNALS[1])
    last      = get_last_signal(asset, timeframe)

    # 2. Salva a nova análise
    sb_insert("analyses", {
        "asset": asset, "timeframe": timeframe,
        "signal": signal["signal"], "score": signal["score"],
        "price": signal["price"], "rsi": signal["rsi"],
        "ema9": signal["ema9"], "ema21": signal["ema21"],
        "ema50": signal["ema50"], "ema200": signal["ema200"],
        "macd": signal["macd"],
        "reasons": json.dumps(signal["reasons"]),
        "warnings": json.dumps(signal["warnings"]),
    })

    # 3. Alerta só se transitou de não-compra para compra
    if signal["signal"] in buy_set and last not in buy_set:
        send_telegram(asset, timeframe, signal)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analysis/<timeframe>")
def analysis(timeframe):
    if timeframe not in TIMEFRAMES:
        return jsonify({"error": "Timeframe inválido"}), 400

    config = get_alert_config()
    result = {
        "timeframe": timeframe,
        "updated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "assets": {},
        "config": config,
    }

    for asset, symbol in PAIRS.items():
        try:
            df  = fetch_klines(symbol, timeframe)
            df  = compute_indicators(df)
            sig = generate_signal(df)
            # Apenas exibe — quem salva e alerta é o /api/cron
            result["assets"][asset] = {
                "symbol": symbol,
                "signal": sig,
                "chart": build_chart_data(df),
            }
        except Exception as e:
            result["assets"][asset] = {"error": str(e)}

    return jsonify(result)


@app.route("/api/config", methods=["GET"])
def get_config():
    cfg = get_alert_config()
    cfg["telegram_configured"] = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    cfg["supabase_configured"] = bool(SUPABASE_URL and SUPABASE_KEY)
    return jsonify(cfg)


@app.route("/api/test-telegram")
def test_telegram():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return jsonify({"ok": False, "error": "Token ou chat_id não configurado"}), 400
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": "✅ *VelaSignal conectado!*\n\nAlertas de compra BTC/ETH ativos.", "parse_mode": "Markdown"},
            timeout=8,
        )
        data = r.json()
        if data.get("ok"):
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": data.get("description", "Erro desconhecido")}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/config", methods=["POST"])
def update_config():
    data = flask_request.get_json()
    min_level = int(data.get("min_level", 1))
    if min_level not in (1, 2):
        return jsonify({"error": "min_level deve ser 1 ou 2"}), 400
    sb_patch("alert_config", {"id": 1}, {"min_level": min_level, "updated_at": "now()"})
    return jsonify({"ok": True, "min_level": min_level})


@app.route("/api/history/<asset>/<timeframe>")
def history(asset, timeframe):
    rows = sb_select("analyses", {
        "asset": f"eq.{asset.upper()}",
        "timeframe": f"eq.{timeframe}",
        "order": "created_at.desc",
        "limit": "20",
        "select": "signal,score,price,rsi,created_at",
    })
    return jsonify(rows)


@app.route("/api/cron")
def cron():
    """Endpoint chamado pelo cron-job.org a cada X minutos."""
    secret = flask_request.args.get("secret", "")
    cron_secret = os.environ.get("CRON_SECRET", "")
    if cron_secret and secret != cron_secret:
        return jsonify({"error": "Unauthorized"}), 401

    config = get_alert_config()
    results = {}
    for timeframe in ["1h", "4h"]:
        for asset, symbol in PAIRS.items():
            key = f"{asset}_{timeframe}"
            try:
                df  = fetch_klines(symbol, timeframe)
                df  = compute_indicators(df)
                sig = generate_signal(df)
                save_and_alert(asset, timeframe, sig, config)
                results[key] = {"signal": sig["signal"], "score": sig["score"], "price": sig["price"]}
            except Exception as e:
                results[key] = {"error": str(e)}

    return jsonify({"ok": True, "ran_at": datetime.now().isoformat(), "results": results})


if __name__ == "__main__":
    app.run(debug=True, port=5050)
