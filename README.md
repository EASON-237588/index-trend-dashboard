# 指數趨勢儀表板

那斯達克 30 秒 Range Filter、那斯達克 6 分與黃金 8 分 Donchian 的即時趨勢訊號。單檔 `index.html`，純前端、免金鑰，雙擊或開 GitHub Pages 即可使用。

- 線上版：https://eason-237588.github.io/index-trend-dashboard/
- 版本號唯一來源：`index.html` 裡的 `const VERSION`，標題同步改

## 資料源

| 格 | 商品 | 來源 |
|---|---|---|
| 那斯達克 30 秒 | Binance QQQUSDT 永續合約逐筆成交合成 | 價格依 Hyperliquid `xyz:XYZ100` 換算成那斯達克 100 點數 |
| 那斯達克 6 分 | Binance QQQUSDT 1 分 K 合成 | 同上 |
| 黃金 8 分 | Binance XAUUSDT 1 分 K 合成 | — |

NQ 期貨沒有免費即時來源（Yahoo 延遲 10 分鐘且無 CORS），所以用 QQQ 合約代替；剔除美東休市時段，K 棒從美東 18:00 起算。

## 策略

照 TradingView 版面 `JuwDqkR0` 上的腳本原始碼逐行移植，參數照圖上設定（2026-10-07 讀取）。

- Range Filter：`Optimized Range Filter for Gold`，200／23、hl2、ATR 過濾與量過濾開、止損止盈關
- Donchian：`11分 Donchian 反轉 · 逆勢分批 v1.3.0`，350 小時、1 段；那斯達克只做多＋停損 8 ATR，黃金多空反轉

## 對帳結果（2026-10-07）

- 黃金 8 分：最近一次翻空時間與 TradingView 完全相同（9/02 09:44）
- 那斯達克 6 分：進場晚 3 天（TV 9/18、這裡 9/21），推測是 NQ 回溯調整連續合約換月平移
- 那斯達克 30 秒：美歐時段翻轉差約 2 分鐘；美東半夜 QQQ 合約成交稀疏，會多出假翻轉
