import asyncio
import json
import random
import re
from datetime import datetime
from typing import Optional

import aiohttp

from astrbot.api import logger
from astrbot.api.message_components import Image, Plain
from astrbot.api.platform import (
    AstrBotMessage,
    MessageMember,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
    MessageType,
)

from .bilibili_client import BilibiliClient
from .bilibili_config import CONFIG_METADATA, DEFAULT_CONFIG_TMPL, I18N_RESOURCES
from .bilibili_event import BilibiliPlatformEvent


def _normalize_timestamp(raw) -> Optional[int]:
    """将时间戳归一化为秒级整数。

    兼容数字/字符串与毫秒级输入；解析失败返回 None。
    """
    ts: Optional[int] = None
    if isinstance(raw, (int, float)):
        ts = int(raw)
    elif isinstance(raw, str):
        try:
            ts = int(float(raw))
        except ValueError:
            return None
    if ts is None:
        return None
    if ts > 10**12:  # 毫秒级时间戳
        ts //= 1000
    return ts


@register_platform_adapter(
    "bilibili",
    "Bilibili 私信适配器",
    default_config_tmpl=DEFAULT_CONFIG_TMPL,
    config_metadata=CONFIG_METADATA,
    i18n_resources=I18N_RESOURCES,
    support_streaming_message=False,
    adapter_display_name="Bilibili",
    logo_path="assets/bilibili.svg",
)
class BilibiliAdapter(Platform):
    """Bilibili 私信平台适配器：轮询拉取私信并提交事件到 AstrBot 核心。"""

    def __init__(
        self, platform_config: dict, platform_settings: dict, event_queue: asyncio.Queue
    ):
        super().__init__(platform_config, event_queue)
        logger.info("Bilibili Adapter 正在初始化...")

        # 配置驗證
        self._validate_config(platform_config)

        logger.info("Bilibili Adapter 配置驗證通過。")
        self.settings = platform_settings
        self.poll_interval = platform_config.get("polling_interval", 5)
        self.min_poll_interval = platform_config.get("min_polling_interval", 2)
        self.max_poll_interval = platform_config.get("max_polling_interval", 30)
        self.current_poll_interval = self.poll_interval
        self.consecutive_empty_polls = 0
        # 兼容旧键名 max_retry_attempts，优先使用与校验一致的 max_retry_count
        self.max_retry_count = platform_config.get(
            "max_retry_count", platform_config.get("max_retry_attempts", 3)
        )
        self.client: Optional[BilibiliClient] = None
        self._self_uid: Optional[int] = None
        # 适配器启动时间戳，用于忽略启动前的离线消息（只ACK不响应）
        self._startup_ts: Optional[int] = None
        self._running = False
        # 可選：處理新到已讀消息
        self.process_read_messages: bool = bool(
            platform_config.get("process_read_messages", False)
        )
        self.read_prefetch_window: int = int(
            platform_config.get("read_prefetch_window", 1)
        )
        # 每會話的 ack 與已處理水位
        self._last_ack_seqno_by_talker: dict[int, int] = {}
        self._last_processed_seqno_by_talker: dict[int, int] = {}
        logger.info("Bilibili Adapter 初始化完成。")

    def _validate_config(self, config: dict):
        """验证配置参数"""
        # 验证核心必填配置项
        required_configs = ("SESSDATA", "bili_jct", "device_id", "user_agent")

        for config_key in required_configs:
            value = config.get(config_key)
            if not value:
                logger.critical(
                    f"Bilibili Adapter 配置不完整，缺少 {config_key}。請檢查配置文件。"
                )
                raise ValueError(
                    f"Bilibili Adapter 配置不完整：缺少必需的配置项 {config_key}"
                )

        # 验证数值参数范围
        numeric_params = {
            # 輪詢配置
            "polling_interval": (1, 300),
            "min_polling_interval": (1, 60),
            "max_polling_interval": (5, 600),
            "max_retry_count": (1, 10),
            "read_prefetch_window": (1, 10),
            # 網絡配置
            "timeout_total": (5, 300),
            "timeout_connect": (1, 60),
            "timeout_sock_read": (1, 120),
            "connection_limit": (1, 1000),
            "connection_limit_per_host": (1, 100),
            "dns_cache_ttl": (60, 3600),
            "keepalive_timeout": (10, 300),
            # API 配置
            "message_batch_size": (1, 100),
        }

        for param, (min_val, max_val) in numeric_params.items():
            value = config.get(param)
            if value is not None and not (min_val <= value <= max_val):
                logger.critical(f"配置参数 {param} 超出有效范围 [{min_val}, {max_val}]")
                raise ValueError(
                    f"配置错误：{param} 必须在 {min_val} 到 {max_val} 之间"
                )

        # 验证逻辑关系
        min_poll = config.get("min_polling_interval", 2)
        max_poll = config.get("max_polling_interval", 30)
        if min_poll >= max_poll:
            logger.critical(
                "轮询间隔配置错误：『最小轮询间隔』必须小于『最大轮询间隔』"
            )
            raise ValueError("配置错误：『最小轮询间隔』必须小于『最大轮询间隔』")

    def meta(self) -> "PlatformMetadata":
        return PlatformMetadata(
            name="bilibili",
            description="Bilibili Adapter",
            id=self.config.get("id"),
            adapter_display_name="Bilibili",
            logo_path="assets/bilibili.svg",
        )

    async def shutdown(self):
        logger.info("Bilibili Adapter 正在離開...")
        self._running = False
        if self.client:
            logger.info("Bilibili Client 正在離開...")
            try:
                await self.client.close()
            finally:
                self.client = None
        logger.info("Bilibili Adapter 已成功離開。")

    async def run(self):
        try:
            logger.info("Bilibili Adapter 正在啟動並創建客戶端...")
            self.client = BilibiliClient(
                sessdata=self.config["SESSDATA"],
                bili_jct=self.config["bili_jct"],
                device_id=self.config["device_id"],
                buvid3=self.config.get("buvid3", ""),
                buvid4=self.config.get("buvid4", ""),
                user_agent=self.config["user_agent"],
                # 網絡配置（可選）
                timeout_total=self.config.get("timeout_total", 30),
                timeout_connect=self.config.get("timeout_connect", 10),
                timeout_sock_read=self.config.get("timeout_sock_read", 20),
                connection_limit=self.config.get("connection_limit", 100),
                connection_limit_per_host=self.config.get(
                    "connection_limit_per_host", 30
                ),
                dns_cache_ttl=self.config.get("dns_cache_ttl", 300),
                keepalive_timeout=self.config.get("keepalive_timeout", 60),
                # API 配置（可選）
                message_batch_size=self.config.get("message_batch_size", 20),
                api_build_version=self.config.get("api_build_version", 0),
                api_mobi_app=self.config.get("api_mobi_app", "web"),
            )
            success, uid = await self.client.get_my_info()
            if not success:
                logger.critical("啟動失敗，無法獲取 Bilibili 使用者信息。")
                try:
                    await self.client.close()
                except Exception:
                    pass
                self.client = None
                return
            self._self_uid = uid
            self._running = True
            logger.info(
                f"Bilibili Adapter 啟動成功，UID: {self._self_uid}。開始輪詢消息……"
            )
        except Exception as e:
            logger.critical(f"Bilibili Adapter 啟動失敗: {e}", exc_info=True)
            if self.client:
                try:
                    await self.client.close()
                except Exception:
                    pass
                self.client = None
            return

        begin_ts = int(datetime.now().timestamp())
        # 记录启动时间，用于过滤离线期间的旧消息（只 ACK，不进入处理流水线）
        self._startup_ts = begin_ts
        retry_count = 0

        while self._running:
            try:
                sessions_data = await self.client.get_new_sessions(begin_ts)

                # 檢查 Cookie 調用是否成功
                if sessions_data is None:
                    logger.warning("New Session 獲取失敗，跳過本次輪詢")
                    await asyncio.sleep(self.current_poll_interval)
                    continue

                if sessions_data.get("session_list"):
                    has_messages = False
                    for session_info in sessions_data["session_list"]:
                        # 忽略 Bilibili 官方系統通知 (talker_id 為 0)
                        if session_info.get("talker_id") == 0:
                            continue

                        talker_id = session_info.get("talker_id")
                        unread_count = session_info.get("unread_count", 0)
                        ack_seqno = session_info.get("ack_seqno", 0)
                        prev_ack = self._last_ack_seqno_by_talker.get(talker_id)

                        if unread_count > 0:
                            await self._process_unread_session(session_info)
                            has_messages = True
                        elif self.process_read_messages:
                            # 僅當 ack 從上次輪詢提升時，視為有新到已讀消息
                            if prev_ack is not None and ack_seqno > prev_ack:
                                await self._process_recent_read_session(
                                    session_info, prev_ack, ack_seqno
                                )
                                has_messages = True

                        # 更新該會話的 ack 水位
                        self._last_ack_seqno_by_talker[talker_id] = ack_seqno

                    # 自適應輪詢間隔調整
                    if has_messages:
                        self.consecutive_empty_polls = 0
                        self.current_poll_interval = max(
                            self.min_poll_interval, self.current_poll_interval * 0.9
                        )
                    else:
                        self.consecutive_empty_polls += 1
                        if self.consecutive_empty_polls > 3:
                            self.current_poll_interval = min(
                                self.max_poll_interval, self.current_poll_interval * 1.1
                            )

                # 安全更新時間戳
                if sessions_data and "ack_ts" in sessions_data:
                    begin_ts = sessions_data["ack_ts"]
                    logger.debug(f"更新時間戳為: {begin_ts}")
                # 如果沒有 ack_ts，保持原來的 begin_ts 不變

                retry_count = 0  # 重置重試次數

                # 抖動 ±10% 並夾持在最小/最大輪詢區間
                j = 0.1 * self.current_poll_interval
                jittered = (
                    self.current_poll_interval + ((random.random() * 2) - 1.0) * j
                )
                if jittered < self.min_poll_interval:
                    jittered = self.min_poll_interval
                if jittered > self.max_poll_interval:
                    jittered = self.max_poll_interval
                await asyncio.sleep(jittered)
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                retry_count += 1
                logger.error(
                    f"Bilibili Adapter 網絡/超時錯誤 (重試 {retry_count}/{self.max_retry_count}): {e}",
                    exc_info=True,
                )

                if retry_count >= self.max_retry_count:
                    logger.critical("達到最大重試次數，适配器將停止運行")
                    break

                # 指數退避重試
                backoff_time = min(60, self.poll_interval * (2**retry_count))
                await asyncio.sleep(backoff_time)
            except Exception as e:
                logger.critical(
                    f"Bilibili Adapter 發生非預期錯誤，將停止運行: {e}", exc_info=True
                )
                break

        # 运行结束，确保资源释放
        self._running = False
        if self.client:
            try:
                await self.client.close()
            except Exception:
                try:
                    logger.error("退出時關閉 Bilibili Client 失敗", exc_info=True)
                except Exception:
                    pass
            self.client = None
        logger.info("Bilibili Adapter 已结束运行。")

    async def _process_unread_session(self, session_info: dict):
        talker_id = session_info.get("talker_id")
        session_type = session_info.get("session_type")
        ack_seqno = session_info.get("ack_seqno", 0)

        logger.info(f"發現來自 talker_id: {talker_id} 的未讀消息，正在拉取……")

        try:
            messages_data = await self.client.get_messages(
                talker_id, session_type, ack_seqno
            )

            # 檢查消息獲取是否成功
            if messages_data is None:
                logger.warning(f"獲取 Session {talker_id} 的訊息失敗，跳過處理")
                return

            if messages_data.get("messages"):
                messages = messages_data["messages"]
                max_seqno_in_batch = 0
                for msg_data in messages:
                    if (
                        msg_data["msg_seqno"] > ack_seqno
                        and msg_data.get("sender_uid") != self._self_uid
                    ):
                        # 启动前收到的旧消息：仅 ACK 不回覆
                        msg_ts = _normalize_timestamp(msg_data.get("timestamp")) or 0
                        is_backlog = (
                            self._startup_ts is not None and msg_ts < self._startup_ts
                        )

                        if is_backlog:
                            logger.debug(
                                f"跳过离线期间的旧消息（仅ACK）：talker_id={talker_id}, msg_seqno={msg_data.get('msg_seqno')}"
                            )
                        else:
                            # 使用 talker_id 作为会话标识，确保会话隔离
                            abm = self.convert_message(msg_data, talker_id)
                            if abm:
                                await self.handle_msg(abm)
                        max_seqno_in_batch = max(
                            max_seqno_in_batch, msg_data["msg_seqno"]
                        )

                if max_seqno_in_batch > ack_seqno:
                    await self.client.update_ack(
                        talker_id, session_type, max_seqno_in_batch
                    )

                # 更新已處理水位，避免後續回溯時重複
                prev_proc = self._last_processed_seqno_by_talker.get(talker_id, 0)
                if max_seqno_in_batch > prev_proc:
                    self._last_processed_seqno_by_talker[talker_id] = max_seqno_in_batch

        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(
                f"處理 Session {talker_id} 的訊息時發生網絡/超時錯誤: {e}",
                exc_info=True,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
            logger.error(
                f"處理 Session {talker_id} 的訊息時發生數據處理錯誤: {e}", exc_info=True
            )
        except Exception as e:
            logger.critical(
                f"處理 Session {talker_id} 的訊息時發生非預期錯誤: {e}",
                exc_info=True,
            )

    async def _process_recent_read_session(
        self, session_info: dict, last_ack: int, current_ack: int
    ):
        """處理新到但已被標記為已讀的消息（可選）。

        僅在開關開啟且檢測到 ack 變大時觸發；透過小窗口回溯抓取最近消息，
        再以啟動時間與已處理水位去重，避免對歷史消息重複回覆。
        """
        if not self.process_read_messages or self.read_prefetch_window <= 0:
            return

        talker_id = session_info.get("talker_id")
        session_type = session_info.get("session_type")

        try:
            # 回溯窗口：覆蓋 (last_ack, current_ack]，並多抓幾條以防突發聚合
            begin_seqno = max(0, int(current_ack) - int(self.read_prefetch_window))
            messages_data = await self.client.get_messages(
                talker_id, session_type, begin_seqno
            )

            if messages_data is None:
                logger.debug(f"回溯獲取 Session {talker_id} 消息失敗（已讀模式），跳過")
                return

            msgs = messages_data.get("messages") or []
            if not msgs:
                return

            max_seq_processed = self._last_processed_seqno_by_talker.get(talker_id, 0)
            for msg_data in msgs:
                seq = msg_data.get("msg_seqno", 0)
                if seq <= last_ack:
                    continue  # 僅處理 (last_ack, current_ack] 範圍
                if seq <= max_seq_processed:
                    continue  # 已處理過的跳過
                if msg_data.get("sender_uid") == self._self_uid:
                    continue  # 忽略自己發出的

                # 啟動前的舊消息不處理
                msg_ts = _normalize_timestamp(msg_data.get("timestamp"))
                if (
                    self._startup_ts is not None
                    and msg_ts is not None
                    and msg_ts < self._startup_ts
                ):
                    continue

                abm = self.convert_message(msg_data, talker_id)
                if abm:
                    await self.handle_msg(abm)
                    if seq > max_seq_processed:
                        max_seq_processed = seq

            # 更新已處理水位
            prev_proc = self._last_processed_seqno_by_talker.get(talker_id, 0)
            if max_seq_processed > prev_proc:
                self._last_processed_seqno_by_talker[talker_id] = max_seq_processed

        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(
                f"處理 Session {talker_id} 的已讀消息時發生網絡/超時錯誤: {e}",
                exc_info=True,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
            logger.error(
                f"處理 Session {talker_id} 的已讀消息時發生數據處理錯誤: {e}",
                exc_info=True,
            )
        except Exception as e:
            logger.critical(
                f"處理 Session {talker_id} 的已讀消息時發生非預期錯誤: {e}",
                exc_info=True,
            )

    def convert_message(
        self, data: dict, session_talker_id: int
    ) -> Optional[AstrBotMessage]:
        msg_type = data.get("msg_type")
        sender_uid = data.get("sender_uid")

        abm = AstrBotMessage()

        # 處理私信
        abm.type = MessageType.FRIEND_MESSAGE

        # 消息鏈
        if msg_type == 1:  # 文本消息
            try:
                content_raw = data.get("content")
                if isinstance(content_raw, str):
                    content_data = json.loads(content_raw or "{}")
                elif isinstance(content_raw, dict):
                    content_data = content_raw
                else:
                    return None
                text_content = content_data.get("content", "")
                if not text_content:
                    return None
                abm.message = [Plain(text_content)]
                abm.message_str = text_content
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"無法解析 Bilibili 文本訊息內容: {data.get('content')}")
                return None
        elif msg_type == 2:  # 圖片消息
            try:
                content_raw = data.get("content")
                if isinstance(content_raw, str):
                    content_data = json.loads(content_raw or "{}")
                elif isinstance(content_raw, dict):
                    content_data = content_raw
                else:
                    return None
                image_url = content_data.get("url")
                if not image_url:
                    return None
                abm.message = [Image.fromURL(image_url)]
                abm.message_str = "[圖片]"
            except (json.JSONDecodeError, TypeError):
                # 回退：content 可能是非標準 JSON 字串，嘗試用正則抓取 url
                try:
                    content_raw = data.get("content")
                    if isinstance(content_raw, str):
                        m = re.search(r"\"url\"\s*:\s*\"([^\"\\]+)\"", content_raw)
                        if not m:
                            m = re.search(r"'url'\s*:\s*'([^'\\]+)'", content_raw)
                        if m:
                            url = m.group(1)
                            abm.message = [Image.fromURL(url)]
                            abm.message_str = "[圖片]"
                            # 成功回退則不中斷
                        else:
                            logger.warning(
                                f"無法解析 Bilibili 圖片訊息內容: {data.get('content')}"
                            )
                            return None
                    else:
                        logger.warning(
                            f"無法解析 Bilibili 圖片訊息內容: {data.get('content')}"
                        )
                        return None
                except Exception:
                    logger.warning(
                        f"無法解析 Bilibili 圖片訊息內容: {data.get('content')}"
                    )
                    return None
        else:
            logger.debug(f"忽略不支援的 Bilibili 訊息類型: {msg_type}")
            return None

        # 填充其他必要字段（时间戳统一归一化为秒级）
        ts_val = _normalize_timestamp(data.get("timestamp"))
        if ts_val is None:
            ts_val = int(datetime.now().timestamp())
        abm.timestamp = ts_val
        # 使用會話ID + 序號提高唯一性（msg_seqno 通常在會話作用域內唯一）
        abm.message_id = f"{session_talker_id}-{data.get('msg_seqno')}"
        abm.sender = MessageMember(
            user_id=str(sender_uid), nickname=""
        )  # Bilibili 不提供暱稱獲取
        # 使用 session 的 talker_id 作为 session_id，避免某些消息体下的 sender_uid 与 session 主体不一致
        abm.session_id = str(session_talker_id)
        abm.raw_message = data
        abm.self_id = str(self._self_uid)

        return abm

    async def handle_msg(self, message: Optional[AstrBotMessage]):
        if not message:
            return

        event = BilibiliPlatformEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            client=self.client,
        )
        self.commit_event(event)

    async def terminate(self):
        self._running = False
        await self.shutdown()
