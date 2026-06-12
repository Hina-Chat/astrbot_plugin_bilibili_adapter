import json
import time
import asyncio
import mimetypes
from typing import Optional, Tuple
from collections import OrderedDict

import aiohttp
from astrbot.api import logger

from .bilibili_wbi import extract_key_from_url, sign_params

# Bilibili API endpoints
API_BASE_URL = "https://api.bilibili.com"
VC_API_BASE_URL = "https://api.vc.bilibili.com"

MY_INFO_URL = f"{API_BASE_URL}/x/space/myinfo"
NEW_SESSIONS_URL = f"{VC_API_BASE_URL}/session_svr/v1/session_svr/new_sessions"
# 按实际使用统一到 fetch_session_msgs 接口
FETCH_SESSION_MSGS_URL = f"{VC_API_BASE_URL}/svr_sync/v1/svr_sync/fetch_session_msgs"
SEND_MSG_URL = f"{VC_API_BASE_URL}/web_im/v1/web_im/send_msg"
UPLOAD_IMAGE_URL = f"{API_BASE_URL}/x/dynamic/feed/draw/upload_bfs"
UPDATE_ACK_URL = f"{VC_API_BASE_URL}/session_svr/v1/session_svr/update_ack"
NAV_URL = f"{API_BASE_URL}/x/web-interface/nav"
FINGER_SPI_URL = f"{API_BASE_URL}/x/frontend/finger/spi"


class BilibiliClient:
    """
    一個與 Bilibili 私信 API 交互的客戶端。
    處理認證、消息獲取和發送。
    """

    def __init__(
        self,
        sessdata: str,
        bili_jct: str,
        device_id: str,
        user_agent: str,
        # 手动指定的设备指纹 Cookie（可选，留空时自动获取）
        buvid3: str = "",
        buvid4: str = "",
        # 网络配置参数（可选，有預設值）
        timeout_total: int = 30,
        timeout_connect: int = 10,
        timeout_sock_read: int = 20,
        connection_limit: int = 100,
        connection_limit_per_host: int = 30,
        dns_cache_ttl: int = 300,
        keepalive_timeout: int = 60,
        # API参数（可选，有預設值）
        message_batch_size: int = 20,
        api_build_version: str = "0",
        api_mobi_app: str = "web",
    ):
        # 验证核心必填参数
        if not sessdata or not bili_jct:
            raise ValueError("請提供 SESSDATA 和 bili_jct。")
        if not device_id:
            raise ValueError("請提供 device_id。")
        if not user_agent:
            raise ValueError("請提供 user_agent。")

        # 核心配置
        self._sessdata = sessdata
        self._bili_jct = bili_jct
        self._device_id = device_id
        self._self_uid: Optional[int] = None

        # 網路配置
        self._timeout_total = timeout_total
        self._timeout_connect = timeout_connect
        self._timeout_sock_read = timeout_sock_read
        self._connection_limit = connection_limit
        self._connection_limit_per_host = connection_limit_per_host
        self._dns_cache_ttl = dns_cache_ttl
        self._keepalive_timeout = keepalive_timeout

        # API 配置
        self._message_batch_size = message_batch_size
        # 统一为字符串，兼容外部传入 int 的情况
        self._api_build_version = str(api_build_version)
        self._api_mobi_app = str(api_mobi_app)

        self._headers = {
            "User-Agent": user_agent,
            "Referer": "https://message.bilibili.com/",
            "Origin": "https://message.bilibili.com",
        }
        # 手动配置的 buvid 指纹（优先于自动获取）
        self._manual_buvid: dict = {}
        if buvid3:
            self._manual_buvid["buvid3"] = str(buvid3).strip()
        if buvid4:
            self._manual_buvid["buvid4"] = str(buvid4).strip()

        self._cookies = {
            "SESSDATA": self._sessdata,
            "bili_jct": self._bili_jct,
            **self._manual_buvid,
        }
        self._session: Optional[aiohttp.ClientSession] = None
        # WBI 簽名密鑰快取
        self._wbi_img_key: Optional[str] = None
        self._wbi_sub_key: Optional[str] = None
        self._wbi_keys_expires_at: float = 0.0
        self._wbi_keys_ttl_seconds: int = 3600 * 12
        # buvid 指紋 Cookie 是否已就緒
        self._buvid_ready: bool = False
        # 圖片快取（LRU + TTL）: {key: (image_info, expires_at)}
        self._image_cache: "OrderedDict[str, tuple[dict, float]]" = OrderedDict()
        self._image_cache_max_size: int = 256
        self._image_cache_ttl_seconds: int = 1800

    def _cache_get(self, key: Optional[str]) -> Optional[dict]:
        if not key:
            return None
        now = time.time()
        try:
            value = self._image_cache.get(key)
            if not value:
                return None
            image_info, expires_at = value
            if expires_at < now:
                try:
                    del self._image_cache[key]
                except Exception:
                    pass
                return None
            self._image_cache.move_to_end(key)
            return image_info
        except Exception:
            return None

    def _cache_set(self, key: Optional[str], image_info: Optional[dict]):
        if not key or not image_info:
            return
        try:
            expires_at = time.time() + float(self._image_cache_ttl_seconds)
            self._image_cache[key] = (image_info, expires_at)
            self._image_cache.move_to_end(key)
            while len(self._image_cache) > int(self._image_cache_max_size):
                try:
                    self._image_cache.popitem(last=False)
                except Exception:
                    break
        except Exception:
            pass

    async def _safe_json_from_response(
        self, response: aiohttp.ClientResponse
    ) -> Optional[dict]:
        """安全解析 JSON，非 JSON 或解析失败时返回 None，并打印精简响应片段便于排查。"""
        try:
            text = await response.text()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            try:
                logger.error(f"讀取響應體失敗: url='{response.url}', 錯誤: {e}")
            except Exception:
                logger.error(f"讀取響應體失敗: 錯誤: {e}")
            return None
        try:
            return json.loads(text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            preview = text[:200] if isinstance(text, str) else str(text)[:200]
            try:
                logger.error(
                    f"解析 JSON 失敗: status={response.status}, url='{response.url}', 片段='{preview}', 錯誤: {e}"
                )
            except Exception:
                logger.error(f"解析 JSON 失敗: 錯誤: {e}")
            return None

    def _guess_filename_and_content_type(
        self, image_data: bytes, filename: Optional[str]
    ) -> Tuple[str, str]:
        """盡力推斷圖片文件名與 content-type。優先擴展名；否則以常見魔數判斷；最後回退。"""
        ext: Optional[str] = None
        if filename and "." in filename:
            ext = filename.rsplit(".", 1)[1].lower()

        if not ext and image_data:
            head = image_data[:12]
            try:
                if head.startswith(b"\xff\xd8\xff"):
                    ext = "jpg"
                elif head.startswith(b"\x89PNG\r\n\x1a\n"):
                    ext = "png"
                elif head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
                    ext = "gif"
                elif head.startswith(b"RIFF") and head[8:12] == b"WEBP":
                    ext = "webp"
            except Exception:
                ext = None

        if ext:
            ct = mimetypes.types_map.get(f".{ext}", f"image/{ext}")
            final_name = (
                filename
                if (filename and filename.lower().endswith(f".{ext}"))
                else f"image.{ext}"
            )
            return final_name, ct

        return filename or "image.bin", "application/octet-stream"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        """初始化並返回 aiohttp 客戶端 Session。"""
        if self._session is None or self._session.closed:
            # 使用配置化的连接池和超时参数
            timeout = aiohttp.ClientTimeout(
                total=self._timeout_total,
                connect=self._timeout_connect,
                sock_read=self._timeout_sock_read,
            )

            connector = aiohttp.TCPConnector(
                limit=self._connection_limit,
                limit_per_host=self._connection_limit_per_host,
                ttl_dns_cache=self._dns_cache_ttl,
                use_dns_cache=True,
                keepalive_timeout=self._keepalive_timeout,
                enable_cleanup_closed=True,
            )

            self._session = aiohttp.ClientSession(
                headers=self._headers,
                cookies=self._cookies,
                timeout=timeout,
                connector=connector,
                raise_for_status=False,  # 手动处理HTTP错误
            )
        return self._session

    async def _ensure_buvid(self):
        """獲取並設置 buvid3/buvid4 指紋 Cookie（缺失時容易觸發 412 風控）。"""
        if self._buvid_ready:
            return
        session = await self._get_session()
        if self._manual_buvid:
            self._cookies.update(self._manual_buvid)
            session.cookie_jar.update_cookies(self._manual_buvid)
            self._buvid_ready = True
            logger.debug("已套用手動配置的 buvid 指紋 Cookie。")
            return
        try:
            async with session.get(FINGER_SPI_URL) as response:
                data = await self._safe_json_from_response(response)
                if isinstance(data, dict) and data.get("code") == 0:
                    payload = data.get("data") or {}
                    cookies = {}
                    if payload.get("b_3"):
                        cookies["buvid3"] = payload["b_3"]
                    if payload.get("b_4"):
                        cookies["buvid4"] = payload["b_4"]
                    if cookies:
                        self._cookies.update(cookies)
                        session.cookie_jar.update_cookies(cookies)
                        self._buvid_ready = True
                        logger.debug("已獲取並設置 buvid 指紋 Cookie。")
                else:
                    logger.warning(f"獲取 buvid 指紋失敗: {data}")
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"獲取 buvid 指紋時發生網絡錯誤: {e}")

    async def _get_wbi_keys(
        self, force_refresh: bool = False
    ) -> Optional[Tuple[str, str]]:
        """獲取 WBI 簽名密鑰（img_key, sub_key），帶 TTL 快取。"""
        now = time.time()
        if (
            not force_refresh
            and self._wbi_img_key
            and self._wbi_sub_key
            and now < self._wbi_keys_expires_at
        ):
            return self._wbi_img_key, self._wbi_sub_key

        session = await self._get_session()
        try:
            async with session.get(NAV_URL) as response:
                data = await self._safe_json_from_response(response)
                wbi_img = None
                if isinstance(data, dict):
                    wbi_img = (data.get("data") or {}).get("wbi_img")
                if isinstance(wbi_img, dict):
                    img_key = extract_key_from_url(wbi_img.get("img_url", ""))
                    sub_key = extract_key_from_url(wbi_img.get("sub_url", ""))
                    if img_key and sub_key:
                        self._wbi_img_key = img_key
                        self._wbi_sub_key = sub_key
                        self._wbi_keys_expires_at = now + self._wbi_keys_ttl_seconds
                        logger.debug("WBI 簽名密鑰已更新。")
                        return img_key, sub_key
                logger.error(f"獲取 WBI 簽名密鑰失敗: {data}")
                return None
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"獲取 WBI 簽名密鑰時發生網絡錯誤: {e}")
            return None

    async def _sign_wbi_params(
        self, params: dict, force_refresh: bool = False
    ) -> Optional[dict]:
        """對參數進行 WBI 簽名；密鑰獲取失敗時返回 None。"""
        keys = await self._get_wbi_keys(force_refresh=force_refresh)
        if not keys:
            return None
        return sign_params(params, *keys)

    async def get_my_info(self) -> Tuple[bool, Optional[int]]:
        """獲取當前登錄用戶的信息，主要是 UID。"""
        session = await self._get_session()
        try:
            async with session.get(MY_INFO_URL) as response:
                if response.status == 200:
                    data = await self._safe_json_from_response(response)
                    if isinstance(data, dict) and data.get("code") == 0:
                        self._self_uid = data["data"]["mid"]
                        logger.info(f"Bilibili 用戶信息獲取成功，UID: {self._self_uid}")
                        return True, self._self_uid
                    else:
                        logger.error(f"獲取 Bilibili 用戶信息失敗: {data}")
                        return False, None
                else:
                    body_preview = ""
                    try:
                        body_preview = (await response.text())[:200]
                    except Exception:
                        pass
                    logger.error(
                        f"獲取 Bilibili 用戶信息錯誤: {response.status}, message='{response.reason}', url='{response.url}', body='{body_preview}'"
                    )
                    return False, None
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"請求 Bilibili 用戶信息時發生網絡錯誤: {e}")
            return False, None

    async def get_new_sessions(self, begin_ts: int) -> Optional[dict]:
        """獲取指定時間戳之後的 New Session 列表。"""
        session = await self._get_session()
        params = {
            "begin_ts": begin_ts,
            "build": self._api_build_version,
            "mobi_app": self._api_mobi_app,
        }
        # 輪詢間隔較長時，複用的 Keep-Alive 連接可能已被服務端閒置關閉，
        # 導致請求寫入「半死」連接後讀超時。輪詢請求改用短連接，每次新建。
        headers = {"Connection": "close"}
        for attempt in range(2):
            try:
                async with session.get(
                    NEW_SESSIONS_URL, params=params, headers=headers
                ) as response:
                    if response.status == 200:
                        data = await self._safe_json_from_response(response)
                        if isinstance(data, dict) and data.get("code") == 0:
                            return data.get("data")
                        else:
                            logger.error(f"獲取 Bilibili New Session 失敗: {data}")
                            return None
                    else:
                        logger.error(
                            f"獲取 Bilibili New Session 時發生錯誤: {response.status}, message='{response.reason}', url='{response.url}'"
                        )
                        return None
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == 0:
                    logger.warning(
                        f"獲取 New Session 超時/網絡錯誤，重建連接後重試一次: {e}"
                    )
                    continue
                logger.error(f"獲取 Bilibili New Session 時發生網絡錯誤: {e}")
                return None
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
                logger.error(f"獲取 Bilibili New Session 時發生數據處理錯誤: {e}")
                return None
        return None

    async def get_messages(
        self,
        talker_id: int,
        session_type: int,
        begin_seqno: int,
        size: Optional[int] = None,
    ) -> Optional[dict]:
        """獲取指定 Session 中的消息列表。"""
        session = await self._get_session()
        # 使用配置化的参数
        if size is None:
            size = self._message_batch_size

        params = {
            "talker_id": talker_id,
            "session_type": session_type,
            "begin_seqno": begin_seqno,
            "size": size,
            "build": self._api_build_version,
            "mobi_app": self._api_mobi_app,
        }
        url = FETCH_SESSION_MSGS_URL
        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await self._safe_json_from_response(response)
                    if isinstance(data, dict) and data.get("code") == 0:
                        return data.get("data")
                    else:
                        logger.error(f"獲取 Bilibili 消息列表失敗: {data}")
                        return None
                else:
                    logger.error(
                        f"獲取 Bilibili 消息列表時發生錯誤: {response.status}, message='{response.reason}', url='{response.url}'"
                    )
                    return None
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"獲取 Bilibili 消息列表時發生網絡錯誤: {e}")
            return None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
            logger.error(f"獲取 Bilibili 消息列表時發生數據處理錯誤: {e}")
            return None

    async def update_ack(
        self, talker_id: int, session_type: int, ack_seqno: int
    ) -> bool:
        """更新已讀回執。"""
        session = await self._get_session()
        data = {
            "talker_id": talker_id,
            "session_type": session_type,
            "ack_seqno": ack_seqno,
            "csrf_token": self._bili_jct,
            "csrf": self._bili_jct,
            "build": self._api_build_version,
            "mobi_app": self._api_mobi_app,
        }
        url = UPDATE_ACK_URL
        try:
            async with session.post(url, data=data) as response:
                if response.status == 200:
                    res_data = await self._safe_json_from_response(response)
                    if isinstance(res_data, dict) and res_data.get("code") == 0:
                        logger.debug(
                            f"成功更新 Bilibili 已讀回執: talker_id={talker_id}, ack_seqno={ack_seqno}"
                        )
                        return True
                    else:
                        logger.error(f"更新 Bilibili 已讀回執失敗: {res_data}")
                        return False
                else:
                    logger.error(
                        f"更新 Bilibili 已讀回執時發生錯誤: {response.status}, message='{response.reason}', url='{response.url}'"
                    )
                    return False
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"更新 Bilibili 已讀回執時發生網絡錯誤: {e}")
            return False
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
            logger.error(f"更新 Bilibili 已讀回執時發生數據處理錯誤: {e}")
            return False

    async def download_image_from_url(self, url: str) -> Optional[bytes]:
        """從 URL 下載圖片數據並返回字節。"""
        logger.debug(f"正在從 URL 下載圖片: {url}")
        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    logger.debug(f"圖片下載成功: {url}")
                    return await resp.read()
                else:
                    logger.error(f"從 URL 下載圖片失敗: {url}, 狀態碼: {resp.status}")
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"從 URL 下載圖片時發生網絡錯誤: {url}, 錯誤: {e}")
            return None

    async def upload_image(
        self,
        image_data: bytes,
        filename: str = "image.png",
        cache_key: Optional[str] = None,
    ) -> Optional[dict]:
        """上載圖片到 Bilibili 伺服器。

        :param image_data: 圖片的字節數據。
        :param filename: 檔名，用於 multipart/form-data。
        :param cache_key: 快取鍵（如圖片路徑或 URL），命中時直接返回快取結果。
        :return: 包含 image_url, image_width, image_height 的字典，或在失敗時返回 None。
        """
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug(f"命中圖片快取: {cache_key}")
            return cached

        session = await self._get_session()

        # 動態推斷文件名與 content-type，提高兼容性
        final_name, content_type = self._guess_filename_and_content_type(
            image_data, filename
        )

        data = aiohttp.FormData()
        data.add_field(
            "file_up",
            image_data,
            filename=final_name,
            content_type=content_type,
        )
        data.add_field("category", "daily")
        data.add_field("csrf", self._bili_jct)

        try:
            async with session.post(UPLOAD_IMAGE_URL, data=data) as response:
                if response.status == 200:
                    res_json = await self._safe_json_from_response(response)
                    if isinstance(res_json, dict) and res_json.get("code") == 0:
                        image_info = res_json.get("data")
                        logger.info(f"圖片上載成功: {image_info.get('image_url')}")
                        # 存入快取
                        if cache_key and image_info:
                            self._cache_set(cache_key, image_info)
                            logger.debug(f"圖片已快取: {cache_key}")
                        return image_info
                    else:
                        logger.error(f"上載圖片失敗: {res_json}")
                        return None
                else:
                    body_preview = ""
                    try:
                        body_preview = (await response.text())[:200]
                    except Exception:
                        pass
                    logger.error(
                        f"上載圖片時發生錯誤: {response.status}, message='{response.reason}', url='{response.url}', body='{body_preview}'"
                    )
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"上載圖片時發生網絡錯誤: {e}")
            return None
        except asyncio.CancelledError:
            raise

    async def _send_message(self, payload: dict) -> bool:
        """統一的消息發送方法（帶 WBI 簽名，412 風控時刷新密鑰重試一次）。"""
        session = await self._get_session()
        await self._ensure_buvid()
        receiver_id = payload.get("msg[receiver_id]")
        base_params = {
            "w_sender_uid": payload.get("msg[sender_uid]"),
            "w_receiver_id": receiver_id,
            "w_dev_id": self._device_id,
        }
        last_error = ""
        for attempt in range(2):
            params = await self._sign_wbi_params(base_params, force_refresh=attempt > 0)
            if params is None:
                logger.error(f"向 {receiver_id} 發送消息失敗：無法獲取 WBI 簽名密鑰。")
                return False
            try:
                async with session.post(
                    SEND_MSG_URL, params=params, data=payload
                ) as response:
                    if response.status == 200:
                        data = await self._safe_json_from_response(response)
                        if isinstance(data, dict) and data.get("code") == 0:
                            logger.info(f"向 {receiver_id} 發送消息成功。")
                            return True
                        else:
                            logger.error(f"向 {receiver_id} 發送消息失敗: {data}")
                            return False
                    else:
                        body_preview = ""
                        try:
                            body_preview = (await response.text())[:200]
                        except Exception:
                            pass
                        last_error = (
                            f"{response.status}, message='{response.reason}', "
                            f"url='{response.url}', body='{body_preview}'"
                        )
                        if response.status == 412 and attempt == 0:
                            logger.warning(
                                f"向 {receiver_id} 發送消息被風控攔截 (412)，刷新 WBI 密鑰與 buvid 後重試……"
                            )
                            self._buvid_ready = False
                            await self._ensure_buvid()
                            continue
                        logger.error(
                            f"向 {receiver_id} 發送消息時發生錯誤: {last_error}"
                        )
                        return False
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"向 {receiver_id} 發送消息時發生通信錯誤: {e}")
                return False
        logger.error(f"向 {receiver_id} 發送消息重試後仍失敗: {last_error}")
        return False

    def clear_image_cache(self):
        """清空圖片快取。"""
        try:
            self._image_cache.clear()
        except Exception:
            pass

    def _build_msg_payload(
        self, receiver_id: int, msg_type: int, msg_content: str
    ) -> dict:
        """構造私信發送的表單 payload。

        :param msg_type: 1 為文本消息，2 為圖片消息。
        """
        return {
            "msg[sender_uid]": self._self_uid,
            "msg[receiver_id]": receiver_id,
            "msg[receiver_type]": 1,  # 1 為私信
            "msg[msg_type]": msg_type,
            "msg[msg_status]": 0,
            "msg[content]": msg_content,
            "msg[timestamp]": int(time.time()),
            "msg[new_face_version]": 0,
            "msg[dev_id]": self._device_id,
            "from_filework": 0,
            "build": self._api_build_version,
            "mobi_app": self._api_mobi_app,
            "csrf_token": self._bili_jct,
            "csrf": self._bili_jct,
        }

    async def send_image_message(self, receiver_id: int, image_info: dict) -> bool:
        """發送圖片私信。"""
        if self._self_uid is None:
            logger.error("無法發送消息：未獲取到發送者 UID。")
            return False

        msg_content = json.dumps(
            {
                "url": image_info.get("image_url"),
                "height": image_info.get("image_height"),
                "width": image_info.get("image_width"),
                # size 不在上傳響應中，為可選字段
                "size": image_info.get("image_size", 0),
                "original": 1,
            }
        )
        payload = self._build_msg_payload(
            receiver_id, msg_type=2, msg_content=msg_content
        )
        return await self._send_message(payload)

    async def send_text_message(self, receiver_id: int, content: str) -> bool:
        """發送純文本私信。"""
        if self._self_uid is None:
            logger.error("無法發送消息：未獲取到發送者 UID。")
            return False

        msg_content = json.dumps({"content": content})
        payload = self._build_msg_payload(
            receiver_id, msg_type=1, msg_content=msg_content
        )
        return await self._send_message(payload)

    async def close(self):
        """關閉客戶端 Session。"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Bilibili Client session closed.")
