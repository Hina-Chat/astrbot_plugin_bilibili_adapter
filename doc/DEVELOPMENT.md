# AstrBot Bilibili 私信適配器 - 開發文檔

> 適用版本：插件 v0.2.0+ / AstrBot >= 4.25

## 1. 項目概述

- **定位**：基於逆向工程的非官方 API 的 Bilibili 私信平台適配器插件。
- **技術棧**：Python 3.10+、asyncio、aiohttp、AstrBot 插件機制。
- **認證**：瀏覽器 Cookie（`SESSDATA`, `bili_jct`）+ 設備標識（`device_id`, `user_agent`）。
- **風控**：發送私信需 WBI 簽名（`wts` + `w_rid`）與 buvid 指紋 Cookie。
- **消息類型**：文本、圖片（更多類型可擴展）。

## 2. 架構與職責劃分

| 文件 | 職責 |
| --- | --- |
| `main.py` | 插件入口（`Star`）。導入 `bilibili_adapter` 即完成適配器註冊。 |
| `bilibili_adapter.py` | 平台適配器。配置校驗、主輪詢、會話處理、消息轉換、事件提交。 |
| `bilibili_client.py` | API 客戶端。HTTP 請求、WBI 簽名、buvid 管理、圖片上傳/快取、消息收發、ACK。 |
| `bilibili_event.py` | 平台事件。發送策略：文本合併、圖片三來源處理、接收者解析。 |
| `bilibili_config.py` | 配置定義。默認模板、WebUI 表單元數據、i18n 文案。 |
| `bilibili_wbi.py` | WBI 簽名純函數：mixin key 重排 + 參數排序 + MD5。 |

依賴方向：`main → adapter → {client, config, event}`，`client → wbi`。`config` 與 `wbi` 為無外部依賴的葉子模組。

## 3. 平台註冊與熱重載

- 註冊使用 v4.25+ 的完整參數：

```python
@register_platform_adapter(
    "bilibili",
    "Bilibili 私信适配器",
    default_config_tmpl=DEFAULT_CONFIG_TMPL,
    config_metadata=CONFIG_METADATA,      # WebUI 表單元數據（PR #5045）
    i18n_resources=I18N_RESOURCES,        # 多語言文案
    support_streaming_message=False,
    adapter_display_name="Bilibili",
    logo_path="assets/bilibili.svg",
)
```

- **基類簽名**（v4.25+）：`Platform.__init__(self, config: dict, event_queue: Queue)`，子類須 `super().__init__(platform_config, event_queue)`。
- **熱重載**：核心 `star_manager` 在插件重載時調用 `unregister_platform_adapters_by_module()` 按模組路徑注銷適配器，插件側無需（也不應）手動清理註冊表。
- 核心 `register.py` 對重名有嚴格保護：重複註冊將拋出 `ValueError`。

## 4. 配置體系（bilibili_config.py）

三個導出常量，職責分離：

- **`DEFAULT_CONFIG_TMPL`**：用戶配置的默認值。核心（`id`/`type`/`enable`/Cookie 四項）、消息處理、輪詢、網絡、API 五組。
- **`CONFIG_METADATA`**：`{key: {description, type, hint}}`，WebUI 據此生成表單。未提供時 WebUI 退化為原始鍵值對編輯框。
- **`I18N_RESOURCES`**：`{"zh-CN": {...}, "en-US": {...}}`。zh-CN 由 `CONFIG_METADATA` 派生（單一事實來源），en-US 單獨維護。

新增配置項時需同步三處：`DEFAULT_CONFIG_TMPL`、`CONFIG_METADATA`、`_I18N_EN_US`，並視需要在 `_validate_config()` 的 `numeric_params` 中補充範圍校驗。

## 5. 客戶端要點（bilibili_client.py)

### 端點常量

模組頂部集中定義，消息類接口走 `api.vc.bilibili.com`，其餘走 `api.bilibili.com`。

### WBI 簽名與風控

- `_get_wbi_keys()`：從 `/x/web-interface/nav` 響應的 `wbi_img` 提取 img_key/sub_key，TTL 快取 12 小時，支持 `force_refresh`。
- `_sign_wbi_params()`：調用 `bilibili_wbi.sign_params()` 生成帶 `wts`/`w_rid` 的查詢參數。
- `_ensure_buvid()`：從 `/x/frontend/finger/spi` 獲取 buvid3/buvid4，注入 `self._cookies` 與 session cookie_jar。
- `_send_message()`：簽名參數掛 query string，表單走 body；收到 412 時刷新密鑰與 buvid 後重試一次。

### 其它

- `_build_msg_payload()`：統一構造私信表單（文本 `msg_type=1` / 圖片 `msg_type=2`），`build`/`mobi_app` 取自配置。
- `upload_image()`：LRU + TTL 快取（鍵為圖片 path/url），命中跳過上傳。
- `_safe_json_from_response()`：非 JSON 響應返回 None 並打印精簡片段。
- 統一 `aiohttp.ClientSession`：超時/連接池/DNS TTL/Keep-Alive 全部可配置。

## 6. 主循環與消息處理（bilibili_adapter.py）

- **啟動**：創建 `BilibiliClient` → `get_my_info()` 成功後記錄 `_startup_ts` 並開始輪詢。
- **拉取**：`get_new_sessions(begin_ts)` → 遍歷 `session_list`（忽略 `talker_id=0` 系統通知）。
- **未讀處理**：`_process_unread_session()` → `get_messages()` → 逐條轉換提交 → `update_ack()`。
- **已讀處理（可選）**：`unread_count == 0` 且 ACK 提升且 `process_read_messages=true` 時觸發 `_process_recent_read_session()`，按 `read_prefetch_window` 回溯，結合 `_startup_ts` 與已處理水位去重。
- **時間戳**：模組級 `_normalize_timestamp()` 統一處理數字/字符串/毫秒級輸入，三處調用（未讀、已讀、轉換）。
- **轉換**：`convert_message()` 生成 `AstrBotMessage`：
  - 文本：`[Plain(text)]`；圖片：`[Image.fromURL(url)]`（含非標準 JSON 的正則回退）。
  - `abm.message_id = f"{session_talker_id}-{msg_seqno}"`；`abm.session_id = str(session_talker_id)`；`abm.self_id` 取啟動時 UID。
- **退出**：循環結束統一關閉 client，`terminate()` 走 `shutdown()`。

## 7. 發送策略（bilibili_event.py）

- **文本合併**：遍歷消息鏈，連續 `Plain` 緩衝合併為單條私信；遇圖片或結尾時沖刷。
- **接收者解析**：`session_id` 轉整型優先，失敗回退 `message_obj.sender.user_id`。
- **圖片處理**：支持 `path`/`url`/`raw` 三種來源；本地讀檔用 `asyncio.to_thread()`；上傳命中快取則復用。
- **可觀測性**：不支持的組件記錄 `warning`。

## 8. 開發規範

- 日誌只使用 `from astrbot.api import logger`。
- 網絡請求只使用 aiohttp（異步），禁止 requests。
- 提交前運行 `ruff check .` 與 `ruff format .`。
- API 調用與關鍵節點均需 try/except 並保留 `asyncio.CancelledError` 直接 raise。
- 新依賴須同步寫入 `requirements.txt`。

## 9. 測試與驗收建議

- **文本聚合**：`Plain('A'), Plain('B')` 僅發一條 `AB`。
- **圖文混排**：文本→圖片→文本，圖片前應先沖刷文本緩衝。
- **接收者回退**：`session_id` 非數字時使用 `sender.user_id`。
- **412 重試**：模擬發送被風控，驗證刷新 WBI/buvid 後重試一次的行為。
- **離線消息**：啟動前的舊消息應只 ACK 不回覆。
- **WebUI 表單**：確認各配置項顯示可讀標題與提示（zh-CN / en-US）。
