/* 指數趨勢儀表板・NQ 中繼站
   Yahoo 不給瀏覽器跨網域讀，所以由這裡轉一手。不存資料：每次向 Yahoo 抓
   5 分 K（約 60 天）＋ 2 分 K（約 36 天），2 分 K 涵蓋不到的前段用 5 分 K 補，
   合併成一串回傳 [t(毫秒), o, h, l, c]。這跟 2026-10-08 對帳 TradingView 用的是同一套抓法。 */
const SYM = "NQ=F";
const ALLOW = [/^https:\/\/eason-237588\.github\.io$/, /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/];
const TTL = 60;   // 秒；Yahoo 本身延遲 10 分鐘，快取 1 分鐘沒有差別

async function yahoo(iv, range){
  const u = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(SYM)}?interval=${iv}&range=${range}&includePrePost=true`;
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

    const cache = caches.default, key = new Request(url.origin + "/nq");
    let res = await cache.match(key);
    if(!res){
      try{
        const [m5, m2] = await Promise.all([yahoo("5m", "60d"), yahoo("2m", "60d")]);
        const cut = m2.length ? m2[0][0] : Infinity;
        const bars = m5.filter(b => b[0] < cut).concat(m2);
        res = new Response(JSON.stringify({sym: SYM, at: Date.now(), cut, bars}), {
          headers: {"Content-Type": "application/json", "Cache-Control": `public, max-age=${TTL}`}});
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
