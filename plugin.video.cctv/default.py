# -*- coding: utf-8 -*-
"""央视网 · Kodi 插件入口

URL 约定（Kodi 目录格式）：
    /                              根菜单（11 项：登录态 / 观看历史 / 书签 / 保存 / 搜索 /
                                   推荐内容 / 相关视频 / 频道 / 正在直播 / 电视节目 / 片库）
    /?mode=login                   点登录态那条：未登录则弹登录流程，已登录无反应
    /?mode=history[&kind=offline|sync]  观看历史（离线 = 本地记录，同步 = 账号云端）
    /?mode=bookmark                书签（只读账号收藏）
    /?mode=saved                   保存（下载目录里已存下来的视频）
    /?mode=chmenu                  「频道」子菜单（央剧 / 听音 / 体育 / 4K / 熊猫 / 春晚）
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

import os
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

# 「频道」菜单的条目：(设置里的开关 id 后缀, 名称, mode, 额外参数)
CHANNEL_ITEMS = (
    ("netdrama", "央视网剧", "netdrama", {}),
    ("tingyin", "听音", "tingyin", {}),
    ("sports", "体育", "sports", {}),
    ("fourk", "4K专区", "fourk", {}),
    ("panda", "熊猫", "panda", {}),
    ("chunwan", "总台春晚", "chunwan", {}),
    ("docu", "精品纪录", "special", {"k": "docu"}),
    ("wsp", "微视频", "special", {"k": "wsp"}),
    ("wgs", "微故事", "special", {"k": "wgs"}),
    ("food", "美食", "special", {"k": "food"}),
)


def _page_limit(kodi):
    """每页条数设置已取消，固定按接口原本的单页走（返回 0）。"""
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


MAX_ALL_ITEMS = 3000

# 「相关视频」的上限。接口 total 是 41911，按每页 20 条算要 150 个请求、
# 拿 3000 条，而用户实际只会看最近几百条。单独给这个界面设上限。
VIDEO_MAX_ITEMS = 300

# 一页到底的并发线程数。原来固定 8，提到 12：列表页请求数一多（视频库 150 个），
# 8 个线程排队太长。实测 12 比 8 快约 25%，再高收益递减且容易被 CDN 掐连接。
PAGE_WORKERS = 12


def _view_all(fetch, native, max_items=MAX_ALL_ITEMS):
    """把接口所有页拼成一个列表。fetch(api_page) -> (items, total)。

    先取第 1 页拿 total，其余页并发拉取，最后按 max_items 截断。
    没有 total 的接口退化为顺序翻页，遇到不足一页就停。
    """
    first, total = fetch(1)
    items = list(first or [])
    if not items:
        return [], total
    if not total:
        page = 2
        while len(items) < max_items:
            part, _ = fetch(page)
            if not part:
                break
            items.extend(part)
            if len(part) < native:
                break
            page += 1
        return items[:max_items], len(items)

    page_count = min((total + native - 1) // native,
                     (max_items + native - 1) // native)
    if page_count > 1:
        try:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
                parts = list(pool.map(lambda p: fetch(p)[0], range(2, page_count + 1)))
            for part in parts:
                if part:
                    items.extend(part)
        except Exception as exc:
            xbmc.log("_view_all concurrent failed: %r" % exc, xbmc.LOGWARNING)
    return items[:max_items], total


def main():
    global _ADDON
    addon = xbmcaddon.Addon(ADDON_ID)
    _ADDON = addon
    addon_path = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    if addon_path not in sys.path:
        sys.path.insert(0, addon_path)

    from resources.lib import account, cctv, download as dl, kodi, pages, portal, special

    base = sys.argv[0]
    handle = int(sys.argv[1]) if len(sys.argv) > 1 else -1
    params = dict(parse_qsl((sys.argv[2] if len(sys.argv) > 2 else "").lstrip("?")))
    mode = params.get("mode", "root")

    kodi.log("mode=%s params=%s" % (mode, params))
    _started = time.time()

    try:
        if mode == "root":
            _root(handle, base, kodi, account)
        elif mode == "login":
            _login(handle, base, addon, kodi, account)
        elif mode == "logout":
            _logout(handle, base, kodi, account)
        elif mode == "clearsync":
            _clear_sync(handle, base, kodi, account)
        elif mode == "clearoffline":
            _clear_offline(handle, base, kodi, account)
        elif mode == "history":
            _history(handle, base, kodi, account, params)
        elif mode == "saved":
            _saved(handle, base, addon, kodi, dl)
        elif mode == "bookmark":
            _bookmark(handle, base, kodi, account)
        elif mode == "chmenu":
            _chmenu(handle, base, addon, kodi)
        elif mode == "special":
            _special(handle, base, kodi, special, params)
        elif mode == "go":
            _go(handle, base, kodi, params)
        elif mode == "collect":
            _collect(handle, base, kodi, account, params)
        elif mode == "home":
            _home(handle, base, kodi, portal, pages, params)
        elif mode == "news":
            _news(handle, base, kodi, portal, pages)
        elif mode == "newslist":
            _newslist(handle, base, kodi, portal, params)
        elif mode == "newsgroup":
            _news_group(handle, base, kodi, pages, params)
        elif mode == "more":
            _root(handle, base, kodi, account)
        elif mode == "shizheng":
            _shizheng(handle, base, kodi, pages)
        elif mode == "kandian":
            _kandian(handle, base, kodi, pages)
        elif mode == "src":
            _src(handle, base, kodi, pages, params)
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
        elif mode == "tycat":
            _tycat(handle, base, kodi, pages)
        elif mode == "tycatlist":
            _tycatlist(handle, base, kodi, pages, params)
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
        elif mode == "match":
            _match(handle, base, kodi, portal, params)
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
        elif mode == "live":
            _live(handle, base, kodi, cctv)
        elif mode == "play":
            _play(handle, base, kodi, cctv, params, account)
        elif mode == "liveplay":
            _live_play(handle, kodi, cctv, params)
        elif mode == "download":
            _download(handle, addon, kodi, dl, params)
        elif mode == "dltext":
            _dltext(handle, addon, kodi, portal, dl, params)
        else:
            _root(handle, base, kodi, account)
    except Exception as exc:  # 任何异常都不能让 Kodi 卡在转圈
        kodi.log("unhandled: %r" % exc, xbmc.LOGERROR)
        kodi.notify("出错了：%s" % exc, error=True)
        if mode in ("play", "liveplay", "login", "collect", "go"):
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        elif mode in ("download", "dltext"):
            pass  # 这两条是 RunPlugin 触发的，没有目录要收尾
        else:
            kodi.end_directory(handle, succeeded=False)
    finally:
        kodi.log("mode=%s 用时 %.2fs" % (mode, time.time() - _started))


# ---------------------------------------------------------------- 菜单
def _root(handle, base, kodi, account):
    login_ctx = []
    if account.is_logged_in():
        login_ctx = [("退出登录", "RunPlugin(%s)" % kodi.build_url(base, mode="logout"))]
    kodi.add_folder(handle, base, account.label(), "login",
                    thumb=kodi.menu_icon("user"), plot=account.plot(),
                    context=login_ctx)
    for label, mode, icon in (
        ("观看历史", "history", "history"),
        ("书签", "bookmark", "bookmark"),
        ("保存", "saved", "save"),
        ("搜索", "search", "search"),
        ("推荐内容", "home", "home"),
        ("相关视频", "videos", "video"),
        ("频道", "chmenu", "folder-cctv"),
        ("正在直播", "live", "live"),
        ("电视节目", "columns", "monitor"),
        ("片库", "pianku", "pianku"),
    ):
        kodi.add_folder(handle, base, label, mode,
                        thumb=kodi.menu_icon(icon), plot=label)
    kodi.end_directory(handle)


def _chmenu(handle, base, addon, kodi):
    # 条目都在设置 →「频道管理」里能单独关掉，关掉的这里就不出。
    for key, label, mode, extra in CHANNEL_ITEMS:
        if not kodi.setting_bool(addon, "ch_" + key, True):
            continue
        kodi.add_folder(handle, base, label, mode, plot=label, **extra)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 账号
def _input(dialog, heading, hidden=False):
    """弹输入框。Kodi 各版本对隐藏输入的支持不一致，退两步兜底。"""
    try:
        option = xbmcgui.ALPHANUM_HIDE_INPUT if hidden else 0
        return dialog.input(heading, "", xbmcgui.INPUT_ALPHANUM, option) or ""
    except Exception:
        try:
            return dialog.input(heading) or ""
        except Exception:
            return ""


def _login(handle, base, addon, kodi, account):
    """登录：点根目录第一条就弹手机号/密码。结束后回到根目录，不停在空目录里。"""
    def done():
        kodi.cancel_playback(handle)
        kodi.end_directory(handle)
        try:
            xbmc.executebuiltin("Container.Update(%s?mode=root,replace)" % base)
        except Exception:
            pass

    # 已登录时点这个条目什么都不做
    if account.is_logged_in():
        done()
        return

    dialog = xbmcgui.Dialog()
    phone = "".join(ch for ch in _input(dialog, "输入手机号") if ch.isdigit())
    if not phone:
        done()
        return
    if len(phone) != 11:
        kodi.notify("手机号格式不对", error=True)
        done()
        return

    password = _input(dialog, "输入密码", hidden=True)
    if not password:
        done()
        return

    agreed = False
    try:
        agreed = bool(dialog.yesno(
            "用户协议",
            "登录即表示同意《央视网用户服务协议》和《隐私政策》。是否同意并登录？",
            "不同意", "同意并登录"))
    except Exception:
        agreed = True
    if not agreed:
        kodi.notify("需要同意协议才能登录")
        done()
        return

    remember = True
    try:
        remember = bool(dialog.yesno("记住我", "记住登录状态？下次打开 Kodi 不用重新登录。",
                                     "不记住", "记住"))
    except Exception:
        remember = True

    progress = kodi.progress_open("正在登录")
    try:
        ok, message = account.login(phone, password, remember)
    except Exception as exc:
        kodi.log("login failed: %r" % exc, xbmc.LOGERROR)
        ok, message = False, "登录失败：%s" % exc
    finally:
        kodi.progress_close(progress)

    if ok:
        kodi.notify("登录成功")
    else:
        kodi.notify(message, error=True)
    done()


def _logout(handle, base, kodi, account):
    """设置里「退出登录」按钮：没登录就提示账户未登录，登录了先确认再退出。"""
    if not account.is_logged_in():
        kodi.notify("账户未登录")
        return
    try:
        yes = bool(xbmcgui.Dialog().yesno("退出登录", "确定退出当前账户？"))
    except Exception:
        yes = True
    if not yes:
        return
    try:
        account.clear()
    except Exception as exc:
        kodi.log("logout failed: %r" % exc, xbmc.LOGERROR)
        return
    kodi.notify("登录已退出")


def _clear_sync(handle, base, kodi, account):
    """设置 → 账户 →「删除同步记录」：把央视网账号里的观看历史清空。"""
    if not account.is_logged_in():
        kodi.notify("账户未登录")
        return
    try:
        yes = bool(xbmcgui.Dialog().yesno(
            "删除同步记录", "会删掉央视网账号里的全部观看历史（网页和 App 上也会一起消失），确定吗？"))
    except Exception:
        yes = False
    if not yes:
        return
    ok, message, _count = account.clear_history()
    kodi.notify(message, error=not ok)


def _clear_offline(handle, base, kodi, account):
    """设置 → 账户 →「清除离线记录」：只删本机存的那份观看记录。"""
    count = len(account.offline_history())
    if not count:
        kodi.notify("没有离线记录")
        return
    try:
        yes = bool(xbmcgui.Dialog().yesno("清除离线记录", "确定清掉本机的 %d 条观看记录？" % count))
    except Exception:
        yes = False
    if not yes:
        return
    account.clear_offline()
    kodi.notify("已清除 %d 条离线记录" % count)


def _history(handle, base, kodi, account, params):
    """观看历史：先给「离线 / 同步」两个文件夹，再按 kind 出列表。

    离线 = 未登录时在插件里看过的（本地记录）；
    同步 = 登录后上报到央视网账号的观看历史（网页/App 也能看到）。
    """
    kind = params.get("kind") or ""
    if not kind:
        label_off = "离线"
        kodi.add_folder(handle, base, label_off, "history", kind="offline",
                        thumb=kodi.menu_icon("offline"), plot=label_off)
        kodi.add_folder(handle, base, "同步", "history", kind="sync",
                        thumb=kodi.menu_icon("sync"), plot="同步")
        kodi.end_directory(handle)
        return

    if kind == "offline":
        items = account.offline_history()
        if not items:
            kodi.end_directory(handle)
            return
        for it in items:
            _history_item(handle, base, kodi, it)
        kodi.end_directory(handle)
        return

    if not account.is_logged_in():
        kodi.add_action(handle, base, "请先登录", "login",
                        plot="同步历史需要登录央视网账号")
        kodi.end_directory(handle)
        return
    items, cached = account.history()
    if not items:
        kodi.end_directory(handle)
        return
    for it in items:
        _history_item(handle, base, kodi, it)
    if cached:
        kodi.notify("网络未连接")
    kodi.end_directory(handle)


def _history_item(handle, base, kodi, it):
    title = it.get("title") or "未命名"
    plot = title
    pos, length = it.get("position"), it.get("length")
    try:
        pos_i, len_i = int(float(pos)), int(float(length))
        if pos_i > 0:
            plot = "%s\n上次看到 %d:%02d" % (title, pos_i // 60, pos_i % 60)
            if len_i > 0:
                plot += " / %d:%02d" % (len_i // 60, len_i % 60)
    except (TypeError, ValueError):
        pass
    extra = {"t": title}
    save = {"t": title}
    if it.get("guid"):
        extra["g"] = it["guid"]
        save["g"] = it["guid"]
    if it.get("url"):
        extra["u"] = quote(it["url"], safe="")
        save["u"] = quote(it["url"], safe="")
    kodi.add_playable(
        handle, base, title, "play",
        thumb=it.get("thumb") or "", plot=plot,
        context=kodi.download_context(base, "download", "保存", **save),
        **extra)


def _bookmark(handle, base, kodi, account):
    """书签 = 账号里的收藏，只读同步内容，不做本地收藏（本地收藏用 Kodi 自带的收藏夹）。"""
    if not account.is_logged_in():
        kodi.add_action(handle, base, "请先登录", "login",
                        plot="书签需要登录央视网账号")
        kodi.end_directory(handle)
        return
    items, cached = account.bookmarks()
    if not items:
        kodi.end_directory(handle)
        return
    for it in items:
        title = it.get("title") or "未命名"
        # 不是播放接口要的 32 位 guid —— 直接当 g= 传会「该视频不存在」。
        # 这里只传页面地址，让 _play 去页面里解析真正的 guid。
        extra = {"t": title}
        save = {"t": title}
        url = it.get("url") or ""
        if url:
            extra["u"] = quote(url, safe="")
            save["u"] = quote(url, safe="")
        sid = (it.get("guid") or "").strip()
        # 只有长得像 32 位 hex 的才算真 guid（收藏的 source_id 是 VIDE... 这种）
        if re.match(r"^[0-9a-fA-F]{32}$", sid):
            extra["g"] = sid
            save["g"] = sid
        if not extra.get("u") and not extra.get("g"):
            # 既没有页面地址也没有真 guid，播不了，给个明确提示
            kodi.add_action(handle, base, "%s（缺少播放地址）" % title, "login",
                            plot="这条收藏没有可用的播放地址")
            continue
        kodi.add_playable(handle, base, title, "play",
                          thumb=it.get("thumb") or "", plot=title,
                          context=kodi.download_context(base, "download", "保存", **save),
                          **extra)
    if cached:
        kodi.notify("网络未连接")
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 保存（已下载）
SAVED_EXTS = (".ts", ".mp4", ".mkv", ".m4v", ".avi", ".mov", ".webm", ".flv", ".mpg", ".mpeg")


def _saved(handle, base, addon, kodi, dl):
    """「保存」栏目：列下载目录里已经存下来的视频，优先按 .nfo 里的标题/封面显示。"""
    from resources.lib import nfo

    folder = _download_dir(addon, kodi, dl)
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    rows = []
    for name in names:
        if name.startswith(".") or name.endswith(".part"):
            continue
        if not name.lower().endswith(SAVED_EXTS):
            continue
        path = os.path.join(folder, name)
        try:
            mtime = os.path.getmtime(path)
            size = os.path.getsize(path)
        except OSError:
            continue
        rows.append((mtime, path, size, name))
    rows.sort(reverse=True)

    if not rows:
        kodi.end_directory(handle)
        return

    for mtime, path, size, name in rows:
        meta = nfo.read(path)
        title = meta.get("title") or os.path.splitext(name)[0]
        plot = meta.get("plot") or title
        if size:
            plot = "%s\n\n%s" % (plot, dl.human_size(size))
        kodi.add_local(handle, path, title, thumb=meta.get("thumb") or "",
                       plot=plot, base=base)
    kodi.end_directory(handle)


def _special(handle, base, kodi, special, params):
    """新频道：精品纪录 / 微视频 / 微故事 / 美食。"""
    kind = params.get("k") or "wsp"
    page = max(1, int(params.get("page") or 1))
    limit = 20
    try:
        if kind == "wsp":
            # 一次取多页（推荐流没有总数，拉到 150 条或不足一页为止）
            items = []
            page = 1
            while len(items) < 150:
                part = special.wsp_items(page, 50)
                if not part:
                    break
                items.extend(part)
                if len(part) < 50:
                    break
                page += 1
            _add_video_items(handle, base, kodi, items[:150])
        elif kind == "wgs":
            # 微故事就是整页解析，全部显示
            items = special.wgs_items()
            special.fill_missing(items)
            _add_video_items(handle, base, kodi, items)
        elif kind == "food":
            # 首页横幅直接平铺在美食最前面（不再套一层「热播横幅」文件夹）
            for it in special.food_banners():
                kodi.add_folder(handle, base, it["title"], "special", k="foodsub",
                                thumb=it.get("thumb") or None,
                                plot=it.get("title") or it["title"],
                                u=quote(it["url"], safe=""))
            # 分类标签（荤菜/海鲜/素菜…）：名字就是标签，不带数量；图标取该分类第一道菜的图
            for i, (label, items) in enumerate(special.food_tabs()):
                kodi.add_folder(handle, base, label, "special", k="foodtab", i=i,
                                thumb=(items[0].get("thumb") if items else None) or None,
                                plot=label)
            for it in special.food_items():
                kodi.add_folder(handle, base, it["title"], "special", k="foodsub",
                                thumb=it.get("thumb") or None,
                                plot=it.get("title") or it["title"],
                                u=quote(it["url"], safe=""))
        elif kind == "foodbanner":
            for it in special.food_banners():
                kodi.add_folder(handle, base, it["title"], "special", k="foodsub",
                                thumb=it.get("thumb") or None,
                                plot=it.get("title") or it["title"],
                                u=quote(it["url"], safe=""))
        elif kind == "foodtab":
            idx = int(params.get("i") or 0)
            tabs = special.food_tabs()
            items = tabs[idx][1] if 0 <= idx < len(tabs) else []
            for it in items:
                kodi.add_folder(handle, base, it["title"], "special", k="foodsub",
                                thumb=it.get("thumb") or None,
                                plot=it.get("title") or it["title"],
                                u=quote(it["url"], safe=""))
        elif kind == "foodsub":
            url = unquote(params.get("u") or "")
            items = special.food_sub_items(url)
            special.fill_missing(items)
            _add_video_items(handle, base, kodi, items)
        elif kind == "docu":
            for it in special.docu_items():
                kodi.add_folder(handle, base, it["title"], "special", k="docueps",
                                thumb=it.get("thumb") or None,
                                plot=it.get("title") or it["title"],
                                u=quote(it["url"], safe=""))
        elif kind == "docueps":
            url = unquote(params.get("u") or "")
            items = special.docu_episodes(url)
            _add_video_items(handle, base, kodi, items)
    except Exception as exc:
        kodi.log("special(%s) failed: %r" % (kind, exc), xbmc.LOGERROR)
        kodi.notify("出错了：%s" % exc, error=True)
    kodi.end_directory(handle)


def _add_video_items(handle, base, kodi, items):
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        guid = it.get("guid") or ""
        context = kodi.download_context(base, "download", "保存", g=guid, t=title)
        kodi.add_playable(
            handle, base, title, "play",
            thumb=it.get("thumb") or None,
            plot=it.get("plot") or title,
            g=guid,
            context=context,
        )


def _go(handle, base, kodi, params):
    """动作行：取消播放 + 用 Container.Update 换到目标目录。

    Kodi 里可播放条目点下去会等 setResolvedUrl；这里先取消，再跳到目标目录，
    否则会弹「播放失败」。用于「点击刷新」这类占位行。
    """
    target = unquote(params.get("to") or "")
    kodi.cancel_playback(handle)
    if target:
        try:
            xbmc.executebuiltin("Container.Update(%s,replace)" % target)
        except Exception as exc:
            kodi.log("Container.Update failed: %r" % exc, xbmc.LOGWARNING)


def _collect(handle, base, kodi, account, params):
    """长按 → 收藏到央视网 / 取消收藏。"""
    act = params.get("act") or "add"
    source_id = params.get("sid") or ""
    source_type = params.get("st") or "1"
    title = params.get("t") or ""
    url = unquote(params.get("u") or "")
    if not source_id:
        kodi.notify("出错了：没有可收藏的编号", error=True)
        kodi.cancel_playback(handle)
        return
    try:
        if act == "del":
            # 取消收藏需要收藏记录的 id，collect_remove 内部会自己查
            ok, message = account.collect_remove(source_id, source_type)
        else:
            ok, message = account.collect_add(source_id, source_type,
                                              title=title, url=url)
    except Exception as exc:
        kodi.log("collect failed: %r" % exc, xbmc.LOGERROR)
        ok, message = False, "收藏失败：%s" % exc
    kodi.log("collect %s %s -> %s (%s)" % (act, source_id, ok, message))
    kodi.notify(message, error=not ok)
    # 收藏状态变了，刷新一下列表让菜单项跟着变
    if ok:
        try:
            xbmc.executebuiltin("Container.Refresh")
        except Exception:
            pass
    kodi.cancel_playback(handle)


# ---------------------------------------------------------------- 时政
def _shizheng(handle, base, kodi, pages):
    for i, src in enumerate(pages.SHIZHENG):
        kodi.add_folder(handle, base, src["name"], "src",
                        g="shizheng", i=i, plot="时政 · %s" % src["name"])
    kodi.end_directory(handle)


def _chlist(handle, base, kodi, pages, params):
    name = params.get("c") or ""
    page = int(params.get("page") or 1)
    if not pages.channel(name):
        kodi.end_directory(handle, succeeded=False)
        return

    limit = _page_limit(kodi)
    # 频道列表一次拉全部（最多 MAX_ALL_ITEMS），不再提示下一页
    items, total = _view_all(lambda api: pages.channel_items(name, api),
                             pages.MIX_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "这个频道没抓到内容，点这里重试", "chlist",
                        c=name, page=page)
        kodi.end_directory(handle)
        return

    _render_items(handle, base, kodi, items)
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
    try:
        from resources.lib import pages as _pages

        _pages.fill_item_covers(items)
    except Exception as exc:
        kodi.log("fill covers failed: %r" % exc, xbmc.LOGWARNING)
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
                            plot=row["name"])
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
    # 片库一次拉全部（最多 MAX_ALL_ITEMS），不再提示下一页
    items, total = _view_all(lambda api: pages.pianku_items(cat, api, filters),
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
    kodi.add_folder(handle, base, label, "pksel", cat=cat, plot=label)

    for it in items:
        row_label = it["title"]
        if it.get("label"):
            row_label = "%s（%s）" % (row_label, it["label"])
        kodi.add_folder(
            handle, base, row_label, "album",
            thumb=it.get("thumb") or None,
            plot=row_label,
            id=it.get("album") or "",
        )

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
    # 网剧分集一次拉全部
    items, total = _view_all(lambda api: pages.netdrama_episodes(mid, api),
                             pages.NETDRAMA_PAGE_SIZE)
    if not items:
        # 有的网剧还没上传分集（实测 20 部里有 1 部是空的），不要留假入口
        kodi.notify("出错了：这一部还没有可播放的分集")
        kodi.end_directory(handle, succeeded=False)
        return
    _render_items(handle, base, kodi, items)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 听音
_TY_ALBUM_RE = re.compile(r"(VIDA[0-9A-Za-z]+)")


def _tingyin(handle, base, kodi, pages):
    kodi.add_folder(handle, base, "类别", "tycat", plot="类别")

    try:
        sections = pages.ty_home_sections()
    except Exception as exc:
        kodi.log("tingyin home failed: %r" % exc, xbmc.LOGWARNING)
        sections = []

    # 首页平铺的条目很多没有封面（模板占位图），先统一按专辑页补一遍再画
    flat = []
    for name, items in sections:
        for it in items:
            flat.append(it)
    pages.fill_ty_covers(flat)

    for name, items in sections:
        for it in items:
            label = "[%s]%s" % (name, it["title"])
            thumb = it.get("thumb") or None
            url = it["url"]
            if it.get("topic"):
                kodi.add_folder(handle, base, label, "ty_topic",
                                u=quote(url, safe=""), thumb=thumb,
                                plot=label)
                continue
            album = _TY_ALBUM_RE.search(url or "")
            if album:
                kodi.add_folder(handle, base, label, "album", id=album.group(1),
                                thumb=thumb, plot=label)
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
        kodi.notify("出错了：听音专题没抓到内容")
        kodi.end_directory(handle, succeeded=False)
        return
    pages.fill_ty_covers(items)
    _render_items(handle, base, kodi, items)
    kodi.end_directory(handle)


def _tycat(handle, base, kodi, pages):
    """听音 → 类别：来自听音分类条，去掉首页/听音榜。"""
    for name, kind, key in pages.TY_CATEGORIES:
        kodi.add_folder(handle, base, name, "tycatlist", c=key, k=kind,
                        plot=name)
    kodi.end_directory(handle)


def _tycatlist(handle, base, kodi, pages, params):
    kind = params.get("k") or "page"
    key = params.get("c") or ""
    page = int(params.get("page") or 1)
    if not key:
        kodi.end_directory(handle, succeeded=False)
        return
    try:
        # 听音类别一次拉全部专辑
        items, total = _view_all(
            lambda api: pages.ty_cat_items(kind, key, api), pages.TY_PAGE_SIZE)
    except Exception as exc:
        kodi.log("tycat failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("出错了：%s" % exc, error=True)
        kodi.end_directory(handle, succeeded=False)
        return
    if not items:
        kodi.add_folder(handle, base, "没有内容，点这里重试", "tycatlist",
                        c=key, k=kind, page=page)
        kodi.end_directory(handle)
        return
    pages.fill_ty_covers(items)
    _render_items(handle, base, kodi, items)
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
    kodi.add_playable(handle, base, "熊猫频道", "liveplay", ch="ipanda",
                      thumb=kodi.logo_path("ipanda.png") or None,
                      plot="熊猫频道 · 正在直播")
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


def _home(handle, base, kodi, portal, pages, params):
    sec = params.get("sec")

    if sec is None or sec == "":
        # 首页 HTML 只抓一次，热播/精选/板块三处共用
        home_text = pages.tv_home_text()
        hot = pages.home_hotplay(text=home_text)
        picks = pages.home_picks(text=home_text)
        sections = portal.home_sections(text=home_text) or []
        limit = 80

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
                            plot=plain)

        kodi.add_folder(handle, base, "看点", "kandian",
                        plot="看点：今日亚洲、国宝·发现、军事科技…")

        for it in hot:
            _add_home_item(handle, base, kodi, it)
        for it in picks:
            _add_home_item(handle, base, kodi, it)

        # 两个平铺板块合并成一次补图：分开补会各自等满一个 deadline，
        # 实测 4.4s + 4.0s，合并后只等一次。
        flat = []
        for tag, _idx, items in flatten:
            for item in items[:limit]:
                flat.append(item)
        try:
            from resources.lib import cctv

            cctv.fill_thumbs(flat, deadline=5.0)
        except Exception as exc:
            kodi.log("home thumbs failed: %r" % exc, xbmc.LOGWARNING)

        pos = 0
        for tag, _idx, items in flatten:
            items = items[:limit]
            for item in items:
                _add_home_item(handle, base, kodi, item,
                               label="[%s]%s" % (tag, item["title"]))
                pos += 1
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

    for i, src in enumerate(pages.MORE_NEWS):
        kodi.add_folder(handle, base, src["name"], "src", g="morenews", i=i,
                        plot=src["name"])
    kodi.end_directory(handle)


def _newslist(handle, base, kodi, portal, params):
    col = params.get("c") or "news"
    page = int(params.get("page") or 1)
    name = dict(portal.NEWS_COLUMNS).get(col, col)

    limit = _page_limit(kodi)
    # 新闻列表一次拉全部
    items, total = _view_all(lambda api: portal.news_items(col, api),
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

    kodi.end_directory(handle)


def _article(handle, kodi, cctv, portal, params):
    url = unquote(params.get("u") or "")
    if not url:
        kodi.end_directory(handle, succeeded=False)
        return

    title, body, guid = portal.article_page(url)

    # 体育频道有些「图文」页是视频页：正文只有一两句话，播放器才是主体。
    # 这种直接播视频，比给用户看 60 个字有用。
    # 这里不能用 setResolvedUrl —— 这个条目是按目录项加进去的，
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
        kodi.notify("出错了：正文没抓到", error=True)
        kodi.end_directory(handle, succeeded=False)
        return
    kodi.log("article: %s %d 字" % (title[:20], len(body)))
    kodi.text_viewer(title or "报道", body)
    kodi.end_directory(handle, succeeded=False)


# ---------------------------------------------------------------- 视频库
def _videos(handle, base, kodi, portal, params):
    page = int(params.get("page") or 1)
    limit = _page_limit(kodi)
    # 相关视频一次拉全部
    # 相关视频是「全站最新」流，接口报 total=41911，一页到底就是 150 个请求。
    # 这个界面本来就是看最近更新，300 条足够翻很久，再往后没人会滚到。
    items, total = _view_all(lambda api: portal.video_items(api), portal.VIDEO_PAGE_SIZE,
                             max_items=VIDEO_MAX_ITEMS)
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

    kodi.end_directory(handle)


# ---------------------------------------------------------------- 体育
def _sports(handle, base, kodi, portal, params):
    """体育（2.2.0 ）：频道和央视名栏全部平铺在「体育」下面。

    频道来自 sports.cctv.com 的顶部导航（「推荐」除外，它就是首页本身），
    名栏来自首页那几个方框里带《》的电视栏目。
    之前加的「赛事」（中超/NBA/世界杯…）整块去掉：央视大多只发集锦，
    留在这里容易让人以为是整场回放。
    """
    grp = params.get("grp") or ""

    if not grp:
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
        # 体育频道一次拉全部
        items, total = _view_all(
            lambda api: portal.sports_channel_items(name, api)[:2],
            portal.SPORTS_SEARCH_PAGE)
        source = "cms"

    except Exception as exc:
        kodi.log("sports channel %s failed: %r" % (name, exc), xbmc.LOGERROR)
        kodi.notify("出错了：%s 抓不到内容：%s" % (name or "体育频道", exc), error=True)
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

    kodi.end_directory(handle)


# ---------------------------------------------------------------- 赛事
# 数据来自 cbs.sports.cctv.com（央视体育的赛程页）。比赛视频在它的
# /game/game_detail 里，videoList 的 originalId 就是视频中心 guid，
# 播放走现成的 mode=play&g=…，长按照样能预览和下载。
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
    # 4K 专区一次拉全部
    items, total = _view_all(lambda api: pages.fourk_albums(api), pages.FOURK_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "4K 专区暂时没有抓到内容，点这里重试", "fourk")
        kodi.end_directory(handle)
        return
    kodi.log("4k: %d/%d" % (len(items), total))

    _render_items(handle, base, kodi, items)

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
        kodi.notify("出错了：栏目页没有解析到节目", error=True)
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
        _album_rows(handle, base, kodi, episodes, album)
        rest = total - len(episodes)
        if rest > 0:
            kodi.add_folder(handle, base, "看点·花絮（%d）" % rest, "albumx",
                            id=album, page=1,
                            plot="预告、花絮、每集剧情看点，共 %d 条" % rest)
        kodi.end_directory(handle)
        return

    limit = _page_limit(kodi)
    # 专辑分集一次拉全部（听音专辑按升序，第 1 期在最前）
    items, total = _view_all(
        lambda api: portal.album_items(album, api, sort="asc"), portal.ALBUM_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "没有分集，点这里重试", "album", id=album, page=page)
        kodi.end_directory(handle)
        return

    _album_rows(handle, base, kodi, items, album)
    kodi.end_directory(handle)


def _album_rows(handle, base, kodi, items, album=""):
    for it in items:
        extra = {}
        if album:
            # 带上专辑号：播放时就能把整部正片塞进 Kodi 播放列表
            extra["a"] = album
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
            **extra
        )


def _album_extra(handle, base, kodi, portal, params):
    """剧集里的看点/花絮单独一个文件夹。"""
    album = params.get("id") or ""
    page = int(params.get("page") or 1)
    if not album:
        kodi.end_directory(handle, succeeded=False)
        return

    limit = _page_limit(kodi)
    # 看点/花絮一次拉全部
    items, total = _view_all(lambda api: portal.album_extras(album, api),
                             portal.ALBUM_PAGE_SIZE)
    if not items:
        kodi.add_folder(handle, base, "没有看点，点这里重试", "albumx",
                        id=album, page=page)
        kodi.end_directory(handle)
        return

    _album_rows(handle, base, kodi, items, album)
    kodi.end_directory(handle)


# ---------------------------------------------------------------- 搜索
def _search(handle, base, kodi, cctv, portal, params):
    keyword = (params.get("q") or "").strip()
    if not keyword:
        keyword = xbmcgui.Dialog().input("搜索", type=xbmcgui.INPUT_ALPHANUM)
        if not keyword:
            kodi.end_directory(handle, succeeded=False)
            return

    # 搜索结果一次拉全部
    try:
        items, total = _view_all(lambda api: portal.search_grouped(keyword, api), 20)
    except Exception as exc:
        kodi.log("search failed: %r" % exc, xbmc.LOGWARNING)
        kodi.notify("出错了：%s" % exc, error=True)
        kodi.end_directory(handle, succeeded=False)
        return

    if not items:
        kodi.notify("没有找到「%s」" % keyword, error=True)
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

    kodi.end_directory(handle)


# ---------------------------------------------------------------- 直播
def _live(handle, base, kodi, cctv):
    for ch, name in cctv.LIVE_CHANNELS:
        if ch == "ipanda":
            continue
        kodi.add_playable(handle, base, name, "liveplay", ch=ch,
                          thumb=kodi.logo_path(ch + ".png"))
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
        last_position = [0]
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
            try:
                last_position[0] = int(api.getTime() or 0)
            except Exception:
                pass
            nap(500)
    finally:
        player.stop()
    return last_position[0]


def _playlist_entries(base, album, current_guid, kodi):
    """取整部剧集的「选集」，转成 Kodi 播放列表项：[(label, plugin_url, thumb)]。

    走视频页「选集 / 更多 >」的官方接口参数（pub=1&mode=0&n=100）。
    返回 (entries, start)：start 是当前这集在列表里的下标；**找不到时返回 -1**
    （调用方据此决定退化成单集播放，避免播错集）。
    """
    from resources.lib import portal

    eps, _total = _view_all(
        lambda api: portal.album_items(album, api, sort="asc", full=True),
        portal.ALBUM_FULL_PAGE)
    # 剧集里常混着「看点/预告」，有「第 N 集」正片时只放正片
    official = [e for e in eps if portal.is_episode(e.get("title") or "")]
    if official:
        eps = official
    entries = []
    start = -1
    for ep in eps:
        title = ep.get("title") or "未命名"
        g = ep.get("guid") or ""
        u = quote(ep.get("url") or "", safe="")
        if not g and not u:
            continue
        plugin_url = kodi.build_url(base, mode="play", g=g, u=u, a=album, pl="1")
        entries.append((title, plugin_url, ep.get("thumb") or ""))
        if current_guid and g == current_guid:
            start = len(entries) - 1
    return entries, start


def _guess_album(page_url, guid, kodi):
    """首页/搜索这类入口点进来的视频：试着查它属于哪部剧。

    只返回「能确认当前这一集就在里面」的专辑号；确认不了就返回空串，
    调用方会走单集播放（宁可没有播放列表，也不能播错成别的集）。
    """
    if not page_url:
        return ""
    from resources.lib import portal

    try:
        album = portal.album_id_of(page_url)
    except Exception as exc:
        kodi.log("guess album failed: %r" % exc, xbmc.LOGWARNING)
        return ""
    if not album:
        return ""
    # 关键校验：分集列表里必须能找到当前 guid
    try:
        eps, _total = portal.album_items(album, 1, sort="asc", full=True)
    except Exception:
        try:
            eps, _total = portal.album_items(album, 1, sort="asc")
        except Exception as exc:
            kodi.log("guess album verify failed: %r" % exc, xbmc.LOGWARNING)
            return ""
    if not eps:
        return ""
    if any((e.get("guid") or "") == guid for e in eps):
        return album
    # 第一页没有，扩大范围再找一次（剧集很长时当前集可能不在第 1 页）
    try:
        all_eps, _t = _view_all(
            lambda api: portal.album_items(album, api, sort="asc", full=True),
            portal.ALBUM_FULL_PAGE)
        if any((e.get("guid") or "") == guid for e in all_eps):
            return album
    except Exception:
        pass
    return ""


def _no_playlist_source(params, page_url):
    """这些来源按用户要求不做播放列表：听音 / 精品纪录 / 片库。

    听音（收录在 ty 域名下、或来自听音专辑）、精品纪录（docuchina）、
    片库（tv.cctv.com/yxg 的剧集）——注意片库的分集页本身也在 tv.cctv.com，
    所以这里用 sources 参数显式标记（见各栏目加 a= 时传 s=）。
    """
    src = (params.get("s") or "").lower()
    if src in ("tingyin", "docu", "pianku"):
        return True
    url = (page_url or "").lower()
    if "docuchina" in url:
        return True
    return False


def _play(handle, base, kodi, cctv, params, account=None):
    guid = params.get("g") or ""
    page_url = unquote(params.get("u") or "")

    if not guid:
        if not page_url:
            kodi.fail(handle, "出错了：缺少视频地址")
            return
        try:
            guid = cctv.page_guid(page_url)
        except Exception as exc:
            # 给一条明确提示，而不是让 Kodi 弹「播放失败」。
            kodi.log("page_guid failed %s: %r" % (page_url, exc), xbmc.LOGWARNING)
            kodi.notify("出错了：解析不到播放地址", error=True)
            kodi.cancel_playback(handle)
            return

    # 视频页里有 videotvCodes（专辑号）且能定位到当前集、且多于 1 集，才挂播放列表；
    # 没有就单集播，不显示播放列表，也不做任何猜测。
    # 听音 / 精品纪录 / 片库 这三个栏目按用户要求不参与播放列表。
    playlist_entries = None
    playlist_start = 0
    if not params.get("pl") and not _no_playlist_source(params, page_url):
        try:
            album = params.get("a") or ""
            if not album and page_url:
                album = _guess_album(page_url, guid, kodi)
            if album:
                entries, start = _playlist_entries(base, album, guid, kodi)
                # 至少 2 集，且能定位到当前集，才算「有选集」
                if len(entries) > 1 and start >= 0:
                    playlist_entries = entries
                    playlist_start = start
        except Exception as exc:
            kodi.log("playlist prepare failed: %r" % exc, xbmc.LOGWARNING)

    try:
        info = cctv.video_info(guid, referer=page_url or None)
        rows = cctv.clear_ladders(info, for_play=True)
        if not rows:
            raise ValueError("没有找到能正常播放的清晰度")
        stream = rows[0]["url"]
        clear_height = int(rows[0].get("height") or 0)
        title = info.get("title") or ""
        poster = info.get("image") or ""
    except Exception as exc:
        kodi.fail(handle, "出错了：%s" % exc)
        return

    player = None
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

    if playlist_entries:
        # 有选集：挂上播放列表，当前集照常解析播放
        kodi.play_with_playlist(handle, player.url if player else stream,
                                title=title, thumb=poster,
                                entries=playlist_entries, start_index=playlist_start)
    else:
        kodi.play(handle, player.url if player else stream, title=title, thumb=poster)
    _record_play(account, kodi, guid, title, poster, page_url)
    if player:
        last = _hold_playback(player, kodi)
        if last:
            _record_play(account, kodi, guid, title, poster, page_url, position=last)


def _record_play(account, kodi, guid, title, thumb, page_url, position=0):
    """播放时记一笔观看历史：登录了上报云端（同步），没登录写本地（离线）。"""
    if account is None or not guid:
        return
    try:
        account.record_playback(guid, title=title, thumb=thumb,
                                page_url=page_url or "https://tv.cctv.com/",
                                position=position)
    except Exception as exc:
        kodi.log("记录观看历史失败（不影响播放）：%r" % exc, xbmc.LOGWARNING)


def _live_play(handle, kodi, cctv, params):
    channel = params.get("ch") or ""
    name = dict(cctv.LIVE_CHANNELS).get(channel, channel)
    stream = cctv.live_url(channel)
    if not stream:
        kodi.fail(handle, "出错了：%s 没有可用线路" % name)
        return

    player = None
    if cctv.is_drm_channel(channel):
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
    """长按视频 →「保存」：不再弹清晰度，直接存最高档，下完只提示「已保存：标题」。

    2.2.0：去掉预览按钮和预览时长设置。
    2.3.0 beta3：下完顺手写一份 Kodi 的 .nfo（封面/fanart 用在线地址）。
    2.3.0 beta5：去掉清晰度选择框，默认取最高档；下载完成只弹一条通知。
    """
    guid = params.get("g") or ""
    page_url = unquote(params.get("u") or "")
    title = params.get("t") or ""

    # 全程后台静默下载：开始一条「正在保存」提示，完成一条「已保存」，中间不弹进度框
    kodi.notify("正在保存：%s，请勿切断电源" % (title or "视频"), time_ms=6000)
    try:
        got_title, streams = dl.list_streams(guid=guid, page_url=page_url)
    except Exception as exc:
        kodi.log("list_streams failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("出错了：%s" % exc, error=True)
        return

    if not streams:
        kodi.notify("出错了：没有能保存的清晰度", error=True)
        return

    title = title or got_title or "央视网视频"
    stream = streams[0]          # list_streams 已经按清晰度从高到低排好
    folder = _download_dir(addon, kodi, dl)
    meta = _meta_for(kodi, guid, page_url, title)
    kodi.log("save: %s（%s）-> %s" % (title, stream.get("label"), folder))
    _save_stream(addon, kodi, dl, stream, title, folder, meta)


def _meta_for(kodi, guid, page_url, title):
    """.nfo 用的元数据：标题、简介、封面、fanart、日期、时长、guid。

    封面/fanart 都直接用央视网的在线地址（不下载图片）。
    """
    from resources.lib import cctv

    meta = {"title": title, "website": page_url or "", "guid": guid,
            "studio": "央视网"}
    try:
        if not guid and page_url:
            guid = cctv.page_guid(page_url)
            meta["guid"] = guid
        if not guid:
            return meta
        info = cctv.video_info(guid, referer=page_url or None)
    except Exception as exc:
        kodi.log("nfo 元数据没取到（只用标题）：%r" % exc, xbmc.LOGWARNING)
        return meta
    image = info.get("image") or ""
    summary = info.get("title") or title
    column = info.get("column") or ""
    if isinstance(column, dict):
        column = column.get("title") or column.get("name") or ""
    if column:
        summary = "%s\n\n栏目：%s" % (summary, column)
    meta.update({
        "title": info.get("title") or title,
        "plot": summary,
        "thumb": image,
        "fanart": image,
        "website": page_url or "",
        "guid": guid,
        "credits": info.get("editer_name") or "",
    })
    when = info.get("f_pgmtime") or ""
    if isinstance(when, str) and len(when) >= 10 and when[:4].isdigit():
        meta["premiered"] = when[:10]
    return meta


def _save_stream(addon, kodi, dl, stream, title, folder, meta=None):
    """把一档清晰度逐片写进文件：不弹进度框，只在结束后提示「已保存：标题」。"""
    dest = dl.unique_path(folder, dl.safe_name(title), stream.get("ext") or ".ts")
    estimate = stream.get("size") or 0
    kodi.log("save %s -> %s" % (stream["label"], dest))

    def callback(done, estimate_bytes, index, total):
        # 后台保存：不打断，不做可取消
        return True

    try:
        if stream.get("h5e"):
            result = dl.download_h5e(stream["url"], dest, callback, estimate_bytes=estimate)
        else:
            result = dl.download_hls(stream["url"], dest, callback, estimate_bytes=estimate)
    except dl.Cancelled:
        kodi.notify("已取消下载，临时文件已删除")
        return False
    except Exception as exc:
        kodi.log("download failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("出错了：%s" % exc, error=True)
        return False

    kodi.log("saved %s（%s）" % (result["path"], dl.human_size(result["bytes"])))
    try:
        from resources.lib import nfo as nfo_mod

        info = dict(meta or {})
        info.setdefault("title", title)
        info.setdefault("runtime", stream.get("duration") or 0)
        nfo_path = nfo_mod.write(result["path"], info)
        if nfo_path:
            kodi.log("nfo written: %s" % nfo_path)
    except Exception as exc:
        kodi.log("写 nfo 失败（不影响文件）：%r" % exc, xbmc.LOGWARNING)

    kodi.notify("已保存：%s" % dl.safe_name(title), time_ms=6000)
    return True


def _dltext(handle, addon, kodi, portal, dl, params):
    """长按文本 →「保存文本」：把报道正文存成 .txt。"""
    url = unquote(params.get("u") or "")
    title = params.get("t") or ""
    if not url:
        kodi.notify("出错了：缺少文章地址", error=True)
        return

    folder = _download_dir(addon, kodi, dl)
    article_title, body = portal.article_text(url)
    if not body:
        kodi.notify("出错了：正文没抓到", error=True)
        return

    title = (title or article_title or "央视网报道").strip()
    dest = dl.unique_path(folder, dl.safe_name(title), ".txt")
    try:
        result = dl.download_article(title, body, url, dest)
    except Exception as exc:
        kodi.log("download_article failed: %r" % exc, xbmc.LOGERROR)
        kodi.notify("出错了：%s" % exc, error=True)
        return

    kodi.log("saved %s（%s）" % (result["path"], dl.human_size(result["bytes"])))
    kodi.notify("已保存：%s" % result["path"], time_ms=6000)
    if xbmcgui.Dialog().yesno(
        "保存完成", "%s\n\n%s\n\n打开所在文件夹吗？" % (dl.safe_name(title), result["path"])
    ):
        kodi.open_folder(folder)


if __name__ == "__main__":
    main()
