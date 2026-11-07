# -*- coding: utf-8 -*-
"""央视网 2.0 新增的数据源：频道、片库、时政、新闻扩展列。

一共三类接口，都是 cctv.com 自己的：

1. 栏目/频道（90% 的频道首页都在用）
   https://api.cntv.cn/newList/getMixListByPageId?serviceId=<svc>&id=<PAGE…>&p=&n=
   少数站点用 getMixListByPrimaryPageId / getMixListByTopicId，参数一样。

2. 片库（tv.cctv.com/yxg）
   https://api.cntv.cn/List/getVideoAlbumList
       ?channelid=<CHAL…>&serviceId=tvcctv&fc=<类>&n=20&topv=1&p=<页>
       [&sc=类别][&area=地区][&year=年份][&letter=字母][&channel=频道][&bigday=节日]

3. 快看（新闻频道的视频栏）
   https://api.cntv.cn/list/getListByPageid?serviceId=pcnews&kind=a&id=<PAGE…>&level=81

没有可用 page id 的频道（央博、乐龄、阅读、艺术、数智、5G、产业+），
退回「抓频道首页的 .shtml 链接」——条数少，但不会空。
"""

import json
import re
from urllib.parse import urljoin

from . import net
from .portal import _clean, _date_of, _jsonp

# ---------------------------------------------------------------- 接口
MIX_API = (
    "https://api.cntv.cn/newList/getMixListByPageId"
    "?serviceId=%s&id=%s&p=%d&n=%d"
)
MIX_PAGE_SIZE = 36

PAGEID_API = (
    "https://api.cntv.cn/list/getListByPageid"
    "?serviceId=pcnews&kind=a&id=%s&p=%d&n=%d"
)
PAGEID_PAGE_SIZE = 30

# 视频栏目列表（习式妙语这类「一个 mid 一档」的视频栏）
VPLIST_API = (
    "https://media.app.cctv.com/vapi/video/vplist.do"
    "?chid=%s&mid=%s&p=%d&n=%d"
)
VPLIST_PAGE_SIZE = 10

PIANKU_API = (
    "https://api.cntv.cn/List/getVideoAlbumList"
    "?channelid=%s&serviceId=tvcctv&fc=%s&n=%d&topv=1&p=%d"
)
PIANKU_PAGE_SIZE = 20
PIANKU_MAX = 2000

# 片库四个大类的 channelid（来自 tv.cctv.com/yxg/m/<类>/ 页面里的 var cid）
PIANKU_CATS = (
    {"name": "电视剧", "cid": "CHAL1460955853485115"},
    {"name": "动画片", "cid": "CHAL1460955899450127"},
    {"name": "纪录片", "cid": "CHAL1460955924871139"},
    {"name": "特别节目", "cid": "CHAL1460955953877151"},
)

# 筛选组的显示名 / 接口参数名
PIANKU_GROUP_NAMES = {
    "fl": "按类别",
    "dq": "按地区",
    "nf": "按年份",
    "szm": "按字母",
    "pd": "按频道",
    "jr": "按节日/大事件",
}
PIANKU_PARAM = {
    "fl": "sc",
    "dq": "area",
    "nf": "year",
    "szm": "letter",
    "pd": "channel",
    "jr": "bigday",
}

_LETTERS = (("全部", ""),) + tuple((c, c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")

PIANKU_FILTERS = {
    "电视剧": {
        "fl": (("全部", ""), ("谍战", "谍战"), ("悬疑", "悬疑"), ("刑侦", "刑侦"),
               ("历史", "历史"), ("古装", "古装"), ("武侠", "武侠"), ("军旅", "军旅"),
               ("战争", "战争"), ("喜剧", "喜剧"), ("青春", "青春"), ("言情", "言情"),
               ("偶像", "偶像"), ("家庭", "家庭"), ("年代", "年代"), ("革命", "革命"),
               ("农村", "农村"), ("都市", "都市"), ("其他", "其他")),
        "dq": (("全部", ""), ("内地（大陆）", "内地（大陆）"), ("港澳台", "港澳台"),
               ("欧美", "欧美"), ("日韩", "日韩"), ("其他", "其他")),
        "nf": (("全部", ""),) + tuple((str(y), str(y)) for y in range(2024, 1996, -1)),
        "szm": _LETTERS,
    },
    "动画片": {
        "fl": (("全部", ""), ("亲子", "亲子"), ("搞笑", "搞笑"), ("冒险", "冒险"),
               ("动作", "动作"), ("宠物", "宠物"), ("体育", "体育"), ("益智", "益智"),
               ("历史", "历史"), ("教育", "教育"), ("校园", "校园"), ("言情", "言情"),
               ("武侠", "武侠"), ("经典", "经典"), ("未来", "未来"), ("古代", "古代"),
               ("神话", "神话"), ("真人", "真人"), ("励志", "励志"), ("热血", "热血"),
               ("奇幻", "奇幻"), ("童话", "童话"), ("剧情", "剧情"), ("夺宝", "夺宝"),
               ("其他", "其他")),
        "dq": (("全部", ""), ("内地（大陆）", "内地（大陆）"), ("港澳台", "港澳台"),
               ("欧美", "欧美")),
        "nf": (("全部", ""),) + tuple((str(y), str(y)) for y in range(2025, 1999, -1)),
        "szm": _LETTERS,
    },
    "纪录片": {
        "pd": (("全部", ""),
               ("CCTV-1 综合", "CCTV-1综合,CCTV-1高清,CCTV-1综合高清"),
               ("CCTV-2 财经", "CCTV-2财经,CCTV-2高清,CCTV-2财经高清"),
               ("CCTV-3 综艺", "CCTV-3综艺,CCTV-3综艺高清"),
               ("CCTV-4 中文国际", "CCTV-4中文国际,CCTV-4高清,CCTV-4中文国际(亚)高清"),
               ("CCTV-5 体育", "CCTV-5体育,CCTV-5体育高清"),
               ("CCTV-6 电影", "CCTV-6电影,CCTV-6电影高清"),
               ("CCTV-7 国防军事", "CCTV-7军事农业,CCTV-7军事农业高清，CCTV-7国防军事高清"),
               ("CCTV-9 纪录", "CCTV-9纪录,CCTV-9高清,CCTV-9纪录高清"),
               ("CCTV-10 科教", "CCTV-10科教,CCTV-10高清,CCTV-10科教高清"),
               ("CCTV-11 戏曲", "CCTV-11戏曲"),
               ("CCTV-12 社会与法", "CCTV-12社会与法,CCTV-12社会与法高清"),
               ("CCTV-13 新闻", "CCTV-13新闻"),
               ("CCTV-14 少儿", "CCTV-14少儿,CCTV-14少儿高清"),
               ("CCTV-15 音乐", "CCTV-15音乐,CCTV-15音乐高清"),
               ("CCTV-17 农业农村", "CCTV-17农业农村高清")),
        "fl": (("全部", ""), ("人文历史", "人文历史"), ("人物", "人物"), ("军事", "军事"),
               ("探索", "探索"), ("社会", "社会"), ("自然", "自然"), ("时政", "时政"),
               ("经济", "经济"), ("科技", "科技")),
        "nf": (("全部", ""),) + tuple((str(y), str(y)) for y in range(2025, 2007, -1)),
        "szm": _LETTERS,
    },
    "特别节目": {
        "pd": (("全部", ""),
               ("CCTV-1 综合", "CCTV-1综合,CCTV-1高清,CCTV-1综合高清"),
               ("CCTV-2 财经", "CCTV-2财经,CCTV-2高清,CCTV-2财经高清"),
               ("CCTV-3 综艺", "CCTV-3综艺,CCTV-3高清,CCTV-3综艺高清"),
               ("CCTV-4 中文国际", "CCTV-4中文国际,CCTV-4高清,CCTV-4中文国际(亚)高清,"
                                   "CCTV-4中文国际(亚)"),
               ("CCTV-5 体育", "CCTV-5体育,CCTV-5高清,CCTV-5体育高清"),
               ("CCTV-6 电影", "CCTV-6电影,CCTV-6高清,CCTV-6电影高清"),
               ("CCTV-7 国防军事", "CCTV-7军事农业,CCTV-7高清,CCTV-7军事农业高清,"
                                   "CCTV-7国防军事高清"),
               ("CCTV-8 电视剧", "CCTV-8电视剧,CCTV-8高清,CCTV-8电视剧高清"),
               ("CCTV-9 纪录", "CCTV-9纪录,CCTV-9高清,CCTV-9纪录高清"),
               ("CCTV-10 科教", "CCTV-10科教,CCTV-10高清,CCTV-10科教高清"),
               ("CCTV-11 戏曲", "CCTV-11戏曲,CCTV-11高清,CCTV-11戏曲高清"),
               ("CCTV-12 社会与法", "CCTV-12社会与法,CCTV-12高清,CCTV-12社会与法高清"),
               ("CCTV-13 新闻", "CCTV-13新闻,CCTV-13高清,CCTV-13新闻高清"),
               ("CCTV-14 少儿", "CCTV-14少儿,CCTV-14高清,CCTV-14少儿高清"),
               ("CCTV-15 音乐", "CCTV-15音乐,CCTV-15高清,CCTV-15音乐高清"),
               ("CCTV-17 农业农村", "CCTV-17农业农村高清")),
        "fl": (("全部", ""), ("新闻", "新闻"), ("经济", "经济"), ("综艺", "综艺"),
               ("体育", "体育"), ("军事", "军事"), ("影视", "影视"), ("科教", "科教"),
               ("戏曲", "戏曲"), ("青少", "青少"), ("音乐", "音乐"), ("社会", "社会"),
               ("文化", "文化"), ("公益", "公益"), ("其他", "其他")),
        "jr": (("全部", ""), ("元旦", "元旦"), ("春节", "春节"), ("元宵", "元宵"),
               ("清明", "清明"), ("劳动节", "劳动节"), ("青年节", "青年节"),
               ("六一", "六一"), ("端午", "端午"), ("七夕", "七夕"),
               ("教师节", "教师节"), ("中秋", "中秋"), ("国庆", "国庆"),
               ("重阳", "重阳"), ("开学第一课", "开学第一课"), ("谜语大会", "谜语大会"),
               ("成语大会", "成语大会"), ("诗词大会", "诗词大会"), ("汉字听写大会", "汉字听写大会"),
               ("民歌大会", "民歌大会"), ("感动中国", "感动中国"), ("中华之光", "中华之光"),
               ("科技盛典", "科技盛典"), ("寻找最美孝心少年", "寻找最美孝心少年"),
               ("体坛风云人物", "体坛风云人物")),
        "nf": (("全部", ""),) + tuple((str(y), str(y)) for y in range(2025, 2007, -1)),
        "szm": _LETTERS,
    },
}

# 首页顶部第二行的频道条（央央好物是商城，不算内容频道，没收录）
# 有 svc/pid 的走接口（可翻页），没有的退回抓首页链接。
CHANNELS = (
    {"name": "地方", "url": "https://local.cctv.com/",
     "svc": "pclocal", "pid": "PAGEkfZd8kFikxgdipyjs4Hs220424",
     "plot": "地方频道：各地新闻与专题"},
    {"name": "乡村振兴", "url": "https://xczx.cctv.com/index.shtml",
     "svc": "sannong", "pid": "PAGEqz3eMqOKpJn2mYy5v1jY220610",
     "plot": "乡村振兴频道：三农资讯、振兴故事"},
    {"name": "生态", "url": "https://eco.cctv.com/",
     "svc": "pceco", "pid": "PAGEU4mqfCFM3eBBXNhQTEqv220509",
     "plot": "生态环境频道：生态保护、绿色发展"},
    {"name": "一带一路", "url": "https://ydyl.cctv.com/",
     "svc": "pcydyl", "pid": "PAGEa01cKGczgT3HHyPXEAp0211012",
     "plot": "一带一路频道：共建共享的相关报道"},
    {"name": "央博", "url": "https://yangbo.cctv.com/",
     "plot": "央博：博物馆与文物故事"},
    {"name": "文化", "url": "https://www.cctv.com/culture/index.shtml",
     "svc": "pcedu", "pid": "PAGElnxIGUtlEiKcrsOCnHtf201124",
     "plot": "文化频道：传统文化、非遗、文博"},
    {"name": "旅游", "url": "https://culture-travel.cctv.com/",
     "svc": "pctravel", "pid": "PAGExYpDmytJafSfN5on1OBe210121",
     "plot": "旅游频道：目的地、文旅"},
    {"name": "科普", "url": "https://kepu.cctv.cn/",
     "svc": "gongyi", "pid": "PAGEIMmlo1ewBPIT5t01PR6d250811",
     "plot": "科普频道：科学、自然、健康知识"},
    {"name": "健康", "url": "https://jiankang.cctv.com/",
     "svc": "pcjiankang", "pid": "PAGE1409724287058571,PAGE1417765077152627",
     "plot": "健康频道：健康资讯、名医科普"},
    {"name": "乐龄", "url": "https://laoling.cctv.com/index.shtml",
     "plot": "乐龄频道：老年生活、银发服务"},
    {"name": "阅读", "url": "https://book.cctv.com/",
     "plot": "阅读频道：好书、读书节目"},
    {"name": "艺术", "url": "https://arts.cctv.com/index.shtml",
     "plot": "艺术频道：书画、戏曲、展演"},
    {"name": "数智", "url": "https://5gai.cctv.com/",
     "plot": "数智频道：人工智能、数字化"},
    {"name": "5G", "url": "https://5g.cctv.com/",
     "plot": "5G 频道：5G 应用与新场景"},
    {"name": "产业+", "url": "https://business.cctv.com/",
     "plot": "产业+：产业观察、企业故事"},
)

# 新闻频道子导航里插件原来缺的那几个
# 「图片」没有收：photo.cctv.com 是 JS 图集，正文只有 49 字、图片列表也拿不到，
# 塞进菜单就是点了打不开，属于假入口。
NEWS_EXTRA = (
    {"name": "经济", "kind": "cms", "col": "economy"},
    {"name": "生活", "kind": "cms", "col": "life"},
    {"name": "军事", "kind": "pageid", "pid": "PAGEuW4ufGzN1PqixVNeQmoE210923"},
    # 快看是一个多小节拼起来的页面：只取其中一个 pageId 会拿到很久以前的精选，
    # 合并主列表 + 8 个分节后才是「最新」那一版（实测最新到 2026-10）。
    {"name": "快看", "kind": "pageid",
     "pid": "PAGE17fgF14A05UY45jAKwyS190524,PAGEwscuo8BgMtTD0zJpO6QO190701,"
            "PAGECApcu40XdBIEv4yVmkYN190701,PAGEagUjmHOlxrXaYH6JYVWt190701,"
            "PAGEKo8KjjL3lmW2Uli5PXxL190701,PAGEohOq6oyQO1JseD8vO4oc190701,"
            "PAGEwrFaGLGduWmBSlMO0TSB190703,PAGE34p6UU7QRV2Ekh6UvWhZ190709,"
            "PAGErAlgGidVHjZ8mdkFKEoN220614"},
)

# 新闻频道右下角的「原创」下拉
ORIGINAL = (
    {"name": "联播+", "kind": "page", "url": "https://news.cctv.com/lbj/index.shtml",
     "host": "news.cctv.com"},
    {"name": "热解读", "kind": "mix", "svc": "pcnews", "pid": "PAGEgTvX6y8GKJaJRCVeZFa5200330"},
    {"name": "天天学习", "kind": "page", "url": "https://news.cctv.com/tiantianxuexi/index.shtml",
     "host": "news.cctv.com"},
    {"name": "央视快评", "kind": "mix", "svc": "pcnews", "pid": "PAGES0fQGGS65U3FzbsI5qSj180226"},
    {"name": "锋面", "kind": "pageid",
     "pid": "PAGETRoGBAUhKLTmRSZ925Zo250716,PAGEh17Lge3pQuih0M652D9S260903"},
    {"name": "8点见", "kind": "pageid", "pid": "PAGETBnsZaPX4ZWcZBbYepTD210220"},
    {"name": "小央画话", "kind": "pageid", "pid": "PAGEG4ppiWkEM8lhGo18H60j240206"},
)

# 「更多」下拉
MORE_NEWS = (
    {"name": "人物", "kind": "pageid", "pid": "PAGEEbwoCRG76wMgJefnNvVh170118"},
    {"name": "专题", "kind": "page_all", "url": "https://news.cctv.com/special/index.shtml",
     "host": "cctv.com"},
)

# 时政（手机版全站地图里「时政」那一列）
# 央视快评、联播+、热解读、天天学习 已经在新闻的「原创」里了，这里不再重复，
# 只留「原创」没有的两项。
SHIZHENG = (
    {"name": "人民领袖习近平", "kind": "page",
     "url": "https://news.cctv.com/rmlx/index.shtml", "host": "news.cctv.com"},
    {"name": "习式妙语", "kind": "vplist", "chid": "EPGC1525679284945000",
     "mid": "18AvMrU30926"},
)

_LINK_RE = re.compile(r"<a\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
_ARTI_RE = re.compile(r"/(?:ARTI|VIDE|VIDA|PHOA)[A-Za-z0-9]+\.s?html?", re.I)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
_IMG_SRC_RE = re.compile(r'(?:data-original|data-src|src)=["\']([^"\']+)["\']', re.I)
_IMG_ALT_RE = re.compile(r'(?:alt|title)=["\']([^"\']{2,120})["\']', re.I)


def _img_of(html, last=False):
    """从一段 HTML 里取一张像样的图（跳过懒加载占位图）。"""
    tags = _IMG_TAG_RE.findall(html or "")
    if last:
        tags = list(reversed(tags))
    for tag in tags:
        m = _IMG_SRC_RE.search(tag)
        if not m:
            continue
        url = m.group(1).strip()
        if not url or "bottomplan" in url.lower() or url.startswith("data:"):
            continue
        return _norm_url(url)
    return ""


def _alt_of(html):
    for tag in _IMG_TAG_RE.findall(html or ""):
        m = _IMG_ALT_RE.search(tag)
        if m and m.group(1).strip():
            return _clean(m.group(1))
    return ""


_TITLE_TAG_RE = re.compile(
    r'<(?:p|h[1-4]|span|div|b|strong)[^>]*class="[^"]*(?:title|tit|name|text|txt)[^"]*"[^>]*>'
    r"(.*?)</(?:p|h[1-4]|span|div|b|strong)>",
    re.S | re.I,
)


def _tidy_title(text):
    """去掉从 JS 拼接字符串里带出来的 '+ ''+ ' 碎片。"""
    t = _clean(text)
    if "''" in t or "'+" in t or t.startswith("+"):
        parts = re.findall(r"[\"']([^\"']{4,80})[\"']", text or "")
        if parts:
            t = max(parts, key=len).strip()
    return t.strip()


def _title_of(inner):
    """预取锚点内部的标题：<p class="title"> > img alt > 纯文本。"""
    m = _TITLE_TAG_RE.search(inner or "")
    if m:
        title = _tidy_title(m.group(1))
        if len(title) >= 2:
            return title
    title = _alt_of(inner)
    if title:
        return title
    return _tidy_title(inner)


# ---------------------------------------------------------------- 基础
def _get_json(url, referer="https://www.cctv.com/"):
    _, text = net.get_text(url, timeout=20, referer=referer)
    return _jsonp(text)


def _kind_of(url):
    return "video" if "/VIDE" in (url or "") else "article"


def _item(title, url, thumb="", plot="", date=""):
    return {
        "kind": _kind_of(url),
        "title": title,
        "url": url,
        "thumb": thumb,
        "plot": plot or title,
        "date": date,
    }


def _norm_url(url):
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


# ---------------------------------------------------------------- 栏目列表
def mix_items(service, pid, page=1, size=MIX_PAGE_SIZE):
    """统一的栏目接口。返回 (items, total)。"""
    data = _get_json(MIX_API % (service, pid, page, size))["data"]
    total = int(data.get("total") or 0)
    items = []
    for row in data.get("list") or []:
        title = _clean(row.get("title"))
        url = _norm_url(row.get("url"))
        if not title or not url:
            continue
        items.append(_item(title, url, (row.get("image") or "").strip(),
                           _clean(row.get("brief")), _date_of(row.get("focus_date"))))
    return items, total


def pageid_items(pid, page=1, size=PAGEID_PAGE_SIZE):
    """快看 / 军事 / 人物 / 锋面 等用的 getListByPageid 接口。返回 (items, total)。"""
    data = _get_json(PAGEID_API % (pid, page, size), "https://news.cctv.com/")["data"]
    total = int(data.get("total") or 0)
    items = []
    for row in data.get("list") or []:
        title = _clean(row.get("title"))
        url = _norm_url(row.get("url"))
        if not title or not url:
            continue
        items.append(_item(title, url, (row.get("image") or "").strip(),
                           _clean(row.get("brief")), _date_of(row.get("focus_date"))))
    return items, total


def vplist_items(chid, mid, page=1, size=VPLIST_PAGE_SIZE):
    """视频栏目（如「习式妙语」）：接口的 data 直接就是一个数组。"""
    data = _get_json(VPLIST_API % (chid, mid, page, size), "https://v.cctv.com/")
    rows = data.get("data") if isinstance(data, dict) else data
    rows = rows or []
    items = []
    for row in rows:
        title = _clean(row.get("title"))
        if not title:
            continue
        guid = (row.get("guid") or "").strip()
        item = _item(title, _norm_url(row.get("url")), (row.get("image1") or "").strip(),
                     _clean(row.get("brief")))
        item["kind"] = "video"
        item["guid"] = guid
        items.append(item)
    return items, int(data.get("count") or 0) if isinstance(data, dict) else len(items)


def link_items(page_url, host="", limit=60, any_link=False):
    """兜底：直接从栏目页把链接抠出来。没有翻页，抓多少算多少。

    2.2.0：顺手把缩略图也抠出来 —— 央博/乐龄/阅读/艺术/数智/5G/产业+ 这 7 个
    频道没有 page id 接口，以前只拿到纯文字，在 Kodi 里全是一排没图的文件夹。
    央视网自己没图的条目不补占位图（保持空白）。
    """
    _, text = net.get_text(page_url, timeout=20, referer=page_url)
    items = []
    seen = set()
    for m in _LINK_RE.finditer(text):
        url = _norm_url(m.group(1))
        url = urljoin(page_url + "/", url).split("?")[0].split("#")[0]
        if not url.startswith("http"):
            continue
        if not _ARTI_RE.search(url) and not (any_link and url.endswith(".shtml")
                                             and host and host in url):
            continue
        inner = m.group(2)
        title = _title_of(inner)
        if len(title) < 4 or url in seen:
            continue
        thumb = _img_of(inner)
        if not thumb:
            # 卡片布局里图片在链接前面，取最近的那一张
            thumb = _img_of(text[max(0, m.start() - 600):m.start()], last=True)
        seen.add(url)
        items.append(_item(title, url, thumb))
        if len(items) >= limit:
            break
    if len(items) < 5:
        # 有些栏目整页都是 JS 渲染的，链接和标题藏在内联数组里（如「人民领袖习近平」）
        for it in _scan_json_items(text, host, limit):
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            items.append(it)
            if len(items) >= limit:
                break
    return items, len(items)


_JSON_OBJ_RE = re.compile(r"\{[^{}]{0,600}?\"url\"\s*:\s*\"([^\"]+)\"[^{}]{0,600}?\}", re.S)
_JSON_TITLE_RE = re.compile(r"\"(?:title|name)\"\s*:\s*\"([^\"]{4,120})\"")
_JSON_IMAGE_RE = re.compile(r"\"(?:image|img)\"\s*:\s*\"([^\"]+)\"")
_JSON_BRIEF_RE = re.compile(r"\"brief\"\s*:\s*\"([^\"]+)\"")


def _scan_json_items(text, host, limit=60):
    """从内联 JSON 数组里抠 {url,title,image,brief}。"""
    items = []
    for m in _JSON_OBJ_RE.finditer(text):
        url = _norm_url(m.group(1))
        obj = m.group(0)
        if not _ARTI_RE.search(url):
            continue
        if host and host not in url:
            continue
        tm = _JSON_TITLE_RE.search(obj)
        if not tm:
            continue
        title = _clean(tm.group(1))
        if len(title) < 4:
            continue
        im = _JSON_IMAGE_RE.search(obj)
        bm = _JSON_BRIEF_RE.search(obj)
        items.append(_item(title, url, _norm_url(im.group(1)) if im else "",
                           _clean(bm.group(1)) if bm else ""))
        if len(items) >= limit:
            break
    return items


def source_items(src, page=1):
    """按 src 的 kind 取列表。返回 (items, total)。"""
    kind = src.get("kind") or ("mix" if src.get("pid") else "page")
    if kind == "mix":
        return mix_items(src["svc"], src["pid"], page)
    if kind == "pageid":
        return pageid_items(src["pid"], page)
    if kind == "vplist":
        return vplist_items(src["chid"], src["mid"], page)
    if kind == "cms":
        from . import portal

        return portal.news_items(src["col"], page)
    return link_items(src["url"], src.get("host") or "", limit=60,
                      any_link=(kind == "page_all"))


# ---------------------------------------------------------------- 央视网剧
NETDRAMA_CHID = "EPGC1716307200000000"
NETDRAMA_API = (
    "https://media.app.cctv.com/vapi/media/%s.do?chid=" + NETDRAMA_CHID + "&p=%d&n=%d"
)
NETDRAMA_EP_API = (
    "https://media.app.cctv.com/vapi/video/dvplist.do?mid=%s&type=1&p=%d&n=%d"
)
NETDRAMA_PAGE_SIZE = 20


def netdrama_items(page=1, board="dnmlist"):
    """央视网剧（dnmlist=按最新，dbmlist=推荐榜单）。返回 (items, total)。"""
    data = _get_json(NETDRAMA_API % (board, page, NETDRAMA_PAGE_SIZE),
                     "https://v.cctv.com/")["data"]
    rows = data if isinstance(data, list) else (data.get("data") or [])
    items = []
    for row in rows:
        title = _clean(row.get("mediaName"))
        mid = (row.get("mediaId") or "").strip()
        if not title or not mid:
            continue
        items.append({
            "kind": "netdrama",
            "title": title,
            "url": "",
            "mid": mid,
            "thumb": (row.get("logoImg") or row.get("bgImg") or "").strip(),
            "plot": _clean(row.get("biref")),
            "date": "",
        })
    return items, len(items)


def netdrama_episodes(mid, page=1):
    """一部网剧的分集。dvplist 必须带 type=1，否则返回空。"""
    data = _get_json(NETDRAMA_EP_API % (mid, page, NETDRAMA_PAGE_SIZE),
                     "https://v.cctv.com/")
    rows = data.get("data") if isinstance(data, dict) else data
    rows = rows or []
    total = int(data.get("count") or 0) if isinstance(data, dict) else len(rows)
    items = []
    for row in rows:
        title = _clean(row.get("title"))
        guid = (row.get("guid") or "").strip()
        if not title or not guid:
            continue
        items.append({
            "kind": "video",
            "title": title,
            "url": "",
            "guid": guid,
            "thumb": (row.get("image1") or "").strip(),
            "plot": _clean(row.get("brief")),
            "date": "",
        })
    return items, total


# ---------------------------------------------------------------- 听音
TY_API = (
    "https://api.cntv.cn/newVideoset/getVideoAlbumListByPageIdTvty"
    "?serviceId=tvty&id=%s&p=%d&n=%d"
)
TY_PAGE_SIZE = 20
TY_SECTIONS = (
    {"name": "历史", "pid": "PAGEZEFdcHJXJL3hm8LIMyIT200828"},
    {"name": "电视剧", "pid": "PAGEucQhBjWzSNBxC93UoCAt201126"},
)


def ty_items(pid, page=1):
    """听音的专辑列表；点开是分集。返回 (items, total)。"""
    data = _get_json(TY_API % (pid, page, TY_PAGE_SIZE), "https://tv.cctv.com/")["data"]
    total = int(data.get("total") or 0)
    items = []
    for row in data.get("list") or []:
        title = _clean(row.get("title"))
        if not title:
            continue
        items.append({
            "kind": "series",
            "title": title,
            "url": _norm_url(row.get("url")),
            "album": (row.get("id") or "").strip(),
            "thumb": (row.get("image") or "").strip(),
            "plot": _clean(row.get("brief")),
            "date": "",
        })
    return items, total


# ---------------------------------------------------------------- 听音首页
# 2.2.0：听音首页（tv.cctv.com/ty/m/index.shtml）的横幅下面有「历史 / 听精品 /
# 听剧 / 名家讲堂 / 评书 / 相声·小品 …」好几段，每段十来条。以前只用接口取了
# 历史、电视剧两个专辑列表，这些内容全丢了。这里把整页解析成
# [(段名, [{"title", "url", "thumb"}])]，由界面层平铺到听音根目录。
TY_HOME_URL = "https://tv.cctv.com/ty/m/index.shtml"
_TY_TITLE_MARK = '<span class="mh_title">'
_TY_SECTION_NAME_RE = re.compile(r"<i>([^<]{1,20})</i>")
_TY_ITEM_RE = re.compile(
    r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I
)
_TY_ITEM_TITLE_RE = re.compile(r'<p class="tit[^"]*"[^>]*>(.*?)</p>', re.S | re.I)
_TY_ALBUM_RE = re.compile(r"(VIDA[0-9A-Za-z]+)")


def ty_home_sections():
    """听音首页的各个板块，返回 [(段名, items)]；每项带 url / title / thumb。"""
    _, text = net.get_text(TY_HOME_URL, timeout=25, referer="https://tv.cctv.com/")
    parts = text.split(_TY_TITLE_MARK)
    sections = []
    for part in parts[1:]:
        m = _TY_SECTION_NAME_RE.search(part)
        if not m:
            continue
        name = _clean(m.group(1))
        if not name:
            continue
        items = []
        seen = set()
        for am in _TY_ITEM_RE.finditer(part):
            url = _norm_url(am.group(1)).split("?")[0]
            topic = "/ty/topic/" in url
            if not _ARTI_RE.search(url) and not topic:
                continue
            inner = am.group(2)
            tm = _TY_ITEM_TITLE_RE.search(inner)
            title = _clean(tm.group(1)) if tm else (_clean(inner) or _alt_of(inner))
            if len(title) < 2 or url in seen:
                continue
            seen.add(url)
            thumb = _img_of(inner) or _img_of(
                part[max(0, am.start() - 600):am.start()], last=True
            )
            items.append({"title": title, "url": url, "thumb": thumb,
                          "topic": topic})
            if len(items) >= 40:
                break
        if items:
            sections.append((name, items))
    return sections


def _page_title(url):
    """专辑页 <title> 当标题用（听音的专题页里图多、字少，只能这样补）。"""
    try:
        _st, text = net.get_text(url, timeout=15, referer=TY_HOME_URL)
    except Exception:
        return ""
    m = re.search(r"<title>(.*?)</title>", text or "", re.S | re.I)
    return _clean(m.group(1)) if m else ""


def ty_topic_items(url):
    """听音专题页（ty/topic/…）下的专辑列表，返回 series 条目。"""
    _, text = net.get_text(url, timeout=25, referer=TY_HOME_URL)
    items, seen = [], set()
    for am in _TY_ITEM_RE.finditer(text):
        link = _norm_url(am.group(1)).split("?")[0]
        m = _TY_ALBUM_RE.search(link)
        if not m or m.group(1) in seen:
            continue
        inner = am.group(2)
        title = _clean(inner) or _alt_of(inner)
        if len(title) < 2:
            title = _page_title(link)
        if len(title) < 2:
            continue
        seen.add(m.group(1))
        items.append({
            "kind": "series",
            "title": title,
            "url": link,
            "album": m.group(1),
            "thumb": _img_of(inner) or _img_of(
                text[max(0, am.start() - 600):am.start()], last=True
            ),
            "plot": title,
            "date": "",
        })
        if len(items) >= 20:
            break
    return items


# ---------------------------------------------------------------- 总台春晚
TOPIC_API = "https://api.app.cctv.com/api/page/iphone/HandheldApplicationSink/7.0.0/%s"
CHUNWAN_ID = "2676"


def chunwan_boards():
    """春晚专题的板块，返回 [(板块名, items)]。"""
    data = _get_json(TOPIC_API % CHUNWAN_ID, "https://app.cctv.com/")["data"]
    boards = []
    for tpl in data.get("templates") or []:
        name = _clean(tpl.get("title")) or "板块"
        items = []
        for row in tpl.get("items") or []:
            title = _clean(row.get("title"))
            url = _norm_url(row.get("itemUrl"))
            if not title or not url:
                continue
            items.append({
                "kind": "video" if "/VIDE" in url else "article",
                "title": title,
                "url": url,
                "thumb": (row.get("img1") or row.get("img2") or "").strip(),
                "plot": _clean(row.get("brief")),
                "date": "",
            })
        if items:
            boards.append((name, items))
    return boards


# ---------------------------------------------------------------- 熊猫
IPANDA_URL = "https://www.ipanda.com/"
_IPANDA_RE = re.compile(
    r"https?://(?:www|live)\.ipanda\.com/\d{4}/\d{2}/\d{2}/(?:VIDE|ARTI)[A-Za-z0-9]+\.s?html?",
    re.I,
)


def panda_items(limit=60):
    """熊猫频道首页的视频（页面是静态的，直接抠链接）。"""
    _, text = net.get_text(IPANDA_URL, timeout=20, referer=IPANDA_URL)
    items = []
    seen = set()
    for m in _LINK_RE.finditer(text):
        url = urljoin(IPANDA_URL, _norm_url(m.group(1))).split("?")[0]
        if not _IPANDA_RE.match(url) or url in seen:
            continue
        title = _clean(m.group(2))
        if len(title) < 4:
            continue
        seen.add(url)
        items.append(_item(title, url))
        if len(items) >= limit:
            break
    return items, len(items)


# 熊猫的栏目：首页那块「栏目入口」图标，每张卡片指向一个专辑页
_PANDA_SEC_RE = re.compile(
    r'<a href="(https?://(?:www|live)\.ipanda\.com/\d{4}/\d{2}/\d{2}/(VIDA[A-Za-z0-9]+)\.shtml[^"]*)"'
    r'[^>]*>([^<]{2,12})</a>',
    re.I,
)


def panda_sections():
    """返回 [(栏目名, 专辑 id)]，比如 熊猫播报 / 熊猫大讲堂 / 大熊猫故事。

    首页里这些卡片有两种排布（图标卡 + 一排文字链），链接后面还可能带 ?spm=，
    统一按专辑 id 去重。
    """
    _, text = net.get_text(IPANDA_URL, timeout=20, referer=IPANDA_URL)
    out = []
    seen = set()
    for m in _PANDA_SEC_RE.finditer(text):
        name = _clean(m.group(3)).strip()
        album = m.group(2)
        if not name or len(name) > 10 or album in seen:
            continue
        seen.add(album)
        out.append((name, album))
    return out


def panda_section_items(per_section=10):
    """把各栏目最近的节目合成一个列表，标题前面加 [栏目名]。"""
    from . import portal

    items = []
    for name, album in panda_sections():
        try:
            eps, _total = portal.album_items(album, 1, sort="desc")
        except Exception:
            continue
        for ep in eps[:per_section]:
            it = dict(ep)
            it["title"] = "[%s] %s" % (name, ep["title"])
            items.append(it)
    return items, len(items)


# ---------------------------------------------------------------- 首页（tv.cctv.com）
TV_HOME = "https://tv.cctv.com/"


def _tv_home():
    _, text = net.get_text(TV_HOME, timeout=25, referer=TV_HOME)
    return text


def home_hotplay(limit=40):
    """首页右侧「正在热播」：《纸韵千年》《绿野“鲜”踪》《六朝》…"""
    text = _tv_home()
    i = text.find('id="hotPlay"')
    if i < 0:
        return []
    block = text[i:i + 9000]
    items = []
    for m in re.finditer(
        r'<a href="(https://tv\.cctv\.com/[^"]+)"[^>]*>\s*<img src="([^"]+)"', block
    ):
        url, thumb = m.group(1), m.group(2)
        tm = re.search(
            r'<div class="title"><a href="%s"[^>]*>([^<]+)</a>' % re.escape(url), block
        )
        title = _clean(tm.group(1)) if tm else ""
        if not title:
            continue
        items.append(_item(title, url, _norm_url(thumb)))
        if len(items) >= limit:
            break
    return items


def home_picks(limit=30):
    """首页右侧「精彩推荐」：那一排 bullet 小标题。"""
    text = _tv_home()
    items = []
    for m in re.finditer(
        r'class="bullet"><a href="([^"]+)"[^>]*>([^<]+)</a>', text
    ):
        url = _norm_url(m.group(1))
        title = _clean(m.group(2))
        if title and url.startswith("http"):
            items.append(_item(title, url))
        if len(items) >= limit:
            break
    return items


# 看点那条 tab 上的栏目（今日亚洲、国宝·发现、军事科技…）
KANDIAN_COLUMNS = (
    ("今日亚洲", "https://tv.cctv.com/lm/jryz/index.shtml"),
    ("国宝·发现", "https://tv.cctv.com/lm/gbfx/index.shtml"),
    ("军事科技", "https://tv.cctv.com/lm/jskj/index.shtml"),
    ("中国舆论场", "https://tv.cctv.com/lm/zgylc/shouye/index.shtml"),
    ("财经调查", "https://tv.cctv.com/lm/cjdc/index.shtml"),
    ("东方时空", "https://tv.cctv.com/lm/dfsk/index.shtml"),
    ("今日环球", "https://tv.cctv.com/lm/jrhq/index.shtml"),
    ("越战越勇", "https://tv.cctv.com/lm/yzyy/index.shtml"),
    ("国家记忆", "https://tv.cctv.com/lm/gjjy/index.shtml"),
)


# ---------------------------------------------------------------- 4K（分页）
FOURK_API = (
    "https://api.cntv.cn/NewVideoset/getVideoAlbumList4K"
    "?serviceId=cctv4k&cid=CHAL1558416868484111&p=%d&n=%d"
)
FOURK_PAGE_SIZE = 24


def fourk_albums(page=1):
    """4K 专区的专辑列表（带封面，可翻页）。返回 (items, total)。"""
    data = _get_json(FOURK_API % (page, FOURK_PAGE_SIZE), "https://tv.cctv.com/4K/")["data"]
    total = int(data.get("total") or 0)
    items = []
    for row in data.get("list") or []:
        title = _clean(row.get("title"))
        album = (row.get("id") or "").strip()
        if not title or not album:
            continue
        extra = []
        for key in ("ep_count", "sc"):
            value = str(row.get(key) or "").strip()
            if value and value != "0":
                extra.append(value)
        items.append({
            "kind": "series",
            "title": title,
            "url": _norm_url(row.get("image3") or ""),
            "album": album,
            "thumb": (row.get("image") or row.get("image3") or "").strip(),
            "plot": " · ".join(extra) or title,
            "date": "",
        })
    return items, total


# ---------------------------------------------------------------- 频道
def channel(name):
    for ch in CHANNELS:
        if ch["name"] == name:
            return ch
    return None


def channel_items(name, page=1):
    ch = channel(name)
    if not ch:
        return [], 0
    src = dict(ch)
    return source_items(src, page)


# ---------------------------------------------------------------- 片库
def pianku_cat(name):
    for cat in PIANKU_CATS:
        if cat["name"] == name:
            return cat
    return None


def pianku_groups(cat_name):
    """这一类的筛选组，按界面上的顺序：类别/地区/年份/字母/频道/节日。"""
    table = PIANKU_FILTERS.get(cat_name) or {}
    order = ("fl", "dq", "nf", "pd", "jr", "szm")
    return [(g, PIANKU_GROUP_NAMES.get(g, g), table[g]) for g in order if g in table]


def pianku_items(cat_name, page=1, filters=None, size=PIANKU_PAGE_SIZE):
    """片库列表。filters 形如 {"fl": "谍战", "nf": "2024"}。返回 (items, total)。"""
    cat = pianku_cat(cat_name)
    if not cat:
        return [], 0
    url = PIANKU_API % (cat["cid"], _quote(cat_name), size, page)
    for key, value in (filters or {}).items():
        if not value:
            continue
        param = PIANKU_PARAM.get(key)
        if not param:
            continue
        url += "&%s=%s" % (param, _quote(value))
    data = _get_json(url, "https://tv.cctv.com/yxg/")["data"]
    total = int(data.get("total") or 0)
    items = []
    for row in data.get("list") or []:
        title = _clean(row.get("title"))
        video_url = _norm_url(row.get("url"))
        if not title or not video_url:
            continue
        extra = []
        for key in ("year", "area", "sc"):
            value = (row.get(key) or "").strip()
            if value:
                extra.append(value)
        actors = (row.get("actors") or "").strip()
        count = (row.get("count") or "").strip()
        plot = _clean(row.get("brief"))
        if actors:
            plot = "%s\n主演：%s" % (plot, actors)
        if count:
            plot = "%s\n共 %s 集" % (plot, count)
        items.append({
            "kind": "series",
            "title": title,
            "url": video_url,
            "album": (row.get("id") or "").strip(),
            "thumb": (row.get("image") or row.get("image3") or "").strip(),
            "plot": plot or title,
            "date": "",
            "label": " · ".join(extra),
        })
    return items, total


def _quote(text):
    from urllib.parse import quote

    return quote(str(text), safe="")
