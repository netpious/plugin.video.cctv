# -*- coding: utf-8 -*-
"""下载：把点播流（HLS）或报道正文存到本地。

Kodi 没有「把某个网址存成文件」的接口（xbmcvfs 只能操作本地/已挂载路径），
所以这里用标准库自己拉。央视点播全是 HLS，核心流程是：

    guid/page -> getHttpVideoInfo.do -> 若干条 master.m3u8
    master    -> 每档清晰度各自一条媒体列表（index.m3u8）
    媒体列表  -> 一串 .ts 分片，顺序写进同一个文件即可播放

央视的 master 把 480P 放在最前面，而且同一档清晰度会在多条 master 里重复出现，
所以这里把所有 master 摊平、按分辨率去重、从高到低排，交给用户挑。

这个模块不 import xbmc*，路径由调用方翻译好再传进来，方便脱离 Kodi 测试。
"""

import os
import re
import time
from urllib.parse import urljoin

from . import net

# 默认下载目录（Kodi 的 special:// 路径，调用方用 xbmcvfs 翻译）
DEFAULT_DIR = "special://profile/addon_data/plugin.video.cctv/downloads/"

REFERER = "https://tv.cctv.com/"
SEGMENT_TIMEOUT = 30
RETRIES = 3

_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_RESOLUTION_RE = re.compile(r"RESOLUTION=(\d+)x(\d+)")
_BANDWIDTH_RE = re.compile(r"BANDWIDTH=(\d+)")
_EXTINF_RE = re.compile(r"#EXTINF:\s*([\d.]+)")
_KEY_RE = re.compile(r"#EXT-X-KEY:[^\n]*METHOD=([A-Za-z0-9-]+)")
_MAP_RE = re.compile(r'#EXT-X-MAP:[^\n]*URI="([^"]+)"')
_BYTERANGE_RE = re.compile(r"#EXT-X-BYTERANGE:\s*(\d+)(?:@(\d+))?")


class Cancelled(Exception):
    """用户按了取消。"""


# ---------------------------------------------------------------- 文本工具
def human_size(num_bytes):
    if not num_bytes or num_bytes < 0:
        return "未知大小"
    if num_bytes < 1024:
        return "%d B" % num_bytes
    if num_bytes < 1024 * 1024:
        return "%.0f KB" % (num_bytes / 1024.0)
    if num_bytes < 1024 ** 3:
        return "%.1f MB" % (num_bytes / 1048576.0)
    return "%.2f GB" % (num_bytes / 1073741824.0)


def human_duration(seconds):
    seconds = int(seconds or 0)
    if seconds <= 0:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%02d:%02d" % (minutes, secs)


def safe_name(name, limit=90):
    """把标题变成合法的文件名（Windows 下 \\ / : * ? " < > | 都不能用）。"""
    text = _BAD_CHARS.sub("_", (name or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" ._")
    if len(text) > limit:
        text = text[:limit].rstrip(" ._")
    return text or "央视网视频"


def ensure_dir(folder):
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    return folder


def unique_path(folder, name, ext):
    """同名文件已存在时自动加 (2)(3)…，不覆盖已有下载。"""
    if not ext.startswith("."):
        ext = "." + ext
    candidate = os.path.join(folder, name + ext)
    if not os.path.exists(candidate):
        return candidate
    for i in range(2, 1000):
        candidate = os.path.join(folder, "%s (%d)%s" % (name, i, ext))
        if not os.path.exists(candidate):
            return candidate
    return os.path.join(folder, "%s (%d)%s" % (name, int(time.time()), ext))


# ---------------------------------------------------------------- 清晰度
def parse_master(text, base_url):
    """把 master.m3u8 摊平成 [{url,width,height,bitrate}]。

    没有 #EXT-X-STREAM-INF 说明它本身就是媒体列表，当成单档「原始码流」。
    """
    text = (text or "").strip()
    if not text.startswith("#EXTM3U"):
        return []
    if "#EXT-X-STREAM-INF" not in text:
        return [{"url": base_url, "width": 0, "height": 0, "bitrate": 0}]

    lines = [line.strip() for line in text.splitlines()]
    rows = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        attr = line.split(":", 1)[1] if ":" in line else ""
        res = _RESOLUTION_RE.search(attr)
        bw = _BANDWIDTH_RE.search(attr)
        uri = ""
        for j in range(i + 1, min(i + 4, len(lines))):
            if lines[j] and not lines[j].startswith("#"):
                uri = lines[j]
                break
        if not uri:
            continue
        rows.append(
            {
                "url": urljoin(base_url, uri),
                "width": int(res.group(1)) if res else 0,
                "height": int(res.group(2)) if res else 0,
                "bitrate": int(bw.group(1)) if bw else 0,
            }
        )
    return rows


def short_side(stream):
    """竖屏视频的分辨率是 720x1280 这种，标「几 P」要看短边（720P）。"""
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if not width or not height:
        return 0
    return min(width, height)


def sort_key(stream):
    return (short_side(stream), int(stream.get("bitrate") or 0))


def quality_label(stream):
    side = short_side(stream)
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if side >= 2160:
        quality = "4K"
    elif side >= 1080:
        quality = "1080P"
    elif side >= 720:
        quality = "720P"
    elif side >= 480:
        quality = "480P"
    elif side:
        quality = "%dP" % side
    else:
        quality = ""

    parts = []
    if quality:
        parts.append(quality)
    if width and height:
        parts.append("%dx%d" % (width, height))
    bitrate = int(stream.get("bitrate") or 0)
    if bitrate:
        parts.append("%.1f Mbps" % (bitrate / 1000000.0))
    duration = human_duration(stream.get("duration"))
    if duration:
        parts.append(duration)
    return " · ".join(parts) or "原始码流"


def estimate_size(stream):
    duration = float(stream.get("duration") or 0)
    bitrate = float(stream.get("bitrate") or 0)
    if duration > 0 and bitrate > 0:
        return bitrate / 8.0 * duration
    return 0


def inspect(media_url, referer=REFERER, depth=0):
    """读一条 HLS 地址，统计时长/分片数/是否加密/容器类型。

    加密的流（AES-128）插件里无法解密，这里先标出来，界面上直接不给下载。
    """
    _, text = net.get_text(media_url, timeout=15, referer=referer)
    text = (text or "").strip()
    if not text.startswith("#EXTM3U"):
        raise ValueError("不是有效的 m3u8（可能链接已失效）")

    if "#EXT-X-STREAM-INF" in text and depth < 2:
        rows = parse_master(text, media_url)
        rows.sort(key=sort_key, reverse=True)
        if not rows:
            raise ValueError("master 里没有可用码流")
        return inspect(rows[0]["url"], referer, depth + 1)

    duration = 0.0
    for m in _EXTINF_RE.finditer(text):
        try:
            duration += float(m.group(1))
        except ValueError:
            pass

    methods = [m.upper() for m in _KEY_RE.findall(text)]
    encrypted = any(m not in ("NONE", "") for m in methods)
    fmp4 = "#EXT-X-MAP" in text or ".m4s" in text

    return {
        "duration": duration,
        "segments": len(
            [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
        ),
        "encrypted": encrypted,
        "fmp4": fmp4,
        "ext": ".mp4" if fmp4 else ".ts",
    }


def _fill_detail(stream, referer):
    try:
        stream.update(inspect(stream["url"], referer=referer))
    except Exception as exc:  # 单档读不出来不影响其它档
        stream["error"] = str(exc)


def list_streams(guid="", page_url="", referer=None):
    """返回 (标题, 清晰度列表)，列表从高到低排好。

    只用**明文** /asp/hls/ 档位：加密的 /asp/enc/ 那套解出来就是花屏，
    与其给用户一个「1080P」的乱码，不如只列真正能播的档位。
    """
    from . import cctv

    if not guid:
        if not page_url:
            raise ValueError("缺少视频地址")
        guid = cctv.page_guid(page_url)

    info = cctv.video_info(guid, referer=page_url or referer)
    title = info.get("title") or ""

    rows = cctv.clear_ladders(info)
    if not rows:
        raise ValueError("没有找到能正常播放的清晰度")

    for i, row in enumerate(rows, 1):
        row["index"] = i
        row["label"] = quality_label(row)
        row["size"] = estimate_size(row)
        row["encrypted"] = False
        row.setdefault("ext", ".ts")
    return title, rows


# ---------------------------------------------------------------- 下载
def _fetch_bytes(url, byterange=None, referer=REFERER):
    last = None
    for attempt in range(RETRIES):
        headers = {"Accept": "*/*"}
        if byterange:
            headers["Range"] = "bytes=%s" % byterange
        try:
            _, _, raw = net.fetch(url, timeout=SEGMENT_TIMEOUT, referer=referer, headers=headers)
            return raw
        except Exception as exc:
            last = exc
            if attempt + 1 < RETRIES:
                time.sleep(0.6 * (attempt + 1))
    raise last


def _segments_of(media_url, text):
    """把媒体列表拆成 [(类型, 地址, Range)]，含 fMP4 的 #EXT-X-MAP 初始化片。"""
    items = []
    for m in _MAP_RE.finditer(text):
        items.append(("init", urljoin(media_url, m.group(1)), None))

    last_offset = {}
    pending_range = None
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        if line.startswith("#EXT-X-BYTERANGE"):
            m = _BYTERANGE_RE.search(line)
            if m:
                length = int(m.group(1))
                offset = int(m.group(2)) if m.group(2) else None
                pending_range = (length, offset)
            continue
        if line.startswith("#"):
            continue

        url = urljoin(media_url, line)
        if pending_range:
            length, offset = pending_range
            if offset is None:
                # 不写 @offset 表示「接着同一个文件的上一段」
                offset = last_offset.get(url, 0)
            items.append(("seg", url, "%d-%d" % (offset, offset + length - 1)))
            last_offset[url] = offset + length
            pending_range = None
        else:
            items.append(("seg", url, None))
    return items


def download_hls(media_url, dest, callback=None, max_segments=None, referer=REFERER,
                 estimate_bytes=0):
    """逐片下载并顺序写进 dest（.ts 或 .mp4）。返回统计信息字典。

    callback(done_bytes, estimate_bytes, index, total) 返回 False 表示用户取消。
    """
    _, text = net.get_text(media_url, timeout=SEGMENT_TIMEOUT, referer=referer)
    text = (text or "").strip()
    if not text.startswith("#EXTM3U"):
        raise ValueError("不是有效的 m3u8（可能链接已失效）")

    if "#EXT-X-STREAM-INF" in text:
        rows = parse_master(text, media_url)
        rows.sort(key=sort_key, reverse=True)
        if not rows:
            raise ValueError("master 里没有可用码流")
        return download_hls(
            rows[0]["url"], dest, callback, max_segments, referer, estimate_bytes
        )

    methods = [m.upper() for m in _KEY_RE.findall(text)]
    if any(m not in ("NONE", "") for m in methods):
        raise ValueError("这条流是 AES-128 加密的，插件内没有解密库，无法下载")

    items = _segments_of(media_url, text)
    if not items:
        raise ValueError("播放列表里没有分片")

    total = len(items)
    if max_segments:
        total = min(total, max_segments)

    duration = 0.0
    for m in _EXTINF_RE.finditer(text):
        try:
            duration += float(m.group(1))
        except ValueError:
            pass

    written = 0
    partial = dest + ".part"
    ensure_dir(os.path.dirname(dest))
    try:
        with open(partial, "wb") as handle:
            for index, (kind, url, byterange) in enumerate(items[:total], 1):
                data = _fetch_bytes(url, byterange, referer=referer)
                handle.write(data)
                written += len(data)
                if callback and not callback(written, estimate_bytes, index, total):
                    raise Cancelled()
    except Exception:
        _silent_remove(partial)
        raise

    os.replace(partial, dest)
    return {
        "path": dest,
        "bytes": written,
        "segments": total,
        "duration": duration,
    }


def _silent_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def download_article(title, body, source, dest):
    """把报道正文存成 .txt（标题 + 来源 + 正文）。"""
    stamp = time.strftime("%Y-%m-%d %H:%M")
    text = "%s\n\n来源：%s\n下载时间：%s\n\n%s\n" % (title or "", source or "", stamp, body or "")
    ensure_dir(os.path.dirname(dest))
    with open(dest, "w", encoding="utf-8") as handle:
        handle.write(text)
    return {"path": dest, "bytes": len(text.encode("utf-8")), "segments": 1, "duration": 0}


# ---------------------------------------------------------------- 明文高清梯（真正能播的）
# 央视点播有两套 HLS 线路，名字只差一段：
#   /asp/hls/{档位}/…      明文，能正常解码 —— 用它
#   /asp/enc/hls/{档位}/…  同一套转码梯的加密版：TS 结构完好、SPS 能读，
#                          但画面数据是加密的，解码满屏 concealing/corrupt —— 垃圾
# 另外 master 里的 maxbr 会把高档位裁掉（maxbr=2048 时连 720p 都不报），
# 所以要自己按档位号拼地址，并把分片开头的 SPS 真正解出来，确认分辨率。
CLEAR_TIERS = (8000, 6000, 4000, 2000, 1200, 850, 450)
TIER_BITRATE = {
    8000: 8192000,
    6000: 6144000,
    4000: 4096000,
    2000: 2048000,
    1200: 1228800,
    850: 870400,
    450: 460800,
}
CLEAR_ALT_HOSTS = ("newcntv.qcloudcdn.com", "hls.cntv.lxdns.com", "dhls.cntv.qcloudcdn.com")
HLS_HEAD_BYTES = 98304


def tier_url(base_url, tier, host=None):
    """把 hls_url（/asp/hls/main/…/main.m3u8）换成某个档位的地址。"""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(base_url)
    path = parts.path
    if "/asp/hls/main/" in path:
        path = path.replace("/asp/hls/main/", "/asp/hls/%d/" % tier)
        path = re.sub(r"/main\.m3u8$", "/%d.m3u8" % tier, path)
    else:
        path = re.sub(r"/asp/hls/\d+/", "/asp/hls/%d/" % tier, path)
        path = re.sub(r"/\d+\.m3u8$", "/%d.m3u8" % tier, path)
    return urlunsplit((parts.scheme, host or parts.netloc, path, strip_maxbr(parts.query), ""))


def strip_maxbr(query_or_url):
    """去掉 maxbr 参数：它会把 master 里的高档位裁掉（2048 时连 720p 都不报）。"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    if "://" in query_or_url:
        parts = urlsplit(query_or_url)
        query = urlencode(
            [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "maxbr"]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    return urlencode([(k, v) for k, v in parse_qsl(query_or_url, keep_blank_values=True)
                      if k != "maxbr"])


def first_segment(media_url, text=None):
    if text is None:
        _, text = net.get_text(media_url, timeout=12, referer=REFERER)
    if not (text or "").strip().startswith("#EXTM3U"):
        return ""
    for kind, url, _range in _segments_of(media_url, text):
        if kind == "seg":
            return url
    return ""


def _rbsp(data):
    """去掉 H.264 的防竞争字节 00 00 03。"""
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0 and i + 2 < len(data) and data[i + 1] == 0 and data[i + 2] == 3:
            out += data[i:i + 2]
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


class _Bits(object):
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def u(self, n):
        value = 0
        for _ in range(n):
            if self.pos >> 3 >= len(self.data):
                raise ValueError("SPS 读完了")
            bit = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
            value = (value << 1) | bit
            self.pos += 1
        return value

    def ue(self):
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("SPS 里的指数哥伦布编码不合法")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)

    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k % 2 else -(k // 2)


def _skip_scaling_list(bits, size):
    last = 8
    nxt = 8
    for _ in range(size):
        if nxt:
            nxt = (last + bits.se() + 256) % 256
        last = nxt or last


def _parse_sps(nal):
    bits = _Bits(_rbsp(nal[1:]))
    profile = bits.u(8)
    bits.u(8)          # constraint flags
    level = bits.u(8)
    bits.ue()          # seq_parameter_set_id
    chroma = 1
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = bits.ue()
        if chroma == 3:
            bits.u(1)
        bits.ue()      # bit_depth_luma_minus8
        bits.ue()      # bit_depth_chroma_minus8
        bits.u(1)      # qpprime_y_zero_transform_bypass_flag
        if bits.u(1):
            for i in range(8 if chroma != 3 else 12):
                if bits.u(1):
                    _skip_scaling_list(bits, 16 if i < 6 else 64)
    bits.ue()          # log2_max_frame_num_minus4
    poc_type = bits.ue()
    if poc_type == 0:
        bits.ue()
    elif poc_type == 1:
        bits.u(1)
        bits.se()
        bits.se()
        for _ in range(bits.ue()):
            bits.se()
    bits.ue()          # max_num_ref_frames
    bits.u(1)          # gaps_in_frame_num_value_allowed_flag
    width = (bits.ue() + 1) * 16
    height = (bits.ue() + 1) * 16
    frame_mbs_only = bits.u(1)
    if not frame_mbs_only:
        bits.u(1)      # mb_adaptive_frame_field_flag
    bits.u(1)          # direct_8x8_inference_flag
    if bits.u(1):      # frame_cropping_flag
        left = bits.ue()
        right = bits.ue()
        top = bits.ue()
        bottom = bits.ue()
        crop_x = 2 if chroma in (1, 2) else 1
        crop_y = (2 if chroma == 1 else 1) * (2 - frame_mbs_only)
        width -= (left + right) * crop_x
        height -= (top + bottom) * crop_y
    return {
        "width": width,
        "height": height,
        "profile": profile,
        "level": level,
    }


def sps_of(raw):
    """从分片开头找 SPS 并解出真实分辨率；解不出来就返回空字典。

    分片里可能碰巧出现 00 00 01 后面跟着「看起来像 NAL 类型 7」的字节，
    所以逐个候选项都试一遍，只接受合理的结果（宽度是 16 的倍数、尺寸在合理区间）。
    """
    pos = 0
    while True:
        i = raw.find(b"\x00\x00\x01", pos)
        if i < 0:
            return {}
        header = i + 3
        if header >= len(raw):
            return {}
        if raw[header] & 0x1F == 7:
            end = raw.find(b"\x00\x00\x01", header + 1)
            if end < 0:
                end = len(raw)
            try:
                info = _parse_sps(raw[header:end])
            except Exception:
                info = {}
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            if 16 <= width <= 7680 and 16 <= height <= 4320 and width % 16 == 0:
                return info
        pos = header + 1


def probe_tier(base_url, tier):
    """探一个档位：播放列表 → 分片开头 → SPS 分辨率 + 分片总大小。

    不存在（404/403）或不是明文可解的流，返回 None。
    """
    for host in (None,) + CLEAR_ALT_HOSTS:
        url = tier_url(base_url, tier, host)
        try:
            _, text = net.get_text(url, timeout=10, referer=REFERER)
        except Exception:
            continue
        text = (text or "").strip()
        if not text.startswith("#EXTM3U"):
            continue
        # 极少数档位地址会回一个 master，取它最高的一档
        if "#EXT-X-STREAM-INF" in text:
            rows = parse_master(text, url)
            rows.sort(key=sort_key, reverse=True)
            if not rows:
                continue
            url = rows[0]["url"]
            try:
                _, text = net.get_text(url, timeout=10, referer=REFERER)
            except Exception:
                continue
        seg = first_segment(url, text)
        if not seg:
            continue
        try:
            raw, total, _headers = net.fetch_part(seg, HLS_HEAD_BYTES, timeout=15, referer=REFERER)
        except Exception:
            continue
        if not raw:
            continue
        sps = sps_of(raw)
        duration = 0.0
        for m in _EXTINF_RE.finditer(text):
            try:
                duration += float(m.group(1))
            except ValueError:
                pass
        segments = len([l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")])
        return {
            "tier": tier,
            "url": url,
            "seg": seg,
            "size": total or 0,
            "width": int(sps.get("width") or 0),
            "height": int(sps.get("height") or 0),
            "profile": sps.get("profile") or 0,
            "duration": duration,
            "segments": segments,
            "bitrate": TIER_BITRATE.get(tier, 0),
            "clear": bool(sps.get("width")),
        }
    return None


def clear_ladders(base_url, workers=7):
    """把某个视频所有「明文可播」的档位找出来，从高到低排好。

    判定：
      * 分片开头能解出 SPS（分辨率）→ 明文，可播；
      * 与更低档位分片字节数完全相同 → CDN 用低档顶的假档位，丢掉。
    """
    from concurrent.futures import ThreadPoolExecutor

    if not base_url:
        return []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = [r for r in pool.map(lambda t: probe_tier(base_url, t), CLEAR_TIERS) if r]

    rows.sort(key=lambda r: r["tier"])
    seen = set()
    real = []
    for row in rows:
        size = row.get("size") or 0
        if size and size in seen:
            continue          # 假档位：内容与低档一模一样
        if size:
            seen.add(size)
        real.append(row)

    playable = [r for r in real if r.get("clear")]
    chosen = playable or real          # 万一都没解出 SPS，退回「存在的档位」
    chosen.sort(key=lambda r: r["tier"], reverse=True)
    return chosen

