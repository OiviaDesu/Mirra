"""Build a reproducible text-source archive from an explicit reviewed file list."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
FILE_LIST = 'config/sources.json'
ARCHIVE_ROOT = 'mirra'
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
FORBIDDEN = {'docs/active.json', 'docs/ACTIVE-WORK.json', 'config/local.json'}
ROOT_FILES = {'README.md', 'NOTICE.md', 'LICENSE', 'VERSION', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md', 'Makefile', '.gitignore'}
SOURCE_DIRECTORIES = {'src', 'scripts', 'tests', 'docs', 'config', '.github'}
SOURCE_SUFFIXES = {'.py', '.m', '.c', '.h', '.md', '.json', '.plist', '.yml'}


def validate_png(data, name):
    """Only pixels and standard color data, without text or trailing payloads."""
    def invalid():
        raise ValueError('Expected a bounded PNG screenshot without metadata: ' + name)
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        invalid()
    offset = 8
    chunks = []
    compressed = bytearray()
    row_bytes = height = 0
    while offset < len(data):
        if offset + 12 > len(data):
            invalid()
        length = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data) or kind not in (b'IHDR', b'IDAT', b'IEND', b'sRGB', b'gAMA', b'cHRM'):
            invalid()
        content = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from('>I', data, end - 4)[0]
        if zlib.crc32(kind + content) & 0xffffffff != crc:
            invalid()
        if not chunks and kind != b'IHDR':
            invalid()
        if kind == b'IHDR':
            if chunks or length != 13:
                invalid()
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', content)
            if (not 0 < width <= 4096 or not 0 < height <= 4096 or width * height > 4_000_000
                    or depth != 8 or color not in (2, 6) or compression or filtering or interlace):
                invalid()
            row_bytes = 1 + width * (3 if color == 2 else 4)
        elif kind == b'IDAT':
            if b'IEND' in chunks or (b'IDAT' in chunks and chunks[-1] != b'IDAT'):
                invalid()
            compressed.extend(content)
        elif kind == b'IEND':
            if length or end != len(data) or b'IDAT' not in chunks:
                invalid()
        elif (b'IDAT' in chunks or kind in chunks
              or length != {b'sRGB': 1, b'gAMA': 4, b'cHRM': 32}[kind]
              or kind == b'sRGB' and content[0] > 3):
            invalid()
        chunks.append(kind)
        offset = end
    if not chunks or chunks[-1] != b'IEND' or not compressed:
        invalid()
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(bytes(compressed), row_bytes * height + 1)
    except zlib.error:
        invalid()
    if (len(pixels) != row_bytes * height or not decoder.eof or decoder.unused_data
            or any(pixels[index] > 4 for index in range(0, len(pixels), row_bytes))):
        invalid()


def checked_name(name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('Source file list contains an invalid path')
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or str(path) != name
            or name in FORBIDDEN or any(part.startswith('.') for part in path.parts[1:])
            or path.parts[0].startswith('.') and path.parts[0] not in ('.github', '.gitignore')):
        raise ValueError('Source path is not distributable: ' + name)
    image = len(path.parts) == 3 and path.parts[:2] == ('docs', 'images') and path.suffix == '.png'
    if name not in ROOT_FILES and not image and (len(path.parts) < 2
            or path.parts[0] not in SOURCE_DIRECTORIES or path.suffix not in SOURCE_SUFFIXES):
        raise ValueError('Source path is outside the export policy: ' + name)
    return path


def read_source(root, name):
    """Use directory descriptors so neither files nor ancestors follow symlinks."""
    path = checked_name(name)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                raise ValueError('Expected a bounded regular source file: ' + name)
            data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
            if len(data) > MAX_FILE_BYTES or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('Source changed or exceeded its size limit: ' + name)
    finally:
        os.close(directory)
    if path.suffix == '.png':
        validate_png(data, name)
        return data
    if b'\0' in data:
        raise ValueError('Binary content is not distributable source: ' + name)
    data.decode('utf-8')
    # This catches a private-key block pasted into an otherwise listed text file;
    # it is supplementary to the exact file list, not a general secret scanner.
    if any(line.strip().startswith(b'-----BEGIN ') and line.strip().endswith(b'PRIVATE KEY-----')
           for line in data.splitlines()):
        raise ValueError('Private-key material in source: ' + name)
    return data


def source_payload(root):
    root = Path(root).resolve()
    declaration = json.loads(read_source(root, FILE_LIST))
    if (not isinstance(declaration, dict) or type(declaration.get('schema_version')) is not int
            or declaration['schema_version'] != 1 or not isinstance(declaration.get('files'), list)):
        raise ValueError('Unsupported source file list')
    names = declaration['files']
    if not names or len(names) > 1000 or any(not isinstance(name, str) for name in names):
        raise ValueError('Invalid source file list')
    if len(set(names)) != len(names) or FILE_LIST not in names:
        raise ValueError('Source file list must be unique and include itself')
    contents = {}
    total = 0
    for name in sorted(names):
        data = read_source(root, name)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Source archive exceeds its size limit')
        contents[name] = data
    manifest = {'schema_version': 1, 'files': {
        name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}}
    contents['manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(ARCHIVE_ROOT + '/' + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    return output.getvalue(), manifest


def export_source(root=ROOT, output=None, *, release=None):
    payload, manifest = source_payload(root)
    digest = hashlib.sha256(payload).hexdigest()
    filename = 'Mirra-' + release['tag'] + '-source.zip' if release else 'Mirra.zip'
    output = Path(output) if output else Path(root) / 'dist/source' / digest[:12] / filename
    output = output.expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = False
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if output.is_symlink() or not output.is_file() or output.read_bytes() != payload:
            raise FileExistsError('Export would replace different content: ' + str(output))
        existing = True
    else:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
    result = {'archive': str(output), 'sha256': digest, 'source_files': len(manifest['files']),
              'bytes': len(payload), 'already_present': existing}
    if release:
        result.update(version=release['version'], build=release['build'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='Optional destination; different existing content is never overwritten')
    args = parser.parse_args()
    try:
        from release import release_info
        print(json.dumps(export_source(output=args.output, release=release_info(ROOT)), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
