"""Generic decoder for WKO4/WKO5 files (Peaksware 'PK' tagged binary format).

Layout: magic ("wko4" / "wko5athlete" / "wko5cache") + 0x1a + TLV stream.
Each entry: varint key; field_id = key >> 3; wire = key & 7:
  0,1,4 -> varint ; 2 -> float64 ; 3 -> length-delimited bytes/string ; 6 -> length-delimited nested message
  5,7   -> unknown (guessed 4 bytes / 1 byte)
Usage: python wko_decode.py FILE [max_entries]
"""

import struct, sys, math

DBL_MAX = 1.7976931348623157e308


def read_varint(b, i):
    shift = 0
    val = 0
    while True:
        c = b[i]
        i += 1
        val |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return val, i


def parse(b, start, end, depth=0, out=None, path=""):
    out = [] if out is None else out
    i = start
    while i < end:
        try:
            key, i = read_varint(b, i)
        except IndexError:
            break
        fid, wt = key >> 3, key & 7
        p = f"{path}/{fid}"
        if wt in (0, 1, 4):
            v, i = read_varint(b, i)
            out.append((depth, p, f"varint{wt}", v))
        elif wt == 2:
            v = struct.unpack_from("<d", b, i)[0]
            i += 8
            out.append((depth, p, "double", None if v == DBL_MAX else v))
        elif wt == 3:
            n, i = read_varint(b, i)
            raw = b[i : i + n]
            i += n
            try:
                s = raw.decode("utf-8")
                printable = all(ch.isprintable() or ch in "\n\r\t" for ch in s)
            except UnicodeDecodeError:
                s = None
                printable = False
            out.append((depth, p, "str" if printable else f"bytes[{n}]", s if printable else raw[:12].hex()))
        elif wt == 6:
            n, i = read_varint(b, i)
            out.append((depth, p, f"msg[{n}]", None))
            parse(b, i, i + n, depth + 1, out, p)
            i += n
        elif wt == 5:
            v = struct.unpack_from("<I", b, i)[0]
            i += 4
            out.append((depth, p, "u32?", v))
        else:
            v = b[i]
            i += 1
            out.append((depth, p, "byte?", v))
    return out


def load(path):
    b = open(path, "rb").read()
    for magic in (b"wko5athlete", b"wko5cache", b"wko4"):
        if b.startswith(magic):
            return b, len(magic) + 1
    raise SystemExit("unknown magic")


if __name__ == "__main__":
    b, start = load(sys.argv[1])
    mx = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    entries = parse(b, start, len(b))
    print(f"{sys.argv[1]}: {len(b)} bytes, {len(entries)} entries")
    for depth, p, t, v in entries[:mx]:
        if v is None and t == "double":
            continue
        vs = repr(v)[:90] if not isinstance(v, str) else repr(v[:90])
        print("  " * depth + f"{p} {t} = {vs}")


def top_level(b, i, stop_fields=(4201, 4401, 4601, 4700), want=None):
    """Scan depth-0 entries only (no recursion). Returns list of (fid, wire, value).
    Strings come back decoded; wire-3 blobs that are not text come back as raw bytes.
    Stops at the first field in stop_fields (range/lap blocks start there)."""
    end = len(b)
    out = []
    while i < end:
        key, i = read_varint(b, i)
        fid, wt = key >> 3, key & 7
        if fid in stop_fields:
            break
        if wt in (0, 1, 4):
            v, i = read_varint(b, i)
        elif wt == 2:
            v = struct.unpack_from("<d", b, i)[0]
            i += 8
            v = None if v == DBL_MAX else v
        elif wt in (3, 6):
            n, i = read_varint(b, i)
            raw = b[i : i + n]
            i += n
            if wt == 6:
                v = ("msg", n)
            else:
                try:
                    v = raw.decode("utf-8")
                except UnicodeDecodeError:
                    v = bytes(raw)
        else:
            out.append((fid, wt, ("unknown-wire", i)))
            break
        if want is None or fid in want:
            out.append((fid, wt, v))
    return out
