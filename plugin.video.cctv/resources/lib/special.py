# -*- coding: utf-8 -*-
"""beta4 新加的四个频道：精品纪录 / 微视频 / 微故事 / 美食。

四个页面结构各不相同，这里按页面实际给的标记解析（2026-11-18 实测）：

* 微视频   `tv.cctv.com/wsp/m/index.shtml`  走 `api.cntv.cn/list/getPCFeedRecommend` 的 jsonp 列表，
          每条自带 title / image / guid，标题前面带 `[栏目]` 前缀，按网页自己的做法去掉。
* 微故事   `tv.cctv.com/yskd/wgs/m/index.shtml`  静态 HTML，`div.video[data-guid][posterImg]` + `a.tit`，
          一部分条目的 posterImg 是空的，这时用点播接口补封面（只补当前页）。
* 美食     `tv.cctv.com/yskd/aims/m/index.shtml`  一级页是「每道菜一个二级页」，
          `li > a > div.data_img > img[data-src]` + `p.title`；二级页里才是视频，结构同微故事。
* 精品纪录 `www.docuchina.cn/showcase/index.shtml`  静态轮播，条目是记录片专题页（VIDA），
          页面里嵌了若干 32 位 guid（分集），拿 `getHttpVideoInfo.do` 逐个验，能用 ack 的当分集。
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor

from . import cctv, net

HOME = "https://tv.cctv.com/"

WSP_API = ("https://api.cntv.cn/list/getPCFeedRecommend"
           "?n=%d&p=%d&serviceId=tvcctv&type=0&utdid=NON-PERSONALIZED")
WSP_PAGE = "https://tv.cctv.com/wsp/m/index.shtml"
WGS_PAGE = "https://tv.cctv.com/yskd/wgs/m/index.shtml"
FOOD_PAGE = "https://tv.cctv.com/yskd/aims/m/index.shtml"
DOCU_PAGE = "http://www.docuchina.cn/showcase/index.shtml"

_TAG = re.compile(r"<[^>]+>")


def _text(raw):
    s = _TAG.sub("", raw or "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", s).strip()


def _abs(url):
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https:" + url
    return url


def _html(url, referer=HOME):
    _, ctype, raw = net.fetch(url, timeout=25, referer=referer)
    return net.decode(raw, ctype)


# ------------------------------------------------------------------ 微视频

def wsp_items(page=1, size=20):
    """微视频：jsonp 接口，按页取。标题去掉 `[栏目]` 前缀。"""
    text = _html(WSP_API % (size, page) + "&cb=cb")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    rows = ((data.get("data") or {}).get("list")) or []
    out = []
    for row in rows:
        title = (row.get("title") or "").strip()
        if "]" in title:
            title = title.split("]", 1)[1].strip()
        guid = (row.get("guid") or "").strip()
        if not title or not guid:
            continue
        out.append({
            "title": title,
            "guid": guid,
            "thumb": _abs(row.get("image") or ""),
            "url": "",
            "plot": title,
        })
    return out


# ------------------------------------------------------------------ 微故事 / 美食二级页

_VIDEO_BLOCK = re.compile(
    r'<div class="video"[^>]*data-guid="([0-9a-fA-F]{32})"[^>]*posterImg="([^"]*)"[^>]*>'
    r'.*?<a class="tit" href="([^"]+)">(.*?)</a>', re.S)


def _video_blocks(html):
    out = []
    seen = set()
    for guid, poster, href, title in _VIDEO_BLOCK.findall(html):
        guid = guid.lower()
        if guid in seen:
            continue
        seen.add(guid)
        out.append({
            "guid": guid,
            "thumb": _abs(poster),
            "url": _abs(href).replace("http://", "https://") if href else "",
            "title": _text(title),
        })
    return out


def wgs_items():
    return _video_blocks(_html(WGS_PAGE, referer=HOME))


def food_sub_items(url):
    return _video_blocks(_html(url, referer=FOOD_PAGE))


def fill_missing(items, workers=8):
    """给没有封面的条目补 title/封面（用点播接口，一次拿两个字段）。"""
    need = [it for it in items if it.get("guid") and (not it.get("thumb") or not it.get("title"))]
    if not need:
        return items

    def one(it):
        try:
            info = cctv.video_info(it["guid"])
        except Exception:
            return
        if not it.get("title"):
            it["title"] = info.get("title") or ""
        if not it.get("thumb"):
            it["thumb"] = info.get("image") or ""

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, need))
    return [it for it in items if it.get("title")]


# ------------------------------------------------------------------ 美食一级页

_FOOD_CARD = re.compile(
    r'<li[^>]*>\s*<a href="([^"]+)"[^>]*>\s*<div class="data_img">\s*<img[^>]*'
    r'(?:data-src="([^"]+)"|src="([^"]+)")[^>]*>\s*</div>\s*<p class="title">(.*?)</p>',
    re.S)


def food_items():
    """美食一级页：每道菜是一个二级页；封面取 img 的 data-src。"""
    html = _html(FOOD_PAGE)
    out = []
    seen = set()
    for href, data_src, src, title in _FOOD_CARD.findall(html):
        url = _abs(href)
        if not url or url in seen:
            continue
        seen.add(url)
        thumb = _abs(data_src or src)
        if not thumb or "img345X194" in thumb:
            thumb = ""
        out.append({
            "title": _text(title),
            "thumb": thumb,
            "url": url,
            "guid": "",
            "plot": _text(title),
        })
    return out


# ------------------------------------------------------------------ 美食：横幅 / 分类标签

_BANNER_BOX = re.compile(r'<div class="lunbo_ind03[^"]*"(.*?)<div class="swiper-pagination', re.S)
_BANNER_SLIDE = re.compile(
    r'<div class="swiper-slide">\s*<a href="([^"]+)"[^>]*>.*?'
    r'<img[^>]*?data-src="([^"]+)"[^>]*>.*?<p class="tit">(.*?)</p>', re.S)


def food_banners():
    """美食首页顶部轮播横幅（浮动的几张图）。"""
    html = _html(FOOD_PAGE)
    m = _BANNER_BOX.search(html)
    body = m.group(1) if m else html
    out = []
    seen = set()
    for href, img, title in _BANNER_SLIDE.findall(body):
        url = _abs(href)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({
            "title": _text(title),
            "thumb": _abs(img),
            "url": url,
            "guid": "",
            "plot": _text(title),
        })
    return out


def food_tabs():
    """美食页的分类标签（荤菜 / 海鲜 / 素菜 …）和各自的内容块，按顺序配对。"""
    html = _html(FOOD_PAGE)
    labels = []
    for raw in re.findall(r"<li[^>]*>([^<]{1,8})</li>", html):
        label = _text(raw)
        if 1 <= len(label) <= 6 and "<" not in raw:
            labels.append(label)
    blocks = []
    parts = re.split(r'class="content(?: cur)?"', html)[1:]
    for part in parts:
        items = []
        seen = set()
        for href, data_src, src, title in _FOOD_CARD.findall(part):
            url = _abs(href)
            if not url or url in seen:
                continue
            seen.add(url)
            thumb = _abs(data_src or src)
            if not thumb or "img345X194" in thumb:
                thumb = ""
            items.append({
                "title": _text(title),
                "thumb": thumb,
                "url": url,
                "guid": "",
                "plot": _text(title),
            })
        blocks.append(items)
    out = []
    for i, items in enumerate(blocks):
        if not items:
            continue
        label = labels[i] if i < len(labels) else "分类 %d" % (i + 1)
        out.append((label, items))
    return out


# ------------------------------------------------------------------ 精品纪录

_LINK = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>')
_IMG = re.compile(r'<img[^>]+src="([^"]+)"')
_TITLE = re.compile(r'<span class="title">(.*?)</span>', re.S)


_H3TITLE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_ALT = re.compile(r'<img[^>]+alt="([^"]*)"')


def docu_items():
    """精品纪录：展示页里所有纪录片卡片（VIDA/ARTI 专题页）。

    页面有两种卡片：顶部轮播用 `<span class="title">`，下面的列表用 `<h3>`；
    以前只认前者，所以只出 5 条。现在两种都认，列表里的都收进来。
    """
    html = _html(DOCU_PAGE, referer="http://www.docuchina.cn/")
    out = []
    seen = set()
    for chunk in html.split('<div class="image"')[1:]:
        href = _LINK.search(chunk)
        img = _IMG.search(chunk)
        if not (href and img):
            continue
        title = ""
        m = _H3TITLE.search(chunk)
        if m:
            title = _text(re.sub(r"<[^>]+>", "", m.group(1)))
        if not title:
            m = _TITLE.search(chunk)
            if m:
                title = _text(m.group(1))
        if not title:
            m = _ALT.search(chunk)
            if m:
                title = _text(m.group(1))
        if not title or len(title) > 40:
            continue
        # 页面侧栏的「通知 / 申报 / 公告」不是纪录片，跳过
        if any(bad in title for bad in ("通知", "申报", "公告", "政策")):
            continue
        url = _abs(href.group(1))
        if not url or url in seen:
            continue
        if not url.lower().endswith(".shtml"):
            continue
        seen.add(url)
        out.append({
            "title": title,
            "thumb": _abs(img.group(1)),
            "url": url,
            "guid": "",
            "plot": title,
        })
    return out


_HEX = re.compile(r"\b[0-9a-f]{32}\b")


def docu_episodes(url, workers=8):
    """记录片专题页里嵌了若干 32 位 guid，能通过点播接口校验的就是分集。"""
    html = _html(url, referer=DOCU_PAGE)
    guids = []
    for g in _HEX.findall(html.lower()):
        if g not in guids:
            guids.append(g)

    def one(g):
        try:
            info = cctv.video_info(g)
        except Exception:
            return None
        if info.get("ack") != "yes":
            return None
        return {
            "title": info.get("title") or "",
            "guid": g,
            "thumb": info.get("image") or "",
            "url": "",
            "plot": info.get("title") or "",
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = [r for r in pool.map(one, guids) if r]
    rows.sort(key=lambda r: r["title"])
    return rows
