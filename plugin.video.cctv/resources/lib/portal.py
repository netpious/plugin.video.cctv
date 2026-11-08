# -*- coding: utf-8 -*-
"""央视网的结构化数据源：新闻、视频库、348 个栏目、剧集(专辑)、体育。

全部基于实测可用的 JSON 接口：
  新闻     https://news.cctv.com/2019/07/gaiban/cmsdatainterface/page/<col>_<p>.jsonp
  体育     https://sports.cctv.com/2019/07/gaiban/cmsdatainterface/page/<col>_<p>.jsonp
  视频库   https://media.app.cctv.com/vapi/video/vplist.do?chid=…&p=&n=
  栏目大全 https://api.cntv.cn/lanmu/columnSearch?…&serviceId=tvcctv&t=jsonp&cb=Callback
  剧集     https://api.cntv.cn/NewVideo/getVideoListByAlbumIdNew?id=<ALBUMID>&n=20&p=&sort=desc…
"""

import html as html_mod
import json
import re
import time
from urllib.parse import quote

from . import net

# ---------------------------------------------------------------- 常量
NEWS_COLUMNS = (
    ("news", "要闻"),
    ("china", "国内"),
    ("world", "国际"),
    ("society", "社会"),
    ("law", "法治"),
    ("tech", "科技"),
    ("ent", "文娱"),
    ("edu", "教育"),
    ("health", "健康"),
)

NEWS_API = "https://news.cctv.com/2019/07/gaiban/cmsdatainterface/page/%s_%d.jsonp"
NEWS_PAGE_SIZE = 80
NEWS_MAX = 500

VIDEO_API = "https://media.app.cctv.com/vapi/video/vplist.do?chid=EPGC1525679284945000&cid=&p=%d&n=20"
VIDEO_PAGE_SIZE = 20

COLUMN_API = (
    "https://api.cntv.cn/lanmu/columnSearch"
    "?fl=&fc=&cid=&p=%d&n=40&serviceId=tvcctv&t=jsonp&cb=Callback"
)
COLUMN_PAGE_SIZE = 40

ALBUM_API = (
    "https://api.cntv.cn/NewVideo/getVideoListByAlbumIdNew"
    "?id=%s&n=20&p=%d&sort=desc&serviceId=tvcctv&t=jsonp&cb=cb"
)
ALBUM_API_SORTED = (
    "https://api.cntv.cn/NewVideo/getVideoListByAlbumIdNew"
    "?id=%s&n=20&p=%d&sort=%s&serviceId=tvcctv&t=jsonp&cb=cb"
)
ALBUM_PAGE_SIZE = 20

_TAG_RE = re.compile(r"<[^>]+>")
_VIDE_ID_RE = re.compile(r"VIDE[A-Za-z0-9]+\.s?html?")
_ALBUM_ID_RE = re.compile(r"VIDA[0-9A-Za-z]+")
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
_SCRIPT_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.S | re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
# 正文块：央视 CMS 会用 <!--repaste.body.begin/end--> 标出来；没有标记时退回容器
_BODY_AREA_RE = re.compile(
    r'<(?:div|section)[^>]+(?:class|id)="[^"]*(?:content_area|text_area|article_content)[^"]*"[^>]*>',
    re.I,
)
_NOISE_RE = re.compile(
    r"playerParas|createVodPlayer|var isHttps|^\s*(?:var|function)\s|京ICP备|版权所有|责任编辑"
    r"|扫一扫|扫二维码"
)
# 正文里的视频占位标记：[!--begin:htmlVideoCode--]<guid>,0,1,16:9,…[!--end:htmlVideoCode--]
_VIDEO_CODE_RE = re.compile(r"\[!--begin:htmlVideoCode--\].*?(?:\[!--end:htmlVideoCode--\]|$)", re.S)
# 新闻频道的正文不是直接写在 HTML 里，而是塞在一段脚本的字符串里：
#   var contentdate  = '<p …>…</p>…';
# 旧版是靠「没删 <script>、<p> 正则顺手捞到」才读出来的，等于碰运气。
_JS_BODY_RE = re.compile(r"var\s+(contentdate|content|article_content)\s*=\s*", re.I)
_JS_VIDEO_RE = re.compile(r"\[!--begin:htmlVideoCode--\]\s*([0-9a-fA-F]{32})")
# 体育 CMS 接口的条目：只有 cctv.com 上 /YYYY/MM/DD/{ARTI,VIDE}xxx.shtml 才是内容页
_SPORTS_PAGE_RE = re.compile(r"cctv\.com/\d{4}/\d{2}/\d{2}/(?:ARTI|VIDE)[A-Za-z0-9]+\.s?html?")


def _clean(raw):
    text = _TAG_RE.sub("", raw or "")
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _date_of(value):
    """接口里的时间有的是字符串，有的是毫秒时间戳。"""
    if isinstance(value, (int, float)) and value > 1e11:
        try:
            import time as _t

            return _t.strftime("%Y-%m-%d", _t.localtime(value / 1000.0))
        except (ValueError, OSError):
            return ""
    return str(value or "")[:10]


def _jsonp(text):
    """接口返回的是 jsonp（cb({...}) / news({...})），剥掉外壳。"""
    t = (text or "").strip()
    if t.startswith("{"):
        return json.loads(t)
    m = re.search(r"[A-Za-z_$][\w$]*\s*\(\s*(\{.*\}|\[.*\])\s*\)\s*;?\s*$", t, re.S)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"=\s*(\{.*\}|\[.*\])\s*;?\s*$", t, re.S)
    if m:
        return json.loads(m.group(1))
    raise ValueError("不是 JSONP：%s" % t[:60])


# ---------------------------------------------------------------- 新闻
def news_items(column, page=1):
    """返回 (items, total)。每页 80 条，最多 500 条。"""
    _, text = net.get_text(
        NEWS_API % (column, page), timeout=15, referer="https://news.cctv.com/"
    )
    data = _jsonp(text)["data"]
    total = int(data.get("total") or 0)
    items = []
    for row in data.get("list") or []:
        title = _clean(row.get("title"))
        url = (row.get("url") or "").strip()
        if not title or not url:
            continue
        items.append(
            {
                "kind": "article",
                "title": title,
                "url": url,
                "thumb": (row.get("image") or "").strip(),
                "plot": _clean(row.get("brief")),
                "date": _date_of(row.get("focus_date")),
            }
        )
    return items, total


def _clean_block(raw):
    """把一段 HTML 变成纯文本，但保留换行（正文没有 <p> 的页面要用）。"""
    text = re.sub(r"<br\s*/?>|</(?:p|div|li|h[1-6]|tr)>", "\n", raw or "", flags=re.I)
    text = html_mod.unescape(_TAG_RE.sub("", text))
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join([line for line in lines if line])


def _js_string(page, pattern):
    """从 `var xxx = '…';` 这种赋值里取出字符串（带单/双引号和转义处理）。"""
    m = pattern.search(page)
    if not m:
        return ""
    i = m.end()
    if i >= len(page) or page[i] not in "'\"":
        return ""
    quote = page[i]
    out = []
    i += 1
    while i < len(page):
        ch = page[i]
        if ch == "\\" and i + 1 < len(page):
            nxt = page[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        if ch == quote:
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _paragraphs(block):
    """从一段 HTML 里取正文段落，过滤掉播放器脚本、备案号之类。"""
    paras = []
    for raw in _P_RE.findall(block):
        line = _clean(_VIDEO_CODE_RE.sub(" ", raw))
        if len(line) > 8 and not _NOISE_RE.search(line):
            paras.append(line)
    if paras:
        return paras
    clean = _clean_block(_VIDEO_CODE_RE.sub(" ", block))
    return [line for line in clean.splitlines()
            if len(line) > 8 and not _NOISE_RE.search(line)]


def _body_best(page):
    """按模板优先级取正文段落（不是「谁长用谁」，否则会混进导航文字）：

    1. `var contentdate = '<p>…</p>'` —— 新闻频道，正文只在脚本字符串里；
    2. `<!--repaste.body.begin/end-->` —— 体育频道等新版模板；
    3. content_area / text_area 之类的容器（多个候选里挑段落最多的）；
    4. 整页兜底（旧版行为）。
    """
    js_body = _js_string(page, _JS_BODY_RE)
    if js_body:
        paras = _paragraphs(js_body)
        if paras:
            return paras

    start = page.find("repaste.body.begin")
    if start >= 0:
        end = page.find("repaste.body.end", start)
        block = page[start:end if end > start else start + 40000]
        paras = _paragraphs(block)
        if paras:
            return paras

    best = []
    for m in _BODY_AREA_RE.finditer(page):
        paras = _paragraphs(page[m.end():m.end() + 40000])
        if len("".join(paras)) > len("".join(best)):
            best = paras

    whole = _paragraphs(page)
    if len("".join(whole)) > len("".join(best)):
        return whole
    return best


def article_page(url):
    """抓一篇报道：返回 (标题, 正文, 内嵌视频 guid)。

    两种模板都要照顾：
      * 新闻频道：正文在 `var contentdate = '<p>…</p>'` 里，页面本身是空壳；
      * 体育频道：正文直接写在 <!--repaste.body.begin/end--> 之间，
        而且播放器脚本就塞在正文 <p> 里，得先删 <script> 再取段落。
    """
    _, page = net.get_text(url, timeout=20, referer="https://news.cctv.com/")

    guid = ""
    for pattern in (
        r'var\s+guid\s*=\s*"([0-9a-fA-F]{32})"',
        r'videoCenterId\s*:\s*"([0-9a-fA-F]{32})"',
        r"\[!--begin:htmlVideoCode--\]\s*([0-9a-fA-F]{32})",
    ):
        m = re.search(pattern, page)
        if m:
            guid = m.group(1)
            break

    clean = _SCRIPT_RE.sub(" ", page)
    title = ""
    m = _H1_RE.search(clean) or re.search(r"<title>(.*?)</title>", clean, re.S | re.I)
    if m:
        title = _clean(m.group(1)).split("_")[0]

    paras = _body_best(page)
    return title, "\n\n".join(paras), guid


def article_text(url):
    """抓一篇报道的正文。返回 (标题, 正文)。"""
    title, body, _guid = article_page(url)
    return title, body


# ---------------------------------------------------------------- 视频库
def video_items(page=1):
    """全站最新视频，每页 20 条。返回 (items, total)。"""
    _, text = net.get_text(VIDEO_API % page, timeout=15, referer="https://v.cctv.com/")
    data = _jsonp(text)
    total = int(data.get("count") or 0)
    items = []
    for row in data.get("data") or []:
        title = _clean(row.get("title"))
        if not title:
            continue
        url = (row.get("webUrl") or row.get("wwwUrl") or "").strip()
        items.append(
            {
                "kind": "video",
                "title": title,
                "url": url,
                "guid": (row.get("guid") or "").strip(),
                "thumb": (row.get("image1") or row.get("mediaImg") or "").strip(),
                "plot": _clean(row.get("vbrief")),
                "duration": str(row.get("vduration") or ""),
                "date": _date_of(row.get("pubTime")),
            }
        )
    return items, total


# ---------------------------------------------------------------- 栏目
def column_list(page=1):
    """348 个电视栏目，每页 40 条。返回 (items, total)。"""
    _, text = net.get_text(COLUMN_API % page, timeout=15, referer="https://tv.cctv.com/lm/")
    resp = _jsonp(text)["response"]
    total = int(resp.get("numFound") or 0)
    items = []
    for row in resp.get("docs") or []:
        name = _clean(row.get("column_name"))
        url = (row.get("column_website") or "").strip()
        if not name or not url:
            continue
        items.append(
            {
                "kind": "column",
                "title": name,
                "url": url,
                "channel": (row.get("channel_name") or "").strip(),
                "thumb": (row.get("column_logo") or "").strip(),
                "plot": _clean(row.get("column_brief")),
                "column_id": (row.get("column_id") or "").strip(),
            }
        )
    return items, total


def _column_page(page):
    return column_list(page)[0]


def _channel_key(name):
    """CCTV-1 / CCTV-2 / … / CCTV-5+ / CCTV-17农业农村 的排序键。"""
    m = re.search(r"CCTV-(\d+)(\+?)", name or "")
    if not m:
        return (99, 0, name or "")
    return (int(m.group(1)), 1 if m.group(2) else 0, name or "")


def column_channels():
    """348 个栏目按「所属 CCTV 频道」分组。

    channel_name 表示「这个栏目在哪个频道播出」，并不是直播频道 ——
    新闻联播在 CCTV-1 播出，今日说法也在 CCTV-1 播出。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    first, total = column_list(1)
    pages = max(1, (int(total) + COLUMN_PAGE_SIZE - 1) // COLUMN_PAGE_SIZE)
    rows = list(first)

    if pages > 1:
        pool = ThreadPoolExecutor(max_workers=4)
        try:
            futures = [pool.submit(_column_page, p) for p in range(2, pages + 1)]
            for future in as_completed(futures):
                try:
                    rows.extend(future.result())
                except Exception:
                    continue
        finally:
            pool.shutdown(wait=False)

    groups = {}
    for row in rows:
        groups.setdefault(row["channel"] or "其他", []).append(row)

    return [(name, groups[name]) for name in sorted(groups, key=_channel_key)]


def _parse_column_page(column_url, limit=60):
    _, page = net.get_text(column_url, timeout=20, referer="https://tv.cctv.com/lm/")
    return parse_video_links(page, limit)


def parse_video_links(page, limit=60):
    """从一页 HTML 里取出 /VIDE…shtml 的链接和标题（同地址去重）。"""
    items = []
    seen = set()
    for m in re.finditer(
        r'<a\s[^>]*href="([^"]*?/VIDE[^"]*?\.shtml)"[^>]*>(.*?)</a>', page, re.S | re.I
    ):
        url = m.group(1)
        if url in seen:
            continue
        title = _clean(m.group(2))
        if len(title) < 4:
            continue
        seen.add(url)
        items.append({"kind": "video", "title": title, "url": url, "thumb": "", "plot": title})
        if len(items) >= limit:
            break
    return items


FOUR_K_URL = "https://tv.cctv.com/4K/index.shtml"


def four_k_items(url=FOUR_K_URL, limit=60):
    """4K 专区。只有一页、没有翻页，内容是固定的十几条。

    这批片子的 hls_enc_url 里带 1920x1080/4096k 的档位，点播时会自动用最高档。
    """
    _, page = net.get_text(url, timeout=20, referer="https://tv.cctv.com/")
    return parse_video_links(page, limit)


def column_name_core(name):
    """「朗读者第一季」「故事里的中国（第三季）」→ 去掉季数，得到节目本名。

    单期标题里通常只写《朗读者》，不写季数，带季数去搜会一条都匹配不上。
    """
    core = re.sub(r"[（(]?第[一二三四五六七八九十\d]+季[）)]?", "", name or "")
    core = re.sub(r"[（(][^）)]*[）)]", "", core)
    return core.strip() or (name or "")


def column_items(column_url, name="", limit=60):
    """某个栏目的单期节目。

    约 2/3 的栏目页现在是空壳（八九十 KB 全是导航，一条视频链接都没有），
    这种情况下退回用栏目名去搜索接口查 —— 搜索能给到最近播出的各期。
    """
    items = []
    try:
        items = _parse_column_page(column_url, limit)
    except Exception:
        items = []

    if len(items) >= 3 or not name:
        return items

    from . import cctv as _cctv

    core = column_name_core(name)
    try:
        found, _ = _cctv.search(core, 1, 20)
    except Exception:
        return items

    hits = [i for i in found if core in i["title"] and i.get("playable", True)]
    hits.sort(key=lambda i: i.get("date") or "", reverse=True)
    return [
        {
            "kind": "video",
            "title": i["title"],
            "url": i["url"],
            "thumb": i.get("thumb") or "",
            "plot": i.get("plot") or i["title"],
            "date": i.get("date") or "",
            "duration": i.get("duration") or "",
        }
        for i in hits
    ]


# ---------------------------------------------------------------- 剧集(专辑)
def album_items(album_id, page=1, sort="desc"):
    """某部剧集的分集列表。返回 (items, total)。

    sort="asc" 时从第 1 集往后排 —— 片库的剧集只有升序才把正片排在前面，
    倒序第一页全是「看点」（每集的剧情摘要），这是 2.0.2 的一个 bug。
    """
    _, text = net.get_text(
        ALBUM_API_SORTED % (album_id, page, sort),
        timeout=15, referer="https://tv.cctv.com/",
    )
    data = _jsonp(text)["data"]
    total = int(data.get("total") or 0)
    items = []
    for row in data.get("list") or []:
        title = _clean(row.get("title"))
        if not title:
            continue
        items.append(
            {
                "kind": "episode",
                "title": title,
                "url": (row.get("url") or "").strip(),
                "guid": (row.get("guid") or "").strip(),
                "thumb": (row.get("image") or "").strip(),
                "plot": _clean(row.get("brief")),
                "date": _date_of(row.get("focus_date")),
                "duration": str(row.get("length") or ""),
            }
        )
    return items, total


def album_id_of(page_url):
    """从视频页里找它属于哪部剧集（页面里的 VIDA… 编号）。"""
    try:
        _, page = net.get_text(page_url, timeout=15, referer="https://tv.cctv.com/")
    except Exception:
        return ""
    m = _ALBUM_ID_RE.search(page)
    return m.group(0) if m else ""


# 「第12集」「第 3 期」「第一讲」都算正片
_EP_RE = re.compile(r"第\s*[0-9０-９一二三四五六七八九十百]+\s*[集期讲课]")


def is_episode(title):
    return bool(_EP_RE.search(title or ""))


def album_episodes(album_id, max_pages=20):
    """把剧集拆成「正片」，返回 (episodes, total)。

    片库的剧集接口默认倒序，第一页全是「看点」（每集的剧情摘要），
    正片反而排到最后 —— 这是 2.0.2 的 bug。改成升序后第 1 集在最前，
    而且正片是连续的一段，遇到没有「第 N 集」的一页就说明正片看完了。
    """
    episodes = []
    total = 0
    for page in range(1, max_pages + 1):
        items, total = album_items(album_id, page, sort="asc")
        if not items:
            break
        hit = [it for it in items if is_episode(it["title"])]
        episodes.extend(hit)
        if not hit:
            break
        if len(episodes) >= total:
            break
    return episodes, total


def album_extras(album_id, page=1):
    """看点/预告/花絮这些非正片条目（接口倒序，正好把新发的排前面）。"""
    items, total = album_items(album_id, page, sort="desc")
    return [it for it in items if not is_episode(it["title"])], total



def series_name(title):
    """从「《熊出没之探险日记2》 第44集 瞌睡熊」里取出剧名。"""
    m = re.search(r"《([^》]{2,30})》", title or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 首页
# 用户说的「电脑版主页」是 tv.cctv.com（CCTV 节目官网），不是 www.cctv.com 门户。
# 它的板块结构很规整：每个板块是一个 column_wrapper，板块名放在 textTab 或
# bangdan_top 里的 h3 中。按这些标题切段，段内的视频链接就是该板块的内容。
HOME_URL = "https://tv.cctv.com/"
HOME_FALLBACK = "https://www.cctv.com/"

_LIVE_LINK_RE = re.compile(r"https?://tv\.cctv\.com/live/(cctv[a-z0-9]+)/", re.I)


def _module_marks(text):
    """找出板块标题的位置。只认 textTab / bangdan_top 里的 h3，避免把卡片标题算进去。"""
    marks = []
    for m in re.finditer(r"<h3[^>]*>(.*?)</h3>", text, re.S | re.I):
        back = text[max(0, m.start() - 400):m.start()]
        if "textTab" in back or "bangdan_top" in back:
            name = _clean(m.group(1))
            if 1 < len(name) < 20:
                marks.append((m.start(), name))
    for m in re.finditer(r'class="bangdan_top"[^>]*>\s*([^<]{2,20})\s*<', text, re.S):
        marks.append((m.start(), _clean(m.group(1))))
    marks.sort()
    return marks


def home_sections(url=HOME_URL):
    """tv.cctv.com 首页按它自己的板块分组。

    实测板块：今日热门 / 直播导视 / 片库 / 看点 / 收视TOP榜 / 经典影视剧场。
    """
    from . import cctv as _cctv

    try:
        _, text = net.get_text(url, timeout=20)
    except Exception:
        _, text = net.get_text(HOME_FALLBACK, timeout=20)

    marks = _module_marks(text)
    if not marks:
        return []

    live_names = dict(_cctv.LIVE_CHANNELS)
    sections = []
    seen = set()

    for i, (pos, name) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        seg = text[pos:nxt]
        items = []

        for m in _cctv._VIDE_RE.finditer(seg):
            link = m.group(1)
            title = _clean(m.group(2))
            if not title or len(title) < 4 or link in seen:
                continue
            seen.add(link)
            items.append(
                {
                    "kind": "video",
                    "title": title,
                    "url": link,
                    "thumb": _cctv._thumb_near(seg, m.start(), m.end()),
                    "plot": title,
                    "date": _cctv.date_of(link),
                }
            )

        # 直播导视里没有视频链接，只有各频道直播页的入口
        for m in _LIVE_LINK_RE.finditer(seg):
            ch = m.group(1).lower()
            if ("live", ch) in seen or ch not in live_names:
                continue
            seen.add(("live", ch))
            items.append(
                {
                    "kind": "live",
                    "ch": ch,
                    "title": "%s · 直播" % live_names[ch],
                    "thumb": "",
                    "plot": "在线直播：%s" % live_names[ch],
                }
            )

        if items:
            sections.append((name, items))

    return sections


# ---------------------------------------------------------------- 体育
# sports.cctv.com 和 news.cctv.com 共用一套 CMS 接口，栏目名是拼音：
#   nba / cba / zhongchao(中超) / yingchao(英超) / dejia(德甲) / yijia(意甲)
#   wangqiu(网球) / paiqiu(排球) / zhongguozuqiu(中国足球) / guojizuqiu(国际足球)
# 顶部导航（「推荐」除外）里的另外 9 个频道没有对应栏目：它们的落地页只剩
# 全站推荐位那几条视频（每个页面都一模一样），电竞赛、亚运会、奥林匹克更是
# 纯 JS 页，一条链接都没有。这些改用搜索接口按关键词取内容。
SPORTS_URL = "https://sports.cctv.com/"
SPORTS_CMS = "https://sports.cctv.com/2019/07/gaiban/cmsdatainterface/page/%s_%d.jsonp"
SPORTS_MAX = 400          # 一个频道最多翻到这么多条，避免无限「下一页」
SPORTS_SEARCH_PAGE = 20

SPORTS_CHANNELS = (
    {"name": "奥林匹克频道", "url": "https://sports.cctv.com/olympicportal/", "keyword": "奥林匹克"},
    {"name": "NBA", "url": "https://sports.cctv.com/nba/", "column": "nba"},
    {"name": "CBA", "url": "https://sports.cctv.com/cba/", "column": "cba"},
    {"name": "中超", "url": "https://sports.cctv.com/csl/", "column": "zhongchao"},
    {"name": "英超", "url": "https://sports.cctv.com/premierleague/", "column": "yingchao"},
    {"name": "德甲", "url": "https://sports.cctv.com/bundesliga/", "column": "dejia"},
    {"name": "法甲", "url": "https://sports.cctv.com/ligue1/", "keyword": "法甲"},
    {"name": "意甲", "url": "https://sports.cctv.com/seriea/", "column": "yijia"},
    {"name": "羽毛球", "url": "https://sports.cctv.com/badminton/", "keyword": "羽毛球"},
    {"name": "乒乓球", "url": "https://sports.cctv.com/tabletennis/", "keyword": "乒乓球"},
    {"name": "电竞", "url": "https://sports.cctv.com/esports/", "keyword": "电竞"},
    {"name": "台球", "url": "https://sports.cctv.com/snooker/", "keyword": "台球"},
    {"name": "网球", "url": "https://sports.cctv.com/tennis/", "column": "wangqiu"},
    {"name": "F1", "url": "https://sports.cctv.com/racing/", "keyword": "F1"},
    {"name": "排球", "url": "https://sports.cctv.com/volleyball/", "column": "paiqiu"},
    {"name": "中国足球", "url": "https://sports.cctv.com/football/china/",
     "column": "zhongguozuqiu"},
    {"name": "国际足球", "url": "https://sports.cctv.com/football/international/",
     "column": "guojizuqiu"},
    {"name": "冬奥会", "url": "https://sports.cctv.com/Milano2026/", "keyword": "冬奥会"},
    {"name": "亚运会", "url": "https://sports.cctv.com/yayun/", "keyword": "亚运会"},
)

# 首页「央视名栏 / 节目官网」那几个方框里的节目，都是 tv.cctv.com 的栏目页，
# 直接复用 栏目 模块的解析（含空壳页退回搜索的兜底）。
SPORTS_PROGRAMS = (
    ("体育新闻", "https://tv.cctv.com/lm/tyxw/index.shtml"),
    ("体育世界", "https://tv.cctv.com/lm/tysj/index.shtml"),
    ("体坛快讯", "https://tv.cctv.com/lm/ttkx/index.shtml"),
    ("体坛晨报", "https://tv.cctv.com/lm/tycb/index.shtml"),
    ("体坛零距离", "https://tv.cctv.com/lm/ttljl/index.shtml"),
    ("运动一起赢", "https://tv.cctv.com/lm/ydyqy/index.shtml"),
    ("天下足球", "https://tv.cctv.com/lm/txzq/index.shtml"),
    ("足球之夜", "https://tv.cctv.com/lm/zqzy/index.shtml"),
    ("冠军欧洲", "https://tv.cctv.com/lm/gjoz/index.shtml"),
    ("篮球公园", "https://tv.cctv.com/lm/lqgy/index.shtml"),
    ("棋牌乐", "https://tv.cctv.com/lm/qpl/index.shtml"),
    ("逐冰追雪", "https://tv.cctv.com/lm/zbzx/index.shtml"),
    ("奥秘无穷", "https://tv.cctv.com/lm/amwq/index.shtml"),
    ("健身动起来", "https://tv.cctv.com/lm/jsdql/index.shtml"),
    ("艺术里的奥林匹克", "https://tv.cctv.com/lm/ysldalpk/index.shtml"),
)


def sports_channel(name):
    """按名字找频道定义。"""
    for spec in SPORTS_CHANNELS:
        if spec["name"] == name:
            return spec
    return None


def _sports_kind(url):
    """VIDE/VIDA 页是视频，ARTI 页是图文。"""
    return "video" if _VIDE_ID_RE.search(url or "") else "article"


def sports_channel_items(name, page=1):
    """某个体育频道的内容（视频 + 图文混排）。返回 (items, total, source)。

    source = "cms" 时是央视 CMS 的栏目列表，翻页靠 total；
    source = "search" 时是搜索兜底，total 已按 SPORTS_MAX 截断。
    """
    spec = sports_channel(name)
    if not spec:
        raise ValueError("没有这个体育频道：%s" % name)

    column = spec.get("column")
    if column:
        _, text = net.get_text(SPORTS_CMS % (column, page), timeout=20, referer=SPORTS_URL)
        data = _jsonp(text)["data"]
        total = int(data.get("total") or 0)
        items = []
        seen = set()
        for row in data.get("list") or []:
            title = _clean(row.get("title"))
            url = (row.get("url") or "").strip()
            if not title or not url or url in seen:
                continue
            seen.add(url)
            items.append(
                {
                    "kind": _sports_kind(url),
                    "title": title,
                    "url": url,
                    "thumb": (row.get("image") or "").strip(),
                    "plot": _clean(row.get("brief")) or title,
                    "date": _date_of(row.get("focus_date")),
                    "duration": "",
                }
            )
        return items, min(total, SPORTS_MAX), "cms"

    from . import cctv as _cctv

    keyword = spec.get("keyword") or name
    rows, total = _cctv.search(keyword, page, SPORTS_SEARCH_PAGE)
    items = []
    seen = set()
    for row in rows:
        url = (row.get("url") or "").strip()
        if not url or url in seen or not _SPORTS_PAGE_RE.search(url):
            continue
        seen.add(url)
        items.append(
            {
                "kind": _sports_kind(url),
                "title": row.get("title") or "",
                "url": url,
                "thumb": row.get("thumb") or "",
                "plot": row.get("plot") or row.get("title") or "",
                "date": row.get("date") or "",
                "duration": row.get("duration") or "",
            }
        )
    return items, min(total, SPORTS_MAX), "search"


# ---------------------------------------------------------------- 赛事（cbs.sports.cctv.com）
# 央视体育「赛程/竞猜」站的 PC 页是 React 单页，数据全在这几个接口：
#   /league/hot_list      热门联赛（9 个，带图标与项目类型）
#   /game/date_list       日期轴：today + 21 天（滚动窗口，没有历史赛季）
#   /game/date_game_list  startTime/endTime[&leagueId] → 按天分组的赛程
#   /game/game_detail     id=<场次id> → 单场详情，videoList 才是视频列表
# 注意两点（都是实测出来的）：
#   * 赛程接口里的 videoList 永远是空的，只有 hasVideo 标志位，视频要逐场拉详情；
#   * 接口要求带 ran=<毫秒时间戳>，否则可能拿到缓存的旧数据。
CBS_API = "https://cbs-u.sports.cctv.com/pc"
CBS_REFERER = "https://cbs.sports.cctv.com/index.html"
CBS_WINDOW = 21            # 日期轴就是 21 天
CBS_DETAIL_WORKERS = 8
CBS_HIGHLIGHT_DAYS = 3     # 「近期集锦」默认看最近 3 天
CBS_KIND = {1: "足球", 2: "篮球", 3: "排球", 4: "网球"}

# 央视赛程接口的 gameStatus：实测 1=未开始 2=进行中 3=已结束
GAME_STATUS = {1: "未开始", 2: "进行中", 3: "已结束"}


def _cbs(path, params=None, timeout=25):
    query = dict(params or {})
    query["ran"] = str(int(time.time() * 1000))
    url = CBS_API + path + "?" + "&".join("%s=%s" % (k, v) for k, v in query.items())
    _status, ctype, raw = net.fetch(
        url, timeout=timeout, referer=CBS_REFERER,
        headers={"Accept": "application/json, text/plain, */*"},
        # 2026-11-05 站方证书到期，校验失败时退到不校验（只读公开 JSON）
        allow_insecure_fallback=True,
    )
    text = net.decode(raw, ctype)
    data = json.loads(text)
    if isinstance(data, dict) and data.get("success") is False:
        raise ValueError(data.get("msg") or "赛事接口返回失败")
    return data


def _stamp(value):
    """'2026-10-31 10:00:00' → (日期, 时间)，两种都可能缺。"""
    text = (value or "").strip()
    if not text:
        return "", ""
    parts = text.split(" ", 1)
    return parts[0], (parts[1][:5] if len(parts) > 1 else "")


def game_time_label(game):
    """赛程里显示的时间：已经过去就显示日期+时间，未来的场次只显示时分。"""
    date, clock = _stamp(game.get("startTime"))
    if not date:
        return ""
    if game.get("gameStatus") == 1 or game.get("statusDesc") == "未开始":
        return clock or date
    return ("%s %s" % (date[5:], clock)).strip()


def game_label(game):
    """一句话说清是哪场比赛：带比分、状态、联赛。"""
    home = (game.get("homeName") or "").strip()
    guest = (game.get("guestName") or "").strip()
    name = (game.get("gameName") or "").strip()

    if home and guest:
        home_score = str(game.get("homeScore") or "").strip()
        guest_score = str(game.get("guestScore") or "").strip()
        if game.get("gameStatus") in (2, 3) and (home_score or guest_score):
            title = "%s %s-%s %s" % (home, home_score or "0", guest_score or "0", guest)
        else:
            title = "%s vs %s" % (home, guest)
    else:
        title = (name or "比赛").replace("vs", " vs ")

    status = game.get("statusDesc") or GAME_STATUS.get(game.get("gameStatus"), "")
    league = (game.get("leagueName") or "").strip()
    round_name = (game.get("roundType") or game.get("gameRound") or "").strip()
    clock = game_time_label(game)
    tags = [t for t in (status, league, round_name, clock) if t]
    return "%s · %s" % (title, " · ".join(tags)) if tags else title


def game_plot(game):
    """比赛详情文本：联赛/轮次/场地/半场比分/分节比分。"""
    lines = []
    league = game.get("leagueName") or ""
    season = (game.get("gameSeason") or "").strip()
    round_name = (game.get("roundType") or game.get("gameRound") or "").strip()
    head = " ".join([t for t in (league, season, round_name) if t])
    if head:
        lines.append(head)
    when = " ".join([t for t in _stamp(game.get("startTime")) if t])
    if when:
        lines.append("开赛：%s" % when)
    place = (game.get("gamePlace") or "").strip()
    if place:
        lines.append("场地：%s" % place)
    if game.get("statusDesc"):
        lines.append("状态：%s" % game["statusDesc"])
    home = (game.get("homeName") or "").strip()
    guest = (game.get("guestName") or "").strip()
    if home or guest:
        lines.append("对阵：%s vs %s" % (home, guest))
    half_home, half_guest = game.get("homeHalfScore"), game.get("guestHalfScore")
    if str(half_home or "").strip() or str(half_guest or "").strip():
        lines.append("半场：%s %s-%s %s" % (home, half_home, half_guest, guest))
    periods = game.get("scores") or {}
    if isinstance(periods, dict):
        rows = []
        for key, value in periods.items():
            if not isinstance(value, dict) or not str(key).lower().startswith("period"):
                continue          # Current 就是最终比分，上面已经有了
            number = re.sub(r"\D", "", str(key))
            if not number:
                continue
            rows.append((int(number), "第 %s 节" % int(number),
                         value.get("team1"), value.get("team2")))
        rows.sort(key=lambda r: r[0])
        if rows:
            lines.append("分节：" + " / ".join("%s %s-%s" % (name, a, b)
                                               for _n, name, a, b in rows))
    if game.get("hasVideo"):
        lines.append("本场有全场/集锦视频（片源最高 640x360）")
    return "\n".join(lines)


def game_videos(game):
    """把一场比赛的 videoList 变成 Kodi 能用的条目（guid 直接能走 vdn 接口）。"""
    items = []
    for row in game.get("videoList") or []:
        guid = (row.get("originalId") or "").strip()
        title = (row.get("title") or "").strip()
        if not guid or not title:
            continue
        items.append({
            "kind": "matchvideo",
            "guid": guid,
            "title": title,
            "thumb": (row.get("img") or "").strip(),
            "plot": title,
            "copyright": row.get("copyright"),
        })
    return items


# ---------------------------------------------------------------- 赛事：全场 vs 集锦
# 2.1.4：一场比赛里除了集锦，还有「赛事」（全场录像/回放）。央视的命名很规整：
#     赛事：[CBA]常规赛11月3日：山西汾酒VS新疆广汇能源      （约 100 分钟）
#     集锦：[CBA]常规赛11月3日：山西汾酒VS新疆广汇能源 集锦 （约 2~5 分钟）
#          [NBA]常规赛10月27日：老鹰VS火箭 比赛回顾        （约 2 分钟，也算集锦）
# 所以「标题带 VS、且没有 集锦/回顾」就是全场。
_FULL_MATCH_RE = re.compile(r"(?:VS|vs|Vs)")


def is_full_match(title):
    """只有「A VS B」这种对阵标题才算全场；「引燃全场」这类词不算。"""
    text = (title or "").strip()
    if not text:
        return False
    if "集锦" in text or "回顾" in text:
        return False
    return bool(_FULL_MATCH_RE.search(text))


def split_match_videos(items):
    """拆成（赛事/全场, 集锦），并给每条打上 full 标记。"""
    full, clips = [], []
    for item in items:
        row = dict(item)
        row["full"] = is_full_match(row.get("title"))
        (full if row["full"] else clips).append(row)
    return full, clips


def sports_date_rows():
    """日期轴，每项 {date, weekDay, today}。返回 (today, rows)。"""
    data = _cbs("/game/date_list").get("data") or {}
    today = (data.get("today") or "").strip()
    rows = []
    for row in data.get("dates") or []:
        date = (row.get("date") or "").strip()
        if not date:
            continue
        rows.append({
            "date": date,
            "weekDay": (row.get("weekDay") or "").strip(),
            "today": date == today,
        })
    return today, rows


def sports_dates():
    """日期轴（往前约 7 天、往后 13 天，共 21 天）。返回 (today, [date…])。"""
    today, rows = sports_date_rows()
    return today, [row["date"] for row in rows]


def sports_leagues():
    """联赛目录：热门联赛在前，再补上 21 天赛程里出现过的其它联赛。

    实测坑：热门联赛列表里有「世界杯」「欧冠」这种当前一场没有的，
    而「解放者杯」「西甲」有比赛却不在热门列表里 —— 两个来源都得要。

    2.1.4：赛事菜单直接展平到联赛，所以把近三周一场比赛都没有的联赛滤掉
    （世界杯、欧冠现在就是空的），免得点进去只有一个「没有赛程」。
    如果整段时间都没有比赛（休赛期），就退回热门列表，至少不留白屏。
    """
    hot_rows = []
    try:
        hot = _cbs("/league/hot_list").get("results") or []
    except Exception:
        hot = []
    for row in hot:
        name = (row.get("shortName") or row.get("fullName") or "").strip()
        league_id = str(row.get("id") or "").strip()
        if not name or not league_id:
            continue
        hot_rows.append({
            "id": league_id,
            "name": name,
            "kind": CBS_KIND.get(row.get("extType"), ""),
            "logo": (row.get("logoPath") or "").strip(),
        })

    window_rows = {}
    try:
        _today, dates = sports_dates()
        data = _cbs("/game/date_game_list", {"startTime": dates[0], "endTime": dates[-1]})
    except Exception:
        data = {}
    for day in data.get("results") or []:
        for game in day.get("list") or []:
            league_id = str(game.get("leagueId") or "").strip()
            name = (game.get("leagueName") or "").strip()
            if not name or not league_id or league_id in window_rows:
                continue
            window_rows[league_id] = {
                "id": league_id,
                "name": name,
                "kind": CBS_KIND.get(game.get("extType"), ""),
                "logo": (game.get("leagueLogo") or "").strip(),
            }

    if not window_rows:
        return hot_rows

    rows = [row for row in hot_rows if row["id"] in window_rows]
    seen = set(row["id"] for row in rows)
    for league_id, row in window_rows.items():
        if league_id not in seen:
            rows.append(row)
            seen.add(league_id)
    return rows


def _games_of(start_date, end_date, league_id=""):
    """单次赛程查询（一天或一段日期）。带 leagueId 时接口才会给全。"""
    params = {"startTime": start_date, "endTime": end_date or start_date}
    if league_id:
        params["leagueId"] = league_id
    try:
        data = _cbs("/game/date_game_list", params)
    except Exception:
        return []
    out = []
    for day in data.get("results") or []:
        for game in day.get("list") or []:
            game = dict(game)
            game["_date"] = day.get("date") or ""
            out.append(game)
    return out


def _sort_games(games):
    """进行中在最前，接着是即将开始的（近的在前），最后是已结束的（新的在前）。"""
    live = [g for g in games if g.get("gameStatus") == 2]
    soon = [g for g in games if g.get("gameStatus") == 1]
    done = [g for g in games if g.get("gameStatus") not in (1, 2)]
    live.sort(key=lambda g: g.get("startTime") or "", reverse=True)
    soon.sort(key=lambda g: g.get("startTime") or "")
    done.sort(key=lambda g: g.get("startTime") or "", reverse=True)
    return live + soon + done


def sports_games(start_date, end_date="", league_id=""):
    """某段日期的所有比赛。

    不带 leagueId 时接口只回一小撮精选场次（实测 10-31 只给 5 场，而 NBA 单独
    就有 10 场、英超 6 场），所以默认按联赛并行拉全再按 id 去重合并。
    """
    from concurrent.futures import ThreadPoolExecutor

    if league_id:
        rows = _games_of(start_date, end_date, league_id)
    else:
        ids = [row["id"] for row in sports_leagues() if row.get("id")]
        rows, seen = [], set()
        with ThreadPoolExecutor(max_workers=CBS_DETAIL_WORKERS) as pool:
            for part in pool.map(lambda lid: _games_of(start_date, end_date, lid), ids):
                for game in part:
                    key = str(game.get("id") or "")
                    if key and key in seen:
                        continue
                    seen.add(key)
                    rows.append(game)
    return _sort_games(rows)


def sports_game(game_id):
    """单场详情（含 videoList）。"""
    if not game_id:
        raise ValueError("缺少场次编号")
    data = _cbs("/game/game_detail", {"id": game_id})
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- 赛事：技术统计
# 「数据 / 分析」两个标签页是 H5 页面（cbs-h5.sports.cctv.com），它们直接打
# cbs-u 的根路径（注意没有 /pc/ 前缀）：statistics/{football|basketball}/game/…
STATS_BASE = "https://cbs-u.sports.cctv.com/"
STATS_TIMEOUT = 6

# 字段名来自 pc_game_data.js / pc_game_analysis.js 里的表格列定义
FOOTBALL_STATS = (
    ("shots", "射门", ""),
    ("shotsOnGoal", "射正", ""),
    ("shotsOffGoal", "射偏", ""),
    ("cornerKicks", "角球", ""),
    ("fouls", "犯规", ""),
    ("offsides", "越位", ""),
    ("yellowCards", "黄牌", ""),
    ("redCards", "红牌", ""),
    ("possessions", "控球率", "%"),
)
BASKETBALL_STATS = (
    ("fieldGoals", "投篮命中", ""),
    ("fieldGoalsAttempted", "投篮出手", ""),
    ("threePointGoals", "三分命中", ""),
    ("threePointAttempted", "三分出手", ""),
    ("freeThrows", "罚球命中", ""),
    ("freeThrowsAttempted", "罚球出手", ""),
    ("rebounds", "篮板", ""),
    ("assists", "助攻", ""),
    ("steals", "抢断", ""),
    ("blocks", "盖帽", ""),
    ("fouls", "犯规", ""),
)


def _stats_json(path, game_id):
    url = "%s%s?gameId=%s&ran=%d" % (STATS_BASE, path, game_id, int(time.time() * 1000))
    _status, ctype, raw = net.fetch(
        url, timeout=STATS_TIMEOUT, referer=CBS_REFERER,
        headers={"Accept": "application/json, text/plain, */*"},
        allow_insecure_fallback=True,
    )
    data = json.loads(net.decode(raw, ctype))
    if isinstance(data, dict) and data.get("success") is False:
        return {}
    return data


def match_stats(game):
    """一场比赛的技术统计。取不到就返回 ok=False，不抛异常。

    2.1.4：两个接口并行拉，各自 6 秒超时。以前是串行 + 15 秒 ——
    cbs 接口一抽风，点开一场比赛能干等半分钟。
    """
    from concurrent.futures import ThreadPoolExecutor

    game_id = str(game.get("id") or "")
    football = game.get("extType") == 1
    out = {
        "ok": False,
        "football": football,
        "rows": [],
        "quarters": [],
    }
    if not game_id:
        return out

    sport = "football" if football else "basketball"
    paths = ["statistics/%s/game/team_stats" % sport]
    if not football:
        paths.append("statistics/basketball/game/quarter_score")

    def load(path):
        try:
            return _stats_json(path, game_id)
        except Exception:
            return {}

    data = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {path: pool.submit(load, path) for path in paths}
        for path, future in futures.items():
            try:
                data[path] = future.result()
            except Exception:
                data[path] = {}

    rows = (data.get(paths[0]) or {}).get("results") or []
    if rows:
        out["rows"] = rows[:2]
        out["ok"] = True
    if not football and len(paths) > 1:
        quarters = (data.get(paths[1]) or {}).get("results") or []
        if quarters:
            out["quarters"] = quarters[:2]
    return out


def _score_line(game):
    home = (game.get("homeName") or "").strip()
    guest = (game.get("guestName") or "").strip()
    if game.get("gameStatus") in (2, 3) or str(game.get("homeScore") or "").strip():
        return "%s %s - %s %s" % (home, game.get("homeScore") or 0,
                                  game.get("guestScore") or 0, guest)
    return "%s vs %s" % (home, guest)


def match_stats_text(game, stats=None):
    """技术统计的完整文本（Kodi 阅读窗里显示的那一份）。"""
    stats = stats if stats is not None else match_stats(game)
    football = bool(stats.get("football"))
    home = (game.get("homeName") or "").strip()
    guest = (game.get("guestName") or "").strip()
    lines = []

    head = " ".join([t for t in ((game.get("leagueName") or ""),
                                 (game.get("gameSeason") or ""),
                                 (game.get("roundType") or game.get("gameRound") or "")) if t])
    if head:
        lines.append(head)
    when = " ".join([t for t in _stamp(game.get("startTime")) if t])
    if when:
        lines.append("开赛：%s" % when)
    place = (game.get("gamePlace") or "").strip()
    if place:
        lines.append("场地：%s" % place)
    if game.get("statusDesc"):
        lines.append("状态：%s" % game["statusDesc"])
    lines.append("比分：%s" % _score_line(game))

    half_home, half_guest = game.get("homeHalfScore"), game.get("guestHalfScore")
    if str(half_home or "").strip() or str(half_guest or "").strip():
        lines.append("半场：%s %s - %s %s" % (home, half_home, guest, half_guest))

    # 分节比分：篮球优先用 quarter_score 接口（字段更全），否则退回详情里的 scores
    periods = []
    for row in stats.get("quarters") or []:
        team = (row.get("teamName") or "").strip()
        values = [row.get(key) for key in ("1stScore", "2ndScore", "3rdScore", "4thScore")]
        values = [v for v in values if v is not None]
        if team and values:
            periods.append("%s %s（共 %s）" % (team, "/".join(str(v) for v in values),
                                              row.get("totalScore")))
    if periods:
        lines.append("分节：" + "；".join(periods))
    else:
        scores = game.get("scores") or {}
        if isinstance(scores, dict):
            cells = []
            for key, value in scores.items():
                if not isinstance(value, dict) or not str(key).lower().startswith("period"):
                    continue
                number = re.sub(r"\D", "", str(key))
                if number:
                    cells.append((int(number), value.get("team1"), value.get("team2")))
            cells.sort(key=lambda c: c[0])
            if cells:
                if football:
                    # 足球只有上下半场，说「第 1 节」很怪
                    names = ("上半场", "下半场", "加时 1", "加时 2")
                    parts = []
                    for index, a, b in cells:
                        title = names[index - 1] if 1 <= index <= len(names) else "第 %d 段" % index
                        parts.append("%s %s-%s" % (title, a, b))
                    lines.append("分节：" + " / ".join(parts))
                else:
                    lines.append("分节：" + " / ".join("第 %d 节 %s-%s" % c for c in cells))

    rows = stats.get("rows") or []
    fields = FOOTBALL_STATS if stats.get("football") else BASKETBALL_STATS
    if rows:
        left = rows[0] if rows else {}
        right = rows[1] if len(rows) > 1 else {}
        lines.append("")
        lines.append("技术统计（%s / %s）" % (home or "主队", guest or "客队"))
        for key, name, unit in fields:
            a, b = left.get(key), right.get(key)
            if a is None and b is None:
                continue
            lines.append("%s：%s / %s" % (
                name,
                "%s%s" % (a, unit) if a is not None else "-",
                "%s%s" % (b, unit) if b is not None else "-",
            ))
    else:
        lines.append("")
        lines.append("这场比赛还没有技术统计")

    if game.get("hasVideo"):
        lines.append("")
        lines.append("本场有集锦视频（片源最高 640x360）")
    return "\n".join(lines)


def match_stats_digest(game, stats=None, limit=3):
    """列表标签上用的一行摘要，例如「射门 15-5 · 射正 8-3 · 控球 63%-37%」。"""
    stats = stats if stats is not None else match_stats(game)
    rows = stats.get("rows") or []
    if len(rows) < 2:
        return ""
    fields = FOOTBALL_STATS if stats.get("football") else BASKETBALL_STATS
    parts = []
    for key, name, unit in fields:
        a, b = rows[0].get(key), rows[1].get(key)
        if a is None or b is None:
            continue
        parts.append("%s %s%s-%s%s" % (name, a, unit, b, unit))
        if len(parts) >= limit:
            break
    return " · ".join(parts)


def match_video_fallback(game, limit=12, days=2):
    """没有 videoList 的场次：用「主队 客队」搜索，并且**只留发布时间在比赛前后
    `days` 天内的视频**。

    为什么必须卡日期：搜索是按关键词的，同一对球队历年的比赛都会出来
    （实测 2026-10-31 的 里尔 vs 朗斯 搜到的是 2020/2024 年的比赛）。不卡日期
    就会把几年前的集锦当成这一场的，比不给更糟。
    """
    from . import cctv as _cctv

    home = (game.get("homeName") or "").strip()
    guest = (game.get("guestName") or "").strip()
    game_date = (game.get("startTime") or "")[:10]
    if not (home and guest and game_date):
        return []
    try:
        base = time.mktime(time.strptime(game_date, "%Y-%m-%d"))
    except (ValueError, OverflowError):
        return []

    try:
        rows, _total = _cctv.search("%s %s" % (home, guest), 1, 30, kind="video",
                                    timeout=8)
    except Exception:
        return []

    out = []
    for row in rows:
        title = row.get("title") or ""
        if not row.get("playable", True) or not title:
            continue
        if home not in title or guest not in title:
            continue
        published = (row.get("date") or "")[:10]
        if not published:
            continue
        try:
            published_ts = time.mktime(time.strptime(published, "%Y-%m-%d"))
        except (ValueError, OverflowError):
            continue
        if abs(published_ts - base) > days * 86400:
            continue
        url = (row.get("url") or "").strip()
        if not _SPORTS_PAGE_RE.search(url):
            continue
        out.append({
            "kind": "matchvideo",
            "guid": "",
            "url": url,
            "title": title,
            "thumb": row.get("thumb") or "",
            "plot": row.get("plot") or title,
            "copyright": None,
            "note": "赛程未关联，按标题+日期匹配",
        })
        if len(out) >= limit:
            break
    return out


def _video_seconds(row):
    try:
        return int(float(row.get("duration") or 0))
    except (TypeError, ValueError):
        return 0


def match_extra_videos(game, existing=None, days=3, limit=8):
    """videoList 没挂视频时的宽松兜底：先按「主队VS客队」，再按「联赛 + 月日」搜。

    央视体育的赛程接口只给一部分场次挂视频（很多比赛 hasVideo=0、videoList=[]），
    但同一场的视频其实可能已经发了，只是标题里不一定同时出现两队全名 ——
    例如 2026-11-03 辽宁对广州，赛程没挂视频，视频中心有一条
    「[CBA]跌宕起伏 辽宁本钢取得三连胜」。

    命中条件（宁可少补，不可错补）：
      * 两队名都在标题里：前后一天都算（电视端有时跨零点上传）；
      * 只提一支队：必须是比赛当天，并带上联赛名（[CBA]/[NBA]…），
        否则很容易串到同队另一场（实测 11-03 北京打宁波，会搜到
        11-02 天津打宁波的「天津先行者主场战胜宁波町渥」）；
      * 不是已经列在 videoList 里的那几条（按地址、标题去重）。
    """
    from . import cctv as _cctv

    home = (game.get("homeName") or "").strip()
    guest = (game.get("guestName") or "").strip()
    league = (game.get("leagueName") or "").strip()
    game_date = (game.get("startTime") or "")[:10]
    if not (home and guest and game_date):
        return []
    try:
        base = time.mktime(time.strptime(game_date, "%Y-%m-%d"))
    except (ValueError, OverflowError):
        return []

    seen_url, seen_title = set(), set()
    for item in existing or []:
        url = (item.get("url") or "").split("?")[0]
        if url:
            seen_url.add(url)
        seen_title.add((item.get("title") or "").strip())

    out = []

    def take(rows):
        for row in rows:
            if len(out) >= limit:
                return
            title = (row.get("title") or "").strip()
            url = (row.get("url") or "").strip()
            if not title or not url or not row.get("playable", True):
                continue
            both = home in title and guest in title
            if not both and home not in title and guest not in title:
                continue
            if not both and league and ("[%s]" % league) not in title:
                continue
            if title in seen_title or url.split("?")[0] in seen_url:
                continue
            published = (row.get("date") or "")[:10]
            if not published:
                continue
            if both:
                try:
                    published_ts = time.mktime(time.strptime(published, "%Y-%m-%d"))
                except (ValueError, OverflowError):
                    continue
                if abs(published_ts - base) > days * 86400:
                    continue
            elif published != game_date:
                continue
            if not _SPORTS_PAGE_RE.search(url):
                continue
            out.append({
                "kind": "matchvideo",
                "guid": "",
                "url": url,
                "title": title,
                "thumb": row.get("thumb") or "",
                "plot": row.get("plot") or title,
                "copyright": None,
                "full": is_full_match(title),
                "duration": row.get("duration") or "",
                "note": "赛程未关联，按联赛+日期+队名搜索补全",
            })
            seen_title.add(title)
            seen_url.add(url.split("?")[0])

    # 依次尝试：主队VS客队 → 联赛+轮次（央视的全场标题都是「[英超]第9轮：A VS B」）
    # → 联赛+月日。命中一条全场就停。
    round_name = ""
    for cand in (game.get("gameRound"), game.get("customRound"), game.get("roundType")):
        text = (cand or "").strip()
        if "轮" in text:
            round_name = text
            break
    queries = ["%sVS%s" % (home, guest)]
    if league and round_name:
        queries.append("%s %s" % (league, round_name))

    done_queries = set()
    for query in queries:
        if not query or query in done_queries:
            continue
        done_queries.add(query)
        try:
            rows, _total = _cctv.search(query, 1, 30, kind="video", timeout=6)
        except Exception:
            continue
        take(rows)
        if any(v["full"] for v in out):
            break

    # 全场排前面，同类型里长的在前
    out.sort(key=lambda v: (0 if v["full"] else 1, -_video_seconds(v)))
    return out


def sports_game_items(game_id):
    """一场比赛下面的列表：赛事（全场）+ 集锦。返回 (items, game)。

    赛程记录里没有 videoList 的场次（hasVideo=0）不代表央视网没发视频，
    这时用「主队VS客队」「联赛+轮次」去视频库搜索兜底（match_extra_videos 里做）；
    有 videoList 但只有集锦的，也再搜一次全场。
    """
    game = sports_game(game_id)
    videos = game_videos(game)

    full, clips = split_match_videos(videos)
    if not full or not videos:
        try:
            extra = match_extra_videos(game, videos)
        except Exception:
            extra = []
        if extra:
            videos = extra + videos
            full, clips = split_match_videos(videos)

    # 赛事在前；集锦里「比赛回顾」这种接近全场的排最前
    clips.sort(key=lambda v: 0 if "回顾" in v["title"] else 1)
    return full + clips, game


def sports_highlights(days=CBS_HIGHLIGHT_DAYS, progress=None):
    """最近几天所有比赛的全部视频，平铺成一个列表（一步到视频）。

    取的是**今天以前**（含今天）的 N 天 —— 日期轴本身是往后延伸的，
    直接切尾巴会切到未来几天（那里没有视频）。赛程接口不给视频，只能逐场
    拉详情；一场详情 0.25 秒左右，十几场并行后大约 1 秒。
    progress(done, total) 返回 False 表示用户取消。
    """
    from concurrent.futures import ThreadPoolExecutor

    today, dates = sports_dates()
    if not dates:
        return []
    past = [d for d in dates if not today or d <= today]
    window = past[-days:] if days and days <= len(past) else past
    if not window:
        return []
    start, end = window[0], window[-1]
    games = sports_games(start, end)
    todo = [g for g in games if g.get("hasVideo")]
    if not todo:
        return []

    total = len(todo)
    done = [0]

    def load(game):
        try:
            detail = sports_game(game.get("id"))
        except Exception:
            return []
        league = detail.get("leagueName") or game.get("leagueName") or ""
        vs = "%s vs %s" % (detail.get("homeName") or "", detail.get("guestName") or "")
        rows = game_videos(detail)
        for row in rows:
            row["league"] = league
            row["game"] = vs.strip()
            row["date"] = (detail.get("startTime") or "")[:10]
        return rows

    out = []
    with ThreadPoolExecutor(max_workers=CBS_DETAIL_WORKERS) as pool:
        for rows in pool.map(load, todo):
            done[0] += 1
            if progress and progress(done[0], total) is False:
                pool.shutdown(wait=False)
                return out
            out.extend(rows)

    out.sort(key=lambda v: v.get("date") or "", reverse=True)
    return out


# ---------------------------------------------------------------- 搜索
# 2.1.4：视频搜索照搬网页端的两段结构 ——
#   ① 顶部「推荐 / 其他相关视频集结果」：一叠剧集入口（熊出没之雪岭熊风…）
#   ② 「全部视频结果」：逐条视频，原样铺开
# 以前只取了移动版的视频列表，还把同一个 ALBUMID 的 20 条压成一条
# 「XX（剧集）」，所以搜「熊出没」几乎什么都看不到。
def search_grouped(keyword, page=1, page_size=20):
    """视频搜索，返回 (items, total)。第 1 页先放视频集，再放视频。"""
    from . import cctv as _cctv

    items, total = _cctv.search(keyword, page, page_size, kind="video")

    out = []
    if page <= 1:
        for vs in _cctv.search_videosets(keyword):
            out.append(
                {
                    "kind": "album",
                    "title": vs["title"],
                    "album": vs["album"],
                    "thumb": vs.get("thumb") or "",
                    "plot": "视频集：%s（点开看分集）" % vs["title"],
                    "date": "",
                }
            )

    out.extend(items)
    return out, total


def search_audio(keyword, page=1, page_size=20):
    """音频搜索（网页搜索的「音频」页签，kind=audio），给「听音」用。

    音频条目本身也是视频页（.../VIDE….shtml?guid=…），播放/下载直接复用现成链路。
    guid 必须从接口的 DETAILSID 拿：音频页面上没有 guid 变量，解析页面是拿不到的。
    """
    from . import cctv as _cctv

    rows, total = _cctv.search(keyword, page, page_size, kind="audio")
    items = []
    for it in rows:
        items.append(
            {
                "kind": "audio",
                "title": it["title"],
                "url": it["url"],
                "guid": it.get("guid") or "",
                "thumb": it.get("thumb") or "",
                "plot": it.get("plot") or it["title"],
                "date": it.get("date") or "",
                "duration": it.get("duration") or "",
            }
        )
    return items, total

def search_articles(keyword, page=1, page_size=20):
    """文章搜索（2.2.0 beta1：从「选项」里的「搜索文章」进）。

    走网页搜索的「文章」页签（手机接口 type=article），返回 ARTI/PHOA 链接；
    点开复用现成的「报道」阅读链路。
    """
    from . import cctv as _cctv

    rows, total = _cctv.search(keyword, page, page_size, kind="article")
    items = []
    for it in rows:
        if it.get("playable", True):
            continue        # 这条其实是视频，留给「搜索视频」
        items.append(
            {
                "kind": "article",
                "title": it["title"],
                "url": it["url"],
                "thumb": it.get("thumb") or "",
                "plot": it.get("plot") or it["title"],
                "date": it.get("date") or "",
            }
        )
    return items, total
