"""Bilibili WBI 簽名工具。

自 2023 年起，Bilibili Web 端部分接口（包括私信 send_msg）採用 WBI 簽名鑒權，
需在 Query 參數中附帶 wts（時間戳）與 w_rid（MD5 簽名）。
參考: https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/sign/wbi.md
"""

import time
import urllib.parse
from functools import reduce
from hashlib import md5

# WBI mixin key 重排映射表（固定值）
_MIXIN_KEY_ENC_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]

# 簽名時需要從參數值中過濾的字符
_FILTERED_CHARS = "!'()*"


def get_mixin_key(orig: str) -> str:
    """對 img_key + sub_key 拼接的字符串按映射表重排，取前 32 位作為 mixin key。"""
    return reduce(lambda s, i: s + orig[i], _MIXIN_KEY_ENC_TAB, "")[:32]


def extract_key_from_url(url: str) -> str:
    """從 wbi_img 的 URL 中提取 key（文件名去擴展名）。"""
    if not url:
        return ""
    return url.rsplit("/", 1)[-1].split(".")[0]


def sign_params(params: dict, img_key: str, sub_key: str) -> dict:
    """對請求參數進行 WBI 簽名，返回含 wts 與 w_rid 的新參數字典。"""
    mixin_key = get_mixin_key(img_key + sub_key)
    signed = dict(params)
    signed["wts"] = round(time.time())
    signed = dict(sorted(signed.items()))
    signed = {
        k: "".join(ch for ch in str(v) if ch not in _FILTERED_CHARS)
        for k, v in signed.items()
    }
    query = urllib.parse.urlencode(signed)
    signed["w_rid"] = md5((query + mixin_key).encode()).hexdigest()
    return signed
