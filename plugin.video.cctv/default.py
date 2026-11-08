# -*- coding: utf-8 -*-
"""央视网 · Kodi 插件入口

URL 约定（Kodi 目录格式）：
    /                              根菜单
    /?mode=home                    首页（按官网模块分组）
    /?mode=home&sec=<n>            某个首页模块
    /?mode=news                    新闻的 9 个子栏目
    /?mode=newslist&c=news&page=1  新闻列表
    /?mode=article&u=<文章地址>     阅读报道全文
    /?mode=videos&page=1           视频库
    /?mode=sports[&grp=ch|prog]    体育（频道 / 央视名栏）
    /?mode=sportsch&c=NBA&page=1   某个体育频道的内容（视频 + 图文）
    /?mode=matches[&grp=dates|leagues]  赛事菜单（近期集锦 / 今日 / 按日期 / 按联赛）
    /?mode=matchlist&d=..&l=..     某天或某个联赛的赛程
    /?mode=match&id=<场次>          单场比赛的视频列表
    /?mode=matchinfo&id=<场次>      单场比赛的比分与技术统计
    /?mode=highlights              最近几天的全部比赛视频
    /?mode=columns&page=1          栏目大全（348 个）
    /?mode=column&u=<栏目页>        某个栏目的单期
    /?mode=album&id=<剧集号>        剧集的分集列表
    /?mode=live                    直播频道
    /?mode=search[&q=..&page=1]    搜索（结果里的剧集单独成入口）
    /?mode=play&u=<视频页>          播放点播
    /?mode=liveplay&ch=cctv1       播放直播
    /?mode=download&g=..&u=..&t=..  下载（长按视频 →「保存」，先选清晰度）
    /?mode=dltext&u=..&t=..         下载报道正文（长按文本 →「保存文本」）
"""

import re
import sys
import time
from urllib.parse import parse_qsl, quote, unquote

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

ADDON_ID = "plugin.video.cctv"

# main() 里赋值，供分页设置用（很多列表函数拿不到 addon 参数）
_ADDON = None

# 设置里的「每页最多显示」：20/40/60/80/不限（0=不限，按接口原页翻）
_PAGE_LIMITS = (20, 40, 60, 80, 0)


def _page_limit(kodi):
    """2.2.1：每页条数设置已取消，固定按接口原本的单页走（返回 0）。"""
    return 0


def _view(fetch, page, limit, native):
    """按「每页最多显示 limit 条」取一屏。fetch(api_page) -> (items, total)。

    limit 可以比接口单页小（在接口页内切片）也可以大（拼几页），
    limit=0 表示不限：按接口原本的单页条数走。
    """
    if not limit:
        return fetch(page)
    start = (page - 1) * limit
    api = start // native + 1
    offset = start % native
    need = limit
    items, total = [], 0
    while need > 0:
        part, count = fetch(api)
        if count:
            total = count
        if not part:
            break
        chunk = part[offset:] if offset else part
        take = chunk[:need]
        items.extend(take)
        need -= len(take)
        offset = 0
        api += 1
        if len(part) < native:
            break
    return items, total


def _has_next(page, limit, total, shown, native):
    """要不要加「下一页」。不过这个最大量就不加。"""
    if total:
        if not limit:
            return total > page * native
        return page * limit < total
    return bool(limit) and shown >= limit


def main():
    global _ADDON
    addon = xbmcaddon.Addon(ADDON_ID)
    _ADDON = addon
    addon_path = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    if addon_path not in sys.path:
        sys.path.insert(0, addon_path)

    from resources.lib import cctv, download as dl, kodi, pages, portal

    base = sys.argv[0]
    handle = int(sys.argv[1]) if len(sys.argv) > 1 else -1
    params = dict(parse_qsl((sys.argv[2] if len(sys.argv) > 2 else "").lstrip("?")))
    mode = params.get("mode", "root")

    kodi.log("mode=%s params=%s" % (mode, params))

    try:
        if mode == "root":
            _root(handle, base, kodi)
        elif mode == "home":
            _home(handle, base, addon, kodi, portal, pages, params)
        elif mode == "news":
            _news(handle, base, kodi, portal, pages)
        elif mode == "newslist":
            _newslist(handle, base, kodi, portal, params)
        elif mode == "newsgroup":
            _news_group(handle, base, kodi, pages, params)
        elif mode == "more":
            _root(handle, base, kodi)
        elif mode == "shizheng":
            _shizheng(handle, base, kodi, pages)
        elif mode == "kandian":
            _kandian(handle, base, kodi, pages)
        elif mode == "src":
            _src(handle, base, kodi, pages, params)
        elif mode == "channels":
            _channels(handle, base, kodi, pages)
        elif mode == "chlist":
            _chlist(handle, base, kodi, pages, params)
        elif mode == "pianku":
            _pianku(handle, base, kodi, pages, params)
        elif mode in ("pksel", "pkfilter"):
            _pksel(handle, base, kodi, pages, params)
        elif mode == "pklist":
            _pk_list(handle, base, kodi, pages, params)
        elif mode == "netdrama":
            _netdrama(handle, base, kodi, pages)
        elif mode == "netdrama_eps":
            _netdrama_eps(handle, base, kodi, pages, params)
        elif mode == "tingyin":
            _tingyin(handle, base, kodi, pages)
        elif mode == "tingyin_list":
            _tingyin_list(handle, base, kodi, pages, params)
        elif mode == "ty_search":
            _ty_search(handle, base, kodi, portal, params)
        elif mode == "ty_topic":
            _ty_topic(handle, base, kodi, pages, params)
        elif mode == "chunwan":
            _chunwan(handle, base, kodi, pages)
        elif mode == "chunwan_board":
            _chunwan_board(handle, base, kodi, pages, params)
        elif mode == "panda":
            _panda(handle, base, kodi, pages)
        elif mode == "article":
            _article(handle, kodi, cctv, portal, params)
        elif mode == "videos":
            _videos(handle, base, kodi, portal, params)
        elif mode == "sports":
            _sports(handle, base, kodi, portal, params)
        elif mode == "sportsch":
            _sports_channel(handle, base, kodi, portal, params)
        elif mode == "matches":
            _matches(handle, base, kodi, portal, params)
        elif mode == "matchlist":
            _match_list(handle, base, kodi, portal, params)
        elif mode == "match":
            _match(handle, base, kodi, portal, params)
        elif mode == "matchinfo":
            _match_info(handle, kodi, portal, params)
        elif mode == "highlights":
            _highlights(handle, base, kodi, portal)
        elif mode == "fourk":
            _fourk(handle, base, kodi, portal, pages, params)
        elif mode == "columns":
            _columns(handle, base, kodi, portal, params)
        elif mode == "column":
            _column(handle, base, kodi, portal, params)
        elif mode == "album":
            _album(handle, base, kodi, portal, params)
        elif mode == "albumx":
            _album_extra(handle, base, kodi, portal, params)
        elif mode == "search":
            _search(handle, base, kodi, cctv, portal, params)
        elif mode == "search_article":
            _search_article(handle, base, kodi, portal, params)
        elif mode == "live":
            _live(handle, base, kodi, cctv)
        elif mode == "play":
            _play(handle, addon, kodi, cctv, params)
        elif mode == "liveplay":
            _live_play(handle, addon, kodi, cctv, params)
        elif mode == "download":
            _download(handle, addon, kodi, dl, params)
        elif mode == "dltext":
            _dltext(handle, addon, kodi, portal, dl, params)
        else:
            _root(handle, base, kodi)
    except Exception as exc:  # 任何异常都不能让 Kodi 卡在转圈
        kodi.log("unhandled: %r" % exc, xbmc.LOGERROR)
        kodi.notify("出错了：%s" % exc, error=True)
        if mode in ("play", "liveplay"):
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        elif mode in ("download", "dltext"):
            pass  # 这两条是 RunPlugin 触发的，没有目录要收尾
        else:
            kodi.end_directory(handle, succeeded=False)


# ---------------------------------------------------------------- 菜单
def _root(handle, base, kodi):
    # 2.2.0 beta1：根目录只留 11 个入口；每个条目的「栏目介绍」就是栏目名本身。
    # 「首页」改成「推荐」；新闻 / 熊猫 / 总台春晚 移进「频道」。
    for label, mode in (
        ("推荐内容", "home"),
        ("新闻", "news"),
        ("视频", "videos"),
        ("频道", "channels"),
        ("体育", "sports"),
        ("4K专区", "fourk"),
        ("栏目", "columns"),
        ("片库", "pianku"),
        ("央视网剧", "netdrama"),
        ("听音", "tingyin"),
        ("直播", "live"),
        ("搜索", "search"),
    ):
        kodi.add_folder(handle, base, label, mode, plot=label)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 时政
def _shizheng(handle, base, kodi, pages):
    for i, src in enumerate(pages.SHIZHENG):
        kodi.add_folder(handle, base, src["name"], "src",
                        g="shizheng", i=i, plot="时政 · %s" % src["name"])
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 频道
# 「频道」里每个频道对应设置里的一个开关（设置 → 频道显示）
_CHANNEL_SETTING = {
    "熊猫": "ch_panda",
    "总台春晚": "ch_chunwan",
    "地方": "ch_difang",
    "乡村振兴": "ch_xiangcun",
    "生态": "ch_shengtai",
    "一带一路": "ch_yidaiyilu",
    "央博": "ch_yangbo",
    "文化": "ch_wenhua",
    "旅游": "ch_lvyou",
    "科普": "ch_kepu",
    "健康": "ch_jiankang",
    "乐龄": "ch_leling",
    "阅读": "ch_yuedu",
    "艺术": "ch_yishu",
    "数智": "ch_shuzhi",
    "5G": "ch_5g",
    "产业+": "ch_chanye",
}


def _channel_shown(kodi, name):
    key = _CHANNEL_SETTING.get(name)
    if not key:
        return True
    return kodi.setting_bool(_ADDON, key, True)


def _channels(handle, base, kodi, pages):
    """频道（2.2.1）：哪些显示哪些不显示，去「设置 → 频道显示」里调。"""
    for label, mode in (("熊猫", "panda"), ("总台春晚", "chunwan")):
        if _channel_shown(kodi, label):
            kodi.add_folder(handle, base, label, mode, plot=label)
    for ch in pages.CHANNELS:
        if not _channel_shown(kodi, ch["name"]):
            continue
        kodi.add_folder(handle, base, ch["name"], "chlist", c=ch["name"],
                        plot=ch["name"])
    kodi.end_directory(handle)


def _chlist(handle, base, kodi, pages, params):
    name = params.get("c") or ""
    page = int(params.get("page") or 1)
    if not pages.channel(name):
        kodi.end_directory(handle, succeeded=False)
        return

    limit = _page_limit(kodi)
    items, total = _view(lambda api: pages.channel_items(name, api), page, limit,
                         pages.MIX_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "这个频道没抓到内容，点这里重试", "chlist",
                        c=name, page=page)
        kodi.end_directory(handle)
        return

    _render_items(handle, base, kodi, items)
    if _has_next(page, limit, total, len(items), pages.MIX_PAGE_SIZE) \
            and (not total or not limit or page * limit < 1000):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "chlist",
                        c=name, page=page + 1, plot="%s 共约 %d 条" % (name, total))
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 通用列表
def _src(handle, base, kodi, pages, params):
    """新闻的原创/更多、时政，都是「一组固定栏目」，共用这个入口。"""
    group = params.get("g") or ""
    idx = int(params.get("i") or 0)
    table = {
        "shizheng": pages.SHIZHENG,
        "original": pages.ORIGINAL,
        "morenews": pages.MORE_NEWS,
        "newsextra": pages.NEWS_EXTRA,
    }.get(group) or []
    if idx < 0 or idx >= len(table):
        kodi.end_directory(handle, succeeded=False)
        return
    src = table[idx]
    items, _total = pages.source_items(src, 1)
    if not items:
        kodi.add_folder(handle, base, "没抓到内容，点这里重试", "src", g=group, i=idx)
        kodi.end_directory(handle)
        return
    _render_items(handle, base, kodi, items)
    kodi.end_directory(handle)


def _news_group(handle, base, kodi, pages, params):
    group = params.get("g") or ""
    table = {"original": pages.ORIGINAL, "morenews": pages.MORE_NEWS}.get(group) or []
    for i, src in enumerate(table):
        kodi.add_folder(handle, base, src["name"], "src", g=group, i=i,
                        plot="央视网·%s" % src["name"])
    kodi.end_directory(handle)


def _render_items(handle, base, kodi, items):
    """文章 → 阅读页；视频 → 播放器；剧集 → 分集列表。"""
    for it in items:
        kind = it.get("kind")
        if kind == "netdrama":
            kodi.add_folder(
                handle, base, it["title"], "netdrama_eps",
                thumb=it.get("thumb") or None,
                plot=it.get("plot") or it["title"],
                mid=it["mid"],
            )
            continue
        if kind == "series" and it.get("album"):
            kodi.add_folder(
                handle, base, it["title"], "album",
                thumb=it.get("thumb") or None,
                plot=it.get("plot") or it["title"],
                id=it["album"],
            )
            continue
        if kind in ("video", "episode"):
            kodi.add_playable(
                handle, base, it["title"], "play",
                thumb=it.get("thumb") or None,
                plot=it.get("plot") or it["title"],
                date=it.get("date") or "",
                g=it.get("guid") or "",
                u=quote(it["url"], safe="") if it.get("url") else "",
                context=kodi.download_context(
                    base, "download", "保存",
                    g=it.get("guid") or "", u=quote(it["url"], safe=""), t=it["title"],
                ),
            )
            continue
        kodi.add_folder(
            handle, base, it["title"], "article",
            thumb=it.get("thumb") or None,
            plot=it.get("plot") or it["title"],
            date=it.get("date") or "",
            u=quote(it["url"], safe=""),
            context=kodi.download_context(
                base, "dltext", "保存文本", u=quote(it["url"], safe=""), t=it["title"]
            ),
        )


# ---------------------------------------------------------------- 片库
def _pianku(handle, base, kodi, pages, params):
    """片库：先选四大类；进类之后直接就是全部内容（按最新），顶上放筛选。"""
    cat = params.get("cat") or ""
    if not cat:
        for row in pages.PIANKU_CATS:
            kodi.add_folder(handle, base, row["name"], "pianku", cat=row["name"],
                            plot="片库 · %s" % row["name"])
        kodi.end_directory(handle)
        return

    _pk_list(handle, base, kodi, pages, {"cat": cat, "page": "1"})


def _pksel(handle, base, kodi, pages, params):
    """片库筛选：先弹框选类别/地区/年份/字母，再弹框选具体值，然后出结果。"""
    cat = params.get("cat") or ""
    groups = pages.pianku_groups(cat)
    if not groups:
        kodi.end_directory(handle, succeeded=False)
        return

    labels = []
    for gkey, glabel, values in groups:
        labels.append("%s（%d）" % (glabel, max(0, len(values) - 1)) if len(values) > 1
                      else glabel)
    idx = xbmcgui.Dialog().select("筛选 · %s" % cat, labels)
    if idx < 0:
        _pk_list(handle, base, kodi, pages, {"cat": cat, "page": "1"})
        return

    gkey, glabel, values = groups[idx]
    vidx = xbmcgui.Dialog().select(glabel, [label for label, _value in values])
    if vidx < 0:
        _pk_list(handle, base, kodi, pages, {"cat": cat, "page": "1"})
        return

    value = values[vidx][1]
    kodi.log("pianku filter %s %s=%r" % (cat, gkey, value))
    _pk_list(handle, base, kodi, pages,
             {"cat": cat, "g": gkey, "v": value, "page": "1"})


def _pk_list(handle, base, kodi, pages, params):
    cat = params.get("cat") or ""
    gkey = params.get("g") or ""
    value = params.get("v") or ""
    page = int(params.get("page") or 1)
    filters = {gkey: value} if gkey and value else {}

    limit = _page_limit(kodi)
    items, total = _view(lambda api: pages.pianku_items(cat, api, filters), page, limit,
                         pages.PIANKU_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "这一类没有内容，点这里重试", "pklist",
                        cat=cat, g=gkey, v=value, page=page)
        kodi.end_directory(handle)
        return

    # 筛选永远排在第一行
    label = "筛选"
    if filters:
        label = "筛选（当前：%s）" % value
    kodi.add_folder(handle, base, label, "pksel", cat=cat,
                    plot="按类别 / 地区 / 年份 / 字母筛选%s" % cat)

    for it in items:
        row_label = it["title"]
        if it.get("label"):
            row_label = "%s（%s）" % (row_label, it["label"])
        kodi.add_folder(
            handle, base, row_label, "album",
            thumb=it.get("thumb") or None,
            plot=it.get("plot") or it["title"],
            id=it.get("album") or "",
        )

    if _has_next(page, limit, total, len(items), pages.PIANKU_PAGE_SIZE) \
            and (not total or not limit or page * limit < pages.PIANKU_MAX):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "pklist",
                        cat=cat, g=gkey, v=value, page=page + 1,
                        plot="共约 %d 部" % total)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 央视网剧
def _netdrama(handle, base, kodi, pages):
    items, _total = pages.netdrama_items(1)
    if not items:
        kodi.add_folder(handle, base, "网剧没抓到内容，点这里重试", "netdrama")
        kodi.end_directory(handle)
        return
    _render_items(handle, base, kodi, items)
    kodi.end_directory(handle)


def _netdrama_eps(handle, base, kodi, pages, params):
    mid = params.get("mid") or ""
    page = int(params.get("page") or 1)
    if not mid:
        kodi.end_directory(handle, succeeded=False)
        return
    limit = _page_limit(kodi)
    items, total = _view(lambda api: pages.netdrama_episodes(mid, api), page, limit,
                         pages.NETDRAMA_PAGE_SIZE)
    if not items:
        # 有的网剧还没上传分集（实测 20 部里有 1 部是空的），不要留假入口
        kodi.notify("这一部还没有可播放的分集")
        kodi.end_directory(handle, succeeded=False)
        return
    _render_items(handle, base, kodi, items)
    if _has_next(page, limit, total, len(items), pages.NETDRAMA_PAGE_SIZE) \
            and (not total or not limit or page * limit < 500):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "netdrama_eps",
                        mid=mid, page=page + 1, plot="共 %d 集" % total)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 听音
_TY_ALBUM_RE = re.compile(r"(VIDA[0-9A-Za-z]+)")


def _tingyin(handle, base, kodi, pages):
    # 2.2：搜索音频、历史、电视剧固定在最前面；听音首页其余板块全部平铺到
    # 根目录，用 [板块] 标注来源。「搜索音频」不再写左侧介绍。
    kodi.add_folder(handle, base, "搜索音频", "ty_search")
    for i, sec in enumerate(pages.TY_SECTIONS):
        kodi.add_folder(handle, base, sec["name"], "tingyin_list",
                        i=i, plot="听音 · %s" % sec["name"])

    try:
        sections = pages.ty_home_sections()
    except Exception as exc:
        kodi.log("tingyin home failed: %r" % exc, xbmc.LOGWARNING)
        sections = []

    for name, items in sections:
        for it in items:
            label = "[%s]%s" % (name, it["title"])
            thumb = it.get("thumb") or None
            url = it["url"]
            if it.get("topic"):
                kodi.add_folder(handle, base, label, "ty_topic",
                                u=quote(url, safe=""), thumb=thumb,
                                plot="听音 · %s / %s" % (name, it["title"]))
                continue
            album = _TY_ALBUM_RE.search(url or "")
            if album:
                kodi.add_folder(handle, base, label, "album", id=album.group(1),
                                thumb=thumb, plot="听音 · %s / %s" % (name, it["title"]))
                continue
            kodi.add_playable(
                handle, base, label, "play",
                thumb=thumb, plot="听音 · %s / %s" % (name, it["title"]),
                u=quote(url, safe=""),
                context=kodi.download_context(
                    base, "download", "保存", u=quote(url, safe=""), t=it["title"]),
            )
    kodi.end_directory(handle)


def _ty_topic(handle, base, kodi, pages, params):
    """听音专题页（听精品 / 名家讲堂 里那些图标）。"""
    url = unquote(params.get("u") or "")
    if not url:
        kodi.end_directory(handle, succeeded=False)
        return
    try:
        items = pages.ty_topic_items(url)
    except Exception as exc:
        kodi.log("ty topic failed: %r" % exc, xbmc.LOGWARNING)
        items = []
    if not items:
        kodi.notify("这个听音专题没抓到内容")
        kodi.end_directory(handle, succeeded=False)
        return
    _render_items(handle, base, kodi, items)
    kodi.end_directory(handle)


def _ty_search(handle, base, kodi, portal, params):
    """听音 → 搜索音频：用搜索接口的 audio 页签，条目直接可播、可长按下载。"""
    keyword = (params.get("q") or "").strip()
    if not keyword:
        keyword = xbmcgui.Dialog().input("搜索音频", type=xbmcgui.INPUT_ALPHANUM)
        if not keyword:
            kodi.end_directory(handle, succeeded=False)
            return

    page = int(params.get("page") or 1)
    try:
        items, total = portal.search_audio(keyword, page)
    except Exception as exc:
        kodi.log("audio search failed: %r" % exc, xbmc.LOGWARNING)
        kodi.notify("音频搜索失败：%s" % exc, error=True)
        kodi.add_folder(handle, base, "抓取失败，点这里重试", "ty_search",
                        q=keyword, page=page)
        kodi.end_directory(handle)
        return

    if not items:
        kodi.notify("没有找到「%s」的音频" % keyword, error=True)
        kodi.end_directory(handle, succeeded=False)
        return

    kodi.log("audio search %s p%d: %d 条（共约 %d）" % (keyword, page, len(items), total))
    for it in items:
        url = quote(it["url"], safe="")
        guid = it.get("guid") or ""
        plot = it.get("plot") or it["title"]
        if it.get("date"):
            plot = "%s\n\n%s" % (it["date"], plot)
        kodi.add_playable(
            handle, base, it["title"], "play",
            thumb=it.get("thumb") or None,
            plot=plot,
            date=it.get("date") or "",
            duration=it.get("duration") or "",
            g=guid,
            u=url,
            context=kodi.download_context(
                base, "download", "保存", g=guid, u=url, t=it["title"],
            ),
        )

    if total > page * 20:
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "ty_search",
                        q=keyword, page=page + 1, plot="共约 %d 条音频" % total)
    kodi.end_directory(handle)


def _tingyin_list(handle, base, kodi, pages, params):
    idx = int(params.get("i") or 0)
    page = int(params.get("page") or 1)
    if idx < 0 or idx >= len(pages.TY_SECTIONS):
        kodi.end_directory(handle, succeeded=False)
        return
    sec = pages.TY_SECTIONS[idx]
    limit = _page_limit(kodi)
    items, total = _view(lambda api: pages.ty_items(sec["pid"], api), page, limit,
                         pages.TY_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "没有内容，点这里重试", "tingyin_list", i=idx, page=page)
        kodi.end_directory(handle)
        return
    _render_items(handle, base, kodi, items)
    if _has_next(page, limit, total, len(items), pages.TY_PAGE_SIZE) \
            and (not total or not limit or page * limit < 1000):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "tingyin_list",
                        i=idx, page=page + 1, plot="共约 %d 个专辑" % total)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 总台春晚
def _chunwan(handle, base, kodi, pages):
    boards = pages.chunwan_boards()
    if not boards:
        kodi.add_folder(handle, base, "春晚专题没抓到内容，点这里重试", "chunwan")
        kodi.end_directory(handle)
        return
    for i, (name, items) in enumerate(boards):
        kodi.add_folder(handle, base, "%s（%d）" % (name, len(items)), "chunwan_board",
                        i=i, thumb=items[0].get("thumb") or None,
                        plot="总台春晚 · %s" % name)
    kodi.end_directory(handle)


def _chunwan_board(handle, base, kodi, pages, params):
    idx = int(params.get("i") or 0)
    boards = pages.chunwan_boards()
    if idx < 0 or idx >= len(boards):
        kodi.end_directory(handle, succeeded=False)
        return
    _render_items(handle, base, kodi, boards[idx][1])
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 熊猫
def _panda(handle, base, kodi, pages):
    items, _total = pages.panda_section_items()
    if not items:
        items, _total = pages.panda_items()
    if not items:
        kodi.add_folder(handle, base, "熊猫频道没抓到内容，点这里重试", "panda")
        kodi.end_directory(handle)
        return
    _render_items(handle, base, kodi, items)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 首页
def _plain_label(name):
    """去掉栏目名后面的「（13）」「（9 个栏目）」这类尾巴。"""
    return re.sub(r"[（(][^）)]*[）)]\s*$", "", (name or "").strip())


def _section_short(name):
    """首页板块名 → 方括号标签（今日热门 / 片库 …）。"""
    plain = _plain_label(name)
    for key in ("今日热门", "片库", "直播导视", "看点", "收视TOP榜", "经典影视剧场"):
        if key in plain:
            return key
    return plain


def _add_home_item(handle, base, kodi, item, label=None):
    text = label or item["title"]
    if item.get("kind") == "article":
        kodi.add_folder(
            handle, base, text, "article",
            thumb=item.get("thumb") or None,
            plot=item.get("plot") or item["title"],
            date=item.get("date") or "",
            u=quote(item["url"], safe=""),
            context=kodi.download_context(
                base, "dltext", "保存文本", u=quote(item["url"], safe=""), t=item["title"]
            ),
        )
    elif item.get("kind") == "live":
        kodi.add_playable(handle, base, text, "liveplay", ch=item["ch"],
                          thumb=item.get("thumb") or None,
                          plot=item.get("plot") or "")
    else:
        kodi.add_playable(
            handle, base, text, "play",
            thumb=item.get("thumb") or None,
            plot=item.get("plot") or item["title"],
            date=item.get("date") or "",
            u=quote(item["url"], safe=""),
            context=kodi.download_context(
                base, "download", "保存", u=quote(item["url"], safe=""), t=item["title"]
            ),
        )


def _home(handle, base, addon, kodi, portal, pages, params):
    sec = params.get("sec")

    if sec is None or sec == "":
        # 2.2：首页根目录 = 剩下的板块（顶部）+ 看点 + 正在热播/精彩推荐
        # + 「今日热门」「片库」两段的全部视频（前面加 [板块] 标注）。
        hot = pages.home_hotplay()
        picks = pages.home_picks()
        sections = portal.home_sections() or []
        limit = 80
        show_thumb = kodi.setting_bool(addon, "show_thumb", True)

        flatten = []
        for i, (name, items) in enumerate(sections):
            plain = _plain_label(name)
            if "直播导视" in plain:
                continue
            if "看点" in plain:
                continue
            if "今日热门" in plain or "片库" in plain:
                flatten.append((_section_short(name), i, items))
                continue
            kodi.add_folder(handle, base, plain, "home", sec=i,
                            thumb=(items[0].get("thumb") if items else None) or None,
                            plot="首页板块：%s" % plain)

        kodi.add_folder(handle, base, "看点", "kandian",
                        plot="看点：今日亚洲、国宝·发现、军事科技…")

        for it in hot:
            _add_home_item(handle, base, kodi, it)
        for it in picks:
            _add_home_item(handle, base, kodi, it)

        for tag, _idx, items in flatten:
            items = items[:limit]
            if show_thumb:
                try:
                    from resources.lib import cctv

                    items = cctv.fill_thumbs(items, deadline=4.0)
                except Exception as exc:
                    kodi.log("home thumbs failed: %r" % exc, xbmc.LOGWARNING)
            for item in items:
                _add_home_item(handle, base, kodi, item,
                               label="[%s]%s" % (tag, item["title"]))
        kodi.end_directory(handle)
        return

    sections = portal.home_sections()
    if not sections:
        kodi.end_directory(handle, succeeded=False)
        return

    try:
        name, items = sections[int(sec)]
    except (ValueError, IndexError):
        kodi.end_directory(handle, succeeded=False)
        return

    kodi.log("home section %s: %d 条" % (name, len(items)))

    # 设置里的「每个板块最多显示条数」，默认 80；取前 N 条，避免一个板块几百条。
    limit = 80
    if len(items) > limit:
        items = items[:limit]

    if kodi.setting_bool(addon, "show_thumb", True):
        try:
            from resources.lib import cctv

            items = cctv.fill_thumbs(items, deadline=4.0)
        except Exception as exc:
            kodi.log("home thumbs failed: %r" % exc, xbmc.LOGWARNING)

    for item in items:
        _add_home_item(handle, base, kodi, item)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 新闻
def _news(handle, base, kodi, portal, pages):
    """新闻频道那条子导航：要闻/国内/…/军事，加上时政、原创（直接铺开）、更多。

    2.2.0：「原创」不再是二级菜单，7 个栏目直接放新闻根目录；标题后的栏目数去掉；
    「快看」整栏移除（接口还是不通，不修了）。
    """
    names = (("news", "要闻"), ("china", "国内"), ("world", "国际"),
             ("economy", "经济"), ("society", "社会"), ("law", "法治"),
             ("ent", "文娱"), ("tech", "科技"),
             ("life", "生活"), ("military", "军事"))
    extra = dict((src["name"], i) for i, src in enumerate(pages.NEWS_EXTRA))

    for key, label in names:
        if key in extra:
            kodi.add_folder(handle, base, label, "src", g="newsextra", i=extra[key],
                            plot="央视网·%s" % label)
        else:
            kodi.add_folder(handle, base, label, "newslist", c=key,
                            plot="央视网·%s" % label)

    kodi.add_folder(handle, base, "时政", "shizheng",
                    plot="人民领袖习近平、习式妙语")

    for i, src in enumerate(pages.ORIGINAL):
        kodi.add_folder(handle, base, src["name"], "src", g="original", i=i,
                        plot="央视网·新闻原创·%s" % src["name"])

    # 2.2.0 beta1：「更多」不再是一级菜单，人物 / 专题直接铺在新闻下。
    for i, src in enumerate(pages.MORE_NEWS):
        kodi.add_folder(handle, base, src["name"], "src", g="morenews", i=i,
                        plot=src["name"])
    kodi.end_directory(handle)


def _newslist(handle, base, kodi, portal, params):
    col = params.get("c") or "news"
    page = int(params.get("page") or 1)
    name = dict(portal.NEWS_COLUMNS).get(col, col)

    limit = _page_limit(kodi)
    items, total = _view(lambda api: portal.news_items(col, api), page, limit,
                         portal.NEWS_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "这一页没有内容，点这里重试", "newslist", c=col, page=page)
        kodi.end_directory(handle)
        return

    for it in items:
        kodi.add_folder(
            handle, base, it["title"], "article",
            thumb=it.get("thumb") or None,
            plot=it.get("plot") or it["title"],
            date=it.get("date") or "",
            u=quote(it["url"], safe=""),
            context=kodi.download_context(
                base, "dltext", "保存文本", u=quote(it["url"], safe=""), t=it["title"]
            ),
        )

    if _has_next(page, limit, total, len(items), portal.NEWS_PAGE_SIZE) \
            and (not total or not limit or page * limit < portal.NEWS_MAX):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "newslist",
                        c=col, page=page + 1, plot="%s 共约 %d 条" % (name, total))
    kodi.end_directory(handle)


def _article(handle, kodi, cctv, portal, params):
    url = unquote(params.get("u") or "")
    if not url:
        kodi.end_directory(handle, succeeded=False)
        return

    title, body, guid = portal.article_page(url)

    # 体育频道有些「图文」页其实是视频页：正文只有一两句话，播放器才是主体。
    # 这种直接播视频，比给用户看 60 个字有用。
    # 注意这里不能用 setResolvedUrl —— 这个条目是按目录项加进去的，
    # 直接让播放器起播、再把目录收尾就行。
    if guid and len(body) < 200:
        try:
            stream, name, poster = cctv.resolve_guid(guid)
        except Exception as exc:
            kodi.log("article->video failed: %r" % exc, xbmc.LOGWARNING)
        else:
            kodi.log("article->video: %s" % (name or title))
            kodi.play_file(stream, name or title)
            kodi.end_directory(handle, succeeded=False)
            return

    if not body:
        kodi.notify("正文没抓到，可能页面结构变了", error=True)
        kodi.end_directory(handle, succeeded=False)
        return
    kodi.log("article: %s %d 字" % (title[:20], len(body)))
    kodi.text_viewer(title or "报道", body)
    kodi.end_directory(handle, succeeded=False)


# ---------------------------------------------------------------- 视频库
def _videos(handle, base, kodi, portal, params):
    page = int(params.get("page") or 1)
    limit = _page_limit(kodi)
    items, total = _view(lambda api: portal.video_items(api), page, limit,
                         portal.VIDEO_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "这一页没有内容，点这里重试", "videos", page=page)
        kodi.end_directory(handle)
        return

    for it in items:
        kodi.add_playable(
            handle, base, it["title"], "play",
            thumb=it.get("thumb") or None,
            plot=it.get("plot") or it["title"],
            date=it.get("date") or "",
            duration=it.get("duration") or "",
            g=it.get("guid") or "",
            u=quote(it["url"], safe=""),
            context=kodi.download_context(
                base, "download", "保存",
                g=it.get("guid") or "", u=quote(it["url"], safe=""), t=it["title"],
            ),
        )

    if _has_next(page, limit, total, len(items), portal.VIDEO_PAGE_SIZE):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "videos",
                        page=page + 1, plot="共约 %d 条视频" % total)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 体育
def _sports(handle, base, kodi, portal, params):
    """体育（2.2.0 beta1）：频道和央视名栏全部平铺在「体育」下面。

    频道来自 sports.cctv.com 的顶部导航（「推荐」除外，它就是首页本身），
    名栏来自首页那几个方框里带《》的电视栏目。
    之前加的「赛事」（中超/NBA/世界杯…）整块去掉：央视大多只发集锦，
    留在这里容易让人以为是整场回放。
    """
    grp = params.get("grp") or ""

    if not grp:
        # 2.2.0 beta2：央视名栏整块去掉（多数在「频道/CCTV5」里已经有）；
        # 和联赛重名的频道直接借联赛的 logo 当缩略图。
        logos = {}
        try:
            for row in portal.sports_leagues():
                if row.get("logo"):
                    logos[row["name"]] = row["logo"]
        except Exception as exc:
            kodi.log("sports league logos failed: %r" % exc, xbmc.LOGWARNING)
        for spec in portal.SPORTS_CHANNELS:
            kodi.add_folder(handle, base, spec["name"], "sportsch",
                            c=spec["name"],
                            thumb=logos.get(spec["name"]) or None,
                            plot=spec["name"])
        kodi.end_directory(handle)
        return

    if grp == "ch":
        for spec in portal.SPORTS_CHANNELS:
            kodi.add_folder(handle, base, spec["name"], "sportsch",
                            c=spec["name"],
                            plot="%s · 央视体育频道" % spec["name"])
        kodi.end_directory(handle)
        return

    for name, url in portal.SPORTS_PROGRAMS:
        kodi.add_folder(handle, base, name, "column",
                        u=quote(url, safe=""), n=name,
                        plot="%s · 央视体育栏目，点进去看各期" % name)
    kodi.end_directory(handle)


def _sports_channel(handle, base, kodi, portal, params):
    """某个体育频道：视频可直接播，图文按报道打开。"""
    name = params.get("c") or ""
    page = int(params.get("page") or 1)
    page_size = int(params.get("n") or 0)

    retry = dict(c=name, page=page)
    limit = _page_limit(kodi)
    try:
        items, total = _view(
            lambda api: portal.sports_channel_items(name, api)[:2],
            page, limit, portal.SPORTS_SEARCH_PAGE)
        source = "cms"

    except Exception as exc:
        kodi.log("sports channel %s failed: %r" % (name, exc), xbmc.LOGERROR)
        kodi.notify("%s 暂时抓不到内容：%s" % (name or "体育频道", exc), error=True)
        kodi.add_folder(handle, base, "抓取失败，点这里重试", "sportsch", **retry)
        kodi.end_directory(handle)
        return

    page_size = limit or page_size or len(items) or 1
    kodi.log("sports %s p%d: %d 条（来源 %s，共约 %d）" % (name, page, len(items), source, total))

    if not items:
        kodi.add_folder(handle, base, "这一页没有内容，点这里重试", "sportsch", **retry)
        kodi.end_directory(handle)
        return

    for it in items:
        title = it["title"]
        plot = it.get("plot") or title
        thumb = it.get("thumb") or None
        date = it.get("date") or ""
        url = quote(it["url"], safe="")
        if it["kind"] == "video":
            kodi.add_playable(
                handle, base, title, "play",
                thumb=thumb, plot=plot, date=date,
                duration=it.get("duration") or "",
                u=url,
                context=kodi.download_context(
                    base, "download", "保存", u=url, t=title,
                ),
            )
        else:
            kodi.add_folder(
                handle, base, title, "article",
                thumb=thumb, plot=plot, date=date, u=url,
                context=kodi.download_context(
                    base, "dltext", "保存文本", u=url, t=title,
                ),
            )

    if _has_next(page, limit, total, len(items), portal.SPORTS_SEARCH_PAGE):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "sportsch",
                        c=name, page=page + 1, n=page_size,
                        plot="%s 共约 %d 条" % (name, total))
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 赛事
# 数据来自 cbs.sports.cctv.com（央视体育的赛程页）。比赛视频在它的
# /game/game_detail 里，videoList 的 originalId 就是视频中心 guid，
# 播放走现成的 mode=play&g=…，长按照样能预览和下载。
def _short_title(title):
    """列表里把「[NBA]」这种前缀去掉 —— 联赛已经在标签后半段写着了。"""
    text = (title or "").strip()
    m = re.match(r"^\[[^\]]{1,12}\]\s*", text)
    return text[m.end():] if m else text


def _date_label(row):
    """2026-10-31 → 10月31日 周六（今天）"""
    date = row.get("date") or ""
    try:
        text = "%d月%d日" % (int(date[5:7]), int(date[8:10]))
    except (ValueError, IndexError):
        text = date
    if row.get("weekDay"):
        text += " " + row["weekDay"]
    if row.get("today"):
        text += "（今天）"
    return text


def _matches(handle, base, kodi, portal, params):
    """赛事菜单：2.1.4 起直接把各个赛事（联赛）铺开。

    以前这里是「近期集锦 / 今日赛程 / 按日期 / 按联赛」四层壳，用户要的
    是点进来就看到 NBA、CBA、英超… 这几个赛事本身。
    """
    try:
        leagues = portal.sports_leagues()
    except Exception as exc:
        kodi.log("matches leagues failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("赛程接口暂时打不开：%s" % exc, error=True)
        kodi.add_folder(handle, base, "点这里重试", "matches")
        kodi.end_directory(handle)
        return

    if not leagues:
        kodi.notify("没有取到赛事列表", error=True)
        kodi.end_directory(handle, succeeded=False)
        return

    kodi.log("赛事：%d 个联赛" % len(leagues))
    for row in leagues:
        label = row["name"]
        if row.get("kind"):
            label = "%s（%s）" % (label, row["kind"])
        kodi.add_folder(handle, base, label, "matchlist", l=row["id"],
                        thumb=row.get("logo") or None,
                        plot="%s · 近三周的赛程；点进去看单场比赛的赛事与集锦" % row["name"])
    kodi.end_directory(handle)


def _match_list(handle, base, kodi, portal, params):
    """某一天 / 某个联赛的赛程。每条比赛是一个文件夹（里面是视频 + 比分）。"""
    date = params.get("d") or ""
    league = params.get("l") or ""
    retry = dict(d=date, l=league)

    try:
        if date:
            games = portal.sports_games(date)
            heading = date
        else:
            today, dates = portal.sports_dates()
            games = portal.sports_games(dates[0], dates[-1], league)
            heading = ""
    except Exception as exc:
        kodi.log("matchlist failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("赛程读取失败：%s" % exc, error=True)
        kodi.add_folder(handle, base, "点这里重试", "matchlist", **retry)
        kodi.end_directory(handle)
        return

    if not games:
        text = "这段时间没有比赛" if date else "这个联赛近三周没有赛程"
        kodi.add_folder(handle, base, "%s，点这里重试" % text, "matchlist", **retry)
        kodi.end_directory(handle)
        return

    kodi.log("matchlist %s: %d 场" % (date or league, len(games)))
    for game in games:
        label = portal.game_label(game)
        if not date and game.get("_date"):
            # 联赛视图跨三周，光看「11:00」分不清是哪天
            label = "%s %s" % (game["_date"][5:], label)
        if game.get("hasVideo"):
            label += "  🎬"
        kodi.add_folder(
            handle, base, label, "match",
            id=game.get("id") or "",
            thumb=(game.get("homePicUrl") or "").strip() or None,
            plot=portal.game_plot(game),
        )
    kodi.end_directory(handle)


def _match(handle, base, kodi, portal, params):
    """单场比赛：视频列表 + 比分与技术统计。"""
    game_id = params.get("id") or ""
    try:
        items, game = portal.sports_game_items(game_id)
    except Exception as exc:
        kodi.log("match %s failed: %r" % (game_id, exc), xbmc.LOGERROR)
        kodi.notify("这场比赛读不出来：%s" % exc, error=True)
        kodi.add_folder(handle, base, "点这里重试", "match", id=game_id)
        kodi.end_directory(handle)
        return

    name = "%s %s" % (game.get("leagueName") or "", game.get("gameName") or "")
    kodi.log("match %s: %d 条视频" % (name.strip(), len(items)))

    # 技术统计：取一次，摘要直接写进标签，点开是完整文本
    try:
        stats = portal.match_stats(game)
    except Exception as exc:
        kodi.log("match stats failed: %r" % exc, xbmc.LOGWARNING)
        stats = None
    report = portal.match_stats_text(game, stats)
    digest = portal.match_stats_digest(game, stats)

    if not items:
        # 央视还没把这场比赛的视频挂到赛程上：只给技术统计，不要再摆「搜索」入口
        # （自动搜索已经在 sports_game_items 里做过了）。
        kodi.log("match %s: 没有视频，只给技术统计" % game_id)
        label = "技术统计｜%s" % digest if digest else "技术统计"
        kodi.add_folder(handle, base, label, "matchinfo", id=game_id, plot=report)
        kodi.notify("这场比赛的视频还没上线，先看技术统计")
        kodi.end_directory(handle)
        return

    label = "技术统计｜%s" % digest if digest else "技术统计"
    kodi.add_folder(handle, base, label, "matchinfo", id=game_id, plot=report)

    full = [it for it in items if it.get("full")]
    clips = [it for it in items if not it.get("full")]
    kodi.log("match %s: 赛事 %d 条，集锦 %d 条" % (game_id, len(full), len(clips)))

    # 网站对已结束的比赛会在顶部自动播一个「比赛回放」（用的就是第一段视频）。
    # 有全场录像时直接把全场标成「比赛回放」；只有集锦时补一个和站点一样的入口。
    if game.get("gameStatus") == 3 and items and not full:
        first = items[0]
        home = (game.get("homeName") or "").strip()
        guest = (game.get("guestName") or "").strip()
        home_score = str(game.get("homeScore") or "").strip()
        guest_score = str(game.get("guestScore") or "").strip()
        if home and guest:
            shown = "%s %s-%s %s" % (home, home_score or "0", guest_score or "0", guest)
        else:
            shown = game.get("gameName") or "比赛"
        replay_guid = first.get("guid") or ""
        replay_page = first.get("url") or ""
        kodi.add_playable(
            handle, base, "比赛回放｜%s" % shown, "play",
            thumb=first.get("thumb") or None,
            plot="比赛回放\n\n%s\n\n%s" % (first["title"], report),
            g=replay_guid,
            u=quote(replay_page, safe="") if replay_page else "",
            context=kodi.download_context(
                base, "download", "保存",
                g=replay_guid, u=quote(replay_page, safe="") if replay_page else "",
                t=shown,
            ),
        )

    seen_titles = set()
    for it in items:
        is_full = bool(it.get("full"))
        short = _short_title(it["title"])
        if is_full:
            # 全场录像就是站点顶部的「比赛回放」
            title = "比赛回放｜%s" % short
        else:
            title = short
        if title in seen_titles:
            # 同一场有时会挂两版全场（不同码率/剪辑），标题一样就只留一条
            continue
        seen_titles.add(title)
        plot = it["title"]
        if is_full:
            plot = "全场录像\n\n" + plot
        if it.get("note"):
            plot += "\n（%s）" % it["note"]
        plot += "\n\n" + report
        guid = it.get("guid") or ""
        page = it.get("url") or ""
        kodi.add_playable(
            handle, base, title, "play",
            thumb=it.get("thumb") or None,
            plot=plot,
            g=guid,
            u=quote(page, safe="") if page else "",
            context=kodi.download_context(
                base, "download", "保存",
                g=guid, u=quote(page, safe="") if page else "", t=it["title"],
            ),
        )

    # 没有全场也不再摆「搜索」入口：视频库的补全已经自动做过了。

    kodi.end_directory(handle)


def _match_info(handle, kodi, portal, params):
    """技术统计：Kodi 没网页视图，用阅读窗显示比分、分节、射门/控球等。"""
    game_id = params.get("id") or ""
    try:
        game = portal.sports_game(game_id)
        report = portal.match_stats_text(game)
    except Exception as exc:
        kodi.notify("技术统计读取失败：%s" % exc, error=True)
        kodi.end_directory(handle, succeeded=False)
        return
    home = (game.get("homeName") or "").strip()
    guest = (game.get("guestName") or "").strip()
    name = game.get("gameName") or ""
    if home and guest:
        name = "%s vs %s" % (home, guest)
    title = "%s %s" % (game.get("leagueName") or "赛事", name)
    kodi.log("match stats: %s %d 字" % (game_id, len(report)))
    kodi.text_viewer(title.strip() or "技术统计", report)
    kodi.end_directory(handle, succeeded=False)


def _highlights(handle, base, kodi, portal):
    """近期集锦：把最近几天所有比赛的视频合成一个列表。

    赛程接口不给视频，只能逐场拉详情（一场 0.25 秒左右），所以带进度框、
    可以取消 —— 十几场大约 1~2 秒。
    """
    progress = kodi.progress_open(
        "正在汇总近期集锦…", "最近 %d 天" % portal.CBS_HIGHLIGHT_DAYS)

    def callback(done, total):
        kodi.progress_update(progress, int(done * 100 / max(1, total)),
                             "已处理 %d/%d 场" % (done, total))
        return not kodi.progress_canceled(progress)

    try:
        items = portal.sports_highlights(progress=callback)
    except Exception as exc:
        kodi.progress_close(progress)
        kodi.log("highlights failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("汇总集锦失败：%s" % exc, error=True)
        kodi.add_folder(handle, base, "点这里重试", "highlights")
        kodi.end_directory(handle)
        return
    kodi.progress_close(progress)

    if not items:
        kodi.add_folder(handle, base, "最近几天没有比赛视频，点这里重试", "highlights")
        kodi.end_directory(handle)
        return

    kodi.log("highlights: %d 条视频" % len(items))
    for it in items:
        tags = [t for t in (it.get("league"), (it.get("date") or "")[5:]) if t]
        label = _short_title(it["title"])
        if tags:
            label = "%s · %s" % (label, " · ".join(tags))
        kodi.add_playable(
            handle, base, label, "play",
            thumb=it.get("thumb") or None,
            plot="%s\n\n%s" % (it["title"], it.get("game") or ""),
            date=it.get("date") or "",
            g=it["guid"],
            context=kodi.download_context(
                base, "download", "保存", g=it["guid"], t=it["title"],
            ),
        )
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 4K 专区
def _kandian(handle, base, kodi, pages):
    for name, url in pages.KANDIAN_COLUMNS:
        kodi.add_folder(handle, base, name, "column",
                        u=quote(url, safe=""), n=name,
                        plot="看点 · %s" % name)
    kodi.end_directory(handle)


def _fourk(handle, base, kodi, portal, pages, params):
    page = int(params.get("page") or 1)
    limit = _page_limit(kodi)
    items, total = _view(lambda api: pages.fourk_albums(api), page, limit,
                         pages.FOURK_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "4K 专区暂时没有抓到内容，点这里重试", "fourk")
        kodi.end_directory(handle)
        return
    kodi.log("4k page %d: %d/%d" % (page, len(items), total))

    _render_items(handle, base, kodi, items)

    if _has_next(page, limit, total, len(items), pages.FOURK_PAGE_SIZE) \
            and (not total or not limit or page * limit < 500):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "fourk",
                        page=page + 1, plot="4K 专区共 %d 部" % total)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 栏目
def _columns(handle, base, kodi, portal, params):
    ch = params.get("ch") or ""
    if not ch:
        # 第一层：348 个栏目按所属 CCTV 频道分组
        groups = portal.column_channels()
        for name, rows in groups:
            kodi.add_folder(handle, base, "%s（%d 个栏目）" % (name, len(rows)), "columns",
                            ch=name, thumb=rows[0].get("thumb") or None,
                            plot="CCTV 频道 %s 播出的电视栏目" % name)
        kodi.end_directory(handle)
        return

    # 第二层：该频道下的栏目
    groups = dict(portal.column_channels())
    rows = groups.get(ch) or []
    if not rows:
        kodi.add_folder(handle, base, "这个频道下没有栏目，点这里重试", "columns", ch=ch)
        kodi.end_directory(handle)
        return

    for it in rows:
        kodi.add_folder(
            handle, base, it["title"], "column",
            thumb=it.get("thumb") or None,
            plot=it.get("plot") or "%s · %s" % (it["title"], ch),
            u=quote(it["url"], safe=""),
            n=it["title"],
        )
    kodi.end_directory(handle)


def _column(handle, base, kodi, portal, params):
    url = unquote(params.get("u") or "")
    name = params.get("n") or ""
    if not url:
        kodi.end_directory(handle, succeeded=False)
        return
    items = portal.column_items(url, name)
    if not items:
        kodi.add_folder(handle, base, "这个栏目暂时没有可抓的节目，点这里重试", "column",
                        u=quote(url, safe=""), n=name)
        kodi.end_directory(handle)
        kodi.notify("栏目页没有解析到节目", error=True)
        return
    for it in items:
        kodi.add_playable(handle, base, it["title"], "play",
                          thumb=it.get("thumb") or None,
                          plot=it.get("plot") or it["title"],
                          date=it.get("date") or "",
                          duration=it.get("duration") or "",
                          u=quote(it["url"], safe=""),
                          context=kodi.download_context(
                              base, "download", "保存",
                              u=quote(it["url"], safe=""), t=it["title"],
                          ))
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 剧集
def _album(handle, base, kodi, portal, params):
    album = params.get("id") or ""
    page = int(params.get("page") or 1)
    if not album:
        kodi.end_directory(handle, succeeded=False)
        return

    # 片库的剧集默认是倒序，第一页全是「看点」，正片排到最后。先按升序把正片捞出来。
    episodes, total = portal.album_episodes(album)
    if episodes:
        _album_rows(handle, base, kodi, episodes)
        rest = total - len(episodes)
        if rest > 0:
            kodi.add_folder(handle, base, "看点·花絮（%d）" % rest, "albumx",
                            id=album, page=1,
                            plot="预告、花絮、每集剧情看点，共 %d 条" % rest)
        kodi.end_directory(handle)
        return

    limit = _page_limit(kodi)
    items, total = _view(lambda api: portal.album_items(album, api), page, limit,
                         portal.ALBUM_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "没有分集，点这里重试", "album", id=album, page=page)
        kodi.end_directory(handle)
        return

    _album_rows(handle, base, kodi, items)

    if _has_next(page, limit, total, len(items), portal.ALBUM_PAGE_SIZE) \
            and (not total or not limit or page * limit < 1000):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "album",
                        id=album, page=page + 1, plot="共约 %d 集" % total)
    kodi.end_directory(handle)


def _album_rows(handle, base, kodi, items):
    for it in items:
        kodi.add_playable(
            handle, base, it["title"], "play",
            thumb=it.get("thumb") or None,
            plot=it.get("plot") or it["title"],
            date=it.get("date") or "",
            duration=it.get("duration") or "",
            g=it.get("guid") or "",
            u=quote(it["url"], safe=""),
            context=kodi.download_context(
                base, "download", "保存",
                g=it.get("guid") or "", u=quote(it["url"], safe=""), t=it["title"],
            ),
        )


def _album_extra(handle, base, kodi, portal, params):
    """剧集里的看点/花絮单独一个文件夹。"""
    album = params.get("id") or ""
    page = int(params.get("page") or 1)
    if not album:
        kodi.end_directory(handle, succeeded=False)
        return

    limit = _page_limit(kodi)
    items, total = _view(lambda api: portal.album_extras(album, api), page, limit,
                         portal.ALBUM_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "没有看点，点这里重试", "albumx",
                        id=album, page=page)
        kodi.end_directory(handle)
        return

    _album_rows(handle, base, kodi, items)

    if _has_next(page, limit, total, len(items), portal.ALBUM_PAGE_SIZE) \
            and (not total or not limit or page * limit < 1000):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "albumx",
                        id=album, page=page + 1, plot="共约 %d 条" % total)
    kodi.end_directory(handle)



# ---------------------------------------------------------------- 搜索
def _search(handle, base, kodi, cctv, portal, params):
    keyword = (params.get("q") or "").strip()
    if not keyword:
        keyword = xbmcgui.Dialog().input("搜索央视网", type=xbmcgui.INPUT_ALPHANUM)
        if not keyword:
            kodi.end_directory(handle, succeeded=False)
            return

    page = int(params.get("page") or 1)
    try:
        items, total = portal.search_grouped(keyword, page)
    except Exception as exc:
        kodi.log("search failed: %r" % exc, xbmc.LOGWARNING)
        kodi.notify("搜索失败：%s" % exc, error=True)
        kodi.end_directory(handle, succeeded=False)
        return

    if not items:
        kodi.notify("没有找到「%s」的结果" % keyword, error=True)
        kodi.end_directory(handle, succeeded=False)
        return

    for it in items:
        if it.get("kind") == "album":
            kodi.add_folder(handle, base, it["title"], "album",
                            id=it["album"],
                            thumb=it.get("thumb") or None,
                            plot=it.get("plot") or "")
            continue
        if not it.get("playable", True):
            continue
        kodi.add_playable(
            handle, base, it["title"], "play",
            thumb=it.get("thumb") or None,
            plot=it.get("plot") or it["title"],
            date=it.get("date") or "",
            duration=it.get("duration") or "",
            u=quote(it["url"], safe=""),
            context=kodi.download_context(
                base, "download", "保存",
                u=quote(it["url"], safe=""), t=it["title"],
            ),
        )

    if total > page * 20:
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "search",
                        q=keyword, page=page + 1)
    kodi.end_directory(handle)


def _search_article(handle, base, kodi, portal, params):
    """只看文章：网页搜索的「文章」页签（2.2.0 beta1 从「选项」里进）。"""
    keyword = (params.get("q") or "").strip()
    if not keyword:
        keyword = xbmcgui.Dialog().input("搜索文章", type=xbmcgui.INPUT_ALPHANUM)
        if not keyword:
            kodi.end_directory(handle, succeeded=False)
            return

    page = int(params.get("page") or 1)
    try:
        items, total = portal.search_articles(keyword, page)
    except Exception as exc:
        kodi.log("article search failed: %r" % exc, xbmc.LOGWARNING)
        kodi.notify("搜索失败：%s" % exc, error=True)
        kodi.end_directory(handle, succeeded=False)
        return

    if not items:
        kodi.notify("没有找到「%s」的文章" % keyword, error=True)
        kodi.end_directory(handle, succeeded=False)
        return

    kodi.log("article search %r p%d: %d 条，共约 %d" % (keyword, page, len(items), total))
    for it in items:
        url = quote(it["url"], safe="")
        kodi.add_folder(
            handle, base, it["title"], "article",
            thumb=it.get("thumb") or None,
            plot=it.get("plot") or it["title"],
            date=it.get("date") or "",
            u=url,
            context=kodi.download_context(
                base, "dltext", "保存文本", u=url, t=it["title"]
            ),
        )

    if total > page * 20:
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1),
                        "search_article", q=keyword, page=page + 1)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 直播
def _live(handle, base, kodi, cctv):
    for ch, name in cctv.LIVE_CHANNELS:
        kodi.add_playable(handle, base, name, "liveplay", ch=ch)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 播放
def _start_decrypted(info, kodi):
    """起解密代理，返回 Playback；不适用就返回 None（调用方退回明文）。"""
    from resources.lib import h5eplay

    master, which = h5eplay.pick_h5e(info or {})
    if not master:
        return None
    player = h5eplay.Playback(master)
    player.start()
    # 先把第一片解好：不然 Kodi 打开播放器后要干等两三秒，容易判超时
    try:
        player._segment_bytes(0)
    except Exception as exc:
        kodi.log("首片解密失败，退回明文：%r" % exc, xbmc.LOGWARNING)
        player.stop()
        return None
    kodi.log("加密播放已就绪：%s %s" % (which, player.url))
    return player


def _hold_playback(player, kodi, api=None, monitor=None, grace=20.0):
    """代理要活到播放结束为止：插件进程一退出，本地服务就没了。

    这里只负责「陪跑」，任何异常都不许冒出去 —— 一旦冒出去，
    外层会把已经成功的 setResolvedUrl 又改写成失败，用户看到的就是「播放失败」。
    api / monitor / grace 只是给测试注入用的。
    """
    import time

    def nap(ms):
        try:
            xbmc.sleep(ms)
        except AttributeError:
            time.sleep(ms / 1000.0)

    if api is None:
        try:
            api = xbmc.Player()
        except Exception as exc:
            kodi.log("拿不到播放器，直接收摊：%r" % exc, xbmc.LOGWARNING)
            player.stop()
            return

    try:
        monitor = monitor if monitor is not None else xbmc.Monitor()

        def aborting():
            try:
                return bool(monitor.abortRequested())
            except Exception:
                return False
    except Exception:
        def aborting():
            return False

    try:
        deadline = time.time() + grace
        started = False
        while time.time() < deadline:
            try:
                if api.isPlaying():
                    started = True
                    break
            except Exception:
                break
            if aborting():
                break
            nap(300)

        if not started:
            kodi.log("播放没有起来，关掉解密代理", xbmc.LOGWARNING)
            return

        while True:
            try:
                if not api.isPlaying():
                    break
            except Exception:
                break
            if aborting():
                break
            nap(500)
    finally:
        player.stop()


def _play(handle, addon, kodi, cctv, params):
    guid = params.get("g") or ""
    page_url = unquote(params.get("u") or "")

    if not guid:
        if not page_url:
            kodi.fail(handle, "缺少视频地址")
            return
        try:
            guid = cctv.page_guid(page_url)
        except Exception as exc:
            kodi.fail(handle, "解析失败：%s" % exc)
            return

    try:
        info = cctv.video_info(guid, referer=page_url or None)
        rows = cctv.clear_ladders(info)
        if not rows:
            raise ValueError("没有找到能正常播放的清晰度")
        stream = rows[0]["url"]
        clear_height = int(rows[0].get("height") or 0)
        title = info.get("title") or ""
        poster = info.get("image") or ""
    except Exception as exc:
        kodi.fail(handle, "解析失败：%s" % exc)
        return

    player = None
    if kodi.setting_bool(addon, "h5e_play", True):
        try:
            player = _start_decrypted(info, kodi)
        except Exception as exc:
            kodi.log("加密播放没起来，走明文：%r" % exc, xbmc.LOGWARNING)
            player = None
        # 解密档要是反而更低，就没必要折腾，直接走明文
        if player is not None and player.height and player.height <= clear_height:
            kodi.log("加密档 %s 不高于明文 %sp，走明文" % (player.resolution, clear_height))
            player.stop()
            player = None

    kodi.play(handle, player.url if player else stream, title=title, thumb=poster)
    if player:
        _hold_playback(player, kodi)


def _live_play(handle, addon, kodi, cctv, params):
    channel = params.get("ch") or ""
    name = dict(cctv.LIVE_CHANNELS).get(channel, channel)
    stream = cctv.live_url(channel)
    if not stream:
        kodi.fail(handle, "%s 暂时没有可用线路（该频道可能只对部分地区开放）" % name)
        return

    player = None
    if kodi.setting_bool(addon, "h5e_play", True) and cctv.is_drm_channel(channel):
        try:
            from resources.lib import h5eplay

            # 地址可能带 Kodi 的 "|头" 后缀，代理只认裸地址
            player = h5eplay.LivePlayback(stream.split("|")[0])
            player.start()
            kodi.log("直播解密已就绪：%s %s" % (name, player.url))
        except Exception as exc:
            kodi.log("直播解密没起来，直接连源：%r" % exc, xbmc.LOGWARNING)
            if player is not None:
                try:
                    player.stop()
                except Exception:
                    pass
            player = None

    kodi.play(handle, player.url if player else stream, title=name, is_live=True)
    if player:
        _hold_playback(player, kodi, grace=30.0)


# ---------------------------------------------------------------- 下载
def _download_dir(addon, kodi, dl):
    folder = kodi.setting_str(addon, "download_path", dl.DEFAULT_DIR)
    return kodi.path_of(folder)


def _download(handle, addon, kodi, dl, params):
    """长按视频 →「保存」：先弹清晰度列表，选一档就逐片存文件。

    2.2.0：去掉预览按钮和预览时长设置；列表里优先给 H5E 解密出来的
    720P / 1080P 高清档（明文梯只有 360P），最后一行仍是下载目录。
    """
    guid = params.get("g") or ""
    page_url = unquote(params.get("u") or "")
    title = params.get("t") or ""

    progress = kodi.progress_open("正在获取清晰度…", title or "央视网视频")
    try:
        got_title, streams = dl.list_streams(guid=guid, page_url=page_url)
    except Exception as exc:
        kodi.progress_close(progress)
        kodi.log("list_streams failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("获取清晰度失败：%s" % exc, error=True)
        return
    kodi.progress_close(progress)

    title = title or got_title or "央视网视频"
    folder = _download_dir(addon, kodi, dl)
    kodi.log("download: %s，%d 档清晰度，目录 %s" % (title, len(streams), folder))

    while True:
        entries = []
        labels = []
        for stream in streams:
            size = stream.get("size") or 0
            hint = " · 约 %s" % dl.human_size(size) if size else ""
            labels.append("⬇ 下载  %s%s" % (stream["label"], hint))
            entries.append(("download", stream))

        labels.append("📁 下载目录：%s" % folder)
        entries.append(("dir", None))

        index = xbmcgui.Dialog().select("%s — 选择清晰度" % title, labels)
        if index < 0:
            return
        kind, stream = entries[index]

        if kind == "dir":
            picked = kodi.pick_folder(folder)
            if picked:
                folder = picked
                kodi.set_setting(addon, "download_path", picked)
                kodi.notify("下载目录已改为：%s" % picked)
            continue

        _save_stream(addon, kodi, dl, stream, title, folder)
        return


def _save_stream(addon, kodi, dl, stream, title, folder):
    """把一档清晰度逐片写进文件，全程带进度条，可取消。"""
    dest = dl.unique_path(folder, dl.safe_name(title), stream.get("ext") or ".ts")
    estimate = stream.get("size") or 0
    kodi.log("save %s -> %s" % (stream["label"], dest))

    progress = kodi.progress_open("下载中…", stream["label"])

    def callback(done, estimate_bytes, index, total):
        total_bytes = estimate_bytes or estimate
        if total_bytes:
            percent = min(99, int(done * 100 / total_bytes))
            detail = "%s / 约 %s" % (dl.human_size(done), dl.human_size(total_bytes))
        else:
            percent = min(99, int(index * 100 / max(1, total)))
            detail = dl.human_size(done)
        kodi.progress_update(
            progress, percent, "%s · 第 %d/%d 段" % (stream["label"], index, total), detail
        )
        return not kodi.progress_canceled(progress)

    try:
        if stream.get("h5e"):
            result = dl.download_h5e(stream["url"], dest, callback, estimate_bytes=estimate)
        else:
            result = dl.download_hls(stream["url"], dest, callback, estimate_bytes=estimate)
    except dl.Cancelled:
        kodi.progress_close(progress)
        kodi.notify("已取消下载，临时文件已删除")
        return False
    except Exception as exc:
        kodi.progress_close(progress)
        kodi.log("download failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("下载失败：%s" % exc, error=True)
        return False

    kodi.progress_close(progress)
    kodi.log("saved %s（%s）" % (result["path"], dl.human_size(result["bytes"])))
    kodi.notify("已保存：%s" % result["path"], time_ms=6000)
    if xbmcgui.Dialog().yesno(
        "下载完成", "%s\n\n%s\n\n现在播放吗？" % (dl.safe_name(title), result["path"])
    ):
        kodi.play_file(result["path"], title)
    return True


def _dltext(handle, addon, kodi, portal, dl, params):
    """长按文本 →「保存文本」：把报道正文存成 .txt。"""
    url = unquote(params.get("u") or "")
    title = params.get("t") or ""
    if not url:
        kodi.notify("缺少文章地址", error=True)
        return

    folder = _download_dir(addon, kodi, dl)
    article_title, body = portal.article_text(url)
    if not body:
        kodi.notify("正文没抓到，无法保存", error=True)
        return

    title = (title or article_title or "央视网报道").strip()
    dest = dl.unique_path(folder, dl.safe_name(title), ".txt")
    try:
        result = dl.download_article(title, body, url, dest)
    except Exception as exc:
        kodi.log("download_article failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("保存失败：%s" % exc, error=True)
        return

    kodi.log("saved %s（%s）" % (result["path"], dl.human_size(result["bytes"])))
    kodi.notify("已保存：%s" % result["path"], time_ms=6000)
    if xbmcgui.Dialog().yesno(
        "保存完成", "%s\n\n%s\n\n打开所在文件夹吗？" % (dl.safe_name(title), result["path"])
    ):
        kodi.open_folder(folder)


if __name__ == "__main__":
    main()
