"""
指數趨勢儀表板 A-1（那斯達克短趨勢，30 秒 Range Filter 200/23，多空反轉）→ LINE「黃金訊號」推播。

為什麼在這台電腦跑：A-1 的資料是 Binance QQQUSDT 合約逐筆成交，Binance 擋 Cloudflare 的連線，
雲端那支黃金推播程式拿不到。所以這裡每分鐘算一次，有翻轉就 POST 給雲端的 /relay，由它用 LINE 金鑰發出。
電腦關機或睡眠時收不到；醒來後若方向已經變了，會補推一則並標「延遲通知」。

運作
- 第一次（或狀態檔不見、斷太久）：用 實際紀錄/更新紀錄.py 的 bars_A 抓最近 7 天建 30 秒 K，只記方向、不推。
- 之後每分鐘：依成交編號往後抓新成交併進 30 秒 K，取最近 5,000 根用同一個 calc_rf 重算。
- 只看已收盤的 K。方向與「上次通知的方向」不同就推。

檔案
- 推播設定.json   雲端網址與 RELAY_KEY（通關碼）。**不進 git**（專案是公開的）
- 狀態.json       30 秒 K、最後成交編號、上次通知的方向。不進 git
- 推播紀錄.log    每次推播與錯誤
"""
import importlib.util, json, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "推播設定.json")
STATE = os.path.join(HERE, "狀態.json")
LOGF = os.path.join(HERE, "推播紀錄.log")
FAPI = "https://fapi.binance.com/fapi/v1"
UA = {"User-Agent": "Mozilla/5.0"}
WINDOW = 5000          # 重算用的 30 秒 K 根數（暖機 1,200 根之外留足）
KEEP = 6000            # 狀態檔保留根數
PER, MULT = 200, 23    # A-1 參數，與儀表板、紀錄程式一致
TW = timezone(timedelta(hours=8))

spec = importlib.util.spec_from_file_location("rec", os.path.join(HERE, "..", "實際紀錄", "更新紀錄.py"))
rec = importlib.util.module_from_spec(spec); spec.loader.exec_module(rec)

def log(msg):
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(TW):%Y/%m/%d %H:%M:%S} {msg}\n")

def jget(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read())

def tpe(t): return datetime.fromtimestamp(t / 1000, TW).strftime("%m/%d %H:%M")

def add_trade(B, T, p, q):
    """與 index.html addTrade、更新紀錄.py bars_A 同一套：只收 CME 時段、空檔補平盤（2 小時內）"""
    if not rec.cme_open(T): return
    t = T // 30000 * 30000
    if not B or t > B[-1][0]:
        if B and t - B[-1][0] <= 2 * 3600000:
            c = B[-1][4]; x = B[-1][0] + 30000
            while x < t:
                if rec.cme_open(x): B.append([x, c, c, c, c, 0.0])
                x += 30000
        B.append([t, p, p, p, p, q])
    elif t == B[-1][0]:
        b = B[-1]; b[2] = max(b[2], p); b[3] = min(b[3], p); b[4] = p; b[5] += q

def init_state():
    # 7 天：中間夾週末時 3 天只湊得到兩千多根，暖機（1,200 根）後剩太少、方向會算錯（實測 10/12 早上算成空）
    since = int(time.time() * 1000) - 7 * 86400000
    rec.NOW = int(time.time() * 1000)
    bars = [list(b) for b in rec.bars_A(since)]
    last = jget(f"{FAPI}/aggTrades?symbol=QQQUSDT&limit=1")[0]
    # bars_A 只留已收盤的 K；從最後一根之後的成交接著抓
    ch = jget(f"{FAPI}/aggTrades?symbol=QQQUSDT&startTime={bars[-1][0] + 30000}&limit=1")
    last_id = (ch[0]["a"] - 1) if ch else last["a"]
    return {"bars": bars[-KEEP:], "last_id": last_id, "notified": None}

def forward(st):
    n = 0
    while True:
        ch = jget(f"{FAPI}/aggTrades?symbol=QQQUSDT&fromId={st['last_id'] + 1}&limit=1000")
        if not ch: break
        for x in ch: add_trade(st["bars"], x["T"], float(x["p"]), float(x["q"]))
        st["last_id"] = ch[-1]["a"]; n += 1
        if len(ch) < 1000 or n > 200: break
    st["bars"] = st["bars"][-KEEP:]

def nq_ratio():
    try:
        j = jget("https://api.hyperliquid.xyz/info", json.dumps({"type": "allMids", "dex": "xyz"}).encode(),
                 {"Content-Type": "application/json"})
        return float(j["xyz:XYZ100"])
    except Exception:
        return None

def push(cfg, text):
    r = jget(cfg["url"].rstrip("/") + "/relay", json.dumps({"text": text}).encode(),
             {"Content-Type": "application/json", "x-relay-key": cfg["key"]})
    return r

def main():
    cfg = json.load(open(CFG, encoding="utf-8"))
    st = None
    if os.path.exists(STATE):
        try: st = json.load(open(STATE, encoding="utf-8"))
        except Exception: st = None
    try:
        if st is None: raise RuntimeError("沒有狀態")
        forward(st)
    except Exception as e:   # 第一次、狀態壞掉、或斷線超過 2 天（依編號查不到）→ 重建
        log(f"重建 30 秒 K（{e}）")
        notified = st["notified"] if st else None
        st = init_state(); st["notified"] = notified

    now = int(time.time() * 1000)
    done = [b for b in st["bars"] if b[0] + 30000 <= now][-WINDOW:]
    pos, warm = rec.calc_rf([tuple(b) for b in done], PER, MULT)
    cur = pos[-1]
    if st["notified"] is None:            # 第一次：只記方向，不推
        st["notified"] = cur; log(f"起始方向 {rec.SIDE[cur]}，不推播")
    elif cur != st["notified"] and cur != 0:
        i = len(pos) - 1
        while i > 0 and pos[i - 1] == cur: i -= 1
        b = done[i]; px = b[4]
        ndx = nq_ratio(); last_q = done[-1][4]
        late = now - (b[0] + 30000) > 5 * 60000
        price = (f"{px * ndx / last_q:,.0f}（QQQ 合約 {px:.2f}）" if ndx else f"QQQ 合約 {px:.2f}")
        text = (f"{'▲ 翻多' if cur > 0 else '▼ 翻空'}{'（延遲通知）' if late else ''}\n"
                f"那斯達克 短趨勢 A-1（30 秒 RF 200/23）\n\n"
                f"訊號時間　{tpe(b[0])}\n"
                f"訊號價　　{price}\n")
        if late: text += "\n電腦剛恢復，這是之前就翻轉的方向\n"
        text += "\nhttps://eason-237588.github.io/index-trend-dashboard/"
        r = push(cfg, text)
        log(f"推播 {rec.SIDE[cur]} @{tpe(b[0])} → {r}")
        if r.get("ok"): st["notified"] = cur
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8")); os.replace(tmp, STATE)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"錯誤：{e!r}")
        if "--debug" in sys.argv: raise
