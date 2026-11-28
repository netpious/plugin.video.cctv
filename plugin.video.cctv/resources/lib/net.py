# -*- coding: utf-8 -*-
"""HTTP 层：只用标准库，避免插件依赖 script.module.requests。

2.3.4：从 urllib 换成 http.client + 连接复用。原来每个请求都重新做
TCP + TLS 握手，实测同一接口连打 8 次：urllib 2.97 秒、复用连接 0.59 秒，
差 5 倍。握手本身约 0.05 秒，一屏几十个请求就白烧掉一两秒。

连接按「主机 + 是否跳过证书校验」分桶，每个线程各存一份（http.client 的
连接不是线程安全的，而 fill_thumbs / _view_all 都是多线程并发拉）。
"""

import gzip
import http.client
import re
import ssl
import threading
from urllib.parse import urljoin, urlsplit

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

# 必须用「不带 UA」的裸请求才返回数据。命中时 _fetch 会自动降级重试。
_BARE_UA_HOSTS = set()

# 每个线程一份连接池：{key: (conn, 创建时的时间戳)}
_LOCAL = threading.local()

# 连接被服务端关掉时抛的异常，命中就丢弃重连
_STALE_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    http.client.CannotSendRequest,
    ConnectionResetError,
    BrokenPipeError,
)

MAX_REDIRECTS = 5


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
    """证书错误可能被包一层，真正的原因在 .reason 里。"""
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


# ---------------------------------------------------------------- 连接池
def _pool():
    pool = getattr(_LOCAL, "pool", None)
    if pool is None:
        pool = {}
        _LOCAL.pool = pool
    return pool


def _close_conn(key):
    pool = _pool()
    conn = pool.pop(key, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def close_all():
    """放掉本线程所有连接。插件进程退出前调一次，避免连接悬着。"""
    pool = _pool()
    for key in list(pool.keys()):
        _close_conn(key)


def _conn_for(scheme, host, port, timeout, context):
    """取一条可复用的连接，没有就新建。"""
    key = (scheme, host, port, id(context))
    pool = _pool()
    conn = pool.get(key)
    if conn is None:
        cls = (http.client.HTTPSConnection if scheme == "https"
               else http.client.HTTPConnection)
        if scheme == "https":
            conn = cls(host, port, timeout=timeout, context=context)
        else:
            conn = cls(host, port, timeout=timeout)
        pool[key] = conn
    return conn, key


def _request(url, method, headers, body, timeout, context):
    """发一个请求，返回 (status, 响应头, 原始字节)。

    连接被服务端关掉时重连一次——复用连接必然会遇到这个，
    不重试的话「偶发请求失败」会变成常态。
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    last = None
    for attempt in range(2):
        conn, key = _conn_for(scheme, host, port, timeout, context)
        try:
            conn.timeout = timeout
            if conn.sock is not None:
                conn.sock.settimeout(timeout)
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, resp.headers, raw
        except _STALE_ERRORS as exc:
            last = exc
            _close_conn(key)
            if attempt:
                raise
        except Exception:
            _close_conn(key)
            raise
    raise last


def _decode_body(raw, resp_headers):
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    return raw


# ---------------------------------------------------------------- 主入口
def fetch(url, timeout=15, referer=None, headers=None, ua=None, allow_insecure_fallback=False):
    """返回 (status, content_type, bytes)。非 2xx 抛 HttpError。

    allow_insecure_fallback：证书校验失败时，用不校验证书的连接再试一次。
    只给央视赛事接口（cbs-u.sports.cctv.com）这种「公开 JSON、站方证书过期」
    的地址用。同一个主机一旦确认证书有问题就记住，后续直接走不校验。
    """
    host = ""
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        pass

    if allow_insecure_fallback and host in _BAD_CERT_HOSTS:
        return _fetch(url, timeout, referer, headers, ua, False, _insecure_context())
    return _fetch(url, timeout, referer, headers, ua, allow_insecure_fallback, _ssl_context())


def _build_headers(referer, headers, ua, url):
    hdrs = dict(DEFAULT_HEADERS)
    # 必须真正不带 UA 才返回数据。DEFAULT_HEADERS 里默认就有 UA，
    # 所以「裸请求」必须显式删掉它，只清 ua 参数是不够的。
    if _needs_bare_ua(url):
        hdrs.pop("User-Agent", None)
    elif ua:
        hdrs["User-Agent"] = ua
    if referer:
        hdrs["Referer"] = referer
    if headers:
        hdrs.update(headers)
    return hdrs


def _fetch(url, timeout, referer, headers, ua, allow_insecure_fallback, context, depth=0):
    hdrs = _build_headers(referer, headers, ua, url)
    try:
        status, resp_headers, raw = _request(url, "GET", hdrs, None, timeout, context)
    except ssl.SSLError as exc:
        if allow_insecure_fallback and _is_cert_error(exc):
            try:
                _BAD_CERT_HOSTS.add(urlsplit(url).hostname or "")
            except Exception:
                pass
            return _fetch(url, timeout, referer, headers, ua, False,
                          _insecure_context(), depth)
        raise
    except OSError as exc:
        if allow_insecure_fallback and _is_cert_error(exc):
            try:
                _BAD_CERT_HOSTS.add(urlsplit(url).hostname or "")
            except Exception:
                pass
            return _fetch(url, timeout, referer, headers, ua, False,
                          _insecure_context(), depth)
        raise

    # 3xx：自己跟跳转（urllib 以前是自动跟的，http.client 不会）
    if status in (301, 302, 303, 307, 308) and depth < MAX_REDIRECTS:
        location = resp_headers.get("Location") or ""
        if location:
            target = urljoin(url, location)
            if _same_target(url, target):
                # 302 自循环：这个主机带浏览器 UA 就会绕回自己，改成裸请求
                host = (urlsplit(url).hostname or "").lower()
                if host and host not in _BARE_UA_HOSTS:
                    _BARE_UA_HOSTS.add(host)
                    return _fetch(url, timeout, referer, headers, ua,
                                  allow_insecure_fallback, context, depth)
                raise HttpError(status, url)
            return _fetch(target, timeout, referer, headers, ua,
                          allow_insecure_fallback, context, depth + 1)

    if status >= 400:
        raise HttpError(status, url)

    ctype = resp_headers.get("Content-Type", "") or ""
    return status, ctype, _decode_body(raw, resp_headers)


def _same_target(a, b):
    """两个地址是不是指向同一个位置（忽略末尾斜杠和锚点）。"""
    try:
        pa, pb = urlsplit(a), urlsplit(b)
    except Exception:
        return a == b
    if (pa.hostname or "").lower() != (pb.hostname or "").lower():
        return False
    return (pa.path or "/").rstrip("/") == (pb.path or "/").rstrip("/") and pa.query == pb.query


def _needs_bare_ua(url):
    """这些主机带浏览器 UA 会被 302 自循环，必须用「裸请求」（不带 UA）。"""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    return host in _BARE_UA_HOSTS


def fetch_headers(url, timeout=15, referer=None, headers=None):
    """GET 只取响应头（登录后收 Set-Cookie 用）。"""
    hdrs = _build_headers(referer, headers, None, url)
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    # 只读响应头，连接不能复用，用完就关
    conn, key = _conn_for(scheme, host, port, timeout, _ssl_context())
    try:
        conn.request("GET", path, headers=hdrs)
        resp = conn.getresponse()
        return resp.headers
    finally:
        _close_conn(key)


def _send_form(url, data, timeout, referer, headers, ua, method):
    """表单请求（POST / PUT / DELETE 共用）。"""
    import urllib.parse

    hdrs = _build_headers(referer, headers, ua, url)
    hdrs["Accept"] = "application/json, text/javascript, */*; q=0.01"
    body = None
    if data is not None:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        body = urllib.parse.urlencode(data).encode("utf-8")
    status, resp_headers, raw = _request(
        url, method, hdrs, body, timeout, _ssl_context())
    if status >= 400:
        raise HttpError(status, url)
    return status, _decode_body(raw, resp_headers), resp_headers


def post_form(url, data, timeout=20, referer=None, headers=None, ua=None):
    """表单 POST（登录用），返回 (status, bytes, headers)。"""
    return _send_form(url, data, timeout, referer, headers, ua, "POST")


def delete_form(url, data, timeout=20, referer=None, headers=None, ua=None):
    """表单 DELETE（取消收藏用），返回 (status, bytes, headers)。"""
    return _send_form(url, data, timeout, referer, headers, ua, "DELETE")


def request_json(url, method="GET", data=None, timeout=20, referer=None, headers=None, ua=None):
    """通用请求（支持 PUT/DELETE/GET/POST），返回原始 bytes。

    央视网的收藏接口要 PUT（收藏）和 DELETE（取消），
    post_form/delete_form 不够用，这里统一走一个。
    """
    method = (method or "GET").upper()
    if method == "GET":
        hdrs = _build_headers(referer, headers, ua, url)
        hdrs["Accept"] = "application/json, text/javascript, */*; q=0.01"
        status, resp_headers, raw = _request(
            url, "GET", hdrs, None, timeout, _ssl_context())
        if status >= 400:
            raise HttpError(status, url)
        return _decode_body(raw, resp_headers)
    return _send_form(url, data, timeout, referer, headers, ua, method)[1]


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
        status, _hdrs, raw = _request(url, "GET", headers, None, timeout, _ssl_context())
        if status >= 400:
            return False
        return raw[:16].startswith(b"#EXTM3U")
    except Exception:
        return False


def fetch_part(url, nbytes=98304, timeout=15, referer=None, headers=None):
    """只读开头 nbytes 字节，返回 (bytes, 总长度, 响应头)。

    总长度优先取 Content-Range（分片有几 MB，用 Range 一次就能拿到大小 + 开头的 SPS），
    服务器不认 Range 时退回 Content-Length。

    这里只读一部分就把连接扔掉：响应体没读完的连接不能复用，
    留着会让下一次请求读到上一个响应的残留。
    """
    hdrs = _build_headers(referer, headers, None, url)
    hdrs["Accept"] = "*/*"
    hdrs["Range"] = "bytes=0-%d" % max(1, nbytes - 1)

    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    host = parts.hostname or ""
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    conn, key = _conn_for(scheme, host, port, timeout, _ssl_context())
    try:
        conn.timeout = timeout
        if conn.sock is not None:
            conn.sock.settimeout(timeout)
        conn.request("GET", path, headers=hdrs)
        resp = conn.getresponse()
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
    finally:
        _close_conn(key)
