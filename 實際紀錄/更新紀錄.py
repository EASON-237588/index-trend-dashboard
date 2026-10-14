"""
指數趨勢儀表板・實際進出紀錄
每次「開工」跑一次：抓最新資料 → 用儀表板同一套規則算出四格部位 → 把上次紀錄之後的進出點補進 進出紀錄.csv → 印出績效。

  A 那斯達克短趨勢        Binance QQQUSDT 逐筆合成 30 秒，Range Filter 200/23，多空反轉。點數依 Hyperliquid 換算成那斯達克
  B 那斯達克短趨勢・參考  Hyperliquid xyz:XYZ100 1 分，Range Filter 100/16，多空反轉
  C 那斯達克中長趨勢      Yahoo 那斯達克期貨「單一月份合約」6 分，Donchian 350 小時只做多＋8 ATR 停損
  D 黃金中長趨勢          Binance XAUUSDT 8 分，Donchian 350 小時多空反轉
  G 那斯達克短趨勢・Gate  Gate QQQ_USDT 合約逐筆合成 30 秒，Range Filter 200/23（規則同 A-1，只換資料源；LINE 推播這組）
                          2026-10-12 取代 A-2：A2 舊紀錄保留，之後不再更新
  E 黃金中長趨勢・上方    Binance 現貨 PAXGUSDT 11 分（24 小時），Donchian 350 小時只做多＋8 ATR 停損
                          （= 黃金儀表板上方、LINE 推播那組，算法照 index.html 的 calcDonG）

紀錄規則
- 只往後追加，不改舊列。舊列就是當時的實際紀錄，日後資料源修正也不回頭改。
- C 格用單一月份合約算訊號與損益，不用連續月（連續月換月時有價差，回測會失真）。
  換月日如果有部位，記一筆「換倉」：舊約平倉、新約同時開倉，價差照實算進損益。
- 起算日：A、B 從 2026-10-06，C、D、E 從 2026-08-15。起算當下已有的部位，以起算時的價格記「起算持有」。
- 價格是訊號那根 K 棒的收盤價，未扣手續費與滑價。

只用 Python 內建模組，三台電腦都能直接跑。
"""
import csv, io, json, os, sys, time, urllib.request, zipfile
from collections import deque
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "進出紀錄.csv")
FIELDS = ["格子", "時間", "動作", "部位", "價格", "換算那斯達克", "合約", "備註", "時間戳"]
FAPI = "https://fapi.binance.com/fapi/v1"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time() * 1000)

def ms(y, m, d, h=0):  # 台北時間 → 毫秒
    return int(datetime(y, m, d, h, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)

# A2＝A-2（取樣 400、倍數 16），起算日與 A-1 同為 10/6，方便並排比較。
# 注意：A-2 的參數是用 10/6～10/10 的資料挑出來的，這四天是「樣本內」，成績會偏好看；
# 真正公平的比較要看 2026-10-12（下週一開盤）之後的表現。
# 紀錄檔裡原本的「A」欄位就是 A-1（舊紀錄只追加不改，欄位名稱不動）。
# E（2026-10-12 加入）＝黃金上方策略，起算日比照 C、D。
# G（2026-10-12 加入，取代 A-2）＝Gate 版，起算日與 A-1 同為 10/6，方便並排比較。A2 舊列保留但不再更新、不再印績效。
START = {"A": ms(2026, 10, 6), "A2": ms(2026, 10, 6), "G": ms(2026, 10, 6), "B": ms(2026, 10, 8), "C": ms(2026, 8, 15), "D": ms(2026, 1, 5), "E": ms(2020, 9, 15)}
# 2026-10-14 使用者要求 D、E 回溯拉到最大：XAUUSDT 2025/12/11 上市、PAXGUSDT 1 分 K 從 2020/08/28，各扣 350 小時暖機。
# C 維持 8/15：單一月份合約的真 5 分 K 只有 60 天，更早只剩小時 K（9 月約已下市）。
ORDER = ["A", "G", "B", "C", "D", "E"]
# LABEL 是畫面代號（v1.8.4 起照畫面順序叫 A～F），只用在印績效與曲線圖標題；紀錄檔「格子」欄仍是左邊的內部代號。
LABEL = {"A": "A", "A2": "A-2", "G": "B", "B": "C", "C": "D", "D": "E", "E": "F"}
NAME = {"A": "那斯達克短趨勢（Binance 30 秒，取樣 200、倍數 23）", "A2": "那斯達克短趨勢（取樣 400、倍數 16）", "G": "那斯達克短趨勢（Gate 30 秒，取樣 200、倍數 23）", "B": "那斯達克短趨勢（1 分，取樣 100、倍數 16）", "C": "那斯達克中長趨勢（6 分，空手放空）", "D": "黃金中長趨勢（8 分）", "E": "黃金中長趨勢（11 分，空手放空）"}
GOLD = ("D", "E")   # 單位是美元/盎司

# C 格合約與換月日（CME 慣例：到期前約一週的週四換到下一季）。過了最後一列要往下加
C_CONTRACTS = [
    ("NQZ26.CME", "2026-12 合約", None),
    ("NQH27.CME", "2027-03 合約", ms(2026, 12, 11)),
    ("NQM27.CME", "2027-06 合約", ms(2027, 3, 12)),
    ("NQU27.CME", "2027-09 合約", ms(2027, 6, 11)),
]

# ============ 時間 ============
def _nth_sunday(y, m, n):
    d = datetime(y, m, 1, tzinfo=timezone.utc)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)

_DST = {}
def ny_offset_h(t):
    y = datetime.fromtimestamp(t / 1000, timezone.utc).year
    if y not in _DST:  # 3 月第二個週日 2:00 到 11 月第一個週日 2:00（美東）
        _DST[y] = ((_nth_sunday(y, 3, 2) + timedelta(hours=7)).timestamp() * 1000,
                   (_nth_sunday(y, 11, 1) + timedelta(hours=6)).timestamp() * 1000)
    a, b = _DST[y]
    return -4 if a <= t < b else -5

def et(t):
    d = datetime.fromtimestamp(t / 1000, timezone.utc) + timedelta(hours=ny_offset_h(t))
    return d.weekday(), d.hour, d.minute  # 週一=0

def cme_open(t):
    wd, h, _ = et(t)
    if wd == 5: return False
    if wd == 6: return h >= 18
    if wd == 4: return h < 17
    return h != 17

def sess_bucket(t, tf):
    _, h, m = et(t)
    mins = ((h - 18 + 24) % 24) * 60 + m
    return t - (mins % tf) * 60000

def tpe(t):
    return datetime.fromtimestamp(t / 1000, timezone(timedelta(hours=8))).strftime("%Y/%m/%d %H:%M")

# ============ 抓資料 ============
def jget(url, data=None):
    for k in range(5):
        try:
            req = urllib.request.Request(url, data=data, headers=dict(UA, **({"Content-Type": "application/json"} if data else {})))
            return json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            if e.code in (418, 429): print("  交易所請求太頻繁，等 60 秒"); time.sleep(60); continue
            if k >= 2: raise
        except Exception:
            if k >= 2: raise
        time.sleep(2 * (k + 1))

def agg_session(raw, tf):
    out = []; cur = None
    for t, o, h, l, c, v in raw:
        if not cme_open(t): continue
        st = sess_bucket(t, tf)
        if cur is None or cur[0] != st:
            if cur: out.append(tuple(cur))
            cur = [st, o, h, l, c, v]
        else:
            cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v
    if cur: out.append(tuple(cur))
    return [b for b in out if b[0] + tf * 60000 <= NOW]  # 只留已收完的 K 棒

def bars_A(since):
    """QQQUSDT 逐筆 → 30 秒 K。完整日用 data.binance.vision 每日檔，剩下的用介面依編號往後抓。"""
    trades = []; day = datetime.fromtimestamp(since / 1000, timezone.utc).date(); last_id = None
    while True:
        u = f"https://data.binance.vision/data/futures/um/daily/aggTrades/QQQUSDT/QQQUSDT-aggTrades-{day}.zip"
        try:
            raw = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60).read()
        except Exception:
            break
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), "utf-8")):
                if not row[0].isdigit(): continue
                trades.append((int(row[5]), float(row[1]), float(row[2]))); last_id = int(row[0])
        day += timedelta(days=1)
    if last_id is None:  # 每日檔還沒出：從現在往回抓到 since
        top = jget(f"{FAPI}/aggTrades?symbol=QQQUSDT&limit=1")[0]["a"]; fid = top
        while True:
            fid -= 1000; ch = jget(f"{FAPI}/aggTrades?symbol=QQQUSDT&fromId={max(fid,0)}&limit=1000")
            if not ch or ch[0]["T"] < since: last_id = ch[0]["a"] - 1 if ch else fid; break
    n = 0
    while True:
        ch = jget(f"{FAPI}/aggTrades?symbol=QQQUSDT&fromId={last_id + 1}&limit=1000")
        if not ch: break
        trades += [(x["T"], float(x["p"]), float(x["q"])) for x in ch]; last_id = ch[-1]["a"]; n += 1
        if len(ch) < 1000: break
        if n % 50 == 0: time.sleep(1)
    trades.sort()
    B = []
    for T, p, q in trades:
        if T < since or not cme_open(T): continue
        t = T // 30000 * 30000
        if not B or t > B[-1][0]:
            if B and t - B[-1][0] <= 2 * 3600000:  # 交易時段內沒成交的 30 秒補平盤
                c = B[-1][4]; x = B[-1][0] + 30000
                while x < t:
                    if cme_open(x): B.append([x, c, c, c, c, 0.0])
                    x += 30000
            B.append([t, p, p, p, p, q])
        elif t == B[-1][0]:
            b = B[-1]; b[2] = max(b[2], p); b[3] = min(b[3], p); b[4] = p; b[5] += q
    return [tuple(b) for b in B if b[0] + 30000 <= NOW]

def bars_G(since):
    """Gate QQQ_USDT 合約逐筆 → 30 秒 K，切法同 bars_A（CME 時段、空檔補平盤）。
    Gate 原生 30 秒 K 只能回溯約 3.5 天，起算要回到 10/6，所以用逐筆（可回溯至少 30 天）。
    注意：create_time_ms 名稱寫毫秒、實際是「秒」，一律用 create_time × 1000。"""
    def page(a, b):
        j = jget(f"https://api.gateio.ws/api/v4/futures/usdt/trades?contract=QQQ_USDT&limit=1000&from={a}&to={b}")
        if len(j) >= 1000 and b - a > 10:
            m = (a + b) // 2
            return page(a, m) + page(m, b)
        return j
    tr = {}; t = since // 1000; end = NOW // 1000
    while t < end:
        for x in page(t, min(end, t + 900)):
            tr[x["id"]] = (int(float(x["create_time"]) * 1000), float(x["price"]), abs(float(x["size"])))
        t += 900
    B = []
    for T, p, q in sorted(tr.values()):
        if T < since or not cme_open(T): continue
        s = T // 30000 * 30000
        if not B or s > B[-1][0]:
            if B and s - B[-1][0] <= 2 * 3600000:
                c = B[-1][4]; x = B[-1][0] + 30000
                while x < s:
                    if cme_open(x): B.append([x, c, c, c, c, 0.0])
                    x += 30000
            B.append([s, p, p, p, p, q])
        elif s == B[-1][0]:
            b = B[-1]; b[2] = max(b[2], p); b[3] = min(b[3], p); b[4] = p; b[5] += q
    return [tuple(b) for b in B if b[0] + 30000 <= NOW]

def bars_B():
    st = NOW - 5000 * 60000
    d = jget("https://api.hyperliquid.xyz/info", json.dumps({"type": "candleSnapshot", "req": {
        "coin": "xyz:XYZ100", "interval": "1m", "startTime": st, "endTime": NOW}}).encode())
    return [(x["t"], float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]), float(x["v"]))
            for x in d if x["t"] + 60000 <= NOW and cme_open(x["t"])]  # 跟儀表板一樣剔除休市時段（2026-10-11 補上，之前週末也在算）

def yahoo(sym, iv, rng):
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={iv}&range={rng}"
    r = jget(u)["chart"]["result"][0]; q = r["indicators"]["quote"][0]
    return [(t * 1000, o, h, l, c, v or 0) for t, o, h, l, c, v in
            zip(r.get("timestamp") or [], q["open"], q["high"], q["low"], q["close"], q["volume"]) if c is not None]

def bars_C(sym):
    """單一月份合約 6 分 K：2 分 K 優先、5 分 K 補前段；更早只為了算通道，用小時 K 拆成 10 根（高低點保留在第一根）。"""
    b2 = yahoo(sym, "2m", "60d"); b5 = yahoo(sym, "5m", "60d"); b1h = yahoo(sym, "1h", "1y")
    s2 = b2[0][0] if b2 else NOW; s5 = b5[0][0] if b5 else s2
    pseudo = []
    for t, o, h, l, c, v in b1h:
        if t >= s5: break
        pseudo.append((t, o, h, l, c, v))
        pseudo += [(t + k * 360000, c, c, c, c, 0) for k in range(1, 10)]
    raw = pseudo + [b for b in b5 if b[0] < s2] + b2
    return agg_session(raw, 6), s5

def bars_D(since):
    out = []; st = since
    while st < NOW:
        ch = jget(f"{FAPI}/klines?symbol=XAUUSDT&interval=1m&limit=1500&startTime={st}")
        if not ch: break
        out += [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in ch]
        st = int(ch[-1][0]) + 60000
        if len(ch) < 1500: break
    return agg_session(out, 8)

def bars_E(since):
    """Binance 現貨 PAXGUSDT 1 分 → 11 分（24 小時，時間桶從 1970 起算，同儀表板 aggAll）。只留已收盤的 K。"""
    out = []; st = since
    while st < NOW:
        ch = jget(f"https://data-api.binance.vision/api/v3/klines?symbol=PAXGUSDT&interval=1m&limit=1000&startTime={st}")
        if not ch: break
        out += [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in ch]
        st = int(ch[-1][0]) + 60000
        if len(ch) < 1000: break
    ms11 = 11 * 60000; B = []
    for t, o, h, l, c, v in out:
        s = t // ms11 * ms11
        if B and B[-1][0] == s:
            _, o0, h0, l0, _, v0 = B[-1]; B[-1] = (s, o0, max(h0, h), min(l0, l), c, v0 + v)
        else: B.append((s, o, h, l, c, v))
    while B and B[-1][0] + ms11 > NOW: B.pop()
    return B

# ============ 指標（照 index.html 逐行移植） ============
def pine_ma(src, n, alpha):
    out = [None] * len(src); prev = None; s = 0.0; cnt = 0
    for i, v in enumerate(src):
        if v is None: out[i] = prev; continue
        if prev is None:
            s += v; cnt += 1
            if cnt == n: prev = s / n; out[i] = prev
        else:
            prev = alpha * v + (1 - alpha) * prev; out[i] = prev
    return out

def sma(src, n):
    out = [None] * len(src); s = 0.0
    for i, v in enumerate(src):
        s += v
        if i >= n: s -= src[i - n]
        if i >= n - 1: out[i] = s / n
    return out

def true_range(B):
    return [b[2] - b[3] if i == 0 else max(b[2] - b[3], abs(b[2] - B[i-1][4]), abs(b[3] - B[i-1][4])) for i, b in enumerate(B)]

def roll(x, n, mx):
    out = [None] * len(x); dq = deque()
    for i in range(len(x)):
        if i >= n: out[i] = x[dq[0]]
        while dq and (x[dq[-1]] <= x[i] if mx else x[dq[-1]] >= x[i]): dq.pop()
        dq.append(i)
        if dq[0] <= i - n: dq.popleft()
    return out

def calc_rf(B, per, mult):
    n = len(B); src = [(b[2] + b[3]) / 2 for b in B]
    diff = [None] + [abs(src[i] - src[i-1]) for i in range(1, n)]
    avr = pine_ma(diff, per, 2 / (per + 1)); m2 = per * 2 - 1
    sm = [None if v is None else v * mult for v in pine_ma(avr, m2, 2 / (m2 + 1))]
    filt = [None] * n; prev = None
    for i in range(n):
        x, r = src[i], sm[i]
        if r is None: prev = None; continue
        pv = 0.0 if prev is None else prev
        prev = (pv if x - r < pv else x - r) if x > pv else (pv if x + r > pv else x + r)
        filt[i] = prev
    vma = sma([b[5] or 0 for b in B], 20)
    pos = [0] * n; up = dn = 0; ci = None; cur = 0
    for i in range(n):
        f = filt[i]; pf = filt[i-1] if i else None
        if f is not None and pf is not None:
            up = up + 1 if f > pf else 0 if f < pf else up
            dn = dn + 1 if f < pf else 0 if f > pf else dn
        s = src[i]; moved = i > 0 and s != src[i-1]
        volok = vma[i] is not None and (B[i][5] or 0) > vma[i] * 0.8
        L = f is not None and s > f and moved and up > 0 and volok
        S = f is not None and s < f and moved and dn > 0 and volok
        pci = ci; ci = 1 if L else -1 if S else pci
        if L and pci == -1: cur = 1
        elif S and pci == 1: cur = -1
        pos[i] = cur
    return pos, 6 * per

def calc_don(B, lookH, tf, mode, stop_atr, atr_len=14):
    n = len(B); look = min(4999, max(2, round(lookH * 60 / tf))); exn = max(10, round(look / 3))
    H = [b[2] for b in B]; L = [b[3] for b in B]
    hh, ll, he, le = roll(H, look, True), roll(L, look, False), roll(H, exn, True), roll(L, exn, False)
    atr = pine_ma(true_range(B), atr_len, 1 / atr_len)
    rev = mode == "both"; pos = [0] * n; d = side = 0; avg = None
    for i in range(n):
        c = B[i][4]
        if hh[i] is not None and c > hh[i] and mode != "short": d = 1
        elif ll[i] is not None and c < ll[i] and mode != "long": d = -1
        elif not rev and d == 1 and le[i] is not None and c < le[i]: d = 0
        elif not rev and d == -1 and he[i] is not None and c > he[i]: d = 0
        flip = d != 0 and d != side
        if not rev and side != 0 and atr[i] and atr[i] > 0 and (avg - c) * side >= stop_atr * atr[i]:
            side = 0; avg = None; d = 0
        if not rev and d == 0 and side != 0: side = 0; avg = None
        if flip: side = d; avg = c
        pos[i] = side
    return pos, look

def calc_don_g(B, lookH=350, tf=11, stop_atr=8, atr_len=14):
    """E 格：照 index.html 的 calcDonG（＝黃金儀表板 compute、LINE 推播 donchian.js）。
    與 calc_don 不同處：停損後封鎖同方向、要先跌破出場線才接受下一次突破；ATR 用 Wilder（第 n 根簡單平均當種子）。"""
    n = len(B); look = max(20, round(lookH * 60 / tf)); exn = max(10, round(look / 3))
    H = [b[2] for b in B]; L = [b[3] for b in B]
    hh, ll, he, le = roll(H, look, True), roll(L, look, False), roll(H, exn, True), roll(L, exn, False)
    tr = [None] + [max(B[i][2] - B[i][3], abs(B[i][2] - B[i-1][4]), abs(B[i][3] - B[i-1][4])) for i in range(1, n)]
    atr = [None] * n
    if n > atr_len:
        atr[atr_len] = sum(tr[1:atr_len + 1]) / atr_len
        for i in range(atr_len + 1, n): atr[i] = (atr[i-1] * (atr_len - 1) + tr[i]) / atr_len
    pos = [0] * n; d = side = blocked = 0; avg = None
    for i in range(n):
        c = B[i][4]
        if i >= look:
            if c > hh[i]: d = 1
            elif c < ll[i]: d = -1
            elif d == 1 and c < le[i]: d = 0
            elif d == -1 and c > he[i]: d = 0
        a = atr[i]
        if a is not None and a > 0:
            if blocked and d != blocked: blocked = 0
            if side and (avg - c) * side >= stop_atr * a: blocked = side; side = 0; avg = None
            if side and d != side: side = 0; avg = None
            if not side and d > 0 and d != blocked: side = 1; avg = c
        pos[i] = side
    return pos, look + atr_len

# ============ 紀錄 ============
def load_log():
    if not os.path.exists(LOG): return []
    with open(LOG, encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))

def save_log(rows):
    with open(LOG, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, FIELDS); w.writeheader(); w.writerows(rows)

SIDE = {1: "多", -1: "空", 0: "空手"}

def action(a, b):
    if a == 0: return "開" + SIDE[b]
    if b == 0: return "平" + SIDE[a]
    return f"平{SIDE[a]}→開{SIDE[b]}"

def update(k, B, pos, warm, contract, nqconv=None, min_t=None):
    """把上次紀錄之後的部位變化補進紀錄。回傳新增的列。"""
    rows = [r for r in LOG_ROWS if r["格子"] == k]
    new = []
    def add(i, act, side, note=""):
        t = B[i][0]; p = B[i][4]
        r = {"格子": k, "時間": tpe(t), "動作": act, "部位": SIDE[side], "價格": f"{p:g}",
             "換算那斯達克": f"{nqconv(t, p):.2f}" if nqconv else "", "合約": contract, "備註": note, "時間戳": t}
        new.append(r)
    lo = max(warm, 0)
    if not rows:
        s0 = next((i for i in range(lo, len(B)) if B[i][0] >= START[k] and (min_t is None or B[i][0] >= min_t)), None)
        if s0 is None: return []
        if pos[s0] != 0: add(s0, "起算持有", pos[s0], "起算時已有的部位，以起算時價格計")
        else: add(s0, "起算", 0, "起算時空手")
        cur = pos[s0]; start_i = s0 + 1
    else:
        last_t = int(rows[-1]["時間戳"]); cur = {"多": 1, "空": -1, "空手": 0}[rows[-1]["部位"]]
        start_i = next((i for i in range(len(B)) if B[i][0] > last_t), len(B))
        if start_i < lo:
            print(f"  ⚠ {k} 格資料不夠暖機（上次紀錄後缺口太長），從可算的第一根接續")
            start_i = lo
        j = start_i - 1
        if 0 <= j and j >= lo and pos[j] != cur:
            add(start_i if start_i < len(B) else j, action(cur, pos[j]), pos[j], "資料源重算後部位不同，校正")
            cur = pos[j]
    for i in range(start_i, len(B)):
        if pos[i] != cur:
            add(i, action(cur, pos[i]), pos[i]); cur = pos[i]
    return new

# 2026-10-13 起 C、E 格「空手改放空」（使用者指定）。2026-10-14 使用者要求進場時間與績效跟策略同步，
# 紀錄已手動回溯至起算日（C 8/18、10/13 08:06 與 E 8/27 改成翻空，刪掉 10/13 15:24 補開的列），switch_rule 不會再觸發：
# 原規則（只做多）空手的時段一律持空、永遠在場；進出時機不變。儀表板 v1.8.0 的 toFlatShort 同一套。
# 黃金儀表板「上方」與 LINE 推播仍是只做多，E 格從此跟它們不同。
def flat_short(pos): return [1 if p == 1 else -1 for p in pos]

def switch_rule(k, B, pos, contract):
    """紀錄最後是空手、新規則部位不是空手 → 以最新一根收盤價補一筆開倉。新規則不再有空手，所以只會發生一次。"""
    rows = [r for r in LOG_ROWS if r["格子"] == k]
    if not rows or rows[-1]["部位"] != "空手" or pos[-1] == 0: return []
    t = B[-1][0]; p = B[-1][4]
    r = {"格子": k, "時間": tpe(t), "動作": action(0, pos[-1]), "部位": SIDE[pos[-1]], "價格": f"{p:g}",
         "換算那斯達克": "", "合約": contract, "備註": "規則改為空手放空（2026-10-13 起），以當時價格開倉", "時間戳": t}
    LOG_ROWS.append(r)
    return [r]

# ============ 績效 ============
def perf(k, rows, last_price, last_t, nq_last=None):
    pts = pct = 0.0; n = win = 0; side = 0; ent = None; ent_nq = None; ent_t = None; trades = []
    for r in rows:
        p = float(r["價格"]); pn = float(r["換算那斯達克"]) if r["換算那斯達克"] else p
        new_side = {"多": 1, "空": -1, "空手": 0}[r["部位"]]
        if r["動作"] == "換倉":  # 舊約平倉價寫在備註
            old = float(r["備註"].split("舊約平倉 ")[1].split(" ")[0])
            g = (old - ent) * side; pts += g; pct += g / ent * 100; n += 1; win += g > 0
            trades.append((SIDE[side], ent_t, ent_nq, r["時間"] + " 換倉", old, g, g / ent * 100, pts))
            ent = p; ent_nq = pn; ent_t = r["時間"]; continue
        if side != 0 and new_side != side:
            g = (p / ent - 1) * side; pts += g * ent_nq; pct += g * 100; n += 1; win += g > 0
            trades.append((SIDE[side], ent_t, ent_nq, r["時間"], pn, g * ent_nq, g * 100, pts))
        if new_side != side:
            side = new_side; ent = p if side else None; ent_nq = pn if side else None; ent_t = r["時間"] if side else None
    op = None
    if side:
        g = (last_price / ent - 1) * side
        op = dict(side=SIDE[side], t=ent_t, p=ent_nq, now=nq_last or last_price, pts=g * ent_nq, pct=g * 100)
    return dict(n=n, win=win, pts=pts, pct=pct, op=op, trades=trades)

def equity(rows, B, start):
    """逐根 K 棒的「已實現＋浮動」。有 K 棒的期間每根都算；沒有 K 棒的期間只在平倉時跳一階（不猜中途浮動）。
    算法與 perf 相同：A 格浮動＝價格漲跌 % × 進場換算點數。回傳 [(毫秒, 累計)]"""
    b0 = B[0][0] if B else None
    if B and rows and rows[-1]["動作"] == "換倉":  # 換倉前的 K 棒是舊約，不能用新約價格算浮動
        b0 = max(b0, int(rows[-1]["時間戳"]))
    items = sorted([(int(r["時間戳"]), 0, r) for r in rows] + [(b[0], 1, b) for b in B if b[0] >= max(start, b0 or 0)],
                   key=lambda x: (x[0], x[1]))
    side = 0; ent = en = None; real = 0.0; out = [(start, 0.0)]
    items_b0 = next((t for t, kind, _ in items if kind == 1), None)
    for t, kind, x in items:
        if kind == 1:
            if out[-1][0] < t and len(out) and t == items_b0: out.append((t, out[-1][1]))  # 無 K 棒區段平接，不畫斜線
            out.append((t, real + ((x[4] / ent - 1) * side * en if side else 0))); continue
        p = float(x["價格"]); pn = float(x["換算那斯達克"]) if x["換算那斯達克"] else p
        ns = {"多": 1, "空": -1, "空手": 0}[x["部位"]]; before = real
        if x["動作"] == "換倉":
            old = float(x["備註"].split("舊約平倉 ")[1].split(" ")[0]); real += (old - ent) * side; ent = p; en = pn
        else:
            if side and ns != side: real += (p / ent - 1) * side * en
            if ns != side: side = ns; ent = p if side else None; en = pn if side else None
        if b0 is None or t < b0: out.append((t, before))  # 沒有 K 棒的期間畫成階梯
        out.append((t, real))
    return out

def draw_curves(res, path):
    if not res: return
    """績效成長曲線：每格一張，各用自己的單位；圓點＝目前合計"""
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt, matplotlib.dates as mdates
    plt.rcParams["font.family"] = ["Microsoft JhengHei"]; plt.rcParams["axes.unicode_minus"] = False
    INK, MUTED, GRID, LINE, BG = "#1f1f1e", "#6b6a66", "#e6e5e1", "#2a78d6", "#fcfcfb"
    TZ = timezone(timedelta(hours=8))
    D = lambda t: datetime.fromtimestamp(t / 1000, TZ).replace(tzinfo=None)
    fig, axs = plt.subplots(len(res), 1, figsize=(9, 2.6 * len(res)), facecolor=BG)
    for ax, (k, unit, tot, totp, last_t, eq, b0) in zip(axs, res):
        eq = eq + [(last_t, tot)]
        xs = [D(t) for t, _ in eq]; ys = [v for _, v in eq]
        ax.set_facecolor(BG)
        ax.axhline(0, color=MUTED, lw=0.8)
        ax.plot(xs, ys, color=LINE, lw=1.6)
        ax.plot([xs[-1]], [tot], "o", ms=8, color=LINE, mec=BG, mew=2)
        ax.annotate(f"{tot:+,.0f}", (xs[-1], tot), xytext=(8, 0), textcoords="offset points", va="center", color=INK, fontsize=10)
        ax.set_title(f"{LABEL[k]} {NAME[k]}　合計 {tot:+,.0f} {unit}（{totp:+.2f}%）", loc="left", color=INK, fontsize=11)
        if b0 and b0 > eq[0][0]:  # 前段沒有 K 棒，只畫平倉階梯
            ax.axvspan(xs[0], D(b0), color=GRID, alpha=0.5, lw=0)
            ax.text(xs[0], 1.0, " 灰底：無 K 線，只在平倉時更新", transform=ax.get_xaxis_transform(), va="top", color=MUTED, fontsize=8.5)
        ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
        for s in ("top", "right"): ax.spines[s].set_visible(False)
        for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=9)
        if (eq[-1][0] - eq[0][0]) < 15 * 86400000: ax.xaxis.set_major_locator(mdates.DayLocator())
        if (eq[-1][0] - eq[0][0]) > 400 * 86400000:  # 跨年（E 格回溯到 2020）只標年份，否則每格都是 01/01
            ax.xaxis.set_major_locator(mdates.YearLocator()); ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        else: ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.margins(x=0.04)
    fig.text(0.01, 0.003, "每根 K 線收盤的「已實現＋持有中浮動」，單位為點數（黃金：美元/盎司）。未扣手續費。", color=MUTED, fontsize=8.5)
    fig.tight_layout(rect=(0, 0.015, 1, 1)); fig.savefig(path, dpi=130, facecolor=BG); plt.close(fig)

# ============ 主程式 ============
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    LOG_ROWS = load_log()
    def last_t(k, default):
        r = [x for x in LOG_ROWS if x["格子"] == k]
        return int(r[-1]["時間戳"]) if r else default
    added = []; LAST = {}

    print("抓 B：Hyperliquid 1 分 K")
    BB = bars_B(); posB, wB = calc_rf(BB, 100, 16)
    hl_close = {b[0]: b[4] for b in BB}
    added += update("B", BB, posB, wB, "xyz:XYZ100"); LAST["B"] = (BB[-1][4], BB[-1][0], None)

    print("抓 A：QQQUSDT 逐筆成交（第一次較久）")
    since = min(last_t("A", START["A"]), NOW) - 3 * 86400000
    BA = bars_A(since); posA, wA = calc_rf(BA, 200, 23)
    ratio = [None]
    def nqconv(t, p):
        m = t // 60000 * 60000
        for dt in range(0, 30):
            h = hl_close.get(m - dt * 60000)
            if h: ratio[0] = h / p; break
        return p * (ratio[0] or 41.05)
    added += update("A", BA, posA, wA, "QQQUSDT", nqconv)
    LAST["A"] = (BA[-1][4], BA[-1][0], nqconv(BA[-1][0], BA[-1][4]))
    # A2 自 2026-10-12 起不再更新（由 G 取代），舊列保留

    print("抓 G：Gate QQQ_USDT 逐筆成交（第一次較久）")
    BG = bars_G(min(last_t("G", START["G"]), NOW) - 3 * 86400000); posG, wG = calc_rf(BG, 200, 23)
    added += update("G", BG, posG, wG, "QQQ_USDT", nqconv)
    LAST["G"] = (BG[-1][4], BG[-1][0], nqconv(BG[-1][0], BG[-1][4]))

    print("抓 C：Yahoo 那斯達克單一月份合約")
    act = [c for c in C_CONTRACTS if c[2] is None or c[2] <= NOW][-1]
    rowsC = [x for x in LOG_ROWS if x["格子"] == "C"]
    BC, real_from = bars_C(act[0]); posC, wC = calc_don(BC, 350, 6, "long", 8); posC = flat_short(posC)
    if rowsC and rowsC[-1]["合約"] != act[1]:  # 換月
        prev = [c for c in C_CONTRACTS if c[1] == rowsC[-1]["合約"]][0]
        BO, _ = bars_C(prev[0]); posO, _ = calc_don(BO, 350, 6, "long", 8); posO = flat_short(posO)
        LOG_ROWS_SAVE = LOG_ROWS
        oldnew = update("C", [b for b in BO if b[0] < act[2]], posO, wC, prev[1]); added += oldnew; LOG_ROWS += oldnew
        side_before = {"多": 1, "空": -1, "空手": 0}[([x for x in LOG_ROWS if x["格子"] == "C"])[-1]["部位"]]
        i_roll = next(i for i, b in enumerate(BC) if b[0] >= act[2]); i_old = max(i for i, b in enumerate(BO) if b[0] < act[2])
        if side_before:
            r = {"格子": "C", "時間": tpe(BC[i_roll][0]), "動作": "換倉", "部位": SIDE[side_before], "價格": f"{BC[i_roll][4]:g}",
                 "換算那斯達克": "", "合約": act[1], "備註": f"舊約平倉 {BO[i_old][4]:g} （{prev[1]}）", "時間戳": BC[i_roll][0]}
        else:
            r = {"格子": "C", "時間": tpe(BC[i_roll][0]), "動作": "換約", "部位": "空手", "價格": f"{BC[i_roll][4]:g}",
                 "換算那斯達克": "", "合約": act[1], "備註": f"空手換到 {act[1]}", "時間戳": BC[i_roll][0]}
        added.append(r); LOG_ROWS.append(r)
    added += switch_rule("C", BC, posC, act[1])
    added += update("C", BC, posC, wC, act[1], min_t=real_from); LAST["C"] = (BC[-1][4], BC[-1][0], None)

    print("抓 D：Binance XAUUSDT 1 分 K")
    BD = bars_D(min(last_t("D", START["D"]), NOW) - 70 * 86400000); posD, wD = calc_don(BD, 350, 8, "both", 8)
    added += update("D", BD, posD, wD, "XAUUSDT"); LAST["D"] = (BD[-1][4], BD[-1][0], None)

    print("抓 E：Binance 現貨 PAXGUSDT 1 分 K")
    BE = bars_E(min(last_t("E", START["E"]), NOW) - 70 * 86400000); posE, wE = calc_don_g(BE); posE = flat_short(posE)
    added += switch_rule("E", BE, posE, "PAXGUSDT")
    added += update("E", BE, posE, wE, "PAXGUSDT"); LAST["E"] = (BE[-1][4], BE[-1][0], None)

    BARS = {"A": BA, "G": BG, "B": BB, "C": BC, "D": BD, "E": BE}
    allrows = [r for r in LOG_ROWS if r not in added] + added
    allrows.sort(key=lambda r: (r["格子"], int(r["時間戳"])))
    save_log(allrows)
    # 儀表板「交易明細」從這個時間之後用網頁即時資料接著算（2026-10-13 起）。
    # 記倒數第二根（最後一根走完的 K 棒）：最後一根可能還在形成，部位之後還會變。
    with open(os.path.join(HERE, "紀錄狀態.json"), "w", encoding="utf-8") as f:
        json.dump({"算到": {k: BARS[k][-2][0] for k in ORDER}, "執行時間": NOW}, f, ensure_ascii=False)

    print(f"\n本次新增 {len(added)} 筆紀錄")
    for r in sorted(added, key=lambda r: int(r["時間戳"])):
        print(f"  {r['格子']} {r['時間']} {r['動作']} @{r['換算那斯達克'] or r['價格']} {r['備註']}")
    print("\n========== 績效（未扣成本） ==========")
    curves = []
    for k in ORDER:
        if not any(r["格子"] == k for r in allrows):
            print(f"{LABEL[k]} {NAME[k]}：尚未起算（{tpe(START[k])[:10]} 開盤起算）"); continue
        p = perf(k, [r for r in allrows if r["格子"] == k], *LAST[k])
        unit = "美元/盎司" if k in GOLD else "點"
        tot_pts = p["pts"] + (p["op"]["pts"] if p["op"] else 0); tot_pct = p["pct"] + (p["op"]["pct"] if p["op"] else 0)
        print(f"{LABEL[k]} {NAME[k]}（{tpe(START[k])[:10]} 起，資料到 {tpe(LAST[k][1])}）")
        print(f"   已平倉 {p['n']} 筆，賺 {p['win']}／賠 {p['n']-p['win']}，已實現 {p['pts']:+,.0f} {unit}（{p['pct']:+.2f}%）")
        if p["op"]:
            o = p["op"]; print(f"   持有中：{o['t']} 進場{o['side']} @{o['p']:,.2f}，現價 {o['now']:,.2f}，浮動 {o['pts']:+,.0f} {unit}（{o['pct']:+.2f}%）")
        else:
            print("   目前空手")
        print(f"   合計 {tot_pts:+,.0f} {unit}（{tot_pct:+.2f}%）")
        rk = [r for r in allrows if r["格子"] == k]; eq = equity(rk, BARS[k], START[k])
        curves.append((k, unit, tot_pts, tot_pct, LAST[k][1], eq, BARS[k][0][0]))
        print("   逐筆明細（# 方向 進場時間 進場價 → 出場時間 出場價 盈虧 % 累計）")
        for i, (sd, et, ep, xt, xp, g, gp, cum) in enumerate(p["trades"], 1):
            print(f"   {i:>2} {sd} {et} {ep:,.2f} → {xt} {xp:,.2f}  {g:+,.0f}（{gp:+.2f}%） 累計 {cum:+,.0f}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "績效曲線.png")
    draw_curves(curves, out); print(f"\n績效曲線圖：{out}")
