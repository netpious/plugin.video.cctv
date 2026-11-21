# -*- coding: utf-8 -*-
"""Kodi 的 .nfo 元数据文件：保存视频时一并写一份，Kodi 扫描时能直接显示标题/简介/封面。

格式按 Kodi 的 movie nfo（`<movie>` 根节点）。封面、fanart 一律用央视网的**在线地址**，
不额外下载图片。
"""

import os
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape


def path_for(video_path):
    return os.path.splitext(video_path)[0] + ".nfo"


def _s(value):
    return ("" if value is None else str(value)).strip()


def write(video_path, meta):
    """在视频旁边写同名 .nfo。返回写出的路径（失败返回空串）。"""
    meta = meta or {}
    title = _s(meta.get("title"))
    if not title:
        title = os.path.splitext(os.path.basename(video_path))[0]

    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', "<movie>"]
    lines.append("  <title>%s</title>" % escape(title))
    if meta.get("originaltitle"):
        lines.append("  <originaltitle>%s</originaltitle>" % escape(_s(meta["originaltitle"])))
    if meta.get("plot"):
        lines.append("  <plot>%s</plot>" % escape(_s(meta["plot"])))
        lines.append("  <outline>%s</outline>" % escape(_s(meta["plot"])))
    if meta.get("thumb"):
        lines.append("  <thumb>%s</thumb>" % escape(_s(meta["thumb"])))
        lines.append("  <cover>%s</cover>" % escape(_s(meta["thumb"])))
    if meta.get("fanart"):
        lines.append("  <fanart>%s</fanart>" % escape(_s(meta["fanart"])))
    if meta.get("website"):
        lines.append("  <website>%s</website>" % escape(_s(meta["website"])))
    if meta.get("studio"):
        lines.append("  <studio>%s</studio>" % escape(_s(meta["studio"])))
    if meta.get("credits"):
        lines.append("  <credits>%s</credits>" % escape(_s(meta["credits"])))
    premiered = _s(meta.get("premiered"))
    if premiered:
        lines.append("  <premiered>%s</premiered>" % escape(premiered))
        lines.append("  <year>%s</year>" % escape(premiered[:4]))
    seconds = 0
    try:
        seconds = int(float(meta.get("runtime") or 0))
    except (TypeError, ValueError):
        seconds = 0
    if seconds:
        # Kodi 的 movie nfo 里 runtime 是「分钟」
        lines.append("  <runtime>%d</runtime>" % max(1, int(round(seconds / 60.0))))
    if meta.get("guid"):
        lines.append('  <uniqueid type="cctv" default="true">%s</uniqueid>'
                     % escape(_s(meta["guid"])))
        lines.append("  <id>%s</id>" % escape(_s(meta["guid"])))
    lines.append("  <dateadded>%s</dateadded>" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("</movie>")
    lines.append("")

    out = path_for(video_path)
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return out
    except OSError:
        return ""


def read(video_path):
    """读同名 .nfo 里的关键字段；没有或解析失败返回空字典。"""
    path = path_for(video_path)
    if not os.path.isfile(path):
        return {}
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return {}
    out = {}
    for key in ("title", "plot", "outline", "thumb", "cover", "fanart", "website", "year",
                "premiered", "runtime", "credits", "studio"):
        node = root.find(key)
        if node is not None and (node.text or "").strip():
            out[key] = node.text.strip()
    if "thumb" not in out and "cover" in out:
        out["thumb"] = out["cover"]
    if "plot" not in out and "outline" in out:
        out["plot"] = out["outline"]
    return out
