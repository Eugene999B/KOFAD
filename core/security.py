import base64
import hashlib
import hmac
import secrets
import struct
import time


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def matching_step(secret, code):
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return None
    try:
        key = base64.b32decode(secret)
    except Exception:
        return None
    for step in range(int(time.time() // 30) - 1, int(time.time() // 30) + 2):
        digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
        offset = digest[-1] & 15
        value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000
        if hmac.compare_digest(f"{value:06}", code):
            return step
    return None
