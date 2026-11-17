"""Pure-Python AES (FIPS-197) with the two modes browser cookies use: CBC (macOS Chrome/Edge)
and GCM (Windows Chrome/Edge, our own cookie file). Standard library only. Slow, but cookie
values are a few blocks each."""
import hmac

_SBOX = [0] * 256
_INV = [0] * 256


def _build_sbox():
    p = q = 1
    while True:
        p ^= ((p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1 | q >> 7) & 0xFF) ^ ((q << 2 | q >> 6) & 0xFF) ^ ((q << 3 | q >> 5) & 0xFF) ^ ((q << 4 | q >> 4) & 0xFF) ^ 0x63
        _SBOX[p] = x
        _INV[x] = p
        if p == 1:
            break
    _SBOX[0] = 0x63
    _INV[0x63] = 0


_build_sbox()


def _xt(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xt(a)
        b >>= 1
    return r


_M9, _M11, _M13, _M14 = ([_mul(i, k) for i in range(256)] for k in (9, 11, 13, 14))


def _expand(key):
    nk = len(key) // 4
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 16, 24 or 32 bytes")
    rounds = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (rounds + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = [_SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xt(rcon)
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([a ^ b for a, b in zip(w[i - nk], t)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(rounds + 1)]


class AES:
    def __init__(self, key):
        self.rk = _expand(bytes(key))

    def encrypt_block(self, block):
        s = [a ^ b for a, b in zip(block, self.rk[0])]
        last = len(self.rk) - 1
        for r in range(1, last + 1):
            s = [_SBOX[b] for b in s]
            s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]
            if r != last:
                t = []
                for c in range(4):
                    a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                    x = a0 ^ a1 ^ a2 ^ a3
                    t += [a0 ^ x ^ _xt(a0 ^ a1), a1 ^ x ^ _xt(a1 ^ a2), a2 ^ x ^ _xt(a2 ^ a3), a3 ^ x ^ _xt(a3 ^ a0)]
                s = t
            s = [a ^ b for a, b in zip(s, self.rk[r])]
        return bytes(s)

    def decrypt_block(self, block):
        last = len(self.rk) - 1
        s = [a ^ b for a, b in zip(block, self.rk[last])]
        for r in range(last - 1, -1, -1):
            s = [s[(i - 4 * (i % 4)) % 16] for i in range(16)]
            s = [_INV[b] for b in s]
            s = [a ^ b for a, b in zip(s, self.rk[r])]
            if r:
                t = []
                for c in range(4):
                    a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                    t += [_M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3], _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3],
                          _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3], _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3]]
                s = t
        return bytes(s)


def cbc_decrypt(key, iv, data, unpad=True):
    if len(data) % 16:
        raise ValueError("CBC data is not a multiple of 16 bytes")
    a, prev, out = AES(key), bytes(iv), bytearray()
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        out += bytes(x ^ y for x, y in zip(a.decrypt_block(blk), prev))
        prev = blk
    if unpad:
        n = out[-1] if out else 0
        if not 1 <= n <= 16 or out[-n:] != bytes([n]) * n:
            raise ValueError("bad CBC padding")
        del out[-n:]
    return bytes(out)


def _ghash(h, aad, ct):
    def blocks(b):
        for i in range(0, len(b), 16):
            yield int.from_bytes(b[i:i + 16].ljust(16, b"\0"), "big")
    y = 0
    for x in list(blocks(aad)) + list(blocks(ct)) + [(len(aad) * 8) << 64 | len(ct) * 8]:
        y = _gf_mul(y ^ x, h)
    return y


def _gf_mul(x, y):
    r, R = 0, 0xE1 << 120
    for i in range(127, -1, -1):
        if (y >> i) & 1:
            r ^= x
        x = (x >> 1) ^ R if x & 1 else x >> 1
    return r


def _gcm(key, nonce, data, aad):
    a = AES(key)
    h = int.from_bytes(a.encrypt_block(bytes(16)), "big")
    if len(nonce) == 12:
        j0 = int.from_bytes(bytes(nonce) + b"\0\0\0\1", "big")
    else:
        j0 = _ghash(h, b"", bytes(nonce))
    out = bytearray()
    for i in range(0, len(data), 16):
        ks = a.encrypt_block(((j0 & ~0xFFFFFFFF) | ((j0 + 1 + i // 16) & 0xFFFFFFFF)).to_bytes(16, "big"))
        out += bytes(x ^ y for x, y in zip(data[i:i + 16], ks))
    return a, h, j0, bytes(out)


def _tag(a, h, j0, aad, ct):
    return (_ghash(h, aad, ct) ^ int.from_bytes(a.encrypt_block(j0.to_bytes(16, "big")), "big")).to_bytes(16, "big")


def gcm_encrypt(key, nonce, plaintext, aad=b""):
    """-> ciphertext + 16-byte tag."""
    a, h, j0, ct = _gcm(key, nonce, plaintext, aad)
    return ct + _tag(a, h, j0, aad, ct)


def gcm_decrypt(key, nonce, data, aad=b""):
    """data = ciphertext + 16-byte tag. Raises ValueError when the tag does not match."""
    if len(data) < 16:
        raise ValueError("GCM data shorter than the tag")
    ct, tag = data[:-16], data[-16:]
    a, h, j0, pt = _gcm(key, nonce, ct, aad)
    if not hmac.compare_digest(_tag(a, h, j0, aad, ct), tag):
        raise ValueError("GCM tag mismatch")
    return pt
