from typing import Optional
import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.platform import AstrBotMessage, PlatformMetadata

from .bilibili_client import BilibiliClient


def _read_file_bytes(path: str) -> bytes:
    """阻塞式读取文件字节，供 asyncio.to_thread 调用。"""
    with open(path, "rb") as f:
        return f.read()


def _image_cache_key(item: Image) -> Optional[str]:
    """提取稳定且不会包含大段 base64 数据的图片缓存键。"""
    for field in ("path", "url", "file"):
        value = getattr(item, field, None)
        if (
            isinstance(value, str)
            and value
            and not value.startswith(("base64://", "data:"))
        ):
            return value
    return None


class BilibiliPlatformEvent(AstrMessageEvent):
    def get_message_outline(self) -> str:
        """重寫此方法以規避核心框架的遍歷問題，安全生成消息概要。
        兼容 self.message_obj.message 既可能是 MessageChain 也可能是 list 的情況。
        """
        if not self.message_obj or not self.message_obj.message:
            return ""

        # 核心修復：確保在遍歷前調用 .get_chain()
        chain = self.message_obj.message
        if isinstance(chain, MessageChain):
            iterable_chain = chain.chain
        else:  # 如果它已經是列表，則直接使用
            iterable_chain = chain

        outline_parts = []
        for item in iterable_chain:
            if isinstance(item, Plain):
                outline_parts.append(item.text)
            elif isinstance(item, Image):
                outline_parts.append("[圖片]")
            else:
                outline_parts.append(f"[{item.__class__.__name__}]")

        return " ".join(outline_parts).strip()

    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        client: BilibiliClient,
    ):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.client = client

    def _resolve_receiver_id(self) -> Optional[int]:
        """解析接收者 ID：
        1) 首选使用会话 ID（talker_id）
        2) 若非纯数字，回退到消息发送者的 user_id（在私信场景通常等价）
        解析失败则返回 None
        """
        sid = self.get_session_id()
        # 首选：会话 ID
        try:
            return int(sid)
        except (ValueError, TypeError):
            pass

        # 回退：消息发送者 ID（仅私信场景合理）
        try:
            sender_id = getattr(self.message_obj, "sender", None)
            if sender_id and getattr(sender_id, "user_id", None) is not None:
                return int(sender_id.user_id)
        except (ValueError, TypeError):
            pass
        return None

    async def send(self, message: MessageChain):
        """發送消息到 Bilibili：
        - 合併連續的文本段為單條私信
        - 圖片單獨發送
        - 先解析接收者 ID，避免每個 item 重複解析
        """
        if not self.client:
            logger.error("Bilibili client is not available.")
            return

        # 解析接收者 ID
        receiver_id = self._resolve_receiver_id()
        if receiver_id is None:
            logger.error(
                f"無法解析 receiver_id，session_id={self.get_session_id()}，取消發送。"
            )
            return

        # 迭代消息鏈，合併連續文本
        text_buffer: list[str] = []

        async def flush_text_buffer():
            if text_buffer:
                text = "".join(text_buffer)
                text_buffer.clear()
                if text:
                    await self.client.send_text_message(receiver_id, text)

        # 核心修復：確保在遍歷前獲取可迭代列表
        chain = message.chain
        for item in chain:
            try:
                if isinstance(item, Plain):
                    if item.text:
                        text_buffer.append(item.text)
                elif isinstance(item, Image):
                    await flush_text_buffer()

                    image_bytes: Optional[bytes] = None
                    cache_key = _image_cache_key(item)

                    # raw 是部分舊插件使用的非標準字段，保留兼容；標準 Image
                    # 統一走 AstrBot 的解析 API。官方元件把 URL/base64 放在
                    # item.file，而不是 item.url。
                    raw = getattr(item, "raw", None)
                    if isinstance(raw, (bytes, bytearray, memoryview)) and raw:
                        image_bytes = bytes(raw)
                    else:
                        try:
                            image_path = await item.convert_to_file_path()
                            image_bytes = await asyncio.to_thread(
                                _read_file_bytes, image_path
                            )
                        except Exception as e:
                            logger.error(
                                "解析 AstrBot 圖片元件失敗，"
                                f"無法取得可上傳的圖片數據: {e}"
                            )

                    if image_bytes:
                        image_info = await self.client.upload_image(
                            image_bytes, cache_key=cache_key
                        )
                        if image_info:
                            await self.client.send_image_message(
                                receiver_id, image_info
                            )
                        else:
                            logger.error("上傳圖片到 Bilibili 失敗。")
                    else:
                        logger.error("無法獲取圖片數據，無法發送圖片。")
                elif isinstance(item, Reply):
                    pass  # Bilibili 私信不支持引用回覆，靜默跳過
                else:
                    await flush_text_buffer()
                    logger.warning(f"忽略不支持的消息組件: {item.__class__.__name__}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"發送 Bilibili 消息時出錯: {e}", exc_info=True)

        # 末尾衝刷可能殘留的文本
        await flush_text_buffer()
