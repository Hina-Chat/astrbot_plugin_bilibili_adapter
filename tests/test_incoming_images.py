import asyncio
import base64
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


def _install_astrbot_stubs() -> None:
    """提供导入适配器所需的最小 AstrBot API，便于独立运行单元测试。"""

    class Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    class Plain:
        def __init__(self, text):
            self.text = text

    class Image:
        def __init__(self, file):
            self.file = file
            self.path = None
            self.url = None
            self.raw = None
            self.resolved_path = None

        @staticmethod
        def fromBytes(data):
            return Image(f"base64://{base64.b64encode(data).decode()}")

        async def convert_to_file_path(self):
            if self.resolved_path:
                return self.resolved_path
            raise ValueError("test image has no resolved path")

    class Reply:
        pass

    class AstrBotMessage:
        pass

    class MessageMember:
        def __init__(self, user_id, nickname):
            self.user_id = user_id
            self.nickname = nickname

    class Platform:
        def __init__(self, platform_config, event_queue):
            self.platform_config = platform_config
            self.event_queue = event_queue

    class PlatformMetadata:
        pass

    class MessageType:
        FRIEND_MESSAGE = "friend"

    def register_platform_adapter(*args, **kwargs):
        return lambda cls: cls

    class AstrMessageEvent:
        def __init__(self, *args, **kwargs):
            pass

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = chain or []

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    components = types.ModuleType("astrbot.api.message_components")
    platform = types.ModuleType("astrbot.api.platform")
    event = types.ModuleType("astrbot.api.event")

    api.logger = Logger()
    components.Image = Image
    components.Plain = Plain
    components.Reply = Reply
    platform.AstrBotMessage = AstrBotMessage
    platform.MessageMember = MessageMember
    platform.Platform = Platform
    platform.PlatformMetadata = PlatformMetadata
    platform.register_platform_adapter = register_platform_adapter
    platform.MessageType = MessageType
    event.AstrMessageEvent = AstrMessageEvent
    event.MessageChain = MessageChain

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.message_components": components,
            "astrbot.api.platform": platform,
            "astrbot.api.event": event,
        }
    )


_install_astrbot_stubs()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT.parent))
PACKAGE_NAME = PROJECT_ROOT.name
BILIBILI_ADAPTER = importlib.import_module(f"{PACKAGE_NAME}.bilibili_adapter")
BILIBILI_CLIENT = importlib.import_module(f"{PACKAGE_NAME}.bilibili_client")
BILIBILI_EVENT = importlib.import_module(f"{PACKAGE_NAME}.bilibili_event")


class _Response:
    def __init__(
        self,
        status,
        data=b"",
        content_length=None,
        content_type="image/png",
    ):
        self.status = status
        self._data = data
        self.content_length = (
            len(data) if content_length is None and status == 200 else content_length
        )
        self.headers = {"Content-Type": content_type}
        self.read_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        self.read_called = True
        return self._data


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested_urls = []

    def get(self, url):
        self.requested_urls.append(url)
        return self.responses.pop(0)


class IncomingImageClientTests(unittest.TestCase):
    def _make_client(self):
        return BILIBILI_CLIENT.BilibiliClient(
            sessdata="sess",
            bili_jct="csrf",
            device_id="device",
            user_agent="ua",
        )

    def test_only_bilibili_image_cdns_are_trusted(self):
        check = BILIBILI_CLIENT.BilibiliClient._is_trusted_bilibili_image_url
        self.assertTrue(check("https://message.biliimg.com/a.png"))
        self.assertTrue(check("https://i0.hdslb.com/bfs/a.png"))
        self.assertFalse(check("http://message.biliimg.com/a.png"))
        self.assertFalse(check("https://message.biliimg.com.example.org/a.png"))
        self.assertFalse(check("https://example.org/a.png"))

    def test_untrusted_url_is_rejected_before_opening_authenticated_session(self):
        client = self._make_client()
        client._get_session = AsyncMock()
        client._ensure_buvid = AsyncMock()

        result = asyncio.run(
            client.download_incoming_image("https://example.org/credential-probe.png")
        )

        self.assertIsNone(result)
        client._get_session.assert_not_awaited()
        client._ensure_buvid.assert_not_awaited()

    def test_412_refreshes_fingerprint_and_retries(self):
        client = self._make_client()
        session = _Session([_Response(412), _Response(200, b"image-bytes")])
        client._get_session = AsyncMock(return_value=session)
        client._ensure_buvid = AsyncMock()

        result = asyncio.run(
            client.download_incoming_image(
                "https://message.biliimg.com/private.png"
            )
        )

        self.assertEqual(result, b"image-bytes")
        self.assertEqual(len(session.requested_urls), 2)
        self.assertEqual(client._ensure_buvid.await_count, 2)

    def test_content_length_limit_rejects_before_reading(self):
        client = self._make_client()
        response = _Response(
            200,
            b"unused",
            content_length=BILIBILI_CLIENT._MAX_INCOMING_IMAGE_BYTES + 1,
        )
        client._get_session = AsyncMock(return_value=_Session([response]))
        client._ensure_buvid = AsyncMock()

        result = asyncio.run(
            client.download_incoming_image(
                "https://message.biliimg.com/oversized.png"
            )
        )

        self.assertIsNone(result)
        self.assertFalse(response.read_called)


class IncomingImageConversionTests(unittest.TestCase):
    def _make_adapter(self, image_data):
        adapter = object.__new__(BILIBILI_ADAPTER.BilibiliAdapter)
        adapter.client = types.SimpleNamespace(
            download_incoming_image=AsyncMock(return_value=image_data)
        )
        adapter._self_uid = 42
        return adapter

    @staticmethod
    def _message():
        return {
            "msg_type": 2,
            "sender_uid": 123,
            "content": '{"url":"https://message.biliimg.com/private.png"}',
            "timestamp": 1_700_000_000,
            "msg_seqno": 456,
        }

    def test_incoming_image_is_embedded_instead_of_forwarding_remote_url(self):
        adapter = self._make_adapter(b"image-bytes")

        message = asyncio.run(adapter.convert_message(self._message(), 123))

        self.assertEqual(
            message.message[0].file,
            f"base64://{base64.b64encode(b'image-bytes').decode()}",
        )
        self.assertNotIn("message.biliimg.com", message.message[0].file)
        self.assertEqual(message.message_str, "[圖片]")

    def test_download_failure_degrades_to_plain_component(self):
        adapter = self._make_adapter(None)

        message = asyncio.run(adapter.convert_message(self._message(), 123))

        self.assertEqual(message.message[0].text, "[圖片下載失敗]")
        self.assertEqual(message.message_str, "[圖片下載失敗]")


class OutgoingImageConversionTests(unittest.TestCase):
    def test_official_file_field_is_resolved_and_uploaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "daily-cache.png"
            image_path.write_bytes(b"cached-image-bytes")

            image = sys.modules["astrbot.api.message_components"].Image(
                image_path.as_uri()
            )
            image.resolved_path = str(image_path)
            plain = sys.modules["astrbot.api.message_components"].Plain("狀態小記")
            chain = sys.modules["astrbot.api.event"].MessageChain([plain, image])

            client = types.SimpleNamespace(
                send_text_message=AsyncMock(return_value=True),
                upload_image=AsyncMock(return_value={"image_url": "uploaded"}),
                send_image_message=AsyncMock(return_value=True),
            )
            event = object.__new__(BILIBILI_EVENT.BilibiliPlatformEvent)
            event.client = client
            event.get_session_id = lambda: "3546881005193386"

            asyncio.run(event.send(chain))

            client.send_text_message.assert_awaited_once_with(
                3546881005193386, "狀態小記"
            )
            client.upload_image.assert_awaited_once_with(
                b"cached-image-bytes", cache_key=image_path.as_uri()
            )
            client.send_image_message.assert_awaited_once_with(
                3546881005193386, {"image_url": "uploaded"}
            )


if __name__ == "__main__":
    unittest.main()
