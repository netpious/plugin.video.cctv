# -*- coding: utf-8 -*-
"""央视网登录参数加密：纯标准库实现的 AES-128 / RSA(PKCS#1 v1.5) / SHA-512。

Kodi 自带的 Python 没有 pycryptodome / cryptography，所以这里自己实现。
只做「加密」这一个方向：登录请求是客户端加密、服务端解密。
"""

import base64
import hashlib
import os

# ------------------------------------------------------------------ AES-128
# 只实现加密方向（ECB / CBC），够登录用。


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a


def _build_sbox():
    """用 GF(2^8) 乘法逆元 + 仿射变换生成 S 盒，避免手抄表出错。"""
    sbox = [0] * 256
    for i in range(256):
        inv = 0
        if i:
            for j in range(1, 256):
                a, b, p = i, j, 0
                while b:
                    if b & 1:
                        p ^= a
                    a = _xtime(a)
                    b >>= 1
                if p == 1:
                    inv = j
                    break
        x = inv
        s = x
        for _ in range(4):
            x = ((x << 1) | (x >> 7)) & 0xFF
            s ^= x
        sbox[i] = s ^ 0x63
    return sbox


SBOX = _build_sbox()
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _expand_key(key):
    if len(key) != 16:
        raise ValueError("AES-128 需要 16 字节密钥")
    w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        temp = list(w[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ temp[j] for j in range(4)])
    return [sum(w[i * 4:i * 4 + 4], []) for i in range(11)]


def _encrypt_block(block, round_keys):
    s = [block[i] ^ round_keys[0][i] for i in range(16)]
    for rnd in range(1, 11):
        s = [SBOX[s[i]] for i in range(16)]
        s = [s[0], s[5], s[10], s[15],
             s[4], s[9], s[14], s[3],
             s[8], s[13], s[2], s[7],
             s[12], s[1], s[6], s[11]]
        if rnd != 10:
            out = [0] * 16
            for c in range(4):
                a0, a1, a2, a3 = s[c * 4:c * 4 + 4]
                out[c * 4 + 0] = _xtime(a0) ^ (_xtime(a1) ^ a1) ^ a2 ^ a3
                out[c * 4 + 1] = a0 ^ _xtime(a1) ^ (_xtime(a2) ^ a2) ^ a3
                out[c * 4 + 2] = a0 ^ a1 ^ _xtime(a2) ^ (_xtime(a3) ^ a3)
                out[c * 4 + 3] = (_xtime(a0) ^ a0) ^ a1 ^ a2 ^ _xtime(a3)
            s = out
        s = [s[i] ^ round_keys[rnd][i] for i in range(16)]
    return bytes(s)


def _pad(data):
    n = 16 - len(data) % 16
    return data + bytes([n]) * n


def aes_cbc_encrypt(plain, key, iv=None):
    """AES-128-CBC + PKCS#7。页面里 key 和 iv 都是同一个 nonce。"""
    iv = iv if iv is not None else key
    if isinstance(plain, str):
        plain = plain.encode("utf-8")
    data = _pad(plain)
    rk = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        enc = _encrypt_block(block, rk)
        out += enc
        prev = enc
    return bytes(out)


def aes_ecb_encrypt(plain, key):
    if isinstance(plain, str):
        plain = plain.encode("utf-8")
    data = _pad(plain)
    rk = _expand_key(key)
    return b"".join(_encrypt_block(data[i:i + 16], rk) for i in range(0, len(data), 16))


# ------------------------------------------------------------------ DER / RSA


def _der_read(buf, pos):
    tag = buf[pos]
    pos += 1
    length = buf[pos]
    pos += 1
    if length & 0x80:
        nbytes = length & 0x7F
        length = int.from_bytes(buf[pos:pos + nbytes], "big")
        pos += nbytes
    return tag, pos, length


def rsa_public_key_from_spki(b64_spki):
    """SubjectPublicKeyInfo(base64) -> (n, e)。"""
    der = base64.b64decode(b64_spki)
    tag, pos, length = _der_read(der, 0)
    if tag != 0x30:
        raise ValueError("不是 SEQUENCE")
    tag, p1, l1 = _der_read(der, pos)          # AlgorithmIdentifier
    pos2 = p1 + l1
    tag, p2, l2 = _der_read(der, pos2)          # BIT STRING
    if tag != 0x03 or der[p2] != 0x00:
        raise ValueError("BIT STRING 不对")
    inner = p2 + 1
    tag, p3, l3 = _der_read(der, inner)         # RSAPublicKey
    tag, pn, ln = _der_read(der, p3)
    n = int.from_bytes(der[pn:pn + ln], "big")
    tag, pe, le = _der_read(der, pn + ln)
    e = int.from_bytes(der[pe:pe + le], "big")
    return n, e


def rsa_encrypt_pkcs1v15(message, n, e):
    """PKCS#1 v1.5 加密，和 JSEncrypt 的行为一致。"""
    if isinstance(message, str):
        message = message.encode("utf-8")
    k = (n.bit_length() + 7) // 8
    ps_len = k - 3 - len(message)
    if ps_len < 8:
        raise ValueError("明文太长")
    ps = bytearray()
    while len(ps) < ps_len:
        b = os.urandom(1)[0]
        if b:
            ps.append(b)
    em = b"\x00\x02" + bytes(ps) + b"\x00" + message
    c = pow(int.from_bytes(em, "big"), e, n)
    return c.to_bytes(k, "big")


def b64e(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def sha512_hex(text):
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha512(text).hexdigest()


# ------------------------------------------------------------------ 央视网登录用

PUBLIC_KEY = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCfZ2v+re+b8CUFkL5InZvpwe1BEOhzpSqWNiKTGnDnohYXSpVQbIQGnoIiL4mmqdyog+WwnK99Yc8fCUmXf5D/nFXtmCyLyDhOgLJ5Ha7H0JJWNaVKTRHCT+aFjSr08/K/NLPFV2/4q7C1S5cOf6do76PJ+GdxU7V0GpFmAgsIPQIDAQAB"
)
_RSA_N, _RSA_E = rsa_public_key_from_spki(PUBLIC_KEY)


def make_nonce(length=16):
    """页面 passkey(16) 用数字字符集。"""
    return "".join(str(i) for i in (int.from_bytes(os.urandom(1), "big") % 10 for _ in range(length)))


def encryptras(message, nonce):
    """等价页面的 encryptras：AES-128-CBC(key=iv=nonce) → base64。"""
    return b64e(aes_cbc_encrypt(message, nonce.encode("utf-8")))


def encrypt_rsa_nonce(nonce):
    """等价页面的 encryptd.encrypt(nonce)：RSA/PKCS#1 v1.5 → base64。"""
    return b64e(rsa_encrypt_pkcs1v15(nonce, _RSA_N, _RSA_E))


def encrypt_aes_point(point_json, secret_key):
    """等价页面的 encryptraso：AES-128-ECB(key=secret_key) → base64。"""
    return b64e(aes_ecb_encrypt(point_json, secret_key.encode("utf-8")))
