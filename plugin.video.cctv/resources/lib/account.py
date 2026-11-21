# -*- coding: utf-8 -*-
"""央视网账号：登录、登录态保存、观看历史 / 书签（收藏）读取。

登录协议是照着网页上的 loginStructure.js / mylogin.js 逆出来的（2026-11-15 实测）：
    GET  /openapi/v2/user/captcha/dynamic/puzzle     拼图验证码（背景图 + 拼图块）
    POST /openapi/v2/user/captcha/dynamic/puzzle/check  -> captcha_checked_code
    POST /openapi/v2/user/login?timestamp=<秒>        -> 成功时下发 cookname/cookpas/uct/userSeqId

请求参数全部客户端加密：
    nonce      16 位数字
    各字段     AES-128-CBC(key=iv=nonce, PKCS#7) 后 base64
    nonce 字段 RSA/PKCS#1 v1.5(nonce) 后 base64
    encryptdata 参数串的 sha512（账号、密码都先转小写）
"""

import http.cookies
import json
import os
import time

from . import crypt, net, puzzle

APPID = "dgtGyfXwN9Gcr7ow"
REFERER = "https://user.cctv.com/"
LOGIN_API = "https://reg.cctv.com/openapi/v2/user/login"
INFO_API = "https://reg.cctv.com/openapi/v2/user/info"
HISTORY_API = "https://i.cctv.com/openapi/v2/video/records"
RECORD_API = "https://i.cctv.com/openapi/v2/video/record"
COLLECT_API = "https://i.cctv.com/openapi/v2/collect/records"

ADDON_DATA = "special://profile/addon_data/plugin.video.cctv/"
ACCOUNT_FILE = "account.json"
HISTORY_CACHE = "history_cache.json"
COLLECT_CACHE = "collect_cache.json"
HISTORY_OFFLINE = "history_offline.json"

ERRORS = {
    "-21018": "账号或密码错误",
    "-23003": "验证码校验失败，请重试",
    "-11005": "接口缺少 appid",
    "-11022": "时间戳不合法（本机时间可能不准）",
    "-10003": "参数错误",
    "10012": "该账号不存在",
}


# ---------------------------------------------------------------- 存取

def _profile_dir():
    path = ADDON_DATA
    try:
        import xbmcvfs

        path = xbmcvfs.translatePath(ADDON_DATA)
    except Exception:
        pass
    try:
        return path
    finally:
        pass


def _path(name):
    folder = _profile_dir()
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
    except OSError:
        pass
    return os.path.join(folder, name)


def _read_json(name, default):
    try:
        with open(_path(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(name, data):
    try:
        with open(_path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def load():
    data = _read_json(ACCOUNT_FILE, {})
    return data if isinstance(data, dict) else {}


def save(data):
    return _write_json(ACCOUNT_FILE, data)


def clear():
    try:
        os.remove(_path(ACCOUNT_FILE))
    except OSError:
        pass


def is_logged_in():
    data = load()
    cookies = data.get("cookies") if isinstance(data, dict) else None
    return bool(cookies)


def label():
    return "已登录" if is_logged_in() else "未登录"


def plot():
    if not is_logged_in():
        return "未登录 · 点击登录央视网账号"
    data = load()
    phone = data.get("phone") or ""
    if len(phone) == 11:
        phone = phone[:3] + "****" + phone[-4:]
    return "已登录（%s）" % phone if phone else "已登录"


def cookie_header():
    data = load()
    cookies = data.get("cookies") or {}
    if not isinstance(cookies, dict):
        return ""
    return "; ".join("%s=%s" % (k, v) for k, v in cookies.items() if v is not None)


# ---------------------------------------------------------------- 登录

def _parse_set_cookie(headers):
    out = {}
    try:
        raw = headers.get_all("Set-Cookie") or []
    except Exception:
        raw = []
    for line in raw:
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(line)
        except Exception:
            continue
        for key, morsel in jar.items():
            out[key] = morsel.value
    return out


def _friendly(resp):
    code = str(resp.get("code"))
    if code in ERRORS:
        return ERRORS[code]
    msg = resp.get("message") or resp.get("msg") or ""
    return "登录失败（%s）%s" % (code, msg)


def _header_of(cookies):
    return "; ".join("%s=%s" % (k, v) for k, v in cookies.items() if v is not None)


def _follow_sso(resp, cookies):
    """登录成功后会返回一组 sso 地址，逐个 POST 才能拿到各域下的会话 cookie。

    网页上的做法是 jsonp 跨域 POST（见 mylogin.js 里 ssoArray 那段），
    这里用普通 POST + 收 Set-Cookie 就够，服务端不校验 jsonp 回调。
    """
    data = resp.get("data") or {}
    sso = data.get("sso") or []
    if isinstance(sso, str):
        sso = [sso]
    for url in sso:
        url = (url or "").strip()
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://reg.cctv.com" + url
        try:
            _, _, headers = net.post_form(
                url, {}, timeout=15, referer=REFERER, headers={"Cookie": _header_of(cookies)})
            cookies.update(_parse_set_cookie(headers))
        except Exception:
            continue
    return cookies


def login(phone, password, remember=True, report=None):
    """手机号 + 密码登录。返回 (成功?, 提示文案)。

    report(stage, text)：可选的进度回调（Kodi 里拿去刷通知）。
    """
    phone = (phone or "").strip()
    password = (password or "").strip()
    if len(phone) != 11 or not phone.isdigit():
        return False, "手机号格式不对"

    if report:
        report("captcha", "正在通过滑块验证…")
    code, info = puzzle.solve(tries=8)
    if not code:
        return False, "滑块验证没通过（%s），请重试" % ((info or {}).get("score") or "未知")

    if report:
        report("login", "正在登录…")
    nonce = crypt.make_nonce(16)
    ts = str(int(time.time()))
    plain = (
        "account=" + phone.lower()
        + "&appid=dgtgyfxwn9gcr7ow"
        + "&captcha_pass_code=" + code.lower()
        + "&captcha_type=1"
        + "&dynamic_captcha_type=puzzle"
        + "&nonce=" + nonce.lower()
        + "&password=" + password.lower()
        + "&rem_login=" + ("1" if remember else "0")
        + "&ts=" + ts
    )
    data = {
        "account": crypt.encryptras(phone, nonce),
        "appid": crypt.encryptras("dgtGyfXwN9Gcr7ow", nonce),
        "captcha_pass_code": crypt.encryptras(code, nonce),
        "captcha_type": crypt.encryptras("1", nonce),
        "dynamic_captcha_type": crypt.encryptras("PUZZLE", nonce),
        "nonce": crypt.encrypt_rsa_nonce(nonce),
        "password": crypt.encryptras(password, nonce),
        "rem_login": crypt.encryptras("1" if remember else "0", nonce),
        "ts": crypt.encryptras(ts, nonce),
        "encryptdata": crypt.sha512_hex(plain),
    }
    headers = {}
    cookie = cookie_header()
    if cookie:
        headers["Cookie"] = cookie
    try:
        _, raw, resp_headers = net.post_form(
            "%s?timestamp=%s" % (LOGIN_API, ts), data, timeout=25, referer=REFERER, headers=headers)
        resp = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        return False, "登录请求失败：%s" % exc

    if str(resp.get("code")) != "0":
        return False, _friendly(resp)

    cookies = _parse_set_cookie(resp_headers)
    cookies = _follow_sso(resp, cookies)
    if not cookies:
        # 兜底：有的账号 sso 只下发在页面响应里，抓一次首页的 Set-Cookie
        for probe in ("https://user.cctv.com/", "https://tv.cctv.com/"):
            try:
                headers = net.fetch_headers(probe, timeout=15, referer=REFERER)
                cookies.update(_parse_set_cookie(headers))
            except Exception:
                continue
    if not cookies:
        return False, "登录接口通过了，但没拿到会话 cookie（站点可能改版）"
    old = load().get("cookies") or {}
    if isinstance(old, dict):
        merged = dict(old)
        merged.update(cookies)
        cookies = merged
    save({
        "cookies": cookies,
        "phone": phone,
        "remember": bool(remember),
        "login_ts": int(time.time()),
    })
    return True, "登录成功"


# ---------------------------------------------------------------- 账号数据

def _authorized_json(url):
    ok = is_logged_in()
    headers = {"Cookie": cookie_header()} if ok else {}
    if ok:
        headers["verifycode"] = (load().get("cookies") or {}).get("verifycode", "")
    _, text = net.get_text(url, timeout=20, referer=REFERER, headers=headers)
    return json.loads(text)


def _ts():
    return str(int(time.time()))


def _normalize_history(row):
    return {
        "title": row.get("video_title") or row.get("title") or "",
        "guid": (row.get("video_id") or "").strip(),
        "url": row.get("video_page_url") or row.get("url") or "",
        "thumb": row.get("video_image") or row.get("image") or "",
        "position": row.get("play_position") or row.get("position") or "",
        "length": row.get("video_length") or "",
        "played_at": row.get("play_time") or "",
        "raw": row,
    }


def _normalize_collect(row):
    return {
        "title": row.get("source_title") or row.get("title") or "",
        "guid": (row.get("source_id") or "").strip(),
        "url": row.get("source_url") or row.get("url") or "",
        "thumb": row.get("source_logo") or row.get("image") or "",
        "type": row.get("source_type"),
        "raw": row,
    }


def history():
    """观看历史。返回 (列表, 是否来自缓存)。"""
    if not is_logged_in():
        return [], False
    url = "%s?timestamp=%s&appid=%s&page_no=1&page_size=100" % (HISTORY_API, _ts(), APPID)
    try:
        resp = _authorized_json(url)
        if str(resp.get("code")) != "0":
            raise ValueError(_friendly(resp))
        rows = (resp.get("data") or {}).get("rows") or []
        items = [_normalize_history(r) for r in rows if isinstance(r, dict)]
        _write_json(HISTORY_CACHE, {"ts": int(time.time()), "rows": items})
        return items, False
    except Exception:
        cached = _read_json(HISTORY_CACHE, {})
        return (cached.get("rows") or []), True


def bookmarks():
    """书签（收藏）。只读账号里的收藏，不做本地收藏。返回 (列表, 是否来自缓存)。"""
    if not is_logged_in():
        return [], False
    url = "%s?timestamp=%s&appid=%s&page_no=1&page_size=100&source_type=1" % (COLLECT_API, _ts(), APPID)
    try:
        resp = _authorized_json(url)
        if str(resp.get("code")) != "0":
            raise ValueError(_friendly(resp))
        rows = (resp.get("data") or {}).get("rows") or []
        items = [_normalize_collect(r) for r in rows if isinstance(r, dict)]
        _write_json(COLLECT_CACHE, {"ts": int(time.time()), "rows": items})
        return items, False
    except Exception:
        cached = _read_json(COLLECT_CACHE, {})
        return (cached.get("rows") or []), True


# ---------------------------------------------------------------- 播放历史

MAX_OFFLINE = 300


def offline_history():
    """未登录时在插件里看过的视频（本地记录），最近的在前。"""
    data = _read_json(HISTORY_OFFLINE, {})
    rows = data.get("rows") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def add_offline(guid, title="", thumb="", page_url="", position=0):
    """记一条本地离线历史：同一个视频只留最新一条，最多 MAX_OFFLINE 条。"""
    guid = (guid or "").strip()
    if not guid:
        return False
    rows = [r for r in offline_history() if (r.get("guid") or "").strip() != guid]
    rows.insert(0, {
        "guid": guid,
        "title": title or "",
        "thumb": thumb or "",
        "url": page_url or "",
        "position": int(position or 0),
        "played_at": int(time.time()),
    })
    return _write_json(HISTORY_OFFLINE, {"rows": rows[:MAX_OFFLINE]})


def report_cloud(guid, page_url="", position=0):
    """把一次播放上报到央视网账号的观看历史（其他设备就能看到）。"""
    if not is_logged_in():
        return False
    guid = (guid or "").strip()
    if not guid:
        return False
    ts = _ts()
    data = {
        "video_id": guid,
        "appid": APPID,
        "timestamp": ts,
        "play_time": ts,
        "video_page_url": page_url or "https://tv.cctv.com/",
        "play_position": str(int(position or 0)),
    }
    try:
        _, raw, _headers = net.post_form(
            RECORD_API, data, timeout=15, referer=REFERER,
            headers={"Cookie": cookie_header()})
        resp = json.loads(raw.decode("utf-8", "replace"))
        return str(resp.get("code")) == "0"
    except Exception:
        return False


def record_playback(guid, title="", thumb="", page_url="", position=0):
    """播放时调用：登录了先同步到云端；没登录、或者登录了但没网/上报失败，就写本地离线历史。"""
    if is_logged_in():
        if report_cloud(guid, page_url=page_url, position=position):
            return True
        # 登录状态但网络不通/上报失败 → 也要留在离线记录里
    return add_offline(guid, title=title, thumb=thumb, page_url=page_url, position=position)


# ---------------------------------------------------------------- 收藏（书签）

COLLECT_STATUS = "https://i.cctv.com/openapi/v2/collect/status"
COLLECT_RECORD = "https://i.cctv.com/openapi/v2/collect/record"
COLLECT_DEL = "https://i.cctv.com/openapi/v2/collect/del/record"
COLLECT_APPID = "5C3nsVIZOzk3o0kc"      # 视频页收藏用的 appid，和阅读历史的不是同一个


def _collect_headers():
    cookies = load().get("cookies") or {}
    headers = {}
    cookie = cookie_header()
    if cookie:
        headers["Cookie"] = cookie
    verify = cookies.get("verifycode") or ""
    if verify:
        headers["verifycode"] = verify
    return headers


def collect_status(source_id, source_type=1):
    """查一个条目是否已收藏。未登录或失败返回 None。"""
    if not is_logged_in():
        return None
    url = "%s?timestamp=%s&appid=%s&source_id=%s&source_type=%s" % (
        COLLECT_STATUS, _ts(), COLLECT_APPID, source_id, source_type)
    try:
        resp = _authorized_json(url)
    except Exception:
        return None
    if str(resp.get("code")) != "0":
        return None
    return resp.get("data")


def collect_add(source_id, source_type=1, title="", url="", thumb=""):
    """收藏到央视网账号。返回 (成功?, 提示)。"""
    if not is_logged_in():
        return False, "请先登录"
    data = {
        "appid": COLLECT_APPID,
        "timestamp": _ts(),
        "source_id": source_id,
        "source_type": str(source_type),
        "source_title": title or "",
        "source_url": url or "",
        "source_logo": thumb or "",
    }
    try:
        _, raw, _headers = net.post_form(
            "%s?timestamp=%s" % (COLLECT_RECORD, _ts()), data, timeout=20,
            referer=REFERER, headers=_collect_headers())
        resp = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        return False, "收藏请求失败：%s" % exc
    if str(resp.get("code")) == "0":
        return True, "已收藏"
    return False, resp.get("message") or ("收藏失败（%s）" % resp.get("code"))


def collect_remove(source_id, source_type=1):
    """从央视网账号取消收藏。返回 (成功?, 提示)。"""
    if not is_logged_in():
        return False, "请先登录"
    data = {
        "appid": COLLECT_APPID,
        "timestamp": _ts(),
        "source_id": source_id,
        "source_type": str(source_type),
    }
    try:
        _, raw, _headers = net.delete_form(
            "%s?timestamp=%s" % (COLLECT_DEL, _ts()), data, timeout=20,
            referer=REFERER, headers=_collect_headers())
        resp = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        return False, "取消收藏失败：%s" % exc
    if str(resp.get("code")) == "0":
        return True, "已取消收藏"
    return False, resp.get("message") or ("取消收藏失败（%s）" % resp.get("code"))
