# Bilibili Adapter For AstrBot

为 AstrBot 设计的 Bilibili 私信适配器。

接入 Bilibili 私信系统，实现消息的接收和发送。

From Gemini 2.5 Pro & Claude Opus 4 & ChatGPT 5.

---

## 功能

- **接收消息**: 透过 HTTP 轮询方式获取新的私信。
- **收发消息**: 支援收发 **纯文本** 和 **图片** 消息。
- **智能轮询**: 自动调整轮询频率，以平衡实时性和资源消耗。
- **网路优化**: 内置连接池、超时管理和指数退避重试机制，提升稳定性。

## 安装

1. 下载 Code 为 ZIP；
2. 插件管理 - 安装插件，选择下载的 ZIP；
3. 配置平台适配器的 `SESSDATA` 和 `bili_jct`，其他参数酌情配置。

## 设定（基本）

```yaml
SESSDATA: 来自 Cookies
bili_jct: 来自 Cookies
device_id: 系统 UUID（或固定的自订字符串，需非空）
user_agent: 浏览器 UA
buvid3: 来自 Cookies（可选，留空时自动获取）
buvid4: 来自 Cookies（可选，留空时自动获取）
process_read_messages: 开启时，会处理近期的『已读』消息
read_prefetch_window: 已读消息的回溯数量（范围 1-10，默认 1）
```

## 設定（進階）

### 輪詢相關參數
```yaml
polling_interval: 5           # 正常輪詢間隔(秒)，每5秒檢查一次新消息
min_polling_interval: 2       # 最小輪詢間隔(秒)，最快每2秒檢查一次
max_polling_interval: 30      # 最大輪詢間隔(秒)，最慢每30秒檢查一次
max_retry_count: 3           # 最大重試次數
```

### HTTP 超時配置
```yaml
timeout_total: 30            # 總超時時間(秒)
timeout_connect: 10          # 連接超時(秒)
timeout_sock_read: 20        # 讀取超時(秒)
```

### 連接池配置
```yaml
connection_limit: 100              # 總連接池大小
connection_limit_per_host: 30      # 單主機最大連接數
```

### DNS 和保持連接配置
```yaml
dns_cache_ttl: 300          # DNS緩存存活時間(秒)
keepalive_timeout: 60       # 保持連接超時(秒)
```

### API 請求配置
```yaml
message_batch_size: 20      # 每次拉取的消息的數量
api_build_version: "0"      # API 構建版本號
api_mobi_app: "web"         # 移動應用標識
```

## 基本参数获取

###  获取 Cookies

1.  登入账户
2.  打开浏览器开发者工具 (F12)
3.  切换到 `Application` -> `Cookies` -> `https://www.bilibili.com`
4.  找到并获取 `SESSDATA` `bili_jct` `buvid3` `buvid4` 的值

### 获取 device_id

```cmd
wmic csproduct get uuid
```

这不是必须，随意编写一个或许也可以？

### 获取 user_agent

随意找一个 user_agent 生成工具，这将很有帮助。

## 依赖

- `aiohttp`

## 参考

- [bilibili-SVG](https://github.com/Remix-Design/remixicon?ref=svgrepo.com)【Apache License】
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)【CC BY-NC 4.0】

## 声明

本插件仅供学习和研究目的。

本专案与 Bilibili 官方无任何关联，作者不对因使用本程式产生的任何后果承担责任。