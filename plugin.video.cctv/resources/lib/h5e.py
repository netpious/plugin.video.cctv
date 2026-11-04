# -*- coding: utf-8 -*-
"""CCTV 点播的 H5E 解密。

背景：央视网点播接口会给三条 HLS 线：
  * hls_url      /asp/hls/  明文，但未登录账号只给到 480x270；
  * hls_h5e_url  /asp/h5e/  档位齐全（720P / 1080P），内容是加密的；
  * hls_enc2_url /asp/enc2/ 同上，另一套封装。
明文线封顶 270P，高画质只在这两条加密线上，所以才需要在这里解。

加密形态：不是 HLS 的 AES-128（清单里没有 EXT-X-KEY），而是**逐个 NAL 的定长网格**，
密钥就藏在 NAL 自己的字节里：
  * type5（IDR）：key = RBSP[5:21]，从第 64 字节起，步长 = 基表[idx] | key[idx]，每次解 8 字节（TEA-16）；
  * type1（P/B）：步长同上但 key = RBSP[1:7]，每次解 4 字节（G 变换，含按头部选择的翻转掩码）；
  * classic：key = RBSP[16:32]，从 32 起固定步长 80；
  * type25 的 NAL 是开关：payload[1] == 1 才开始解，[2:4] = 01 09 用新网格、01 06 用 classic。
网格位置都按 RBSP 下标算（跳过 00 00 03 里的 03），解完再把多余的 03 去掉，
所以 NAL 会变短，必须重新装回 TS 包（用自适应字段填充把差额吃掉），
音轨、PCR、连续计数器都不动。
"""

import sys

_BASE = (160, 192, 224, 256, 288, 320)
_DELTA = 0x9E3779B9
_MASK32 = 0xFFFFFFFF
_EPB = b"\x00\x00\x03"
_MIN_TYPE1 = 129
_GUARD = 17
_MAX_AF = 182

_B_STEP = (0, 0, 1, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0)


def log(msg, level=None):
    try:
        import xbmc

        xbmc.log("[cctv-h5e] " + str(msg), level if level is not None else xbmc.LOGINFO)
    except Exception:
        sys.stderr.write("[cctv-h5e] %s\n" % msg)


# ---------------------------------------------------------------- TEA-16
def _tea_decrypt(block, key):
    v0 = block[0] | (block[1] << 8) | (block[2] << 16) | (block[3] << 24)
    v1 = block[4] | (block[5] << 8) | (block[6] << 16) | (block[7] << 24)
    k0 = key[0] | (key[1] << 8) | (key[2] << 16) | (key[3] << 24)
    k1 = key[4] | (key[5] << 8) | (key[6] << 16) | (key[7] << 24)
    k2 = key[8] | (key[9] << 8) | (key[10] << 16) | (key[11] << 24)
    k3 = key[12] | (key[13] << 8) | (key[14] << 16) | (key[15] << 24)
    total = (_DELTA * 16) & _MASK32
    for _ in range(16):
        v1 = (v1 - ((((v0 << 4) + k2) ^ (v0 + total) ^ ((v0 >> 5) + k3)) & _MASK32)) & _MASK32
        v0 = (v0 - ((((v1 << 4) + k0) ^ (v1 + total) ^ ((v1 >> 5) + k1)) & _MASK32)) & _MASK32
        total = (total - _DELTA) & _MASK32
    return bytes((v0 & 0xFF, (v0 >> 8) & 0xFF, (v0 >> 16) & 0xFF, (v0 >> 24) & 0xFF,
                  v1 & 0xFF, (v1 >> 8) & 0xFF, (v1 >> 16) & 0xFF, (v1 >> 24) & 0xFF))


# ---------------------------------------------------------------- RBSP / EPB
def _epb_positions(nal):
    out = []
    start = 0
    while True:
        i = nal.find(_EPB, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


def _rbsp_map(nal):
    """RBSP 下标 -> EBSP 真实下标。"""
    m = []
    i, n = 0, len(nal)
    while i < n:
        if i + 2 < n and nal[i] == 0 and nal[i + 1] == 0 and nal[i + 2] == 3:
            m.append(i)
            m.append(i + 1)
            i += 3
        else:
            m.append(i)
            i += 1
    return m


def _drop_epb(nal, epbs):
    buf = bytearray(nal)
    for e in reversed(epbs):
        if e + 2 >= len(buf):
            continue
        del buf[e + 2]
    return bytes(buf)


# ---------------------------------------------------------------- 步长
def _stride_of(key6):
    le = key6[0] | (key6[1] << 8) | (key6[2] << 16) | (key6[3] << 24)
    idx = le % 6
    return _BASE[idx] | key6[idx]


def _type5_stride(nal):
    if len(nal) < 11:
        return 0
    if _EPB in nal:
        r2e = _rbsp_map(nal)
        if len(r2e) < 11:
            return 0
        key6 = bytes(nal[r2e[5 + b]] for b in range(6))
    else:
        key6 = nal[5:11]
    return _stride_of(key6)


def _type1_stride(nal):
    if len(nal) < 7:
        return 0
    if _EPB in nal:
        r2e = _rbsp_map(nal)
        if len(r2e) < 7:
            return 0
        key6 = bytes(nal[r2e[1 + b]] for b in range(6))
    else:
        key6 = nal[1:7]
    return _stride_of(key6)


# ---------------------------------------------------------------- type1 的 G 变换
def _fbit(w):
    w0 = w & 1
    w8 = (w >> 8) & 1
    w15 = (w >> 15) & 1
    w19 = (w >> 19) & 1
    w25 = (w >> 25) & 1
    w30 = (w >> 30) & 1
    w31 = (w >> 31) & 1
    t = w0 | w8
    return (w31 ^ w15 ^ t ^ (w8 & w19) ^ (w25 & (w0 ^ w19))
            ^ (w0 & (1 ^ w8) & w30) ^ ((1 ^ w0) & w19 & w30)
            ^ (w25 & w30 & (w8 ^ w19))) & 1


def _flip_mask(hdr):
    b0, b1, b2 = hdr[0], hdr[1], hdr[2]
    m = 0
    if b0 == 0x01 and b1 == 0xA8:
        if (b2 >> 7) & 1: m |= 1 << 0
        if (b2 >> 6) & 1: m |= 1 << 1
        if not (b2 >> 5) & 1: m |= 1 << 2
        if (b2 >> 4) & 1: m |= 1 << 3
        if (b2 >> 3) & 1: m |= 1 << 4
        if (b2 >> 1) & 1: m |= 1 << 6
        if (b2 >> 0) & 1: m |= 1 << 7
        m |= 1 << 9
        m |= 1 << 12
        return m
    if b0 == 0x61:
        if (b2 >> 1) & 1: m |= 1 << 0
        if (b2 >> 0) & 1: m |= 1 << 1
        if not (b2 >> 5) & 1: m |= 1 << 2
        if (b2 >> 3) & 1: m |= 1 << 4
        if (b2 >> 2) & 1: m |= 1 << 5
        if (b2 >> 1) & 1: m |= 1 << 6
        if (b2 >> 0) & 1: m |= 1 << 7
        if (b2 >> 3) & 1: m |= 1 << 14
        if (b2 >> 2) & 1: m |= 1 << 15
        return m
    if (b0 & 0x1F) == 1 and (b1 & 0xF0) == 0x90:
        t = (b1 >> 2) & 1
        if (b2 >> 7) & 1: m |= 1 << 0
        if (b2 >> 6) & 1: m |= 1 << 1
        s2 = ((b0 & 1) ^ ((b2 >> 5) & 1)) if t else (1 ^ ((b2 >> 5) & 1))
        if s2: m |= 1 << 2
        if (b2 >> 4) & 1: m |= 1 << 3
        if (b2 >> 3) & 1: m |= 1 << 4
        if (b2 >> 2) & 1: m |= 1 << 5
        if (b2 >> 1) & 1: m |= 1 << 6
        if (b2 >> 0) & 1: m |= 1 << 7
        if t:
            if b0 & 1:
                for s in (9, 10, 11, 12, 14):
                    m |= 1 << s
            m |= 1 << 13
        else:
            for s in (9, 10, 11, 12, 14):
                m |= 1 << s
        if b1 & 1: m |= 1 << 15
        return m
    if b0 == 0x41 and b1 == 0x88:
        if (b2 >> 7) & 1: m |= 1 << 0
        if (b2 >> 6) & 1: m |= 1 << 1
        if not (b2 >> 5) & 1: m |= 1 << 2
        if (b2 >> 4) & 1: m |= 1 << 3
        if (b2 >> 3) & 1: m |= 1 << 4
        if (b2 >> 2) & 1: m |= 1 << 5
        if (b2 >> 1) & 1: m |= 1 << 6
        if (b2 >> 0) & 1: m |= 1 << 7
        m |= 1 << 9
        m |= 1 << 10
        m |= 1 << 12
        if b1 & 1: m |= 1 << 15
        return m
    return 0


def _g_transform(x, y, mask):
    w = (x | (y << 16)) & _MASK32
    p1 = 0
    for s in range(16):
        b = (_fbit(w) ^ ((mask >> s) & 1)) ^ _B_STEP[s]
        p1 |= b << (15 - s)
        w = ((w << 1) | b) & _MASK32
    return p1


def _type1_block(blk, mask):
    x = blk[0] | (blk[1] << 8)
    y = blk[2] | (blk[3] << 8)
    p1 = _g_transform(x, y, mask)
    return bytes((p1 & 0xFF, (p1 >> 8) & 0xFF, x & 0xFF, (x >> 8) & 0xFF))


# ---------------------------------------------------------------- 三种网格
def _rbsp(nal):
    """去掉 00 00 03 里的 03，得到真正的 RBSP。

    这样网格单元的下标就等于真实下标，不用再建逐字节映射表，
    速度能快一个数量级（直播要实时，必须这样）。
    """
    if _EPB in nal:
        return nal.replace(_EPB, b"\x00\x00")
    return nal


def _tea_keys(key):
    return (key[0] | (key[1] << 8) | (key[2] << 16) | (key[3] << 24),
            key[4] | (key[5] << 8) | (key[6] << 16) | (key[7] << 24),
            key[8] | (key[9] << 8) | (key[10] << 16) | (key[11] << 24),
            key[12] | (key[13] << 8) | (key[14] << 16) | (key[15] << 24))


def _tea8(buf, o, k0, k1, k2, k3):
    """原地解 buf[o:o+8]（TEA-16）。整块赋值比逐字节写快，别改成 8 次下标赋值。"""
    v0 = buf[o] | (buf[o + 1] << 8) | (buf[o + 2] << 16) | (buf[o + 3] << 24)
    v1 = buf[o + 4] | (buf[o + 5] << 8) | (buf[o + 6] << 16) | (buf[o + 7] << 24)
    total = (_DELTA * 16) & _MASK32
    for _ in range(16):
        v1 = (v1 - ((((v0 << 4) + k2) ^ (v0 + total) ^ ((v0 >> 5) + k3)) & _MASK32)) & _MASK32
        v0 = (v0 - ((((v1 << 4) + k0) ^ (v1 + total) ^ ((v1 >> 5) + k1)) & _MASK32)) & _MASK32
        total = (total - _DELTA) & _MASK32
    buf[o:o + 8] = bytes((v0 & 0xFF, (v0 >> 8) & 0xFF, (v0 >> 16) & 0xFF, (v0 >> 24) & 0xFF,
                          v1 & 0xFF, (v1 >> 8) & 0xFF, (v1 >> 16) & 0xFF, (v1 >> 24) & 0xFF))


def _rbsp(nal):
    """去掉 00 00 03 里的 03，得到真正的 RBSP。

    这样网格单元下标就等于真实下标，不必建逐字节映射表，快一个数量级
    （直播是经典布局，覆盖率高，靠这个才够实时）。
    """
    if _EPB in nal:
        return nal.replace(_EPB, b"\x00\x00")
    return nal


def _decrypt_type5(nal, stride):
    """IDR 帧：8 字节 TEA 单元，key 在 RBSP[5:21]。"""
    if len(nal) < 21 or stride < 8:
        return nal
    body = _rbsp(nal)
    if len(body) < 21:
        return nal
    k0, k1, k2, k3 = _tea_keys(body[5:21])
    buf = bytearray(body)
    cells = 0
    o = 64
    end = len(buf) - 8
    while o <= end and o + _GUARD <= len(buf):
        _tea8(buf, o, k0, k1, k2, k3)
        cells += 1
        o += stride
    return bytes(buf) if cells else nal


def _decrypt_type1(nal, stride, start=64, guard=_GUARD):
    """P/B 帧：4 字节 G 变换单元。"""
    if stride < 4 or len(nal) < _MIN_TYPE1:
        return nal
    body = _rbsp(nal)
    if len(body) < _MIN_TYPE1:
        return nal
    mask = _flip_mask(body[:3])
    buf = bytearray(body)
    cells = 0
    o = start
    while o + guard <= len(buf) and o + 4 <= len(buf):
        buf[o:o + 4] = _type1_block(buf[o:o + 4], mask)
        cells += 1
        o += stride
    return bytes(buf) if cells else nal


def _decrypt_classic(nal):
    """老布局（直播就是这套）：key 在 RBSP[16:32]，从 32 起、步长 80、每格 8 字节。"""
    if len(nal) < _MIN_TYPE1:
        return nal
    body = _rbsp(nal)
    if len(body) < 112:
        return nal
    k0, k1, k2, k3 = _tea_keys(body[16:32])
    buf = bytearray(body)
    cells = 0
    o = 32
    end = len(buf) - 8
    while o <= end:
        _tea8(buf, o, k0, k1, k2, k3)
        cells += 1
        o += 80
    return bytes(buf) if cells else nal

class Decoder(object):
    """跨 NAL 保持开关状态（type25 随时可能切换布局）。

    直播（cdrm）没有 type25 开关，全程走 classic 网格，
    所以 live=True 时直接强制 classic，不再看开关。
    """

    def __init__(self, live=False):
        self.live = live
        self.new_mode = False
        self.should = bool(live)
        self.nals = 0
        self.slices = 0

    def feed(self, nal):
        if not nal:
            return nal
        ntype = nal[0] & 0x1F
        self.nals += 1
        if ntype == 25 and not self.live:
            self.should = len(nal) >= 2 and nal[1] == 1
            if len(nal) >= 4 and nal[2] == 0x01:
                if nal[3] == 0x09:
                    self.new_mode = True
                elif nal[3] == 0x06:
                    self.new_mode = False
            return nal
        if ntype not in (1, 5) or not self.should:
            return nal
        if self.live or not self.new_mode:
            self.slices += 1
            return _decrypt_classic(nal)
        if ntype == 5:
            s = _type5_stride(nal)
            if s < 8:
                return nal
            self.slices += 1
            return _decrypt_type5(nal, s)
        if len(nal) < _MIN_TYPE1:
            return nal
        self.slices += 1
        return _decrypt_type1(nal, _type1_stride(nal) or 511)


# ---------------------------------------------------------------- ES / NAL
def _split_nals(es):
    """按起始码切 NAL：返回 [(起始码, NAL)]。"""
    marks = []
    n = len(es)
    pos = es.find(b"\x00\x00\x01", 0)
    while pos >= 0:
        if pos > 0 and es[pos - 1] == 0:
            marks.append((pos - 1, 4))
        else:
            marks.append((pos, 3))
        pos = es.find(b"\x00\x00\x01", pos + 3)
    out = []
    for idx, (start, sc) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else n
        out.append((es[start:start + sc], es[start + sc:end]))
    return out


def decrypt_es(es, decoder=None):
    """把一整段视频 ES 解密，返回 (新 ES, decoder)。"""
    dec = decoder or Decoder()
    out = bytearray()
    for sc, nal in _split_nals(es):
        out += sc + dec.feed(nal)
    return bytes(out), dec


# ---------------------------------------------------------------- TS
def _packet_payload(data, off):
    """一个 188 包里的 (payload 起点, 有效长度)；没有负载返回 (0, 0)。"""
    if data[off] != 0x47:
        return 0, 0
    afc = (data[off + 3] >> 4) & 3
    if afc in (0, 2):
        return 0, 0
    pi = 4 if afc == 1 else 5 + data[off + 4]
    if pi >= 188:
        return 0, 0
    return pi, 188 - pi


def video_pid(data):
    """从 PAT/PMT 里找 H.264(0x1B) 或 HEVC(0x24) 的 PID。失败返回 0x100。"""
    pmt_pid = None
    for off in range(0, len(data) - 187, 188):
        if data[off] != 0x47:
            continue
        pid = ((data[off + 1] & 0x1F) << 8) | data[off + 2]
        pusi = data[off + 1] & 0x40
        pi, plen = _packet_payload(data, off)
        if not plen:
            continue
        if pid == 0 and pusi:
            ptr = data[off + pi]
            sec = data[off + pi + 1 + ptr:off + pi + plen]
            if len(sec) < 12:
                continue
            seclen = ((sec[1] & 0x0F) << 8) | sec[2]
            body = sec[8:3 + seclen - 4]
            j = 0
            while j + 4 <= len(body):
                prog = (body[j] << 8) | body[j + 1]
                ppid = ((body[j + 2] & 0x1F) << 8) | body[j + 3]
                if prog != 0:
                    pmt_pid = ppid
                    break
                j += 4
        elif pmt_pid is not None and pid == pmt_pid and pusi:
            ptr = data[off + pi]
            sec = data[off + pi + 1 + ptr:off + pi + plen]
            if len(sec) < 16:
                continue
            pil = ((sec[10] & 0x0F) << 8) | sec[11]
            k = 12 + pil
            while k + 5 <= len(sec) - 4:
                stype = sec[k]
                epid = ((sec[k + 1] & 0x1F) << 8) | sec[k + 2]
                esil = ((sec[k + 3] & 0x0F) << 8) | sec[k + 4]
                if stype in (0x1B, 0x24):
                    return epid
                k += 5 + esil
    return 0x100


def _pcr_of(data, off):
    """取这个包自适应字段里的 PCR（6 字节），没有返回 None。"""
    afc = (data[off + 3] >> 4) & 3
    if afc not in (2, 3):
        return None
    af_len = data[off + 4]
    if af_len < 7:
        return None
    if not (data[off + 5] & 0x10):
        return None
    return bytes(data[off + 6:off + 12])


def _rebuild_packet(buf, off, payload, pcr):
    """整包重写：负载不足 184 时用自适应字段填充，PCR 原样带过去。"""
    pid_hi = buf[off + 1] & 0x1F
    pusi = buf[off + 1] & 0x40
    pid_lo = buf[off + 2]
    cc = buf[off + 3] & 0x0F
    n = len(payload)
    if n >= 184:
        buf[off] = 0x47
        buf[off + 1] = pusi | pid_hi
        buf[off + 2] = pid_lo
        buf[off + 3] = 0x10 | cc
        buf[off + 4:off + 188] = payload[:184]
        return
    use_pcr = pcr is not None and n <= 176
    af_len = 183 - n
    buf[off] = 0x47
    buf[off + 1] = pusi | pid_hi
    buf[off + 2] = pid_lo
    buf[off + 3] = 0x30 | cc
    buf[off + 4] = af_len
    buf[off + 5] = 0x10 if use_pcr else 0x00
    idx = off + 6
    if use_pcr:
        buf[idx:idx + 6] = pcr
        idx += 6
    end_payload = off + 5 + af_len
    if end_payload > idx:
        buf[idx:end_payload] = b"\xff" * (end_payload - idx)
    if n:
        buf[end_payload:end_payload + n] = payload


def decrypt_ts(data, vpid=None, live=False):
    """重建整段 TS：只动视频包的负载，音轨 / PCR / 连续计数器都保持原样。

    返回 (新 TS, 统计)。不像 H5E（一个切片都没解到）时原样返回。
    """
    if len(data) < 376 or data[0] != 0x47:
        return data, {"ok": False, "why": "not-ts"}
    pid = vpid or video_pid(data)
    buf = bytearray(data)
    decoder = Decoder(live=live)

    groups = []
    cur = None
    for off in range(0, len(buf) - 187, 188):
        if buf[off] != 0x47:
            continue
        if (((buf[off + 1] & 0x1F) << 8) | buf[off + 2]) != pid:
            continue
        if buf[off + 1] & 0x40:
            cur = []
            groups.append(cur)
        if cur is None:
            continue
        pi, cap = _packet_payload(buf, off)
        if not cap:
            continue
        cur.append((off, pi, cap))

    for g in groups:
        if not g:
            continue
        pes = b"".join(bytes(buf[o + pi:o + 188]) for (o, pi, cap) in g)
        if len(pes) < 10 or pes[:3] != b"\x00\x00\x01":
            continue
        hdr_len = 9 + pes[8]
        if hdr_len >= len(pes):
            continue
        new_es, _ = decrypt_es(pes[hdr_len:], decoder)
        new_pes = pes[:hdr_len] + new_es

        off = 0
        for (pkt, pi, cap) in g:
            chunk = new_pes[off:off + cap]
            off += len(chunk)
            _rebuild_packet(buf, pkt, chunk, _pcr_of(buf, pkt))
        if off < len(new_pes):
            log("PES 塞不下：还剩 %d 字节" % (len(new_pes) - off))

    stats = {"ok": decoder.slices > 0, "pid": pid,
             "nals": decoder.nals, "slices": decoder.slices}
    return bytes(buf), stats
