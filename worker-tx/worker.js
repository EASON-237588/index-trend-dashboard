/* 指數趨勢儀表板・台指即時轉接站
   期交所即時行情網站不穩（2026-10-16 實測會整站回 520），改由 TradingView 警報送資料：
   台指期 1 秒圖掛一支腳本，每約 15 秒把這段 1 秒 K 打包成一則警報，打到 POST /tx/push?k=密碼。
   警報在 TradingView 伺服器上跑，三台電腦全關也照送。
   資料存在 Durable Object 的 SQLite（KV 免費一天只能寫 1,000 次，不夠），只留最近 3 天，
   更早的由網頁讀盤後逐筆檔。
   GET /tx?since=秒  回傳 {at, sym, bars:[[t(秒), o, h, l, c, v], ...]}，t 是該根 1 秒 K 的開始時間。
   2026-10-18 加恆生：小型恆生期貨 30 秒圖掛「恆生傳送」腳本，POST /hsi/push?k=密碼（同一組密碼）、GET /hsi?since=秒，
   存在另一個 Durable Object（名稱 hsi），跟台指分開。恆生沒有盤後檔可補，策略暖機要約 2 個交易日，所以留 10 天。
   2026-10-22 v2 改存法（使用者選）：原本一根 K 一列，網頁每次打開整包讀三天份 1 秒 K 約 20 萬列，
   免費方案一天只能讀 500 萬列，開約 25 次就爆（10/22 晚上實際爆掉，錯誤「Exceeded allowed rows read
   in Durable Objects free tier」，台指、恆生、連續月一起停到隔天 08:00）。
   改成一段時間的 K 合成一列 JSON（台指 1 秒 K 每分鐘一列、30 秒 K 每小時一列），讀寫列數約降到六十分之一；
   回傳給網頁的內容與舊版逐字相同（部署前本機比對過）。舊表 bars 第一次被呼叫時搬進新表，搬完留著不刪
   （刪 20 萬列會吃掉寫入額度）。 */
const ALLOW = [/^https:\/\/eason-237588\.github\.io$/, /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/];
// 2026-10-21 加 tw：台指以 TradingView 為準、一律連續月（使用者定）。台指期大台 TXF1! 30 秒圖開「依合約變更調整」，
// 掛「台指傳送」腳本循環補送舊 K 棒；換約後補送的就是平移後的價格，以 K 棒時間覆蓋，約 70 分鐘整段換成連續月。
// keep＝保留秒數；grp＝幾秒的 K 合成一列（v2）
const FEEDS = {tx: {keep: 3 * 86400, sym: "TXF1!", grp: 60}, hsi: {keep: 10 * 86400, sym: "MHI1!", grp: 3600},
               tw: {keep: 4 * 86400, sym: "TXF1!", grp: 3600}};

function cors(origin){
  const ok = origin && ALLOW.some(re => re.test(origin));
  return ok ? {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} : {};
}
function json(obj, status, extra){
  return new Response(JSON.stringify(obj), {status: status || 200,
    headers: Object.assign({"Content-Type": "application/json", "Cache-Control": "no-store"}, extra || {})});
}

export class TxStore {
  constructor(state){
    this.sql = state.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS grp(g INTEGER PRIMARY KEY, j TEXT);
                   CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);`);
    this.ready = false;
  }
  // v2：舊表 bars（一根一列）第一次呼叫時整包搬進 grp；搬完記 meta v2，之後不再檢查
  migrate(grp){
    if(this.ready) return;
    if(!this.sql.exec("SELECT v FROM meta WHERE k='v2'").toArray().length){
      if(this.sql.exec("SELECT name FROM sqlite_master WHERE type='table' AND name='bars'").toArray().length){
        const G = new Map();
        for(const r of this.sql.exec("SELECT t,o,h,l,c,v FROM bars ORDER BY t")){
          const g = Math.floor(r.t / grp) * grp;
          if(!G.has(g)) G.set(g, []);
          G.get(g).push([r.t, r.o, r.h, r.l, r.c, r.v]);
        }
        for(const [g, B] of G) this.sql.exec("INSERT OR REPLACE INTO grp VALUES(?,?)", g, JSON.stringify(B));
      }
      this.sql.exec("INSERT OR REPLACE INTO meta VALUES('v2','1')");
    }
    this.ready = true;
  }
  async fetch(req){
    const url = new URL(req.url);
    const grp = Number(url.searchParams.get("grp")) || 60;
    this.migrate(grp);
    if(url.pathname === "/push"){
      const d = await req.json();
      const G = new Map();
      let n = 0;
      for(const b of d.b || []){
        if(!Array.isArray(b) || b.length < 6 || !b.every(Number.isFinite)) continue;
        const g = Math.floor(b[0] / grp) * grp;
        if(!G.has(g)) G.set(g, []);
        G.get(g).push(b.slice(0, 6));
        n++;
      }
      // 同一段的舊 K 讀出來，以 K 棒時間覆蓋（同舊版 INSERT OR REPLACE），排好序寫回
      for(const [g, B] of G){
        const old = this.sql.exec("SELECT j FROM grp WHERE g = ?", g).toArray();
        const m = new Map(old.length ? JSON.parse(old[0].j).map(x => [x[0], x]) : []);
        for(const b of B) m.set(b[0], b);
        this.sql.exec("INSERT OR REPLACE INTO grp VALUES(?,?)", g, JSON.stringify([...m.values()].sort((a, b) => a[0] - b[0])));
      }
      const now = Math.floor(Date.now() / 1000);
      const cut = now - (Number(url.searchParams.get("keep")) || 3 * 86400);
      // cut：舊版會把 t < cut 的 K 刪掉；新版整段都過期才刪，邊界那段在讀取時濾掉，結果相同
      this.sql.exec("INSERT OR REPLACE INTO meta VALUES('sym',?),('at',?),('cut',?)", String(d.s || ""), String(now), String(cut));
      this.sql.exec("DELETE FROM grp WHERE g < ?", cut - grp);
      return json({ok: true, n});
    }
    const meta = Object.fromEntries(this.sql.exec("SELECT k,v FROM meta").toArray().map(r => [r.k, r.v]));
    const lo = Math.max(Number(url.searchParams.get("since")) || 0, Number(meta.cut) || 0);
    const bars = [];
    for(const r of this.sql.exec("SELECT j FROM grp WHERE g >= ? ORDER BY g", Math.floor(lo / grp) * grp))
      for(const b of JSON.parse(r.j)) if(b[0] >= lo) bars.push(b);
    return json({at: Number(meta.at) || 0, sym: meta.sym || "", bars});
  }
}

export default {
  async fetch(req, env){
    const origin = req.headers.get("Origin");
    const url = new URL(req.url);
    const m0 = url.pathname.match(/^\/(tx|hsi|tw)(\/push)?$/);
    if(!m0) return new Response("not found", {status: 404});
    const name = m0[1], F = FEEDS[name];
    const stub = env.TX.get(env.TX.idFromName(name));
    const q = "keep=" + F.keep + "&grp=" + F.grp;
    if(m0[2] && req.method === "POST"){
      if(!env.PUSH_KEY || url.searchParams.get("k") !== env.PUSH_KEY.trim()) return new Response("forbidden", {status: 403});
      // TradingView 送來的是警報訊息原文；內容是 JSON 字串
      // 第一版腳本用 syminfo.tickerid，v6 會帶出一串含引號的設定文字把 JSON 弄壞，
      // 那時建好的警報不重建也能用：整段解析失敗就只挑出 "b":[...] 那段
      const text = await req.text();
      let body;
      try{ body = JSON.parse(text); }catch(e){
        const m = text.match(/"b":(\[[\[\]\d.,\s-]*\])\}\s*$/);
        try{ body = {s: F.sym, b: JSON.parse(m[1])}; }catch(e2){ return new Response("bad json", {status: 400}); }
      }
      try{ return await stub.fetch("https://do/push?" + q, {method: "POST", body: JSON.stringify(body)}); }
      catch(e){ return json({err: String(e && e.message || e)}, 503); }
    }
    if(!m0[2] && req.method === "GET"){
      // v2：出錯時回 503＋錯誤原文並帶跨網域標頭，網頁才看得到原因（舊版回空白 500，網頁只顯示 Failed to fetch）
      let out;
      try{
        const r = await stub.fetch("https://do/get?" + q + "&since=" + encodeURIComponent(url.searchParams.get("since") || "0"));
        out = new Response(r.body, r);
      }catch(e){ out = json({err: String(e && e.message || e)}, 503); }
      for(const [k, v] of Object.entries(cors(origin))) out.headers.set(k, v);
      return out;
    }
    return new Response("not found", {status: 404});
  }
};
