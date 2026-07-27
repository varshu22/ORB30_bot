#!/usr/bin/env python3
"""
ORB + GOLDEN ZONE — Telegram Alert Bot (Pine v7 logic ka Python port)
=====================================================================
Logic bilkul TradingView wale "ORB-GZ N150" indicator jaisa:

  1. OR LOCK ....... 9:15 se OR_MINUTES (default 30) tak ka High/Low lock.
  2. GOLDEN ZONE ... OR candle bullish (close>=open) ho toh fib 0.5/0.618
                     HIGH se neeche; bearish ho toh LOW se upar. Din bhar frozen.
  3. ORB ........... Completed 5-min candle ka CLOSE OR High ke upar => BO,
                     OR Low ke neeche => BD. Din ka pehla break LOCK (first-signal mode).
  4. GZ ............ Zone touch => TST; close > gzTop => UP; close < gzBot => DN.
  5. SWEEPS ........ Wick line ko SWEEP_MIN_PCT se pierce kare, close wapas
                     andar => OR sweep. GZ sweep strict: close wapas ZONE ke andar.
  6. ✓ CONFIRM ..... Signal candle ke BAAD wala candle bhi breakout candle ke
                     close se aage band ho => ✓. ALERT SIRF ✓ PAR JATA HAI.
  7. DEDUPE ........ state/ me JSON — har (symbol, event) din me sirf ek baar.

Run: har 5 min (GitHub Actions / cron-job.org). Poore din ke 5-min candles
har run me dobara compute hote hain, isliye missed run se signal nahi chhootta.
"""

import json
import os
import sys
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
import requests

# ────────────────────────── CONFIG ──────────────────────────
IST = ZoneInfo("Asia/Kolkata")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")

OR_MINUTES    = int(os.environ.get("OR_MINUTES", "30"))     # 5 / 30 / 60
SCAN_TF_MIN   = int(os.environ.get("SCAN_TF_MIN", "2"))     # candle TF minutes (Pine: Scan TF)
FIB1          = 0.500
FIB2          = 0.618
SWEEP_MIN_PCT = float(os.environ.get("SWEEP_MIN_PCT", "0.02"))  # % pierce
TRAP_CONF_N   = int(os.environ.get("TRAP_CONF_N", "2"))     # Pine: TRAP N candles held
RE_ARM        = os.environ.get("RE_ARM", "1") == "1"        # re-arm: ORB + GZ + SWEEPS + TRAP
SESSION_START = dt.time(9, 15)
SESSION_END   = dt.time(15, 30)

STATE_DIR = os.environ.get("STATE_DIR", "state")

# Telegram message me event-type grouping — readable headers + order
EVENT_META = {
    "ORB▲✓":  ("🟢 ORB Breakout", 0, +1),
    "ORB▼✓":  ("🔴 ORB Breakdown", 1, -1),
    "GZ▲✓":   ("🟩 Golden Zone → UP", 2, +1),
    "GZ▼✓":   ("🟥 Golden Zone → DOWN", 3, -1),
    "SwL✓":   ("🪤 Low Sweep (bullish trap)", 4, +1),
    "SwH✓":   ("🪤 High Sweep (bearish trap)", 5, -1),
    "gSwL✓":  ("💧 GZ Low Sweep (bullish)", 6, +1),
    "gSwH✓":  ("💧 GZ High Sweep (bearish)", 7, -1),
    "TRAP▲":  ("🔄 Bullish Trap (failed breakdown)", 8, +1),
    "TRAP▼":  ("🔄 Bearish Trap (failed breakout)", 9, -1),
}

# ── FULL UNIVERSE: Pine ke B0+B1+B2+B3+B4 ek saath (Python me 40 ki limit nahi) ──
# Indices: Yahoo ke apne symbols. Jo symbol Yahoo par na mile (data khali aaye),
# code use chupchaap skip kar deta hai — error nahi aayega.
INDICES = {
    "NIFTY":      "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "SENSEX":     "^BSESN",
    # "FINNIFTY":   "NIFTY_FIN_SERVICE.NS",
    # "MIDCPNIFTY": "NIFTY_MID_SELECT.NS",
}

# NSE stocks — Yahoo ticker = naam + ".NS" (M&M.NS, BAJAJ-AUTO.NS bhi aise hi).
STOCKS_BY_SECTOR = {}
# STOCKS_BY_SECTOR = {
#     "BK": ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "INDUSINDBK",
#            "CANBK", "BANKBARODA", "PNB", "UNIONBANK", "IDFCFIRSTB", "FEDERALBNK",
#            "AUBANK", "BANDHANBNK"],
#     "FN": ["BAJFINANCE", "BAJAJFINSV", "JIOFIN", "SHRIRAMFIN", "CHOLAFIN",
#            "BAJAJHLDNG", "MUTHOOTFIN", "LICHSGFIN", "PFC", "RECLTD", "IRFC",
#            "HUDCO", "HDFCAMC", "SBICARD"],
#     "IN": ["HDFCLIFE", "SBILIFE", "ICICIGI", "ICICIPRULI", "LICI"],
#     "IT": ["INFY", "TCS", "HCLTECH", "WIPRO", "TECHM", "LTIM"],
#     "NT": ["PAYTM", "POLICYBZR", "NAUKRI", "ETERNAL", "IRCTC"],
#     "AU": ["MARUTI", "M&M", "TMPV", "EICHERMOT", "HEROMOTOCO", "BAJAJ-AUTO",
#            "ASHOKLEY", "TVSMOTOR", "BHARATFORG", "MOTHERSON", "BOSCHLTD", "MRF",
#            "BALKRISIND", "APOLLOTYRE", "EXIDEIND", "TIINDIA", "ESCORTS"],
#     "PH": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "ZYDUSLIFE", "LUPIN",
#            "AUROPHARMA", "TORNTPHARM", "ALKEM", "BIOCON", "GLENMARK"],
#     "HC": ["APOLLOHOSP", "MAXHEALTH", "FORTIS", "LALPATHLAB"],
#     "FM": ["ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA", "TATACONSUM",
#            "GODREJCP", "DABUR", "MARICO", "COLPAL", "VBL", "UNITDSPR", "UBL"],
#     "RT": ["TRENT", "DMART", "PAGEIND", "BATAINDIA", "JUBLFOOD"],
#     "MT": ["TATASTEEL", "JSWSTEEL", "HINDALCO"],
#     "EN": ["RELIANCE", "ONGC", "COALINDIA", "BPCL", "GAIL", "PETRONET", "IGL",
#            "HINDPETRO", "IOC"],
#     "PW": ["NTPC", "POWERGRID", "TATAPOWER", "ADANIGREEN", "ADANIENSOL",
#            "ADANIPOWER", "NHPC", "SJVN", "TORNTPOWER"],
#     "CM": ["ULTRACEMCO", "GRASIM", "AMBUJACEM", "ACC", "SHREECEM"],
#     "RE": ["DLF", "LODHA", "GODREJPROP", "OBEROIRLTY"],
#     "CG": ["LT", "BEL", "SIEMENS", "ABB", "CUMMINSIND", "POLYCAB", "KEI",
#            "ADANIENT"],
#     "CH": ["PIDILITIND", "ASTRAL", "SUPREMEIND", "SRF", "PIIND", "UPL",
#            "COROMANDEL", "TATACHEM", "DEEPAKNTR"],
#     "CD": ["TITAN", "ASIANPAINT", "BERGEPAINT", "HAVELLS", "DIXON", "VOLTAS",
#            "BLUESTARCO", "CROMPTON"],
#     "TL": ["BHARTIARTL"],
#     "TR": ["ADANIPORTS", "CONCOR", "INDIGO"],
# }

# name -> yf ticker, aur name -> sector (message grouping ke liye)
SYMBOLS = dict(INDICES)
SECTOR_OF = {n: "IX" for n in INDICES}
for _sec, _names in STOCKS_BY_SECTOR.items():
    for _n in _names:
        if _n not in SYMBOLS:  # duplicates auto-dedupe
            SYMBOLS[_n] = _n + ".NS"
            SECTOR_OF[_n] = _sec

# ────────────────────────── HELPERS ──────────────────────────

# ── REPLAY CUTOFF (testing): --cutoff 10:00 => aisa chalega jaise bot
#    us waqt run hua ho — sirf us time tak ke candles count honge.
CUTOFF_TIME = None  # dt.time ya None


def now_ist() -> dt.datetime:
    return dt.datetime.now(tz=IST)


def market_open_today(ts: dt.datetime) -> bool:
    if ts.weekday() >= 5:  # Sat/Sun
        return False
    return SESSION_START <= ts.time() <= SESSION_END


def send_telegram(text: str, html: bool = False) -> bool:
    """html=True => text me <a>/<b>/<pre> tags allowed (links clickable)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("[WARN] TELEGRAM_TOKEN / TELEGRAM_CHAT_ID set nahi hai — dry run:")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT, "text": text,
               "disable_web_page_preview": True}
    if html:
        payload["parse_mode"] = "HTML"
    try:
        r = requests.post(url, json=payload, timeout=15)
        ok = r.status_code == 200 and r.json().get("ok", False)
        if not ok:
            print(f"[ERROR] Telegram: {r.status_code} {r.text[:200]}")
        return ok
    except Exception as e:
        print(f"[ERROR] Telegram exception: {e}")
        return False


def tv_link(name: str) -> str:
    """Symbol -> TradingView chart link (tap => chart khul jata hai)."""
    import html as _html
    special = {"NIFTY": "NSE:NIFTY", "BANKNIFTY": "NSE:BANKNIFTY",
               "SENSEX": "BSE:SENSEX", "FINNIFTY": "NSE:FINNIFTY",
               "MIDCPNIFTY": "NSE:MIDCPNIFTY"}
    tv = special.get(name, "NSE:" + name.replace("&", "_").replace("-", "_"))
    url = "https://www.tradingview.com/chart/?symbol=" + tv.replace(":", "%3A")
    return f'<a href="{url}">{_html.escape(name)}</a>'


def load_state(day_key: str) -> dict:
    path = os.path.join(STATE_DIR, f"alerted_{day_key}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(day_key: str, state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"alerted_{day_key}.json")
    with open(path, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    # Purane din ki files साफ (7 din se purani)
    cutoff = (now_ist() - dt.timedelta(days=7)).strftime("%Y-%m-%d")
    for fn in os.listdir(STATE_DIR):
        if fn.startswith("alerted_") and fn.endswith(".json"):
            d = fn[len("alerted_"):-len(".json")]
            if d < cutoff:
                try:
                    os.remove(os.path.join(STATE_DIR, fn))
                except OSError:
                    pass


# ────────────────────── CORE: PINE LOGIC PORT ──────────────────────

def analyze_symbol(df: pd.DataFrame) -> dict:
    """Ek symbol ke aaj ke candles => confirmed events (latest per event type).

    Pine ke scanner (f_scan) jaisa: signals COMPLETED candle par, re-arm ON,
    ✓ confirm agle candle ke close se (confRef = breakout candle close).
    Return: {"ORB▲✓": "9:50", "SwH✓": "10:15", ...} sirf CONFIRMED events.
    """
    if df is None or df.empty or len(df) < 3:
        return {}

    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    if df.empty:
        return {}

    # sirf COMPLETED candles: jinka end (start + TF) ab tak beet chuka ho.
    # CUTOFF mode me "ab" = data wale din ka cutoff time (replay jaisa).
    if CUTOFF_TIME is not None:
        now = df.index[0].replace(hour=CUTOFF_TIME.hour, minute=CUTOFF_TIME.minute,
                                  second=0, microsecond=0)
    else:
        now = now_ist()
    df = df[[ts + dt.timedelta(minutes=SCAN_TF_MIN) <= now for ts in df.index]]
    if len(df) < 2:
        return {}

    day0 = df.index[0]
    or_end = day0.replace(hour=SESSION_START.hour, minute=SESSION_START.minute,
                          second=0, microsecond=0) + dt.timedelta(minutes=OR_MINUTES)

    or_df = df[df.index < or_end]
    post  = df[df.index >= or_end]
    if or_df.empty or len(post) < 1:
        return {}

    # ── OR LOCK ──
    rO = float(or_df["Open"].iloc[0])
    rC = float(or_df["Close"].iloc[-1])
    kH = float(or_df["High"].max())
    kL = float(or_df["Low"].min())

    # ── GOLDEN ZONE (anchor High/Low, direction from OR candle body) ──
    bl = rC >= rO
    rg = kH - kL
    x1 = (kH - rg * FIB1) if bl else (kL + rg * FIB1)
    x2 = (kH - rg * FIB2) if bl else (kL + rg * FIB2)
    gzT, gzB = max(x1, x2), min(x1, x2)

    pcH = kH * SWEEP_MIN_PCT / 100
    pcL = kL * SWEEP_MIN_PCT / 100
    pcT = gzT * SWEEP_MIN_PCT / 100
    pcB = gzB * SWEEP_MIN_PCT / 100

    # ── STATE MACHINES (re-arm sab par: ORB · GZ · SWEEPS · TRAP) ──
    orb_st = 0   # 1 BO / -1 BD
    gz_st  = 0   # 2 UP / -2 DN / 3 TST
    sw_st  = 0   # 1 high swept / -1 low swept (latest)
    gw_st  = 0   # 1 gz-top swept / -1 gz-bottom swept (latest)
    orb = {"ref": None, "i": None, "cf": 0, "t": None}
    gz  = {"ref": None, "i": None, "cf": 0, "t": None}
    sw  = {"i": None, "cf": 0, "t": None}
    gw  = {"i": None, "cf": 0, "t": None}
    orb_armed = False  # reArmOrb: range me wapas close => agla break naya event
    gz_armed  = False  # reArmGz: zone me wapas close => agla GZ break naya event
    # SWEEP RE-ARM: fire hone ke baad jaise hi condition ek candle FALSE ho,
    # wo direction phir se armed — ek hi lambi wick lagataar spam nahi karegi.
    swH_armed = True
    swL_armed = True
    gwH_armed = True
    gwL_armed = True
    # TRAP (Pine grpSm): failed-break reversal — break ke baad OR level ka
    # reclaim jo TRAP_CONF_N lagataar candles tak HELD rahe.
    last_break = 0    # -1 = last close OR Low ke neeche tha, +1 = OR High ke upar
    above_cnt = 0     # down-break ke baad lagataar closes OR Low ke UPAR
    below_cnt = 0     # up-break ke baad lagataar closes OR High ke NEECHE
    trap_up_t = None  # latest confirmed bullish trap (T▲) ka time
    trap_dn_t = None  # latest confirmed bearish trap (T▼) ka time

    rows = list(post.itertuples())
    for i, r in enumerate(rows):
        pH, pL, pC = float(r.High), float(r.Low), float(r.Close)
        tstr = r.Index.strftime("%H:%M")

        # ── pehle pending confirms settle karo (agla candle = ye wala) ──
        if orb["i"] is not None and i == orb["i"] + 1 and orb["cf"] == 0:
            orb["cf"] = 1 if (pC > orb["ref"] if orb_st == 1 else pC < orb["ref"]) else -1
        if gz["i"] is not None and i == gz["i"] + 1 and gz["cf"] == 0:
            gz["cf"] = 1 if (pC > gz["ref"] if gz_st == 2 else pC < gz["ref"]) else -1
        if sw["i"] is not None and i == sw["i"] + 1 and sw["cf"] == 0:
            sw["cf"] = 1 if (pC < kH if sw_st == 1 else pC > kL) else -1
        if gw["i"] is not None and i == gw["i"] + 1 and gw["cf"] == 0:
            gw["cf"] = 1 if (pC < gzT if gw_st == 1 else pC > gzB) else -1

        # ── ORB (re-arm ON: range me wapas close => agla break NAYA event) ──
        if RE_ARM and orb_st != 0 and kL <= pC <= kH:
            orb_armed = True
        orb_locked = orb_st != 0 and not orb_armed
        if pC > kH and (orb_st != 1 or orb_armed) and not orb_locked:
            orb_st, orb = 1, {"ref": pC, "i": i, "cf": 0, "t": tstr}
            orb_armed = False
        elif pC < kL and (orb_st != -1 or orb_armed) and not orb_locked:
            orb_st, orb = -1, {"ref": pC, "i": i, "cf": 0, "t": tstr}
            orb_armed = False

        # ── GOLDEN ZONE (re-arm ON: zone me wapas close => agla break NAYA) ──
        if gz_st == 0 and pL <= gzT and pH >= gzB:
            gz_st = 3  # tested — touch ke baad hi break count hota hai (Pine same)
        if RE_ARM and gz_st in (2, -2) and gzB <= pC <= gzT:
            gz_armed = True
        gz_locked = gz_st in (2, -2) and not gz_armed
        if gz_st != 0 and not gz_locked:
            if pC > gzT and (gz_st != 2 or gz_armed):
                gz_st, gz = 2, {"ref": pC, "i": i, "cf": 0, "t": tstr}
                gz_armed = False
            elif pC < gzB and (gz_st != -2 or gz_armed):
                gz_st, gz = -2, {"ref": pC, "i": i, "cf": 0, "t": tstr}
                gz_armed = False

        # ── OR SWEEP (RE-ARM: condition ek candle FALSE ho jaye => phir armed) ──
        condH = pH > kH + pcH and pC < kH
        condL = pL < kL - pcL and pC > kL
        if condH and swH_armed:
            sw_st, sw = 1, {"i": i, "cf": 0, "t": tstr}
            swH_armed = False
        elif not condH and RE_ARM:
            swH_armed = True
        if condL and swL_armed:
            sw_st, sw = -1, {"i": i, "cf": 0, "t": tstr}
            swL_armed = False
        elif not condL and RE_ARM:
            swL_armed = True

        # ── GZ SWEEP (RE-ARM same rule; strict: close wapas zone ke ANDAR) ──
        gcondH = pH > gzT + pcT and pC < gzT and pC > gzB
        gcondL = pL < gzB - pcB and pC > gzB and pC < gzT
        if gcondH and gwH_armed:
            gw_st, gw = 1, {"i": i, "cf": 0, "t": tstr}
            gwH_armed = False
        elif not gcondH and RE_ARM:
            gwH_armed = True
        if gcondL and gwL_armed:
            gw_st, gw = -1, {"i": i, "cf": 0, "t": tstr}
            gwL_armed = False
        elif not gcondL and RE_ARM:
            gwL_armed = True

        # ── TRAP (RE-ARM: naya break + naya N-candle reclaim => NAYA trap) ──
        if pC < kL:
            last_break = -1
        elif pC > kH:
            last_break = 1
        above_cnt = above_cnt + 1 if (last_break == -1 and pC > kL) else 0
        below_cnt = below_cnt + 1 if (last_break == 1 and pC < kH) else 0
        if above_cnt == TRAP_CONF_N and (RE_ARM or trap_up_t is None):
            trap_up_t = tstr   # bullish trap: breakdown fail + reclaim held
        if below_cnt == TRAP_CONF_N and (RE_ARM or trap_dn_t is None):
            trap_dn_t = tstr   # bearish trap: breakout fail + reject held

    # ── sirf ✓ CONFIRMED events return karo ──
    ev = {}
    if orb_st != 0 and orb["cf"] == 1:
        ev["ORB▲✓" if orb_st == 1 else "ORB▼✓"] = orb["t"]
    if gz_st in (2, -2) and gz["cf"] == 1:
        ev["GZ▲✓" if gz_st == 2 else "GZ▼✓"] = gz["t"]
    if sw_st != 0 and sw["cf"] == 1:
        ev["SwH✓" if sw_st == 1 else "SwL✓"] = sw["t"]
    if gw_st != 0 and gw["cf"] == 1:
        ev["gSwH✓" if gw_st == 1 else "gSwL✓"] = gw["t"]
    # TRAP: N-candle hold khud hi confirmation hai, isliye alag ✓ nahi chahiye
    if trap_up_t is not None:
        ev["TRAP▲"] = trap_up_t
    if trap_dn_t is not None:
        ev["TRAP▼"] = trap_dn_t
    return ev


# ────────────────────────── MAIN ──────────────────────────

def main() -> int:
    global CUTOFF_TIME
    ts = now_ist()
    day_key = ts.strftime("%Y-%m-%d")
    force = "--force" in sys.argv  # off-hours testing ke liye

    # --cutoff HH:MM => replay mode (sirf us time tak ke signals)
    if "--cutoff" in sys.argv:
        try:
            i = sys.argv.index("--cutoff")
            hh, mm = sys.argv[i + 1].split(":")
            CUTOFF_TIME = dt.time(int(hh), int(mm))
            day_key += f"-cut{hh}{mm}"  # alag state, asli state ganda na ho
            print(f"[REPLAY] Cutoff {CUTOFF_TIME:%H:%M} — aise chalega jaise "
                  f"bot us waqt run hua ho.")
        except (IndexError, ValueError):
            print("Usage: --cutoff HH:MM (jaise --cutoff 10:00)")
            return 1

    if not force and not market_open_today(ts):
        print(f"[{ts:%H:%M}] Market band hai — exit.")
        return 0

    print(f"[{ts:%H:%M}] {len(SYMBOLS)} symbols fetch ho rahe hain...")
    tickers = list(SYMBOLS.values())
    data = yf.download(tickers, period="1d", interval=f"{SCAN_TF_MIN}m",
                       group_by="ticker",
                       threads=True, progress=False, auto_adjust=False)

    state = load_state(day_key)
    new_items = []

    for name, tk in SYMBOLS.items():
        try:
            # group_by="ticker" par 1 symbol me bhi columns nested ho sakte hain
            df = data[tk] if isinstance(data.columns, pd.MultiIndex) else data
            if df is None or df.empty:
                continue
            df = df.copy()
            # yfinance index UTC/exchange-tz ho sakta hai => IST me lao
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df.index = df.index.tz_convert(IST)
            # HOLIDAY GUARD: weekday-chhutti par Yahoo pichhle din ka data deta
            # hai — bina is check ke purane signals dobara alert ho jate.
            if not force and len(df) and df.index[-1].date() != ts.date():
                continue
            events = analyze_symbol(df)
        except Exception as e:
            print(f"[WARN] {name}: {e}")
            continue

        for ev, at in events.items():
            # time bhi key me — re-armed naya break (naye time par) dobara alert kare
            key = f"{name}|{ev}|{at}"
            if key not in state:
                state[key] = at
                new_items.append((name, ev, at))

    if new_items:
        # ── GROUPED + CLICKABLE: har group ka bold header, symbols par tap
        #    karte hi TradingView chart khulta hai. Order: ORB Breakdown
        #    pehle, group ke andar LATEST first.
        GROUPS = [
            ("ORB▼✓",  "🔴 ORB Breakdown"),
            ("ORB▲✓",  "🟢 ORB Breakout"),
            ("GZ▼✓",   "🟥 Golden Zone DOWN"),
            ("GZ▲✓",   "🟩 Golden Zone UP"),
            ("TRAP▼",  "🔄 Bearish Trap (failed breakout)"),
            ("TRAP▲",  "🔄 Bullish Trap (failed breakdown)"),
            ("SwH✓",   "🪤 High Sweep (bearish)"),
            ("SwL✓",   "🪤 Low Sweep (bullish)"),
            ("gSwH✓",  "💧 GZ High Sweep (bear)"),
            ("gSwL✓",  "💧 GZ Low Sweep (bull)"),
        ]

        def t2m(s: str) -> int:
            h, m = s.split(":")
            return int(h) * 60 + int(m)

        bulls = bears = 0
        by_ev: dict = {}
        for name, ev, at in new_items:
            by_ev.setdefault(ev, []).append((name, at))
            d = EVENT_META.get(ev, ("", 9, 0))[2]
            bulls += 1 if d > 0 else 0
            bears += 1 if d < 0 else 0

        head = (f"📊 ORB-GZ ✓ · {ts:%H:%M} · 🟢{bulls} 🔴{bears} · "
                f"{len(new_items)} naye")
        blocks = []       # HTML blocks (Telegram ke liye)
        plain_lines = []  # console log ke liye
        for ev, label in GROUPS:
            if ev not in by_ev:
                continue
            items = sorted(by_ev[ev], key=lambda x: (-t2m(x[1]), x[0]))  # latest first
            lines = [f"{at}  {tv_link(nm)}" for nm, at in items]
            blocks.append(f"<b>{label} ({len(items)})</b>\n" + "\n".join(lines))
            plain_lines.append(f"{label} ({len(items)}): " +
                               ", ".join(f"{nm}@{at}" for nm, at in items))
        print(f"[ALERT] {len(new_items)} naye confirmed signals:")
        print(head + "\n" + "\n".join(plain_lines))
        # 4096-char limit — blocks ko char-budget ke hisaab se chunks me bhejo
        buf = head
        for b in blocks:
            if len(buf) + len(b) + 2 > 3800:
                send_telegram(buf, html=True)
                buf = head + " (contd.)"
            buf += "\n\n" + b
        send_telegram(buf, html=True)
    else:
        print("Koi naya ✓ confirm nahi.")

    save_state(day_key, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
