# -*- coding: utf-8 -*-
"""播放时解密：起一个本地 HTTP 服务，把 H5E 加密的 HLS 边解边喂给 Kodi。

为什么必须本地代理：
  Kodi 自己没有 H5E 解码能力，而这些高画质流（720P/1080P）只有加密版。
  所以插件在本机 127.0.0.1 上开一个临时 HTTP 服务：
      /index.m3u8   改写好的点播清单（分片指向本机）
      /s/<n>.ts     按需下载 CDN 分片 -> 解密 -> 返回
  Kodi 拿到的就是一条普通 HLS，能正常拖动进度，跟在线看没区别。

代价与边界：
  * 解密是纯 Python，约 1 MB/s；720P(2Mbps) 约 4 倍实时，1080P 约 2 倍，够用但不宽裕；
  * 只在本进程活着的时候有效，所以调用方要等播放结束再退出；
  * 服务只绑 127.0.0.1，随机端口，播放结束立刻关闭。
"""

import http.server
import socket
import socketserver
import threading
import time
from collections import OrderedDict

from . import h5e, net

REFERER = "https://tv.cctv.com/"
CACHE_SEGMENTS = 3

# 这几条 CDN（*.v.cntv.cn）会按 UA 拦人：完整 Chrome 标识一律 403，
# 但短 UA / Kodi / VLC / 不带 UA 都能过（2026-11 实测）。
# 所以这里不用 net.py 那个完整 Chrome UA，单独走一套。
UA = "Mozilla/5.0"


def _get(url, timeout=30):
    """取文本/二进制。不带 Referer，UA 用能过 CDN 的短标识。"""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=net._ssl_context()) as resp:
        return resp.read()


def log(msg, level=None):
    try:
        import xbmc

        xbmc.log("[cctv-h5eplay] " + str(msg), level if level is not None else xbmc.LOGINFO)
    except Exception:
        pass


def strip_maxbr(url):
    """去掉 maxbr：它会把清单里比它高的档位裁掉。"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if k != "maxbr"])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _variants(master_url):
    """解析 master，返回 [(bandwidth, resolution, 绝对地址)]。"""
    text = _get(master_url, timeout=15).decode("utf-8", "replace")
    rows = []
    attrs = None
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#EXT-X-STREAM-INF"):
            attrs = {}
            for part in s[len("#EXT-X-STREAM-INF:"):].split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    attrs[k.strip()] = v.strip()
        elif not s.startswith("#") and attrs is not None:
            try:
                bw = int(attrs.get("BANDWIDTH") or 0)
            except ValueError:
                bw = 0
            from urllib.parse import urljoin

            rows.append((bw, attrs.get("RESOLUTION") or "", urljoin(master_url, s)))
            attrs = None
    return rows


def _media_playlist(media_url):
    """媒体清单 -> (分片绝对地址列表, 时长列表)。"""
    from urllib.parse import urljoin

    text = _get(media_url, timeout=15).decode("utf-8", "replace")
    if not (text or "").lstrip().startswith("#EXTM3U"):
        raise ValueError("不是 m3u8")
    segs, durs, cur = [], [], 0.0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#EXTINF"):
            try:
                cur = float(s.split(":", 1)[1].split(",")[0])
            except (IndexError, ValueError):
                cur = 0.0
        elif s and not s.startswith("#"):
            segs.append(urljoin(media_url, s))
            durs.append(cur or 4.0)
            cur = 0.0
    if not segs:
        raise ValueError("清单里没有分片")
    return segs, durs


def pick_h5e(info):
    """从点播接口返回里挑 H5E 主清单；优先 h5e，其次 enc2。"""
    man = info.get("manifest") or {}
    for key in ("hls_h5e_url", "hls_enc2_url", "hls_enc_url"):
        url = info.get(key) or man.get(key) or ""
        if url:
            return strip_maxbr(url), key
    return "", ""


class Playback(object):
    """一次播放对应的解密代理。"""

    def __init__(self, master_url, referer=REFERER):
        self.master_url = strip_maxbr(master_url)
        self.referer = referer
        self.segments = []
        self.durations = []
        self.resolution = ""
        self.bandwidth = 0
        self.height = 0
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        self._port = 0
        self._vpid = None
        self._decrypted = 0
        self._failed = 0

    # -------------------------------------------------------------- 内部
    def _prepare(self):
        variants = _variants(self.master_url)
        if not variants:
            # 有些地址本身就是媒体清单
            self.segments, self.durations = _media_playlist(self.master_url)
            return
        variants.sort(key=lambda r: r[0])
        self.bandwidth, self.resolution, media = variants[-1]
        try:
            self.height = int(self.resolution.lower().split("x")[-1])
        except (ValueError, IndexError):
            self.height = 0
        self.segments, self.durations = _media_playlist(media)

    def _segment_bytes(self, index):
        with self._lock:
            hit = self._cache.get(index)
            if hit is not None:
                self._cache.move_to_end(index)
                return hit
        raw = _get(self.segments[index], timeout=30)
        if self._vpid is None:
            self._vpid = h5e.video_pid(raw)
        out, stats = h5e.decrypt_ts(raw, self._vpid)
        if not stats.get("ok"):
            self._failed += 1
            if self._failed > 2:
                log("连续解密失败，回退原样输出（可能这条不是 H5E）")
            out = raw
        else:
            self._decrypted += 1
        with self._lock:
            self._cache[index] = out
            while len(self._cache) > CACHE_SEGMENTS:
                self._cache.popitem(last=False)
        return out

    def _playlist_text(self):
        target = max(self.durations) if self.durations else 4
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            "#EXT-X-TARGETDURATION:%d" % int(round(target)),
            "#EXT-X-MEDIA-SEQUENCE:0",
        ]
        for i, dur in enumerate(self.durations):
            lines.append("#EXTINF:%.3f," % dur)
            lines.append("s/%d.ts" % i)
        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    # -------------------------------------------------------------- 对外
    def start(self):
        self._prepare()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def _send(self, body, ctype):
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                try:
                    if path.endswith("index.m3u8"):
                        self._send(owner._playlist_text().encode("utf-8"),
                                   "application/vnd.apple.mpegurl")
                        return
                    if "/s/" in path and path.endswith(".ts"):
                        idx = int(path.rsplit("/", 1)[-1][:-3])
                        try:
                            body = owner._segment_bytes(idx)
                        except KeyError:
                            self.send_error(404)
                            return
                        except Exception as exc:
                            log("分片 %s 失败：%r" % (idx, exc), 4)
                            self.send_error(500)
                            return
                        self._send(body, "video/mp2t")
                        return
                    self.send_error(404)
                except Exception as exc:
                    log("代理请求失败 %s: %r" % (path, exc), 4)
                    try:
                        self.send_error(500)
                    except Exception:
                        pass

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2})
        self._thread.daemon = True
        self._thread.start()
        log("解密代理已启动：端口 %d，%d 个分片，%s @%dk"
            % (self._port, len(self.segments), self.resolution or "?", self.bandwidth // 1000))
        return "http://127.0.0.1:%d/index.m3u8" % self._port

    @property
    def url(self):
        return "http://127.0.0.1:%d/index.m3u8" % self._port

    def stats(self):
        return {"segments": len(self.segments), "decrypted": self._decrypted,
                "failed": self._failed, "resolution": self.resolution,
                "bandwidth": self.bandwidth}

    def stop(self):
        server, thread = self._server, self._thread
        self._server, self._thread = None, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        if thread is not None:
            try:
                thread.join(timeout=2)
            except Exception:
                pass
        with self._lock:
            self._cache.clear()
        log("解密代理已关闭：%s" % self.stats())


class LivePlayback(Playback):
    """直播版：清单是滑动窗口，分片按「序号」取，边拉边解。

    与点播的区别：
      * 清单没有 ENDLIST，Kodi 每隔一个 TARGETDURATION 回来要一次；
      * 每次取清单都去 CDN 拿最新的，把分片登记成 序号 -> 地址；
      * Kodi 请求 /s/<序号>.ts 时按序号取分片，解密后返回。
    """

    def __init__(self, master_url, referer=REFERER):
        Playback.__init__(self, master_url, referer)
        self._media = ""
        self._entries = OrderedDict()
        self._target = 4
        self._last_seq = 0
        self.live = True

    def _prepare(self):
        variants = _variants(self.master_url)
        if variants:
            variants.sort(key=lambda r: r[0])
            self.bandwidth, self.resolution, self._media = variants[-1]
            try:
                self.height = int(self.resolution.lower().split("x")[-1])
            except (ValueError, IndexError):
                self.height = 0
        else:
            self._media = self.master_url
        self._refresh()

    def _refresh(self):
        import re
        from urllib.parse import urljoin

        text = _get(self._media, timeout=15).decode("utf-8", "replace")
        if not text.lstrip().startswith("#EXTM3U"):
            raise ValueError("直播清单无效")
        m = re.search(r"#EXT-X-MEDIA-SEQUENCE:\s*(\d+)", text)
        seq = int(m.group(1)) if m else 0
        m = re.search(r"#EXT-X-TARGETDURATION:\s*(\d+)", text)
        if m:
            self._target = max(1, int(m.group(1)))
        durs, uris, cur = [], [], 0.0
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#EXTINF"):
                try:
                    cur = float(s.split(":", 1)[1].split(",")[0])
                except (IndexError, ValueError):
                    cur = 0.0
            elif s and not s.startswith("#"):
                durs.append(cur or float(self._target))
                uris.append(urljoin(self._media, s))
                cur = 0.0
        if not uris:
            raise ValueError("直播清单里没有分片")
        # 只增不删：Kodi 可能在清单滚动之后才来要旧分片，删掉就 404 卡住
        for i, uri in enumerate(uris):
            self._entries[seq + i] = uri
        while len(self._entries) > 60:
            self._entries.popitem(last=False)
        self._last_seq = seq + len(uris) - 1
        return seq, uris, durs

    def _playlist_text(self):
        seq, uris, durs = self._refresh()
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-TARGETDURATION:%d" % self._target,
            "#EXT-X-MEDIA-SEQUENCE:%d" % seq,
        ]
        for i, dur in enumerate(durs):
            lines.append("#EXTINF:%.3f," % dur)
            lines.append("s/%d.ts" % (seq + i))
        return "\n".join(lines) + "\n"

    def _segment_bytes(self, index):
        with self._lock:
            hit = self._cache.get(index)
            if hit is not None:
                self._cache.move_to_end(index)
                return hit
        uri = self._entries.get(index)
        if uri is None:
            try:
                self._refresh()
            except Exception:
                pass
            uri = self._entries.get(index)
        if uri is None:
            raise KeyError("分片 %s 已经滑出窗口" % index)
        raw = _get(uri, timeout=20)
        if self._vpid is None:
            self._vpid = h5e.video_pid(raw)
        out, stats = h5e.decrypt_ts(raw, self._vpid, live=True)
        if stats.get("ok"):
            self._decrypted += 1
        else:
            self._failed += 1
            out = raw
        with self._lock:
            self._cache[index] = out
            while len(self._cache) > CACHE_SEGMENTS:
                self._cache.popitem(last=False)
        return out


def probe(master_url):
    """不启动服务，只看这条地址能不能用（返回分片数 / 分辨率）。"""
    play = Playback(master_url)
    play._prepare()
    return {"segments": len(play.segments), "resolution": play.resolution,
            "bandwidth": play.bandwidth}


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()
