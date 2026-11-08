"""Read the endpoint's platform without requiring developer tools at setup."""
from pathlib import Path
import struct


def require_ios_endpoint(path):
    with Path(path).open('rb') as stream:
        header = stream.read(32)
        if len(header) != 32:
            raise ValueError('The endpoint has an incomplete Mach-O header')
        magic, cpu, subtype, kind, count, length, _, _ = struct.unpack('<8I', header)
        if magic != 0xFEEDFACF or cpu != 0x100000C or kind != 2:
            raise ValueError('The endpoint must be a thin arm64 iOS executable')
        # arm64e uses the same CPU type as arm64. Checking only cputype accepts
        # a binary that cannot run on A11 and earlier bridge devices.
        if subtype != 0:  # CPU_SUBTYPE_ARM64_ALL, without capability bits.
            raise ValueError('The endpoint must use generic arm64, not arm64e or a restricted CPU subtype')
        if not 0 < count <= 65536 or not 8 <= length <= 1048576:
            raise ValueError('Invalid endpoint load-command bounds')
        commands = stream.read(length)
    if len(commands) != length:
        raise ValueError('The endpoint load commands are truncated')
    offset = 0
    builds = []
    for _ in range(count):
        if offset + 8 > length:
            raise ValueError('The endpoint load command is truncated')
        command, size = struct.unpack_from('<II', commands, offset)
        if size < 8 or size % 8 or offset + size > length:
            raise ValueError('Invalid endpoint load-command size')
        if command == 0x32:  # LC_BUILD_VERSION, device iOS platform = 2.
            if size < 24:
                raise ValueError('Invalid endpoint build-version command')
            platform, minimum, _, tools = struct.unpack_from('<4I', commands, offset + 8)
            if size != 24 + tools * 8:
                raise ValueError('Invalid endpoint build-tool entries')
            builds.append((platform, minimum))
        offset += size
    if offset != length or len(builds) != 1 or builds[0][0] != 2:
        raise ValueError('The endpoint must be built for device iOS')
    version = builds[0][1]
    return {'architecture': 'arm64', 'platform': 'ios',
            'minimum_os': '.'.join(str(x) for x in (version >> 16, (version >> 8) & 255, version & 255))}
