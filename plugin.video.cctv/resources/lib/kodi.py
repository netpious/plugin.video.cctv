# -*- coding: utf-8 -*-
"""Kodi 界面层：把内容包装成标准目录项。

遵循 Kodi 的目录约定：
  * 每个列表项都是 ListItem + isFolder
  * 可播放项用 IsPlayable + setResolvedUrl
  * 列表结束必须调用 endOfDirectory，否则界面会一直转圈
"""

import re
import sys
from urllib.parse import quote, unquote, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

ADDON_ID = "plugin.video.cctv"
LOG_PREFIX = "[cctv] "


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(LOG_PREFIX + str(msg), level)


def notify(message, time_ms=4000, error=False):
    """弹一条通知。

    Kodi 的 Python 绑定（SWIG）对参数个数卡得很死，传多了直接抛
    TypeError('function takes at most N arguments')，而且我们这里一直是
    except 吞掉 —— 于是「通知没出来」比「通知内容不对」更难查。
    所以按 4 → 3 → 2 个参数依次退让，任何一步成功就够。
    """
    icon = xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO
    dialog = xbmcgui.Dialog()
    for args in (
        ("央视网", message, icon, time_ms),
        ("央视网", message, icon),
        ("央视网", message),
    ):
        try:
            dialog.notification(*args)
            return
        except TypeError:
            continue
        except Exception as exc:
            log("notification failed: %r" % exc, xbmc.LOGWARNING)
            return
    log("notification dropped: %s" % message, xbmc.LOGWARNING)


def progress_open(heading, message=""):
    """建一个进度框（create 最多两个参数）。"""
    progress = xbmcgui.DialogProgress()
    try:
        if message:
            progress.create(heading, message)
        else:
            progress.create(heading)
    except TypeError:
        try:
            progress.create(heading)
        except Exception as exc:
            log("progress.create failed: %r" % exc, xbmc.LOGWARNING)
    except Exception as exc:
        log("progress.create failed: %r" % exc, xbmc.LOGWARNING)
    return progress


def progress_update(progress, percent=0, line1="", line2=""):
    """更新进度框，只给两个参数：percent + 一行文字。

    这就是 v0.4.1「下载必失败」的原因：`DialogProgress.update(percent, 行1, 行2)`
    在 Kodi 21 的 Python 侧只接受 2 个参数，传第三个会抛
    TypeError('function takes at most 2 arguments (3 given)')，
    而这行代码在每一片分片上都会执行，于是下完第一片就整个下载失败。
    进度条出问题不该中断下载，所以这里把异常全吞掉。
    """
    if not progress:
        return
    try:
        percent = max(0, min(100, int(percent)))
    except (TypeError, ValueError):
        percent = 0
    text = " · ".join([t for t in (line1, line2) if t])
    try:
        progress.update(percent, text)
    except TypeError:
        try:
            progress.update(percent)
        except Exception:
            pass
    except Exception:
        pass


def progress_canceled(progress):
    """用户点了取消吗？读不出来就当没取消，别让下载莫名其妙地停。"""
    if not progress:
        return False
    try:
        return bool(progress.iscanceled())
    except Exception:
        return False


def progress_close(progress):
    if not progress:
        return
    try:
        progress.close()
    except Exception:
        pass


def _raw_setting(addon, key):
    """getSettingString 对所有类型都安全，不会像 getSettingInt/getSettingBool 那样
    在设置定义解析失败时抛 TypeError('Invalid setting type')。"""
    try:
        return (addon.getSettingString(key) or "").strip()
    except Exception:
        return ""


def setting_int(addon, key, default):
    raw = _raw_setting(addon, key)
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def setting_bool(addon, key, default=False):
    raw = _raw_setting(addon, key).lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    return default


def setting_str(addon, key, default=""):
    raw = _raw_setting(addon, key)
    return raw or default


def set_setting(addon, key, value):
    try:
        addon.setSetting(key, str(value))
        return True
    except Exception as exc:
        log("setSetting(%s) failed: %r" % (key, exc), xbmc.LOGWARNING)
        return False


def path_of(path):
    """special://… 翻译成真实路径（Windows 下再换成反斜杠，os 模块更好用）。"""
    if not path:
        return ""
    try:
        return xbmcvfs.translatePath(path)
    except Exception:
        return path


def play_file(path, title=""):
    """下载完成后直接播放本地文件。"""
    item = xbmcgui.ListItem(path=path, offscreen=True)
    if title:
        item.setInfo("video", {"title": title})
    try:
        xbmc.Player().play(path, item)
        return True
    except Exception as exc:
        log("play_file failed: %r" % exc, xbmc.LOGERROR)
        return False


def open_folder(path):
    """用 Kodi 的文件管理器打开目录，方便找到下载的文件。"""
    try:
        xbmc.executebuiltin('ActivateWindow(FileManager,"%s")' % path)
        return True
    except Exception:
        return False


def pick_folder(default="", heading="选择下载目录"):
    """弹系统的目录选择框。

    Kodi 的 browse() 各版本 type 语义有差异（0 文件 / 1 驱动器 / 2 目录），
    先用 2，函数签名不被接受（TypeError）再退到更短的调用方式。
    注意：用户点「取消」时返回空字符串，这时必须直接收手 —— 否则会连着
    又弹两次选择框（v0.4.1 就是这样，取消一次要关三个框）。
    """
    dialog = xbmcgui.Dialog()
    for kind in (2, 1, 0):
        for args in (
            (kind, heading, "files", "", False, True, default or ""),
            (kind, heading, "files", "", False, True),
        ):
            try:
                return dialog.browse(*args) or ""
            except TypeError:
                continue          # 这个版本的 browse 不收这么多参数，试短一点
            except Exception as exc:
                log("browse failed: %r" % exc, xbmc.LOGWARNING)
                return ""
    return ""


def build_url(base, **params):
    clean = {k: v for k, v in params.items() if v is not None}
    if not clean:
        return base
    return base + "?" + urlencode(clean)


def download_context(base, mode="download", label="保存", **params):
    """长按视频/文本时弹出的「下载」菜单项。"""
    return [(label, build_url(base, mode=mode, **params))]


def end_directory(handle, succeeded=True):
    xbmcplugin.endOfDirectory(handle, succeeded=succeeded, cacheToDisc=False)


def item_context(base, context):
    """条目自己的动作 + 全局动作，合成一个列表。

    2.3.0 beta5：以前分两次 addContextMenuItems（第二次 replaceItems=False），
    在很多 Kodi 版本上第二次调用会把第一次的项顶掉，结果「保存」永远不显示。
    现在只调用一次，把两组菜单拼在一起。
    """
    items = [(label, "RunPlugin(%s)" % url) for label, url in (context or [])]
    items.extend((label, cmd) for label, cmd in global_context(base))
    return items


def apply_context(item, base, context):
    if not context:
        context = []
    try:
        item.addContextMenuItems(item_context(base, context))
    except Exception as exc:
        log("addContextMenuItems failed: %r" % exc, xbmc.LOGWARNING)


def logo_path(name):
    """插件自带的台标（resources/logos/<name>），没有就返回空串。"""
    if not name:
        return ""
    try:
        import os

        base = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo("path"))
        path = os.path.join(base, "resources", "logos", name)
        return path if os.path.isfile(path) else ""
    except Exception as exc:
        log("logo_path failed: %r" % exc, xbmc.LOGWARNING)
        return ""


def global_context(base):
    """每个条目「选项」菜单里都带的全局动作。

    用 Container.Update 换掉当前容器的内容 —— 它在插件目录里最稳。
    2.3.0 beta4：搜索视频/文章/音频合成了一个「搜索」，进去再选类型。
    """
    return [
        ("返回到根目录", "Container.Update(%s?mode=root,replace)" % base),
        ("搜索", "Container.Update(%s?mode=search_hub,replace)" % base),
        ("刷新", "Container.Refresh"),
    ]


def add_folder(handle, base, label, mode, thumb=None, fanart=None, plot="", context=None, **extra):
    url = build_url(base, mode=mode, **extra)
    item = xbmcgui.ListItem(label=label, offscreen=True)
    art = {}
    if thumb:
        art["thumb"] = thumb
        art["poster"] = thumb
    if fanart:
        art["fanart"] = fanart
    if art:
        item.setArt(art)
    info = {"title": label}
    # 没写简介时，左侧信息直接显示这个条目的名字
    info["plot"] = plot or label
    item.setInfo("video", info)
    apply_context(item, base, context)
    xbmcplugin.addDirectoryItem(handle, url, item, isFolder=True)


def _seconds(text):
    """把 "00:05:30" / "5:30" / "330" 统一成秒。"""
    text = (text or "").strip()
    if not text:
        return 0
    if ":" in text:
        total = 0
        for part in text.split(":"):
            try:
                total = total * 60 + int(float(part))
            except ValueError:
                return 0
        return total
    try:
        return int(float(text))
    except ValueError:
        return 0


def _collect_actions(base, label, extra):
    """长按菜单里的收藏项（只在登录后出现）。

    source_id 取视频页/专辑页/文章页地址里的 ID 段；source_type：视频和音频专辑都是 1，
    文章按网页收藏页里的取值用 3（这一项没有登录态无法实测，先按网页 JS 里的值来）。
    """
    try:
        from . import account
    except Exception:
        return []
    try:
        if not account.is_logged_in():
            return []
    except Exception:
        return []

    url = unquote(extra.get("u") or "")
    source_id, source_type = "", "1"
    for prefix, stype in (("VIDE", "1"), ("VIDA", "1"), ("ARTI", "3")):
        m = re.search(r"/(%s[0-9A-Za-z]+)\.shtml" % prefix, url)
        if m:
            source_id, source_type = m.group(1), stype
            break
    if not source_id:
        return []
    title = extra.get("t") or label
    common = {"sid": source_id, "st": source_type, "t": title, "u": quote(url, safe="")}
    return [
        ("收藏到央视网", build_url(base, mode="collect", act="add", **common)),
        ("取消收藏", build_url(base, mode="collect", act="del", **common)),
    ]


def add_playable(handle, base, label, mode, thumb=None, fanart=None, plot="", date="", duration="",
                 context=None, **extra):
    url = build_url(base, mode=mode, **extra)
    item = xbmcgui.ListItem(label=label, offscreen=True)
    art = {}
    if thumb:
        art["thumb"] = thumb
        art["poster"] = thumb
    if fanart:
        art["fanart"] = fanart
    if art:
        item.setArt(art)
    info = {"title": label}
    if plot:
        info["plot"] = plot
    if date:
        info["date"] = date
        try:
            info["year"] = int(date[:4])
        except ValueError:
            pass
    secs = _seconds(duration)
    if secs:
        info["duration"] = secs
    item.setInfo("video", info)
    item.setProperty("IsPlayable", "true")
    merged = list(context or [])
    merged.extend(_collect_actions(base, label, extra))
    apply_context(item, base, merged)
    xbmcplugin.addDirectoryItem(handle, url, item, isFolder=False)


def add_action(handle, base, label, mode, thumb=None, fanart=None, plot="", context=None, **extra):
    """「点一下执行动作」的条目（登录、退出这类）。

    Kodi 的目录项只有可播放项/文件夹能在点的时候回调插件；
    这里用 IsPlayable 顶一下，处理函数最后必须 setResolvedUrl(False) 把播放取消掉，
    否则会弹「播放失败」。它不会进文件夹 —— 用户看到的仍是同一个列表。
    """
    url = build_url(base, mode=mode, **extra)
    item = xbmcgui.ListItem(label=label, offscreen=True)
    art = {}
    if thumb:
        art["thumb"] = thumb
        art["poster"] = thumb
    if fanart:
        art["fanart"] = fanart
    if art:
        item.setArt(art)
    info = {"title": label}
    if plot:
        info["plot"] = plot
    item.setInfo("video", info)
    item.setProperty("IsPlayable", "true")
    apply_context(item, base, context)
    xbmcplugin.addDirectoryItem(handle, url, item, isFolder=False)


def add_local(handle, path, label, thumb=None, plot="", duration="", base=""):
    """本地文件条目（「保存」栏目里列已下载的视频用）。"""
    item = xbmcgui.ListItem(label=label, path=path, offscreen=True)
    art = {}
    if thumb:
        art["thumb"] = thumb
        art["poster"] = thumb
    if art:
        item.setArt(art)
    info = {"title": label}
    if plot:
        info["plot"] = plot
    secs = _seconds(duration)
    if secs:
        info["duration"] = secs
    item.setInfo("video", info)
    item.setProperty("IsPlayable", "true")
    # 本地文件也挂上全局长按菜单（返回到根目录 / 搜索 / 刷新）
    try:
        if base:
            apply_context(item, base, [])
    except Exception as exc:
        log("add_local context failed: %r" % exc, xbmc.LOGWARNING)
    xbmcplugin.addDirectoryItem(handle, path, item, isFolder=False)


def cancel_playback(handle):
    """可播放条目没有真正播放时取消掉，避免 Kodi 报「播放失败」。"""
    try:
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
    except Exception as exc:
        log("setResolvedUrl(False) failed: %r" % exc, xbmc.LOGWARNING)


def play(handle, stream_url, title="", thumb="", is_live=False):
    """交给 Kodi 播放器。HLS 由 Kodi 原生支持，不需要 inputstream。

    stream_url 可以带 Kodi 的 "|头" 后缀（例如 |User-Agent=…&Referer=…），
    Kodi 会把这些头一起发给服务器。
    """
    item = xbmcgui.ListItem(path=stream_url, offscreen=True)
    if title:
        item.setInfo("video", {"title": title})
    if thumb:
        item.setArt({"thumb": thumb})
    item.setProperty("IsPlayable", "true")
    if is_live:
        item.setMimeType("application/vnd.apple.mpegurl")
    xbmcplugin.setResolvedUrl(handle, True, item)


def text_viewer(heading, text):
    """Kodi 没有网页视图，报道正文用内置文本阅读窗展示（可上下翻页）。"""
    try:
        xbmcgui.Dialog().textviewer(heading or "报道", text or "")
    except Exception:
        log("textviewer failed", xbmc.LOGERROR)


def fail(handle, message):
    """解析失败时用占位项把界面收干净，避免 Kodi 一直停在加载状态。"""
    log("FAIL: %s" % message, xbmc.LOGERROR)
    notify(message, error=True)
    xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
