# Bilibili 私信適配器 - 技術文檔

> 適用版本：插件 v0.2.0+ / AstrBot >= 4.25

## 1. 概述

面向 AstrBot 的 Bilibili 私信平台適配器插件（非官方 API）。支持接收與發送私信，目前覆蓋文本與圖片。

- **認證**：瀏覽器 Cookie（`SESSDATA`, `bili_jct`）+ 設備標識（`device_id`, `user_agent`）。
- **風控對策**：發送消息時附加 WBI 簽名與 buvid 指紋 Cookie，規避 412 Precondition Failed。
- **穩定性**：異步輪詢、自適應間隔、連接池、指數退避重試。

---

## 2. 模組結構

```
astrbot_plugin_bilibili_adapter/
├── main.py               # 插件入口（Star）：導入即註冊適配器
├── bilibili_adapter.py   # 平台適配器：生命週期、輪詢、消息轉換
├── bilibili_client.py    # API 客戶端：HTTP 請求、WBI 簽名、圖片上傳
├── bilibili_event.py     # 平台事件：發送策略（文本合併、圖片處理）
├── bilibili_config.py    # 配置定義：默認模板、表單元數據、i18n 文案
├── bilibili_wbi.py       # WBI 簽名算法（mixin key + MD5）
└── metadata.yaml         # 插件元數據（含 astrbot_version 版本約束）
```

### `main.py` - 插件入口

`BilibiliPlugin(Star)` 在 `__init__` 中導入 `bilibili_adapter` 模組以觸發適配器註冊。
熱重載時，AstrBot 核心透過 `unregister_platform_adapters_by_module()` 按模組路徑自動注銷本插件的適配器，無需手動清理。

### `bilibili_adapter.py` - 適配器主體

`BilibiliAdapter` 繼承 `Platform`（v4.25+ 基類簽名為 `__init__(self, config: dict, event_queue: Queue)`）。

- **初始化 (`__init__`)**：`_validate_config()` 校驗必填項與數值範圍，設置輪詢與網絡參數。
- **消息輪詢 (`run`)**：創建 `BilibiliClient` → `get_my_info()` 獲取自身 UID → 進入輪詢循環。忽略 `talker_id=0` 的系統通知；自適應輪詢間隔（含 ±10% 抖動）；網絡錯誤指數退避，超過 `max_retry_count` 後停止。
- **消息轉換 (`convert_message`)**：轉換為 `AstrBotMessage`。文本為 `[Plain(text)]`，圖片為 `[Image.fromURL(url)]`；`abm.message_id = f"{session_talker_id}-{msg_seqno}"`；時間戳由模組級 `_normalize_timestamp()` 統一歸一化為秒級。
- **事件提交 (`handle_msg`)**：組裝 `BilibiliPlatformEvent` 並 `commit_event()`。
- **離線消息過濾**：以啟動時間戳 `_startup_ts` 過濾離線期間舊消息（只 ACK 不回覆）。

### `bilibili_client.py` - API 客戶端

封裝所有與 Bilibili 後端 API 的交互。

- **會話與消息**：`get_new_sessions`, `get_messages`, `update_ack`。
- **發送消息**：`send_text_message` / `send_image_message` → `_build_msg_payload()` 構造表單 → `_send_message()` 統一發送。
- **WBI 簽名**：`_get_wbi_keys()` 從 nav 接口提取 img_key/sub_key（TTL 快取 12 小時），`_sign_wbi_params()` 生成 `wts` 與 `w_rid` 查詢參數。
- **buvid 指紋**：`_ensure_buvid()` 從 finger/spi 接口獲取 buvid3/buvid4 並注入 Cookie。
- **412 重試**：`_send_message()` 遇 412 時強制刷新 WBI 密鑰與 buvid 後重試一次。
- **圖片上傳**：`upload_image` 帶 LRU + TTL 內存快取（按 path/url 鍵，容量 256，TTL 30 分鐘）。
- **網絡配置**：超時、連接池、DNS TTL、Keep-Alive 均可配置；`_safe_json_from_response()` 安全解析 JSON。

### `bilibili_event.py` - 平台事件

`BilibiliPlatformEvent(AstrMessageEvent)`：

- **發送 (`send`)**：連續 `Plain` 合併為單條私信；圖片支持 `path`/`url`/`raw` 三種來源，先上傳後發送；本地讀檔用 `asyncio.to_thread()` 避免阻塞。
- **接收者解析**：優先 `session_id` 轉整型，失敗回退 `sender.user_id`。
- **概要生成**：`get_message_outline()` 兼容 `MessageChain` 與 `list` 兩種形態。

### `bilibili_config.py` - 配置定義

- `DEFAULT_CONFIG_TMPL`：默認配置模板，註冊時傳入 `default_config_tmpl`。
- `CONFIG_METADATA`：每個配置項的 `description`/`type`/`hint`，驅動 WebUI 表單生成。
- `I18N_RESOURCES`：`zh-CN`（由 `CONFIG_METADATA` 派生）與 `en-US`（單獨維護）兩種語言文案。

### `bilibili_wbi.py` - WBI 簽名算法

純函數模組：`get_mixin_key()`（按混淆表重排）、`extract_key_from_url()`、`sign_params()`（排序參數 + 過濾特殊字符 + MD5）。

---

## 3. 消息處理流程

```
接收：Adapter.run → Client.get_new_sessions → Client.get_messages
      → Adapter.convert_message → Adapter.handle_msg → commit_event

發送：Event.send → [Client.upload_image] → Client.send_* 
      → Client._send_message（WBI 簽名 + buvid，412 時重試）
```

- **已讀模式（可選）**：當 `process_read_messages = true`、`unread_count == 0` 且檢測到 ACK 提升時，觸發 `_process_recent_read_session()`，按 `read_prefetch_window`（1-10，默認 1）回溯近期消息，結合啟動時間與已處理水位（`_last_processed_seqno_by_talker`）去重。

---

## 4. API 端點

| 端點 | 方法 | 用途 |
| --- | --- | --- |
| `/x/space/myinfo` | GET | 獲取自身 UID |
| `/x/web-interface/nav` | GET | 獲取 WBI 簽名密鑰 |
| `/x/frontend/finger/spi` | GET | 獲取 buvid3/buvid4 指紋 |
| `/session_svr/v1/session_svr/new_sessions` | GET | 拉取新會話 |
| `/svr_sync/v1/svr_sync/fetch_session_msgs` | GET | 拉取會話內消息 |
| `/session_svr/v1/session_svr/update_ack` | POST | 更新已讀回執 |
| `/web_im/v1/web_im/send_msg` | POST | 發送私信（需 WBI 簽名） |
| `/x/dynamic/feed/draw/upload_bfs` | POST | 上傳圖片 |

前綴：消息類接口為 `api.vc.bilibili.com`，其餘為 `api.bilibili.com`。

---

## 5. 安裝與配置

1) 安裝依賴：

```bash
pip install -r requirements.txt
```

2) 配置必填項（WebUI 平台適配器表單）：

- `SESSDATA`, `bili_jct`：瀏覽器 Cookie。
- `device_id`, `user_agent`：設備標識。
- （可選）輪詢與網絡參數：`polling_interval`、`timeout_total`、`connection_limit` 等。
- （可選）已讀處理：`process_read_messages`（默認 False）、`read_prefetch_window`（1-10，默認 1）。

---

## 6. 運行與熱重載

- 啟用平台配置後，AstrBot 平台管理器調用 `cls_type(platform_config, settings, event_queue)` 創建適配器實例並啟動 `run()`。
- 插件熱重載由核心自動處理：按模組路徑注銷舊適配器後重新註冊，無需插件側清理。

---

## 7. 已知限制

- 非官方 API 存在變更風險，請保持 Cookie 有效並定期更新 `SESSDATA`。
- 僅支持文本與圖片消息，其它類型可按需擴展。
- WBI 簽名算法基於社區逆向成果，B 站升級風控後可能需要跟進。

---

## 8. 故障排查

- **啟動報錯 / 無法獲取 UID**：檢查 `SESSDATA` 與 `bili_jct` 是否有效；檢查網絡與代理。
- **發送消息 412**：適配器會自動刷新 WBI 密鑰與 buvid 重試；若持續失敗，更換 `user_agent` 並更新 Cookie。
- **WebUI 表單顯示鍵名而非標題**：要求 AstrBot >= 4.25（依賴 `config_metadata` / `i18n_resources` 機制）。
- **圖片發送失敗**：檢查圖片來源可達性；觀察是否命中上傳快取；查看 API 返回碼。

---

## 9. 授權與風險聲明

本項目基於逆向工程，僅供學習與研究，請遵守相關法律與站點條款。
