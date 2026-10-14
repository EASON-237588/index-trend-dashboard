/* 指數趨勢儀表板・NQ 中繼站
   Yahoo 不給瀏覽器跨網域讀，所以由這裡轉一手。不存資料：每次向 Yahoo 抓
   5 分 K（約 60 天）＋ 2 分 K（約 36 天），2 分 K 涵蓋不到的前段用 5 分 K 補，
   合併成一串回傳 [t(毫秒), o, h, l, c]。這跟 2026-10-08 對帳 TradingView 用的是同一套抓法。 */
const SYM = "NQ=F";   // 沒帶 ?sym= 時用連續月（舊版網頁）
const SYM_OK = /^NQ[HMUZ]\d{2}\.CME$/;   // v1.8.5 起網頁指定單一月份合約，只接受這種格式
const ALLOW = [/^https:\/\/eason-237588\.github\.io$/, /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/];
const TTL = 60;   // 秒；Yahoo 本身延遲 10 分鐘，快取 1 分鐘沒有差別

async function yahoo(sym, iv, range){
  const u = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?interval=${iv}&range=${range}&includePrePost=true`;
  const r = await fetch(u, {headers: {"User-Agent": "Mozilla/5.0"}});
  if(!r.ok) throw new Error(`Yahoo ${iv} HTTP ${r.status}`);
  const d = (await r.json()).chart.result[0];
  const q = d.indicators.quote[0], out = [];
  d.timestamp.forEach((t, i) => {
    const o = q.open[i], h = q.high[i], l = q.low[i], c = q.close[i];
    if(o != null && h != null && l != null && c != null) out.push([t * 1000, o, h, l, c]);
  });
  return out;
}

function cors(origin){
  const ok = origin && ALLOW.some(re => re.test(origin));
  return ok ? {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} : {};
}

export default {
  async fetch(req, env, ctx){
    const origin = req.headers.get("Origin");
    const url = new URL(req.url);
    if(url.pathname !== "/nq") return new Response("not found", {status: 404});
    const q = url.searchParams.get("sym");
    if(q && !SYM_OK.test(q)) return new Response("bad sym", {status: 400, headers: cors(origin)});
    const sym = q || SYM;
    // v1.9.0：?iv=1d 回傳日線（給 G、H、I 格的日線 60 日均線濾網），快取 30 分鐘
    const daily = url.searchParams.get("iv") === "1d";

    const cache = caches.default, key = new Request(url.origin + "/nq" + (q ? "?sym=" + q : "") + (daily ? (q ? "&" : "?") + "iv=1d" : ""));
    let res = await cache.match(key);
    if(!res){
      try{
        let body;
        if(daily){
          body = {sym, at: Date.now(), bars: await yahoo(sym, "1d", "2y")};
        }else{
          const [m5, m2] = await Promise.all([yahoo(sym, "5m", "60d"), yahoo(sym, "2m", "60d")]);
          const cut = m2.length ? m2[0][0] : Infinity;
          body = {sym, at: Date.now(), cut, bars: m5.filter(b => b[0] < cut).concat(m2)};
        }
        res = new Response(JSON.stringify(body), {
          headers: {"Content-Type": "application/json", "Cache-Control": `public, max-age=${daily ? 1800 : TTL}`}});
        ctx.waitUntil(cache.put(key, res.clone()));
      }catch(e){
        return new Response(JSON.stringify({error: String(e.message || e)}), {status: 502,
          headers: Object.assign({"Content-Type": "application/json"}, cors(origin))});
      }
    }
    const out = new Response(res.body, res);
    for(const [k, v] of Object.entries(cors(origin))) out.headers.set(k, v);
    return out;
  }
};
