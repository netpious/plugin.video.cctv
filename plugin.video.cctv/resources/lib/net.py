# -*- coding: utf-8 -*-
"""HTTP 层：只用标准库，避免插件依赖 script.module.requests。"""

import gzip
import re
import ssl
import urllib.error
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# media.app.cctv.com 这类老服务器不支持 RFC 5746 安全重协商，Python 3.10+ 默认拒绝，
# 会抛 SSLError(UNSAFE_LEGACY_RENEGOTIATION_DISABLED)。0x4 就是
# OP_LEGACY_SERVER_CONNECT（3.12 之前没有这个常量名，所以直接写数值）。
_SSL_CONTEXT = None
_INSECURE_CONTEXT = None
# 已经确认证书有问题的主机（只对显式允许兜底的调用生效）
_BAD_CERT_HOSTS = set()


def _ssl_context():
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        ctx = ssl.create_default_context()
        try:
            ctx.options |= 0x4
        except Exception:
            pass
        _SSL_CONTEXT = ctx
    return _SSL_CONTEXT


def _insecure_context():
    """兜底用：只在站方证书过期这类「不是我们能修」的情况下退到不校验。"""
    global _INSECURE_CONTEXT
    if _INSECURE_CONTEXT is None:
        ctx = ssl._create_unverified_context()
        try:
            ctx.options |= 0x4
        except Exception:
            pass
        _INSECURE_CONTEXT = ctx
    return _INSECURE_CONTEXT


def _is_cert_error(exc):
    """urllib 把证书错误包成 URLError，真正的原因在 .reason 里。"""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(reason)

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


class HttpError(Exception):
    def __init__(self, status, url):
        self.status = status
        self.url = url
        super().__init__("HTTP %s: %s" % (status, url))


def fetch(url, timeout=15, referer=None, headers=None, ua=None, allow_insecure_fallback=False):
    """返回 (status, content_type, bytes)。非 2xx 抛 HttpError。

    allow_insecure_fallback：证书校验失败时，用不校验证书的连接再试一次。
    只给央视赛事接口（cbs-u.sports.cctv.com）这种「公开 JSON、站方证书过期」
    的地址用 —— 2026-11-05 它的证书到期后，赛事菜单直接空了。
    同一个主机一旦确认证书有问题就记住，后续请求直接走不校验，不再每次先失败一遍。
    """
    host = ""
    try:
        from urllib.parse import urlsplit

        host = urlsplit(url).hostname or ""
    except Exception:
        pass

    if allow_insecure_fallback and host in _BAD_CERT_HOSTS:
        return _fetch(url, timeout, referer, headers, ua, False, _insecure_context())
    return _fetch(url, timeout, referer, headers, ua, allow_insecure_fallback, _ssl_context())


def _fetch(url, timeout, referer, headers, ua, allow_insecure_fallback, context):
    hdrs = dict(DEFAULT_HEADERS)
    if ua:
        hdrs["User-Agent"] = ua
    if referer:
        hdrs["Referer"] = referer
    if headers:
        hdrs.update(headers)

    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read()
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, url)
    except urllib.error.URLError as exc:
        if allow_insecure_fallback and _is_cert_error(exc):
            try:
                from urllib.parse import urlsplit

                _BAD_CERT_HOSTS.add(urlsplit(url).hostname or "")
            except Exception:
                pass
            return _fetch(url, timeout, referer, headers, ua, False, _insecure_context())
        raise

    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return status, ctype, raw


def decode(raw, ctype=""):
    """央视网有的页面声明 UTF-8 实际是 GBK，先严格试 UTF-8，失败再回退。"""
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    declared = (m.group(1) if m else "").lower()

    if declared in ("gb2312", "gbk", "gb18030"):
        return raw.decode("gb18030", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", "replace")


def get_text(url, timeout=15, referer=None, headers=None, ua=None, allow_insecure_fallback=False):
    status, ctype, raw = fetch(url, timeout=timeout, referer=referer, headers=headers,
                               ua=ua, allow_insecure_fallback=allow_insecure_fallback)
    return status, decode(raw, ctype)


def get_json(url, timeout=15, referer=None):
    import json

    _, text = get_text(url, timeout=timeout, referer=referer)
    return json.loads(text)


def probe_m3u8(url, timeout=5, referer="https://tv.cctv.com/"):
    """只读开头几十字节，判断是不是可用的 HLS 主播放列表。

    referer 传空值表示「不带任何 Referer」，用来挑一条完全不需要请求头的线路。
    这里用的头必须和最终交给 Kodi 的一致，否则会出现「探测通过、播放 403」。
    """
    try:
        headers = {"User-Agent": UA, "Accept": "*/*"}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            head = resp.read(16)
        return head.startswith(b"#EXTM3U")
    except Exception:
        return False


def fetch_part(url, nbytes=98304, timeout=15, referer=None, headers=None):
    """只读开头 nbytes 字节，返回 (bytes, 总长度, 响应头)。

    总长度优先取 Content-Range（分片有几 MB，用 Range 一次就能拿到大小 + 开头的 SPS），
    服务器不认 Range 时退回 Content-Length。
    """
    hdrs = dict(DEFAULT_HEADERS)
    hdrs["Accept"] = "*/*"
    hdrs["Range"] = "bytes=0-%d" % max(1, nbytes - 1)
    if referer:
        hdrs["Referer"] = referer
    if headers:
        hdrs.update(headers)

    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        raw = resp.read(nbytes)
        total = 0
        crange = resp.headers.get("Content-Range") or ""
        m = re.search(r"/(\d+)\s*$", crange)
        if m:
            total = int(m.group(1))
        else:
            length = resp.headers.get("Content-Length") or ""
            if length.isdigit():
                total = int(length)
    return raw, total, resp.headers

