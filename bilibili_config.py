"""Bilibili 适配器的配置定义。

集中管理默认配置模板、WebUI 表单元数据与国际化文案，
供 bilibili_adapter 在注册平台适配器时使用。
"""

# 默认配置模板：用户在 WebUI 中填写后将作为 platform_config 传入适配器
DEFAULT_CONFIG_TMPL: dict = {
    # 核心
    "id": "default",
    "type": "bilibili",
    "enable": False,
    "SESSDATA": "",
    "bili_jct": "",
    "device_id": "",
    "user_agent": "",
    "buvid3": "",
    "buvid4": "",
    # 消息处理
    "process_read_messages": False,
    "read_prefetch_window": 1,
    # 轮询
    "polling_interval": 5,
    "min_polling_interval": 2,
    "max_polling_interval": 30,
    "max_retry_count": 3,
    # 网络
    "timeout_total": 30,
    "timeout_connect": 10,
    "timeout_sock_read": 20,
    "connection_limit": 100,
    "connection_limit_per_host": 30,
    "dns_cache_ttl": 300,
    "keepalive_timeout": 60,
    # API
    "message_batch_size": 20,
    "api_build_version": 0,
    "api_mobi_app": "web",
}

# 配置项元数据：description/type/hint，用于 WebUI 生成表单
CONFIG_METADATA: dict = {
    "SESSDATA": {
        "description": "SESSDATA",
        "type": "string",
        "hint": "必填。浏览器 Cookie 中的 SESSDATA，用于身份认证。建议定期更新。",
    },
    "bili_jct": {
        "description": "bili_jct（CSRF Token）",
        "type": "string",
        "hint": "必填。浏览器 Cookie 中的 bili_jct，发送消息时必需。",
    },
    "device_id": {
        "description": "设备 ID",
        "type": "string",
        "hint": "必填。用于模拟设备标识，可填写任意 UUID 格式字符串。",
    },
    "user_agent": {
        "description": "User-Agent",
        "type": "string",
        "hint": "必填。填写浏览器的 User-Agent 字符串。",
    },
    "buvid3": {
        "description": "buvid3（可选）",
        "type": "string",
        "hint": "选填。浏览器 Cookie 中的 buvid3 设备指纹。留空时自动从接口获取；填写浏览器的真实值可提高风控信任度。",
    },
    "buvid4": {
        "description": "buvid4（可选）",
        "type": "string",
        "hint": "选填。浏览器 Cookie 中的 buvid4 设备指纹。留空时自动从接口获取；建议与 buvid3 一同填写。",
    },
    "process_read_messages": {
        "description": "处理已读消息",
        "type": "bool",
        "hint": "默认关闭。开启后将尝试处理新到达但已被标记为已读的消息。",
    },
    "read_prefetch_window": {
        "description": "已读消息回溯条数",
        "type": "int",
        "hint": "默认 1。开启『处理已读消息』时生效，设定回溯抓取的最近消息条数。",
    },
    "polling_interval": {
        "description": "轮询间隔（秒）",
        "type": "int",
        "hint": "默认 5。拉取新会话的基础轮询间隔。",
    },
    "min_polling_interval": {
        "description": "最小轮询间隔（秒）",
        "type": "int",
        "hint": "默认 2。自适应轮询间隔的下限。",
    },
    "max_polling_interval": {
        "description": "最大轮询间隔（秒）",
        "type": "int",
        "hint": "默认 30。自适应轮询间隔的上限。",
    },
    "max_retry_count": {
        "description": "最大重试次数",
        "type": "int",
        "hint": "默认 3。连续异常时的最大重试次数，超出后适配器停止运行。",
    },
    "timeout_total": {
        "description": "请求总超时（秒）",
        "type": "int",
        "hint": "默认 30。整体 HTTP 请求的最大耗时。",
    },
    "timeout_connect": {
        "description": "连接超时（秒）",
        "type": "int",
        "hint": "默认 10。TCP 连接建立的超时时间。",
    },
    "timeout_sock_read": {
        "description": "读取超时（秒）",
        "type": "int",
        "hint": "默认 20。收到响应后的数据读取超时。",
    },
    "connection_limit": {
        "description": "连接池总上限",
        "type": "int",
        "hint": "默认 100。HTTP 客户端的最大并发连接数。",
    },
    "connection_limit_per_host": {
        "description": "单主机连接上限",
        "type": "int",
        "hint": "默认 30。对同一主机的最大并发连接数。",
    },
    "dns_cache_ttl": {
        "description": "DNS 缓存时间（秒）",
        "type": "int",
        "hint": "默认 300。DNS 解析结果的缓存时长。",
    },
    "keepalive_timeout": {
        "description": "Keep-Alive 超时（秒）",
        "type": "int",
        "hint": "默认 60。空闲连接的保活时间。",
    },
    "message_batch_size": {
        "description": "单次拉取消息数",
        "type": "int",
        "hint": "默认 20。每次调用消息接口时获取的消息数量。",
    },
    "api_build_version": {
        "description": "API Build 版本",
        "type": "int",
        "hint": "默认 0。Bilibili API 的 build 参数，一般无需修改。",
    },
    "api_mobi_app": {
        "description": "API mobi_app 标识",
        "type": "string",
        "hint": "默认 web。Bilibili API 的 mobi_app 参数，一般无需修改。",
    },
}

# 英文文案（中文直接复用 CONFIG_METADATA，避免重复维护）
_I18N_EN_US: dict = {
    "SESSDATA": {
        "description": "SESSDATA",
        "hint": "Required. SESSDATA from browser cookies, used for authentication. Refresh periodically.",
    },
    "bili_jct": {
        "description": "bili_jct (CSRF Token)",
        "hint": "Required. bili_jct from browser cookies, needed for sending messages.",
    },
    "device_id": {
        "description": "Device ID",
        "hint": "Required. Used to simulate a device identifier. Any UUID-format string works.",
    },
    "user_agent": {
        "description": "User-Agent",
        "hint": "Required. Browser User-Agent string.",
    },
    "buvid3": {
        "description": "buvid3 (Optional)",
        "hint": "Optional. buvid3 device fingerprint from browser cookies. Auto-fetched if empty; using your browser's real value improves risk-control trust.",
    },
    "buvid4": {
        "description": "buvid4 (Optional)",
        "hint": "Optional. buvid4 device fingerprint from browser cookies. Auto-fetched if empty; recommended to fill together with buvid3.",
    },
    "process_read_messages": {
        "description": "Process Already-Read Messages",
        "hint": "Off by default. When enabled, processes newly arrived messages already marked as read.",
    },
    "read_prefetch_window": {
        "description": "Read Message Lookback Count",
        "hint": "Default 1. Number of recent messages to fetch when 'Process Already-Read Messages' is enabled.",
    },
    "polling_interval": {
        "description": "Polling Interval (s)",
        "hint": "Default 5. Base interval for fetching new sessions.",
    },
    "min_polling_interval": {
        "description": "Min Polling Interval (s)",
        "hint": "Default 2. Lower bound for adaptive polling interval.",
    },
    "max_polling_interval": {
        "description": "Max Polling Interval (s)",
        "hint": "Default 30. Upper bound for adaptive polling interval.",
    },
    "max_retry_count": {
        "description": "Max Retry Count",
        "hint": "Default 3. Max consecutive retries before the adapter stops.",
    },
    "timeout_total": {
        "description": "Total Request Timeout (s)",
        "hint": "Default 30. Max total time for an HTTP request.",
    },
    "timeout_connect": {
        "description": "Connect Timeout (s)",
        "hint": "Default 10. TCP connection establishment timeout.",
    },
    "timeout_sock_read": {
        "description": "Socket Read Timeout (s)",
        "hint": "Default 20. Data read timeout after response received.",
    },
    "connection_limit": {
        "description": "Connection Pool Limit",
        "hint": "Default 100. Max total concurrent HTTP connections.",
    },
    "connection_limit_per_host": {
        "description": "Per-Host Connection Limit",
        "hint": "Default 30. Max concurrent connections to a single host.",
    },
    "dns_cache_ttl": {
        "description": "DNS Cache TTL (s)",
        "hint": "Default 300. How long to cache DNS resolution results.",
    },
    "keepalive_timeout": {
        "description": "Keep-Alive Timeout (s)",
        "hint": "Default 60. Idle connection keep-alive duration.",
    },
    "message_batch_size": {
        "description": "Message Fetch Batch Size",
        "hint": "Default 20. Number of messages retrieved per API call.",
    },
    "api_build_version": {
        "description": "API Build Version",
        "hint": "Default 0. Bilibili API build parameter, usually no need to change.",
    },
    "api_mobi_app": {
        "description": "API mobi_app Identifier",
        "hint": "Default web. Bilibili API mobi_app parameter, usually no need to change.",
    },
}

# 国际化资源：zh-CN 由 CONFIG_METADATA 派生，en-US 单独维护
I18N_RESOURCES: dict = {
    "zh-CN": {
        key: {"description": meta["description"], "hint": meta["hint"]}
        for key, meta in CONFIG_METADATA.items()
    },
    "en-US": _I18N_EN_US,
}
