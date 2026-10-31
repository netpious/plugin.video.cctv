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
    /?mode=download&g=..&u=..&t=..  下载（长按视频 →「下载…」，先选清晰度）
    /?mode=dltext&u=..&t=..         下载报道正文（长按文本 →「下载文本…」）
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


def main():
    addon = xbmcaddon.Addon(ADDON_ID)
    addon_path = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    if addon_path not in sys.path:
        sys.path.insert(0, addon_path)

    from resources.lib import cctv, download as dl, kodi, portal

    base = sys.argv[0]
    handle = int(sys.argv[1]) if len(sys.argv) > 1 else -1
    params = dict(parse_qsl((sys.argv[2] if len(sys.argv) > 2 else "").lstrip("?")))
    mode = params.get("mode", "root")

    kodi.log("mode=%s params=%s" % (mode, params))

    try:
        if mode == "root":
            _root(handle, base, kodi)
        elif mode == "home":
            _home(handle, base, addon, kodi, portal, params)
        elif mode == "news":
            _news(handle, base, kodi, portal)
        elif mode == "newslist":
            _newslist(handle, base, kodi, portal, params)
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
            _fourk(handle, base, kodi, portal)
        elif mode == "columns":
            _columns(handle, base, kodi, portal, params)
        elif mode == "column":
            _column(handle, base, kodi, portal, params)
        elif mode == "album":
            _album(handle, base, kodi, portal, params)
        elif mode == "search":
            _search(handle, base, kodi, cctv, portal, params)
        elif mode == "live":
            _live(handle, base, kodi, cctv)
        elif mode == "play":
            _play(handle, kodi, cctv, params)
        elif mode == "liveplay":
            _live_play(handle, kodi, cctv, params)
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
    kodi.add_folder(handle, base, "首页", "home",
                    plot="央视网首页的内容，按它自己的板块分开")
    kodi.add_folder(handle, base, "新闻", "news",
                    plot="要闻 / 国内 / 国际 / 社会 / 法治 / 科技 / 文娱 / 教育 / 健康")
    kodi.add_folder(handle, base, "视频", "videos",
                    plot="央视网视频库，最新发布")
    kodi.add_folder(handle, base, "体育", "sports",
                    plot="央视体育：19 个频道（NBA、CBA、中超、英超…）+ 15 个名栏节目")
    kodi.add_folder(handle, base, "4K专区", "fourk",
                    plot="4K/超高清专区，明文流，实测 1920x1080")
    kodi.add_folder(handle, base, "栏目", "columns",
                    plot="348 个电视栏目，按 CCTV 频道分组")
    kodi.add_folder(handle, base, "直播", "live",
                    plot="CCTV 各频道在线直播")
    kodi.add_folder(handle, base, "搜索", "search",
                    plot="输入关键词，搜索央视网视频")
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 首页
def _home(handle, base, addon, kodi, portal, params):
    sections = portal.home_sections()
    if not sections:
        kodi.add_folder(handle, base, "首页没有抓到内容，点这里重试", "home")
        kodi.end_directory(handle)
        kodi.notify("首页抓取失败", error=True)
        return

    sec = params.get("sec")
    if sec is None or sec == "":
        total = sum(len(items) for _, items in sections)
        kodi.log("home: %d 个板块，共 %d 条" % (len(sections), total))
        for i, (name, items) in enumerate(sections):
            kodi.add_folder(handle, base, "%s（%d）" % (name, len(items)), "home",
                            sec=i, thumb=items[0].get("thumb") or None,
                            plot="首页板块：%s" % name)
        kodi.end_directory(handle)
        return

    try:
        name, items = sections[int(sec)]
    except (ValueError, IndexError):
        kodi.end_directory(handle, succeeded=False)
        return

    kodi.log("home section %s: %d 条" % (name, len(items)))

    # 设置里的「每个板块最多显示条数」，默认 80；取前 N 条，避免一个板块几百条。
    limit = max(1, kodi.setting_int(addon, "home_limit", 80))
    if len(items) > limit:
        items = items[:limit]

    if kodi.setting_bool(addon, "show_thumb", True):
        try:
            from resources.lib import cctv

            items = cctv.fill_thumbs(items, deadline=4.0)
        except Exception as exc:
            kodi.log("home thumbs failed: %r" % exc, xbmc.LOGWARNING)

    for item in items:
        if item.get("kind") == "article":
            kodi.add_folder(
                handle, base, item["title"], "article",
                thumb=item.get("thumb") or None,
                plot=item.get("plot") or item["title"],
                date=item.get("date") or "",
                u=quote(item["url"], safe=""),
                context=kodi.download_context(
                    base, "dltext", "下载文本…", u=quote(item["url"], safe=""), t=item["title"]
                ),
            )
        elif item.get("kind") == "live":
            kodi.add_playable(handle, base, item["title"], "liveplay",
                              ch=item["ch"],
                              plot=item.get("plot") or "")
        else:
            kodi.add_playable(
                handle, base, item["title"], "play",
                thumb=item.get("thumb") or None,
                plot=item.get("plot") or item["title"],
                date=item.get("date") or "",
                u=quote(item["url"], safe=""),
                context=kodi.download_context(
                    base, "download", "下载…", u=quote(item["url"], safe=""), t=item["title"]
                ),
            )
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 新闻
def _news(handle, base, kodi, portal):
    for col, name in portal.NEWS_COLUMNS:
        kodi.add_folder(handle, base, name, "newslist",
                        c=col, plot="央视网·%s" % name)
    kodi.end_directory(handle)


def _newslist(handle, base, kodi, portal, params):
    col = params.get("c") or "news"
    page = int(params.get("page") or 1)
    name = dict(portal.NEWS_COLUMNS).get(col, col)

    items, total = portal.news_items(col, page)
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
                base, "dltext", "下载文本…", u=quote(it["url"], safe=""), t=it["title"]
            ),
        )

    shown = (page - 1) * portal.NEWS_PAGE_SIZE + len(items)
    if shown < min(total, portal.NEWS_MAX):
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
    items, total = portal.video_items(page)
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
                base, "download", "下载…",
                g=it.get("guid") or "", u=quote(it["url"], safe=""), t=it["title"],
            ),
        )

    if page * portal.VIDEO_PAGE_SIZE < total:
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "videos",
                        page=page + 1, plot="共约 %d 条视频" % total)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 体育
def _sports(handle, base, kodi, portal, params):
    """体育：上层的两个入口（频道 / 央视名栏），下面各自展开。

    频道来自 sports.cctv.com 的顶部导航（「推荐」除外，它就是首页本身），
    名栏来自首页那几个方框里带《》的电视栏目。
    """
    grp = params.get("grp") or ""

    if not grp:
        kodi.add_folder(handle, base, "频道（%d 个）" % len(portal.SPORTS_CHANNELS), "sports",
                        grp="ch",
                        plot="央视体育的 19 个频道：奥林匹克频道、NBA、CBA、中超、"
                             "英超、德甲、法甲、意甲、羽毛球、乒乓球、网球、排球…")
        kodi.add_folder(handle, base, "央视名栏（%d 个）" % len(portal.SPORTS_PROGRAMS), "sports",
                        grp="prog",
                        plot="央视体育的名牌栏目：体育新闻、体育世界、天下足球、"
                             "足球之夜、篮球公园…")
        kodi.add_folder(handle, base, "赛事", "matches",
                        plot="央视体育的赛程与比赛视频：近期集锦、今日赛程、"
                             "按日期、按联赛（NBA、CBA、英超、中超…）")
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
    try:
        items, total, source = portal.sports_channel_items(name, page)
    except Exception as exc:
        kodi.log("sports channel %s failed: %r" % (name, exc), xbmc.LOGERROR)
        kodi.notify("%s 暂时抓不到内容：%s" % (name or "体育频道", exc), error=True)
        kodi.add_folder(handle, base, "抓取失败，点这里重试", "sportsch", **retry)
        kodi.end_directory(handle)
        return

    page_size = page_size or len(items) or 1
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
                    base, "download", "下载…", u=url, t=title,
                ),
            )
        else:
            kodi.add_folder(
                handle, base, title, "article",
                thumb=thumb, plot=plot, date=date, u=url,
                context=kodi.download_context(
                    base, "dltext", "下载文本…", u=url, t=title,
                ),
            )

    if page * page_size < total:
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
    """赛事菜单：近期集锦 / 今日赛程 / 按日期 / 按联赛。"""
    grp = params.get("grp") or ""

    if not grp:
        try:
            today, dates = portal.sports_dates()
        except Exception as exc:
            kodi.log("matches dates failed: %r" % exc, xbmc.LOGERROR)
            kodi.notify("赛程接口暂时打不开：%s" % exc, error=True)
            kodi.add_folder(handle, base, "点这里重试", "matches")
            kodi.end_directory(handle)
            return
        kodi.add_folder(handle, base, "近期集锦", "highlights",
                        plot="把最近 %d 天所有比赛的集锦与回顾合成一个视频列表"
                             % portal.CBS_HIGHLIGHT_DAYS)
        kodi.add_folder(handle, base, "今日赛程（%s）" % today, "matchlist", d=today,
                        plot="今天所有比赛，点进去看集锦与比分")
        kodi.add_folder(handle, base, "按日期（%d 天）" % len(dates), "matches", grp="dates",
                        plot="日期轴共 %d 天：%s ~ %s（接口只给这个滚动窗口，没有历史赛季）"
                             % (len(dates), dates[0], dates[-1]))
        kodi.add_folder(handle, base, "按联赛", "matches", grp="leagues",
                        plot="NBA、CBA、英超、中超、意甲、德甲、法甲、西甲、解放者杯…")
        kodi.end_directory(handle)
        return

    if grp == "dates":
        try:
            _today, rows = portal.sports_date_rows()
        except Exception as exc:
            kodi.notify("日期轴读取失败：%s" % exc, error=True)
            kodi.add_folder(handle, base, "点这里重试", "matches", grp="dates")
            kodi.end_directory(handle)
            return
        for row in rows:
            kodi.add_folder(handle, base, _date_label(row), "matchlist", d=row["date"],
                            plot="%s 的比赛" % row["date"])
        kodi.end_directory(handle)
        return

    try:
        leagues = portal.sports_leagues()
    except Exception as exc:
        kodi.notify("联赛列表读取失败：%s" % exc, error=True)
        kodi.add_folder(handle, base, "点这里重试", "matches", grp="leagues")
        kodi.end_directory(handle)
        return
    for row in leagues:
        label = row["name"]
        if row.get("kind"):
            label = "%s（%s）" % (label, row["kind"])
        kodi.add_folder(handle, base, label, "matchlist", l=row["id"],
                        thumb=row.get("logo") or None,
                        plot="%s 近三周的赛程与集锦" % row["name"])
    if not leagues:
        kodi.notify("没有取到联赛列表", error=True)
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
        # 没有集锦就别再摆一个空目录：直接把技术统计当文本显示出来
        kodi.log("match %s: 没有视频，直接显示技术统计" % game_id)
        heading = ("%s %s" % (game.get("leagueName") or "赛事",
                              game.get("gameName") or "")).strip()
        kodi.text_viewer(heading or "技术统计", report)
        kodi.notify("这场比赛的集锦还没上线，先看技术统计")
        kodi.end_directory(handle, succeeded=False)
        return

    label = "技术统计｜%s" % digest if digest else "技术统计"
    kodi.add_folder(handle, base, label, "matchinfo", id=game_id, plot=report)

    for it in items:
        title = _short_title(it["title"])
        plot = it["title"]
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
                base, "download", "下载…",
                g=guid, u=quote(page, safe="") if page else "", t=it["title"],
            ),
        )

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
                base, "download", "下载…", g=it["guid"], t=it["title"],
            ),
        )
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 4K 专区
def _fourk(handle, base, kodi, portal):
    items = portal.four_k_items()
    if not items:
        kodi.add_folder(handle, base, "4K 专区暂时没有抓到内容，点这里重试", "fourk")
        kodi.end_directory(handle)
        kodi.notify("4K 专区抓取失败", error=True)
        return
    for it in items:
        kodi.add_playable(handle, base, it["title"], "play",
                          thumb=it.get("thumb") or None,
                          plot=it.get("plot") or it["title"],
                          u=quote(it["url"], safe=""),
                          context=kodi.download_context(
                              base, "download", "下载…",
                              u=quote(it["url"], safe=""), t=it["title"],
                          ))
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
                              base, "download", "下载…",
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
    items, total = portal.album_items(album, page)
    if not items:
        kodi.add_folder(handle, base, "没有分集，点这里重试", "album", id=album, page=page)
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
                base, "download", "下载…",
                g=it.get("guid") or "", u=quote(it["url"], safe=""), t=it["title"],
            ),
        )

    if page * portal.ALBUM_PAGE_SIZE < min(total, 1000):
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "album",
                        id=album, page=page + 1, plot="共约 %d 集" % total)
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
                base, "download", "下载…",
                u=quote(it["url"], safe=""), t=it["title"],
            ),
        )

    if total > page * 20:
        kodi.add_folder(handle, base, "下一页（第 %d 页）" % (page + 1), "search",
                        q=keyword, page=page + 1)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 直播
def _live(handle, base, kodi, cctv):
    for ch, name in cctv.LIVE_CHANNELS:
        kodi.add_playable(handle, base, name, "liveplay", ch=ch)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 播放
def _play(handle, kodi, cctv, params):
    guid = params.get("g") or ""
    page_url = unquote(params.get("u") or "")

    if guid:
        try:
            stream, title, poster = cctv.resolve_guid(guid)
        except Exception as exc:
            kodi.fail(handle, "解析失败：%s" % exc)
            return
        kodi.play(handle, stream, title=title, thumb=poster)
        return

    if not page_url:
        kodi.fail(handle, "缺少视频地址")
        return
    try:
        stream, title, poster = cctv.resolve_video(page_url)
    except Exception as exc:
        kodi.fail(handle, "解析失败：%s" % exc)
        return
    kodi.play(handle, stream, title=title, thumb=poster)


def _live_play(handle, kodi, cctv, params):
    channel = params.get("ch") or ""
    name = dict(cctv.LIVE_CHANNELS).get(channel, channel)
    stream = cctv.live_url(channel)
    if not stream:
        kodi.fail(handle, "%s 暂时没有可用线路（该频道可能只对部分地区开放）" % name)
        return
    kodi.play(handle, stream, title=name, is_live=True)


# ---------------------------------------------------------------- 下载
def _download_dir(addon, kodi, dl):
    folder = kodi.setting_str(addon, "download_path", dl.DEFAULT_DIR)
    return kodi.path_of(folder)


def _download(handle, addon, kodi, dl, params):
    """长按视频 →「下载…」：先弹清晰度列表，每档都带预览按钮。

    一个列表里每档给两行：▶ 预览 / ⬇ 下载，最后一行是下载目录。
    选预览就播 20 秒再回到这个框，选下载就逐片存文件。
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
            labels.append("▶ 预览  %s" % stream["label"])
            entries.append(("preview", stream))

            size = stream.get("size") or 0
            hint = " · 约 %s" % dl.human_size(size) if size else ""
            if stream.get("encrypted"):
                labels.append("✖ 加密流，不能下载  %s" % stream["label"])
            else:
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

        if kind == "preview":
            _preview(addon, kodi, stream, title)
            continue

        if stream.get("encrypted"):
            kodi.notify("这条流是加密的，不能下载，可以先预览", error=True)
            continue

        _save_stream(addon, kodi, dl, stream, title, folder)
        return


def _preview(addon, kodi, stream, title):
    """预览：直接用 Kodi 播放器放这一档，放够设定时长就停下回到清晰度列表。"""
    seconds = max(5, kodi.setting_int(addon, "preview_seconds", 20))
    kodi.log("preview %s %s" % (stream["label"], stream["url"]))

    player = xbmc.Player()
    item = xbmcgui.ListItem(path=stream["url"], offscreen=True)
    item.setInfo("video", {"title": "%s（%s 预览）" % (title, stream["label"])})
    try:
        player.play(stream["url"], item)
    except Exception as exc:
        kodi.notify("预览失败：%s" % exc, error=True)
        return

    monitor = xbmc.Monitor()
    startup = time.monotonic() + 10
    while not player.isPlaying() and time.monotonic() < startup:
        if monitor.waitForAbort(0.2):
            return

    deadline = time.monotonic() + seconds
    while player.isPlaying() and time.monotonic() < deadline:
        if monitor.waitForAbort(0.25):
            return

    try:
        player.stop()
    except Exception:
        pass


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
    """长按文本 →「下载文本…」：把报道正文存成 .txt。"""
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
