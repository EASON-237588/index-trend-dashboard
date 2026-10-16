/* 指數趨勢儀表板・台指即時轉接站
   期交所即時行情網站不穩（2026-10-16 實測會整站回 520），改由 TradingView 警報送資料：
   台指期 1 秒圖掛一支腳本，每約 15 秒把這段 1 秒 K 打包成一則警報，打到 POST /tx/push?k=密碼。
   警報在 TradingView 伺服器上跑，三台電腦全關也照送。
   資料存在 Durable Object 的 SQLite（KV 免費一天只能寫 1,000 次，不夠），只留最近 3 天，
   更早的由網頁讀盤後逐筆檔。
   GET /tx?since=秒  回傳 {at, sym, bars:[[t(秒), o, h, l, c, v], ...]}，t 是該根 1 秒 K 的開始時間。 */
const ALLOW = [/^https:\/\/eason-237588\.github\.io$/, /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/];
const KEEP = 3 * 86400;   // 秒

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
    this.sql.exec(`CREATE TABLE IF NOT EXISTS bars(t INTEGER PRIMARY KEY, o REAL, h REAL, l REAL, c REAL, v REAL);
                   CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);`);
  }
  async fetch(req){
    const url = new URL(req.url);
    if(url.pathname === "/push"){
      const d = await req.json();
      let n = 0;
      for(const b of d.b || []){
        if(!Array.isArray(b) || b.length < 6 || !b.every(Number.isFinite)) continue;
        this.sql.exec("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?)", ...b.slice(0, 6));
        n++;
      }
      const now = Math.floor(Date.now() / 1000);
      this.sql.exec("INSERT OR REPLACE INTO meta VALUES('sym',?),('at',?)", String(d.s || ""), String(now));
      this.sql.exec("DELETE FROM bars WHERE t < ?", now - KEEP);
      return json({ok: true, n});
    }
    const since = Number(url.searchParams.get("since")) || 0;
    const bars = this.sql.exec("SELECT t,o,h,l,c,v FROM bars WHERE t >= ? ORDER BY t", since).toArray()
      .map(r => [r.t, r.o, r.h, r.l, r.c, r.v]);
    const meta = Object.fromEntries(this.sql.exec("SELECT k,v FROM meta").toArray().map(r => [r.k, r.v]));
    return json({at: Number(meta.at) || 0, sym: meta.sym || "", bars});
  }
}

export default {
  async fetch(req, env){
    const origin = req.headers.get("Origin");
    const url = new URL(req.url);
    const stub = env.TX.get(env.TX.idFromName("tx"));
    if(url.pathname === "/tx/push" && req.method === "POST"){
      if(!env.PUSH_KEY || url.searchParams.get("k") !== env.PUSH_KEY.trim()) return new Response("forbidden", {status: 403});
      // TradingView 送來的是警報訊息原文；內容是 JSON 字串
      // 第一版腳本用 syminfo.tickerid，v6 會帶出一串含引號的設定文字把 JSON 弄壞，
      // 那時建好的警報不重建也能用：整段解析失敗就只挑出 "b":[...] 那段
      const text = await req.text();
      let body;
      try{ body = JSON.parse(text); }catch(e){
        const m = text.match(/"b":(\[[\[\]\d.,\s-]*\])\}\s*$/);
        try{ body = {s: "TXF1!", b: JSON.parse(m[1])}; }catch(e2){ return new Response("bad json", {status: 400}); }
      }
      return stub.fetch("https://do/push", {method: "POST", body: JSON.stringify(body)});
    }
    if(url.pathname === "/tx" && req.method === "GET"){
      const r = await stub.fetch("https://do/get?since=" + encodeURIComponent(url.searchParams.get("since") || "0"));
      const out = new Response(r.body, r);
      for(const [k, v] of Object.entries(cors(origin))) out.headers.set(k, v);
      return out;
    }
    return new Response("not found", {status: 404});
  }
};
