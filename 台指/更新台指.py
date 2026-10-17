"""台指按鈕（盤後版）資料：期交所每日逐筆檔 → 台指期近月 30／37／22 秒 K，給儀表板「台指」頁的 A/B、B2、B3 三格。

為什麼是盤後版（2026-10-16 實測）：期交所即時行情網站 mis.taifex.com.tw 網頁跨網域擋、雲端機房擋，
家裡電腦每秒抓兩次都在幾分鐘後整站打不開（手機行動網路也打不開，8:37～9:07、10:00 起），撐不住即時版。
每日逐筆檔約 16:40 上架完整版（Daily_2026_10_15.zip 的 Last-Modified 為台北 16:40）；當天清晨就有一份只含前一晚夜盤的
檔（10/16 10:19 實測），所以一天跑兩次：早上 7:00 補夜盤、傍晚 17:00 補日盤。還沒涵蓋到日盤收盤的日子記成未完成，下次重抓。

- 檔名日期 D 的逐筆檔＝前一天 15:00 起的夜盤＋D 當天日盤；晚上的夜盤要等隔天的檔才有。
- 逐筆檔只保留約 30 個交易日，所以合成好的 K 存在 台指/日/YYYYMMDD.json 當正本，之後只補新的日子。
- 近月＝第三個星期三（最後交易日）還沒過的最小月份；到期日當天仍算舊約。
- 換倉（2026-10-17 使用者定）：最後交易日尾盤前五分鐘（13:25，舊約日盤 13:30 收）平掉舊約，同一刻用市價進新約。
  所以那天的 K 棒 13:25 以前是舊約、以後是新約，資料檔記下 rt（換倉那根 K 的起點）、out（舊約平倉價）、
  in（新約進倉價）；儀表板用這個價差把換倉以前的 K 整段平移（往回調整）讓趨勢線連續，不會因為近次月價差
  （10/16 約 170 點）產生假訊號，交易明細則真的記一筆「換倉」、多算一次來回成本。
- 時段與切法逐行照 技能 index-trend-ops 的 scripts/tx_short.py（日盤 08:45～13:45、夜盤 15:00～05:00，
  交易時段內沒成交補平盤、缺口 2 小時內），所以 10/6～10/15 的結果要跟它一致：A/B +801、B2 +1,346、B3 +2,348。
只用 Python 內建模組。用法：
  python 更新台指.py          只更新本機資料檔
  python 更新台指.py --push   排程用：先拉下來、更新、只提交 台指/ 底下的資料再推上去（GitHub Pages 隨之更新），
                              輸出另存到暫存資料夾的 台指盤後更新.log"""
import sys, os, io, csv, json, re, zipfile, subprocess, tempfile, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone, date
sys.stdout.reconfigure(encoding="utf-8")

TZ = timezone(timedelta(hours=8))
HERE = os.path.dirname(os.path.abspath(__file__))
DAY_DIR = os.path.join(HERE, "日")
INDEX = os.path.join(HERE, "index.json")
START = date(2026, 9, 24)          # 同 tx_short.py：10/6 起算，前面留暖機（B2 要約 2,700 根 37 秒 K）
TFS = (30, 37, 22)
UA = {"User-Agent": "Mozilla/5.0"}
URL = "https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/Daily_{:%Y_%m_%d}.zip"
ROLL_HM = (13, 25)                 # 換倉時刻：最後交易日尾盤前五分鐘（舊約日盤 13:30 收盤）

def last_trade_day(ym):            # 台指期最後交易日：該月第三個星期三
    y, m = int(ym[:4]), int(ym[4:])
    d = date(y, m, 1); d += timedelta(days=(2 - d.weekday()) % 7)
    return d + timedelta(days=14)

def near_month(d, months):
    ok = sorted(m for m in months if last_trade_day(m) >= d)
    return ok[0] if ok else None

def tx_open(t):                    # 同 tx_short.py
    dt = datetime.fromtimestamp(t / 1000, TZ); wd = dt.weekday(); hm = dt.hour * 60 + dt.minute
    if hm < 300: return wd in (1, 2, 3, 4, 5)
    if 525 <= hm < 825: return wd < 5
    if hm >= 900: return wd < 5
    return False

def bars(T, tf):                   # 同 tx_short.py 的 bars()；一份逐筆檔前後都隔著休息時段，可以每天各自合成
    ms = tf * 1000; B = []
    for t, p, q in T:
        if not tx_open(t): continue
        s = t // ms * ms
        if not B or s > B[-1][0]:
            if B and s - B[-1][0] <= 2 * 3600000:
                c = B[-1][4]; x = B[-1][0] + ms
                while x < s:
                    if tx_open(x): B.append([x, c, c, c, c, 0])
                    x += ms
            B.append([s, p, p, p, p, q])
        elif s == B[-1][0]:
            b = B[-1]; b[2] = max(b[2], p); b[3] = min(b[3], p); b[4] = p; b[5] += q
    return B

def encode(B, tf):                 # 每根 6 個整數：距上一根幾根、開−上一根收、高−開、開−低、收−開、量
    out = [B[0][0]] if B else []; pt = B[0][0] if B else 0; pc = 0; ms = tf * 1000
    for t, o, h, l, c, v in B:
        out += [(t - pt) // ms, o - pc, h - o, o - l, c - o, v]; pt = t; pc = c
    return out

def fetch(d):
    """回傳 (狀態, 逐筆列)。狀態：ok／none（期交所沒有這天的檔，回 HTML 錯誤頁但 HTTP 200）／err。"""
    try:
        raw = urllib.request.urlopen(urllib.request.Request(URL.format(d), headers=UA), timeout=90).read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(d, "下載失敗：", e); return "err", None
    if raw[:2] != b"PK": return "none", None
    z = zipfile.ZipFile(io.BytesIO(raw))
    return "ok", list(csv.reader(z.read(z.namelist()[0]).decode("big5", errors="replace").splitlines()[1:]))

def build(d, rows):
    tx = []; months = set()
    for r in rows:
        if len(r) > 5 and r[1].strip() == "TX":
            m = r[2].strip()
            if re.fullmatch(r"\d{6}", m):
                months.add(m)
                dt = datetime.strptime(r[0].strip() + r[3].strip().zfill(6), "%Y%m%d%H%M%S").replace(tzinfo=TZ)
                tx.append((int(dt.timestamp() * 1000), m, int(float(r[4])), int(float(r[5]))))
    c = near_month(d, months)
    if not c: return None
    nxt = sorted(m for m in months if m > c)
    # 同一秒內的成交保留檔案原本的先後（只照時間排、穩定排序）；照價格排的話每根 K 的開收盤會差幾點
    T = sorted(((t, p, q) for t, m, p, q in tx if m == c), key=lambda x: x[0])
    if not T: return None
    # 換倉：最後交易日 13:25 平舊約、同一刻市價進新約，所以那天 13:25 之後的 K 改用新約
    cut = int(datetime(d.year, d.month, d.day, *ROLL_HM, tzinfo=TZ).timestamp() * 1000)
    N = sorted(((t, p, q) for t, m, p, q in tx if m == nxt[0]), key=lambda x: x[0]) if nxt else []
    c2 = nxt[0] if N and last_trade_day(c) == d and T[0][0] < cut <= N[-1][0] else None
    # 換月平移用（萬一換月那天的檔漏掉，儀表板退回用日界的次月價差）：近月最後一筆成交那一刻的次月價
    nx = None
    if N and not c2:
        lt = T[-1][0]; nx = ([p for t, p, q in N if t <= lt] or [None])[-1]
    LAST = N[-1] if c2 else T[-1]
    out = {"d": f"{d:%Y%m%d}", "c": c, "c2": c2, "roll": {}, "last": LAST[1], "lt": LAST[0], "nx": nx,
           "n": len([1 for t, p, q in T if t < cut]) + len([1 for t, p, q in N if t >= cut]) if c2 else len(T), "b": {}}
    for tf in TFS:
        if c2:
            ms = tf * 1000; rt = -(-cut // ms) * ms       # 換倉那根 K：第一個不早於 13:25 的 K 棒起點
            A = [x for x in T if x[0] < rt]; Bn = [x for x in N if x[0] >= rt]
            if A and Bn:
                out["roll"][str(tf)] = {"rt": rt, "out": A[-1][1], "in": Bn[0][1]}
                out["b"][str(tf)] = encode(bars(A + Bn, tf), tf); continue
        out["b"][str(tf)] = encode(bars(T, tf), tf)
    if c2 and not out["roll"]: out["c2"] = c2 = None       # 三個週期都切不出來就整天照舊約
    return out

def main():
    os.makedirs(DAY_DIR, exist_ok=True)
    idx = json.load(open(INDEX, encoding="utf-8")) if os.path.exists(INDEX) else {"days": [], "none": []}
    # 只有夜盤、還沒有日盤的那天（清晨上架的檔）不算完成，下次重抓
    idx["days"] = [x for x in idx["days"] if x.get("fin", True)]
    have = {x["d"] for x in idx["days"]}; none = set(idx.get("none", []))
    today = datetime.now(TZ).date(); new = 0
    d = START
    while d <= today:
        k = f"{d:%Y%m%d}"
        if d.weekday() < 5 and k not in have and k not in none:
            st, rows = fetch(d)
            if st == "ok":
                day = build(d, rows)
                if day:
                    json.dump(day, open(os.path.join(DAY_DIR, k + ".json"), "w", encoding="utf-8", newline="\n"), separators=(",", ":"))
                    fin = d < today or day["lt"] >= int(datetime(d.year, d.month, d.day, 13, 40, tzinfo=TZ).timestamp() * 1000)
                    e = dict({x: day[x] for x in ("d", "c", "last", "lt", "nx", "n")}, fin=fin)
                    if day["c2"]: e["c2"] = day["c2"]; e["roll"] = day["roll"]
                    idx["days"].append(e); new += 1
                    r = day["roll"].get(str(TFS[0]))
                    print(k, f"近月 {day['c']}，逐筆 {day['n']:,}，最後 {day['last']}，次月 {day['nx']}"
                          + (f"／換倉 {day['c']}→{day['c2']}：{datetime.fromtimestamp(r['rt'] / 1000, TZ):%H:%M:%S} 平 {r['out']} 進 {r['in']}（價差 {r['in'] - r['out']:+}）" if r else "")
                          + ("" if fin else "（只有夜盤，日盤收盤後再補）"))
                else: none.add(k); print(k, "檔案裡沒有台指期近月成交")
            elif st == "none":
                if d < today: none.add(k); print(k, "期交所沒有這天的檔（休市）")
                else: print(k, "今天的檔還沒上架（約 16:40 上架）")
        d += timedelta(days=1)
    idx["days"].sort(key=lambda x: x["d"]); idx["none"] = sorted(none)
    last = idx["days"][-1] if idx["days"] else None
    idx["tfs"] = list(TFS); idx["start"] = "20261006"
    # 資料真的有變才改更新時間（網頁靠它判斷要不要重讀），不然每次排程都會多一筆沒內容的提交
    old = json.load(open(INDEX, encoding="utf-8")) if os.path.exists(INDEX) else {}
    same = {k: v for k, v in old.items() if k != "upd"} == {k: v for k, v in idx.items() if k != "upd"}
    idx["upd"] = old.get("upd") if same and old.get("upd") else datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    json.dump(idx, open(INDEX, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"新增 {new} 天；資料到 {datetime.fromtimestamp(last['lt'] / 1000, TZ):%m/%d %H:%M}，近月 {last['c']}" if last else "沒有資料")
    return new

def git(*a):
    r = subprocess.run(["git", "-C", os.path.dirname(HERE), *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode: print("git", " ".join(a), "失敗：", (r.stderr or r.stdout).strip())
    return r

class Tee:
    def __init__(self, *fs): self.fs = fs
    def write(self, x):
        for f in self.fs: f.write(x)
    def flush(self):
        for f in self.fs: f.flush()

if __name__ == "__main__":
    if "--push" not in sys.argv:
        main(); sys.exit()
    log = open(os.path.join(tempfile.gettempdir(), "台指盤後更新.log"), "a", encoding="utf-8")
    sys.stdout = Tee(sys.stdout, log)
    print()
    print(f"==== {datetime.now(TZ):%Y-%m-%d %H:%M} 盤後更新 ====")
    git("pull", "--rebase", "--autostash", "-q")
    main()
    git("add", "台指/index.json", "台指/日")
    if git("diff", "--cached", "--quiet").returncode:
        last = json.load(open(INDEX, encoding="utf-8"))["days"][-1]
        git("commit", "-q", "-m", f"台指盤後資料：資料到 {datetime.fromtimestamp(last['lt'] / 1000, TZ):%m/%d %H:%M}")
        if git("push", "-q").returncode:          # 別台剛好推過：拉下來再推一次
            git("pull", "--rebase", "-q"); git("push", "-q")
        print("已推上去")
    else: print("資料沒有變，不提交")
