# 指數趨勢儀表板

那斯達克 30 秒 Range Filter、那斯達克 6 分與黃金 8 分 Donchian 的即時趨勢訊號。單檔 `index.html`，純前端、免金鑰，雙擊或開 GitHub Pages 即可使用。

- 線上版：https://eason-237588.github.io/index-trend-dashboard/
- 版本號唯一來源：`index.html` 裡的 `const VERSION`，標題同步改

## 資料源

| 格 | 商品 | 來源 |
|---|---|---|
| 那斯達克 30 秒 | Binance QQQUSDT 永續合約逐筆成交合成 | 價格依 Hyperliquid `xyz:XYZ100` 換算成那斯達克 100 點數 |
| 那斯達克 6 分 | Yahoo `NQ=F` 真 NQ 期貨 5 分＋2 分 K 合成（v1.1.0 起） | 經 Cloudflare 中繼站 `index-trend-nq`，延遲約 10 分鐘 |
| 黃金 8 分 | Binance XAUUSDT 1 分 K 合成 | — |

30 秒線沒有免費的真 NQ 秒級來源，所以用 QQQ 合約代替；剔除美東休市時段，K 棒從美東 18:00 起算。

## NQ 中繼站（`worker/`）

Yahoo 沒開 CORS，由 Cloudflare Worker 轉一手：每次請求抓 5 分 K（60 天）＋2 分 K（約 36 天），2 分 K 涵蓋不到的前段用 5 分 K 補，快取 60 秒，只回應 GitHub Pages 與 localhost。不存資料，所以部位抱超過約 45 天會看不到真正進場點。

- 網址：https://index-trend-nq.eason-237588.workers.dev/nq
- 部署：`cd worker && npx wrangler deploy`（帳號 mp5600kimo@gmail.com；PowerShell 要打 `npx.cmd`）

## 策略

照 TradingView 版面 `JuwDqkR0` 上的腳本原始碼逐行移植，參數照圖上設定（2026-10-07 讀取）。

- Range Filter：`Optimized Range Filter for Gold`，200／23、hl2、ATR 過濾與量過濾開、止損止盈關
- Donchian：`11分 Donchian 反轉 · 逆勢分批 v1.3.0`，350 小時、1 段；那斯達克只做多＋停損 8 ATR，黃金多空反轉

## 對帳結果（2026-10-07）

- 黃金 8 分：最近一次翻空時間與 TradingView 完全相同（9/02 09:44）
- 那斯達克 6 分：用 QQQ 合約時進場晚 3 天（TV 9/18、QQQ 9/21）；2026-10-08 改用 Yahoo 真 NQ 後為 9/18 13:06，與 TV 同一天
- 那斯達克 30 秒：美歐時段翻轉差約 2 分鐘；美東半夜 QQQ 合約成交稀疏，會多出假翻轉
