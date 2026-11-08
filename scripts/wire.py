"""Adapt the reviewed attested LTK layout to the host's platform-3 wire format."""
import base64
import hashlib


def nodes(data, begin=0, end=None):
    end = len(data) if end is None else end
    position = begin
    while position < end:
        start = position
        if position + 2 > end:
            raise ValueError("Truncated DER header")
        tag, length = data[position:position + 2]
        position += 2
        if length & 128:
            count = length & 127
            if not 1 <= count <= 4 or position + count > end:
                raise ValueError("Invalid DER length")
            length = int.from_bytes(data[position:position + count], "big")
            position += count
        stop = position + length
        if stop > end:
            raise ValueError("DER field exceeds parent")
        yield tag, start, position, stop
        position = stop


def wrap(tag, data):
    size = len(data)
    count = (size.bit_length() + 7) // 8
    length = bytes([size]) if size < 128 else bytes([128 + count]) + size.to_bytes(count, "big")
    return bytes([tag]) + length + data


def platform_ltk(encoded, platform):
    if platform != 3:
        raise ValueError("Only the accepted platform-3 profile is supported")
    raw = base64.b64decode(encoded, validate=True)
    if not 512 <= len(raw) <= 65536:
        raise ValueError("Expected an attested signing identity")
    parent, = nodes(raw)
    if parent[0] != 0x30:
        raise ValueError("Expected one DER sequence")
    fields = list(nodes(raw, parent[2], parent[3]))
    if [tag for tag, *_ in fields] != [0x81, 0x85, 0x86, 0x82, 0x83, 0x84]:
        raise ValueError("Unexpected source LTK layout; do not guess")
    modified = wrap(0x30, raw[parent[2]:parent[3]] + wrap(0x87, bytes([platform])))
    updated_parent, = nodes(modified)
    updated = list(nodes(modified, updated_parent[2], updated_parent[3]))
    preserved = all(raw[a[1]:a[3]] == modified[b[1]:b[3]] for a, b in zip(fields, updated))
    if not preserved or len(updated) != len(fields) + 1:
        raise ValueError("Existing LTK fields changed")
    return base64.b64encode(modified).decode(), {
        "wire_platform": platform, "original_bytes": len(raw), "wire_bytes": len(modified),
        "original_fields_byte_identical": preserved,
        "attestation_sha256": hashlib.sha256(raw[fields[1][2]:fields[1][3]]).hexdigest(),
        "stock_phone_acceptance_verified": False,
    }
