# -*- coding: utf-8 -*-
"""Kodi 界面层：把内容包装成标准目录项。

遵循 Kodi 的目录约定：
  * 每个列表项都是 ListItem + isFolder
  * 可播放项用 IsPlayable + setResolvedUrl
  * 列表结束必须调用 endOfDirectory，否则界面会一直转圈
"""

import sys
from urllib.parse import quote, urlencode

import xbmc
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


def download_context(base, mode="download", label="下载…", **params):
    """长按视频/文本时弹出的「下载」菜单项。"""
    return [(label, build_url(base, mode=mode, **params))]


def end_directory(handle, succeeded=True):
    xbmcplugin.endOfDirectory(handle, succeeded=succeeded, cacheToDisc=False)


def apply_context(item, context):
    """长按（遥控器 OK 长按 / 右键 / 键盘 C）弹出的菜单项。

    context 是 [(标签, 插件地址)]；地址必须是 plugin:// 完整地址，
    Kodi 只接受 RunPlugin(...) 这种内建命令包裹的形式。
    """
    if not context:
        return
    items = [(label, "RunPlugin(%s)" % url) for label, url in context]
    try:
        item.addContextMenuItems(items)
    except Exception as exc:
        log("addContextMenuItems failed: %r" % exc, xbmc.LOGWARNING)


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
    if plot:
        info["plot"] = plot
    item.setInfo("video", info)
    apply_context(item, context)
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
    apply_context(item, context)
    xbmcplugin.addDirectoryItem(handle, url, item, isFolder=False)


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
