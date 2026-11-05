# -*- coding: utf-8 -*-
"""央视网内容层：首页抓取、视频地址解析、直播频道。"""

import html as html_mod
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import net

HOME_URL = "https://www.cctv.com/"

# 首页有多个入口，任何一个打不开都不至于让「首页」整个空掉
HOME_SOURCES = (
    ("www.cctv.com", "https://www.cctv.com/"),
    ("tv.cctv.com", "https://tv.cctv.com/"),
    ("m.cctv.com", "https://m.cctv.com/"),
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)
VIDEO_INFO_API = "https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid=%s"
SEARCH_API = (
    "https://search.cctv.com/m/if3g_search.php"
    "?page=%d&qtext=%s&type=%s&sort=relevance&pageSize=%d&channel="
)

# 直播线路，两套：
#   LIVE_HOSTS_HD  —— ldncctvwbcd / cdrmld{ch}_1，时移流，master 里给 1080P/720P/360P，
#                     实测能一路拉到 .ts 分片，优先用这套。
#   LIVE_HOSTS     —— ldncctvwbnd / ld{ch}_2，传统直播流，多数只回一个空壳 master
#                     （里面写的二级地址 404），留作兜底。
LIVE_HOSTS_HD = (
    "http://ldncctvwbcdcnc.v.wscdns.com/ldncctvwbcd/cdrmld%s_1/index.m3u8",
    "http://ldncctvwbcdks.v.kcdnvip.com/ldncctvwbcd/cdrmld%s_1/index.m3u8",
    "http://ldncctvwbcdbd.a.bdydns.com/ldncctvwbcd/cdrmld%s_1/index.m3u8",
    "http://ldncctvwbcdali.v.myalicdn.com/ldncctvwbcd/cdrmld%s_1/index.m3u8",
)

LIVE_HOSTS = (
    "http://ldncctvwbndcnc.v.wscdns.com/ldncctvwbnd/ld%s_2/index.m3u8",
    "http://ldncctvwbndtxy.liveplay.myqcloud.com/ldncctvwbnd/ld%s_2/index.m3u8",
    "http://ldncctvwbndks.v.kcdnvip.com/ldncctvwbnd/ld%s_2/index.m3u8",
    "http://ldncctvwbndbd.a.bdydns.com/ldncctvwbnd/ld%s_2/index.m3u8",
    "http://ldncctvwbndali.v.myalicdn.com/ldncctvwbnd/ld%s_2/index.m3u8",
    "http://ldncctvwbndhwy.cntv.myhwcdn.cn/ldncctvwbnd/ld%s_2/index.m3u8",
)

LIVE_URLS = {
    "cctv1": "https://ldncctvwbcdbd.a.bdydns.com/ldncctvwbcd/cdrmldcctv1_1/index.m3u8?b=200-2100",
    "cctv2": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv2_1/index.m3u8?b=200-2100",
    "cctv3": "https://ldocctvwbcdbd.a.bdydns.com/ldocctvwbcd/cdrmldcctv3_1/index.m3u8?b=200-1200",
    "cctv4": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv4_1/index.m3u8?b=200-2100",
    "cctv5": "https://ldcctvwbcdtxy.liveplay.myqcloud.com/ldcctvwbcd/cdrmldcctv5_1/index.m3u8?b=200-2100",
    "cctv5plus": "https://ldcctvwbcdtxy.liveplay.myqcloud.com/ldcctvwbcd/cdrmldcctv5plus_1/index.m3u8?b=200-2100",
    "cctv6": "https://ldocctvwbcdbd.a.bdydns.com/ldocctvwbcd/cdrmldcctv6_1/index.m3u8?b=200-1200",
    "cctv7": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv7_1/index.m3u8?b=200-2100",
    "cctv8": "https://ldocctvwbcdbd.a.bdydns.com/ldocctvwbcd/cdrmldcctv8_1/index.m3u8?b=200-1200",
    "cctv9": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv9_1/index.m3u8?b=200-2100",
    "cctv10": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv10_1/index.m3u8?b=200-2100",
    "cctv11": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv11_1/index.m3u8?b=200-2100",
    "cctv12": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv12_1/index.m3u8?b=200-2100",
    "cctv13": "https://ldncctvwbcdbd.a.bdydns.com/ldncctvwbcd/cdrmldcctv13_1/index.m3u8?b=200-2100",
    "cctv14": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv14_1/index.m3u8?b=200-2100",
    "cctv15": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv15_1/index.m3u8?b=200-2100",
    "cctv16": "https://ldcctvwbcdtxy.liveplay.myqcloud.com/ldcctvwbcd/cdrmldcctv16_1/index.m3u8?b=200-2100",
    "cctv17": "https://ldocctvwbcdtxy.liveplay.myqcloud.com/ldocctvwbcd/cdrmldcctv17_1/index.m3u8?b=200-2100",
    "ipanda": "https://hdgcwbcdcnc.v.wscdns.com/hdgcwbcd/cdrmipanda_1/index.m3u8",
}

# 这些频道的直播流是 cdrm 加密的（逐 NAL 网格），需要本地解密才能看；
# 熊猫频道是明文，不在此列。
DRM_CHANNELS = frozenset(
    ["cctv%d" % i for i in range(1, 18)] + ["cctv5plus"]
)

LIVE_CHANNELS = (
    ("cctv1", "CCTV-1 综合"),
    ("cctv2", "CCTV-2 财经"),
    ("cctv3", "CCTV-3 综艺"),
    ("cctv4", "CCTV-4 中文国际"),
    ("cctv5", "CCTV-5 体育"),
    ("cctv5plus", "CCTV-5+ 体育赛事"),
    ("cctv6", "CCTV-6 电影"),
    ("cctv7", "CCTV-7 国防军事"),
    ("cctv8", "CCTV-8 电视剧"),
    ("cctv9", "CCTV-9 纪录"),
    ("cctv10", "CCTV-10 科教"),
    ("cctv11", "CCTV-11 戏曲"),
    ("cctv12", "CCTV-12 社会与法"),
    ("cctv13", "CCTV-13 新闻"),
    ("cctv14", "CCTV-14 少儿"),
    ("cctv15", "CCTV-15 音乐"),
    ("cctv16", "CCTV-16 奥林匹克"),
    ("cctv17", "CCTV-17 农业农村"),
    ("ipanda", "熊猫频道"),
)

_TAG_RE = re.compile(r"<[^>]+>")
_VIDE_RE = re.compile(
    r'<a\s[^>]*?href="(https?://[^"]*?/VIDE[^"]*?\.shtml)"[^>]*?>(.*?)</a>', re.S | re.I
)
_GUID_RE = re.compile(r'var\s+guid\s*=\s*"([0-9a-fA-F]{32})"')
_IMG_RE = re.compile(
    r'(?:data-src|src)="((?:https?:)?//[^"]+?\.(?:jpg|jpeg|png|webp))"', re.I
)
_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")
_PLACEHOLDER = "bottomplan.gif"

# 视频页有两种编号：新的是 VIDE，老的是 VIDA
_PLAYABLE_RE = re.compile(r"/VID[EA][0-9A-Za-z]+\.shtml")
_POSTER_RE = re.compile(r'((?:https?:)?//[^"\'\s]*?/fmspic/[^"\'\s]+?-1\.jpg)')


def abs_url(url):
    if url.startswith("//"):
        return "https:" + url
    return url


def _s(value):
    """接口里有的字段是 int，统一成字符串再处理。"""
    return "" if value is None else str(value)


def date_of(url):
    """从 /2026/10/30/ 这样的路径里取出播出日期。"""
    m = _DATE_RE.search(url)
    return "%s-%s-%s" % m.groups() if m else ""


def clean_text(raw):
    text = _TAG_RE.sub("", raw or "")
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _thumb_near(text, start, end):
    """在锚点附近找缩略图；跳过懒加载占位图。"""
    for window in (text[max(0, start - 900):start], text[end:end + 300]):
        for m in _IMG_RE.finditer(window):
            url = m.group(1)
            if _PLACEHOLDER in url.lower():
                continue
            return abs_url(url)
    return ""


# ---------------------------------------------------------------- 首页
def homepage_items(limit=80, report=None):
    """抓首页里的视频条目。

    首页有几个入口，按顺序试，第一个有内容的就是结果；每个入口的结果都记进
    report，方便「首页」抓不到时把原因直接显示在列表里。
    """
    for name, url in HOME_SOURCES:
        try:
            _, text = net.get_text(url, timeout=15)
        except Exception as exc:
            if report is not None:
                report.append((name, "抓取失败：%s" % exc))
            continue

        items = parse_home_videos(text, limit)
        if report is not None:
            report.append((name, "%d 条" % len(items)))
        if items:
            return items
    return []


def parse_home_videos(text, limit=80):
    """从首页 HTML 里取出 /VIDE…shtml 的链接和标题。"""
    items = []
    seen_url = set()
    seen_title = set()
    for m in _VIDE_RE.finditer(text):
        url = m.group(1)
        if url in seen_url:
            continue
        title = clean_text(m.group(2))
        if len(title) < 4 or title in seen_title:
            continue
        seen_url.add(url)
        seen_title.add(title)
        items.append(
            {
                "title": title,
                "url": url,
                "thumb": _thumb_near(text, m.start(), m.end()),
                "date": date_of(url),
            }
        )
        if len(items) >= limit:
            break
    return items


def _poster_of(page_url, timeout=8):
    """视频页里直接带封面地址（fmspic/日期/guid-1.jpg），抓一次就够。"""
    try:
        _, page = net.get_text(page_url, timeout=timeout, referer=HOME_URL)
    except Exception:
        return ""
    m = _POSTER_RE.search(page)
    return abs_url(m.group(1)) if m else ""


def fill_thumbs(items, workers=8, deadline=6.0):
    """首页是新闻门户，多数条目没有配图；并发去视频页补封面，超时就放弃。"""
    from concurrent.futures import TimeoutError as FutureTimeout
    from concurrent.futures import as_completed

    targets = [i for i in items if not i.get("thumb")]
    if not targets:
        return items

    pool = ThreadPoolExecutor(max_workers=workers)
    mapping = {}
    try:
        for item in targets:
            mapping[pool.submit(_poster_of, item["url"])] = item
        try:
            for future in as_completed(mapping, timeout=deadline):
                try:
                    poster = future.result()
                except Exception:
                    continue
                if poster:
                    mapping[future]["thumb"] = poster
        except FutureTimeout:
            pass
    finally:
        pool.shutdown(wait=False)
    return items


# ---------------------------------------------------------------- 点播
# 点播接口会给好几条 HLS 地址，画质差别很大：
#   hls_url      明文 /asp/hls/ 梯，能正常解码；但 master 受 maxbr 限制，常常只报 480p
#   hls_enc_url  /asp/enc/hls/ 梯：档位齐全（甚至 1080p），但画面是加密的，
#                解码满屏 concealing/corrupt，就是「1080P 乱码」
#   hls_h5e_url / hls_enc2_url  多数情况 403
# 结论：只走明文 /asp/hls/，并且自己按档位号把 450/850/1200/2000/4000 都试一遍，
# 用 SPS 解出真实分辨率，确认能解码才交出去。见 download.clear_ladders()。
HLS_KEYS = ("hls_url", "hls_enc_url", "hls_h5e_url", "hls_enc2_url")


def _hls_candidates(info):
    man = info.get("manifest") or {}
    out = []
    for key in HLS_KEYS:
        url = info.get(key) or man.get(key) or ""
        if url and url not in out:
            out.append(url)
    return out


def _plain_base(info):
    """拿到明文 /asp/hls/ 的 main.m3u8（优先 hls_url；只有加密地址时把 /enc/ 去掉）。"""
    from . import download as _dl

    url = info.get("hls_url") or ""
    if not url:
        man = info.get("manifest") or {}
        url = man.get("hls_url") or ""
    if not url:
        for master in _hls_candidates(info):
            if "/asp/hls/" in master:
                url = master
                break
            for enc in ("/asp/enc/hls/", "/asp/enc2/hls/", "/asp/h5e/hls/"):
                if enc in master:
                    url = master.replace(enc, "/asp/hls/")
                    break
            if url:
                break
    if not url:
        return ""
    return _dl.strip_maxbr(url)


def clear_ladders(info):
    """这个视频所有「明文、真能解码」的档位，从高到低。

    这是播放和下载共用的唯一入口 —— 加密档位会被 SPS 解析这一步天然筛掉。
    """
    from . import download as _dl

    base = _plain_base(info)
    if not base:
        raise ValueError("没有拿到 HLS 地址")

    rows = _dl.clear_ladders(base)
    if rows:
        return rows

    # 兜底：万一按档位号拼不出来（路径不规整），退回 master 自己广告的最高档
    try:
        _, text = net.get_text(base, timeout=12, referer="https://tv.cctv.com/")
    except Exception:
        text = ""
    if "#EXT-X-STREAM-INF" in (text or ""):
        advertised = _dl.parse_master(text, base)
        advertised.sort(key=_dl.sort_key, reverse=True)
        if advertised:
            top = advertised[0]
            return [{
                "tier": 0,
                "url": top["url"],
                "width": top["width"],
                "height": top["height"],
                "bitrate": top["bitrate"],
                "duration": 0.0,
                "segments": 0,
                "size": 0,
                "clear": False,
            }]
    return []


def video_info(guid, referer=None):
    """guid -> 点播接口的原始 JSON（含全部 HLS 地址）。下载功能要拿它摊平各档清晰度。"""
    info = net.get_json(VIDEO_INFO_API % guid, timeout=15, referer=referer or HOME_URL)
    if info.get("ack") != "yes":
        raise ValueError(info.get("tip_msg") or "接口返回失败")
    return info


def hls_masters(info):
    """点播接口里的所有 HLS 主播放列表地址（排查用）。"""
    return _hls_candidates(info or {})


def page_guid(page_url):
    """视频页 -> guid。"""
    _, page = net.get_text(page_url, timeout=20, referer=HOME_URL)
    m = _GUID_RE.search(page)
    if not m:
        raise ValueError("页面上没有找到 guid")
    return m.group(1)


def _resolve_pid(guid, referer=None):
    """guid -> (stream, title, poster)。视频库/剧集接口直接给 guid，可以少抓一次页面。"""
    info = video_info(guid, referer=referer)
    return resolve_info(info)


def resolve_info(info):
    """已经拿到点播接口 JSON 时，直接取可播的明文最高档。"""
    rows = clear_ladders(info)
    if not rows:
        raise ValueError("没有找到能正常播放的清晰度")

    best = rows[0]
    return best["url"], info.get("title") or "", info.get("image") or ""


def resolve_guid(guid):
    return _resolve_pid(guid)


def resolve_video(page_url):
    """视频页 -> 可播放 HLS 地址。返回 (stream_url, title, poster)。"""
    return _resolve_pid(page_guid(page_url), referer=page_url)


# ---------------------------------------------------------------- 直播
# 实测：腾讯云( txy )的直播地址不带 Referer 一律 403，而 Kodi 播放时只会拿
# 一个裸地址，不带任何头 —— 所以要把 Referer/UA 用 Kodi 的 "|" 后缀一起交给它。
LIVE_REFERER = "https://tv.cctv.com/"
LIVE_PROBE_TIMEOUT = 3


def _with_kodi_headers(url):
    return "%s|User-Agent=%s&Referer=%s" % (url, net.UA, LIVE_REFERER)


def _probe_hosts(channel, hosts, use_referer, timeout=4.0):
    """在给定的一组主机里并行探测，返回第一个能返回 #EXTM3U 的 master 地址。"""
    from concurrent.futures import TimeoutError as FutureTimeout
    from concurrent.futures import as_completed

    urls = [tpl % channel for tpl in hosts]

    def probe(url):
        return url if net.probe_m3u8(url, timeout=LIVE_PROBE_TIMEOUT, referer=use_referer) else None

    pool = ThreadPoolExecutor(max_workers=len(urls))
    try:
        futures = [pool.submit(probe, u) for u in urls]
        try:
            for future in as_completed(futures, timeout=timeout):
                try:
                    ok = future.result()
                except Exception:
                    continue
                if ok:
                    return ok
        except FutureTimeout:
            pass
    finally:
        # 不能用 with：那会等所有探测都结束，最慢的一个能拖好几秒
        pool.shutdown(wait=False)
    return ""


def is_drm_channel(channel):
    """这个直播频道是不是加密的（cdrm）。

    实测：CCTV-1~17 与 5+ 的直播流都是 cdrm（逐个 NAL 的二维网格，
    经典布局，参数从流里取），而熊猫频道是明文，直接播即可。
    """
    return (channel or "") in DRM_CHANNELS


def live_url(channel, budget=8.0):
    """直播地址。

    2.1 起直接用实测过的地址表：19 个频道（CCTV-1~17、5+、熊猫）逐个验证过
    master → 子清单 → .ts 分片，都是明文 TS、不需要任何请求头，Kodi 裸着就能播。
    表里没有的频道才退回老的线路探测。
    """
    fixed = LIVE_URLS.get(channel)
    if fixed:
        return fixed

    deadline = time.monotonic() + budget
    for hosts, use_referer in (
        (LIVE_HOSTS_HD, False),
        (LIVE_HOSTS, False),
        (LIVE_HOSTS_HD, True),
        (LIVE_HOSTS, True),
    ):
        left = deadline - time.monotonic()
        if left <= 0.5:
            break
        url = _probe_hosts(channel, hosts, use_referer, timeout=min(4.0, left))
        if url:
            return _with_kodi_headers(url) if use_referer else url
    return ""


# ---------------------------------------------------------------- 搜索
# 2.1.4：视频搜索改用网页端（search.cctv.com）自己的接口 ifsearch.php。
# 以前用的是移动版 m/if3g_search.php —— 同一个关键词，两边结果不一样：
# 搜「熊出没」网页端第一条是「电影《熊出没 年年有熊》」，移动版却把最近几天的
# 《动画大放映》排在最前（而且这 20 条全是同一个 ALBUMID，被下面的归并逻辑
# 压成一条「动画大放映（剧集）」），于是插件里几乎什么都看不到。
SEARCH_PAGE_API = "https://search.cctv.com/ifsearch.php"
SEARCH_PAGE_URL = "https://search.cctv.com/search.php?qtext=%s&type=video"

# 每个 `<a>` 的标题里都带 <font color="red">关键词</font>，清理时统一去掉
_VSET_ITEM_RE = re.compile(
    r'<a href="([^"]+)"[^>]*class="image"[^>]*title="([^"]*)"', re.S
)
_VSET_RECOMMEND_RE = re.compile(
    r'<a href="([^"]+)"[^>]*id="video_playlist_cont_(\d+)">(.*?)</a>', re.S
)
_VSET_ALBUM_RE = re.compile(r"(VID[A-Za-z][0-9A-Za-z]+)")


def _search_pc(keyword, page, page_size, timeout=25):
    """网页端视频搜索接口，返回和浏览器「全部视频结果」一致的列表。"""
    from urllib.parse import quote, urlencode

    params = {
        "page": page,
        "qtext": keyword,
        "sort": "relevance",
        "pageSize": page_size,
        "type": "video",
        "vtime": "-1",
        "datepid": "1",
        "channel": "不限",
        "pageflag": 0,
        "qtext_str": keyword,
    }
    url = SEARCH_PAGE_API + "?" + urlencode(params)
    _, text = net.get_text(
        url, timeout=timeout,
        referer=SEARCH_PAGE_URL % quote(keyword),
        headers={"Accept": "application/json, text/plain, */*"},
    )

    data = json.loads(text)
    total = int(data.get("total") or 0)

    items = []
    for row in data.get("list") or []:
        link = _s(row.get("urllink")).strip()
        if not link:
            continue
        title = clean_text(_s(row.get("all_title") or row.get("title")))
        if len(title) < 2:
            continue
        channel = _s(row.get("channel")).strip()
        published = _s(row.get("uploadtime"))[:10] or date_of(link)
        items.append(
            {
                "title": title,
                "url": link,
                "thumb": _s(row.get("imglink")).strip(),
                "plot": " · ".join(t for t in (channel, published) if t),
                "date": published,
                "duration": _s(row.get("durations")).strip(),
                "playable": bool(_PLAYABLE_RE.search(link)),
                "album": "",
                "guid": "",
            }
        )
    return items, total


def _search_mobile(keyword, page, page_size, kind):
    """移动版接口：音频搜索（以及 article 等其它页签）走这里。

    字段：DRETITLE / PAGELINK / IMAGELINK / DRECONTENT / PUBTIME / DURATION。
    sort 必须是 relevance，网页端发的就是它；留空时个别关键词（例如「新闻」）
    会被后端当成「没有结果」。
    """
    from urllib.parse import quote

    url = SEARCH_API % (page, quote(keyword), kind or "audio", page_size)
    _, text = net.get_text(url, timeout=20, referer="https://search.cctv.com/m/", ua=MOBILE_UA)

    data = json.loads(text)
    total = int(data.get("total") or 0)

    items = []
    for row in data.get("list") or []:
        link = _s(row.get("PAGELINK")).strip()
        if not link:
            continue
        title = clean_text(_s(row.get("DRETITLE")))
        if len(title) < 2:
            continue

        items.append(
            {
                "title": title,
                "url": link,
                "thumb": _s(row.get("IMAGELINK")).strip(),
                "plot": clean_text(_s(row.get("DRECONTENT"))),
                "date": _s(row.get("PUBTIME"))[:10] or date_of(link),
                "duration": _s(row.get("DURATION")).strip(),
                "playable": bool(_PLAYABLE_RE.search(link)),
                # 同一部剧集的每一集都带相同的 ALBUMID，用它把「剧集」和「单条视频」分开
                "album": _s(row.get("ALBUMID")).strip(),
                # 音频条目的页面里没有 guid 变量，但接口直接给了（就是 ?guid= 那个值）
                "guid": _s(row.get("DETAILSID")).strip(),
            }
        )
    return items, total


def search(keyword, page=1, page_size=20, kind="video", timeout=25):
    """央视搜索。视频走网页端接口，音频走移动版接口。

    kind 是网页搜索的页签：article（网页/文章）、video（视频）、audio（音频）。
    本插件只用 video 和 audio —— 文章的搜索还没做。timeout 给兜底搜索缩短用。
    """
    if (kind or "video") == "video":
        return _search_pc(keyword, page, page_size, timeout=timeout)
    return _search_mobile(keyword, page, page_size, kind)


def search_videosets(keyword):
    """网页搜索顶部那两块「推荐 / 其他相关视频集结果」，也就是一叠剧集入口。

    这一段是 search.php 服务端直接渲染的 HTML（没有对应的 JSON 接口），
    所以只能取整页来解析；条目形如：
        <a href="http://tv.cctv.com/2015/05/29/VIDA1432879954445219.shtml"
           ... class="image" title="熊出没之雪岭熊风">
    返回 [{"title", "album", "url"}]，album 就是 VIDA… 那一串。
    """
    from urllib.parse import quote

    try:
        _status, page = net.get_text(
            SEARCH_PAGE_URL % quote(keyword), timeout=25,
            referer="https://search.cctv.com/",
        )
    except Exception:
        return []

    out, seen = [], set()

    def push(link, title):
        album = _VSET_ALBUM_RE.search(link or "")
        name = clean_text(title)
        if not album or len(name) < 2:
            return
        key = album.group(1)
        if key in seen:
            return
        seen.add(key)
        out.append({"title": name, "album": key, "url": link})

    # 1) 页面顶部推荐的两个视频集（id="video_playlist_cont_1/2"）
    for match in _VSET_RECOMMEND_RE.finditer(page):
        push(match.group(1), match.group(3))

    # 2) 「其他相关视频集结果」那一段
    start = page.find("其他相关视频集结果")
    end = page.find("全部视频结果")
    segment = page[start:end] if start >= 0 and end > start else page
    for match in _VSET_ITEM_RE.finditer(segment):
        push(match.group(1), match.group(2))

    return out
