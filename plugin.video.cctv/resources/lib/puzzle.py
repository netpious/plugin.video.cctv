# -*- coding: utf-8 -*-
"""央视网登录的拼图滑块验证码。

流程（全部实测过）：
    GET  /openapi/v2/user/captcha/dynamic/puzzle   -> 背景图、拼图块、token、secret_key
    模板匹配算出拼图块在背景图里的 x
    POST /openapi/v2/user/captcha/dynamic/puzzle/check -> captcha_checked_code
    captcha_checked_code 再交给 user/login 当 captcha_pass_code

图片解码、匹配都是纯标准库实现，Kodi 里能直接跑。
"""

import base64
import json
import struct
import time
import zlib

from . import crypt, net

APPID = "dgtGyfXwN9Gcr7ow"
PUZZLE_API = "https://reg.cctv.com/openapi/v2/user/captcha/dynamic/puzzle"
CHECK_API = "https://reg.cctv.com/openapi/v2/user/captcha/dynamic/puzzle/check"
REFERER = "https://user.cctv.com/"
# 模板匹配的色差经验阈值：低于它基本能过，高于它换一张图重来更划算
SCORE_OK = 70.0


def _get_json(url):
    _, text = net.get_text(url, timeout=20, referer=REFERER)
    return json.loads(text)


def _post_json(url, data):
    _, raw, _headers = net.post_form(url, data, timeout=20, referer=REFERER)
    return json.loads(raw.decode("utf-8", "replace"))


# ---------------------------------------------------------------- PNG 解码

def decode_png(data):
    """极简 PNG 解码：8bit、非隔行，支持 RGB/RGBA/灰度/灰度+alpha。

    返回 (宽, 高, RGBA 像素 bytes)。
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    pos = 8
    w = h = None
    bitdepth = colortype = 0
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            w, h, bitdepth, colortype, comp, filt, interlace = struct.unpack(">IIBBBBB", chunk)
            if bitdepth != 8 or interlace != 0:
                raise ValueError("不支持的 PNG（位深 %s / 隔行 %s）" % (bitdepth, interlace))
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
    if not w or not h:
        raise ValueError("PNG 没有 IHDR")

    raw = zlib.decompress(bytes(idat))
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colortype]
    stride = w * channels
    out = bytearray(w * h * 4)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        prev = line
        for x in range(w):
            o = (y * w + x) * 4
            if channels == 4:
                out[o:o + 4] = line[x * 4:x * 4 + 4]
            elif channels == 3:
                out[o:o + 3] = line[x * 3:x * 3 + 3]
                out[o + 3] = 255
            elif channels == 1:
                v = line[x]
                out[o:o + 4] = bytes([v, v, v, 255])
            else:
                g, a = line[x * 2], line[x * 2 + 1]
                out[o:o + 4] = bytes([g, g, g, a])
    return w, h, bytes(out)


def _pix(rgba, w, x, y):
    o = (y * w + x) * 4
    return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]


def _piece_mask(block):
    bw, bh, bp = block
    pts = []
    for y in range(bh):
        row = y * bw * 4
        for x in range(bw):
            o = row + x * 4
            if bp[o + 3] > 128:
                pts.append((x, y))
    return pts


def _grayness_map(origin):
    """背景图每个像素的「灰度」：饱和度越低越像缺口那块灰色剪影（0-255）。"""
    ow, oh, op = origin
    gray = bytearray(ow * oh)
    for y in range(oh):
        base = y * ow * 4
        row = y * ow
        for x in range(ow):
            o = base + x * 4
            r, g, b = op[o], op[o + 1], op[o + 2]
            mx = r if r > g else g
            if b > mx:
                mx = b
            mn = r if r < g else g
            if b < mn:
                mn = b
            gray[row + x] = 255 - (mx - mn)
    return gray


def find_offset(origin, block):
    """找拼图缺口位置，返回 (置信度, 需要拖动的 x, y)。

    关键点：背景图上的缺口是一块**灰色剪影**（画面内容被抹掉了），
    而拼图块里是真实画面内容 —— 所以不能拿块里的颜色去比背景，
    要用「块的不透明形状 盖住的区域 比周围更灰」来判断。
    """
    ow, oh, op = origin
    bw, bh, bp = block
    mask = _piece_mask(block)
    if not mask:
        return None
    xs = min(p[0] for p in mask)
    xe = max(p[0] for p in mask)

    gray = _grayness_map(origin)

    # 形状外扩一圈，用来对比「缺口以外」的灰度
    if len(mask) > 900:
        use_mask = mask[::2]
    else:
        use_mask = mask
    mask_set = set(mask)
    ring = set()
    for (px, py) in mask:
        for dy in (-3, -2, 2, 3):
            for dx in range(-3, 4):
                q = (px + dx, py + dy)
                if q not in mask_set and 0 <= q[0] < bw and 0 <= q[1] < bh:
                    ring.add(q)
        for dx in (-3, -2, 2, 3):
            for dy in range(-2, 3):
                q = (px + dx, py + dy)
                if q not in mask_set and 0 <= q[0] < bw and 0 <= q[1] < bh:
                    ring.add(q)
    ring = list(ring)[::2]

    best = None
    for d in range(0, max(1, ow - xe)):
        inside = 0
        for (px, py) in use_mask:
            X = px + d
            if X >= ow:
                continue
            inside += gray[py * ow + X]
        outside = 0
        cnt = 0
        for (px, py) in ring:
            X = px + d
            if X >= ow:
                continue
            outside += gray[py * ow + X]
            cnt += 1
        if not cnt:
            continue
        score = inside / float(len(use_mask)) - outside / float(cnt)
        if best is None or score > best[0]:
            best = (score, d - xs, 0)

    if best is not None and best[0] > 0.5:
        return best

    # 兜底：万一这张图缺口不够灰（置信度太低），退回按画面内容做匹配
    return _find_offset_by_content(origin, block)


def _find_offset_by_content(origin, block):
    """按画面内容匹配（缺口没被抹灰时的兜底）。块图是一条竖条，纵向不搜。"""
    ow, oh, op = origin
    bw, bh, bp = block
    pts = []
    for y in range(bh):
        row = y * bw * 4
        for x in range(bw):
            o = row + x * 4
            if bp[o + 3] > 128:
                pts.append((x, y, bp[o], bp[o + 1], bp[o + 2]))
    if not pts:
        return None
    xs = min(p[0] for p in pts)
    max_px = max(p[0] for p in pts)
    sample = pts[::2] if len(pts) > 500 else pts
    best = None
    for d in range(0, max(0, ow - 1 - max_px) + 1):
        total = 0
        cnt = 0
        for (px, py, r, g, b) in sample:
            X = px + d
            if X >= ow or py >= oh:
                continue
            o = (py * ow + X) * 4
            total += abs(op[o] - r) + abs(op[o + 1] - g) + abs(op[o + 2] - b)
            cnt += 1
        if not cnt:
            continue
        score = total / float(cnt)
        if best is None or score < best[0]:
            best = (score, d, 0)
    return best


def solve_once():
    """解一次滑块。返回 (captcha_checked_code, info) / (None, info)。"""
    ts = str(int(time.time()))
    resp = _get_json("%s?timestamp=%s&appid=%s" % (PUZZLE_API, ts, APPID))
    if str(resp.get("code")) != "0":
        return None, {"stage": "puzzle", "resp": resp}
    d = resp.get("data") or {}
    ow, oh, op = decode_png(base64.b64decode(d["origin_image_data"]))
    bw, bh, bp = decode_png(base64.b64decode(d["block_image_data"]))
    m = find_offset((ow, oh, op), (bw, bh, bp))
    if not m:
        return None, {"stage": "match", "resp": "no opaque block"}
    score, bx, by = m

    # 页面里 a = 310 * moveBlockLeft / 面板显示宽度；原图天然就是 310 宽，所以直接用像素 x
    x = float(bx)
    point = json.dumps({"x": x, "y": 5}, separators=(",", ":"))
    data = {
        "aes_point": crypt.encrypt_aes_point(point, d["secret_key"]),
        "token": d["token"],
        "appid": APPID,
    }
    res = _post_json("%s?timestamp=%s" % (CHECK_API, str(int(time.time()))), data)
    info = {"stage": "check", "score": round(score, 1), "x": x, "resp": res}
    if str(res.get("code")) == "0":
        return (res.get("data") or {}).get("captcha_checked_code"), info
    return None, info


def solve(tries=6):
    """最多试 tries 次，每次失败都换一张新题（服务端对错误位置有容错区间）。"""
    last = {}
    for _ in range(max(1, tries)):
        code, info = solve_once()
        last = info
        if code:
            return code, info
        time.sleep(0.3)
    return None, last
