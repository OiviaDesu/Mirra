#!/usr/bin/env python3
"""Create a local drag-to-install disk image; never upload or publish it."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile

from configuration import ROOT
from release import release_info
from deployment import verify_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format', choices=('dmg', 'zip'), default='dmg',
                        help='Package format (default: dmg; zip is optional)')
    args = parser.parse_args()
    app = (ROOT / 'build/Mirra.app').resolve()
    release = release_info(ROOT)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleShortVersionString'), info.get('CFBundleVersion')) != (release['version'], release['build']):
        raise RuntimeError('Rebuild Mirra.app before packaging this release')
    deployment = verify_app(app)
    filename = 'Mirra-' + release['tag'] + '.' + args.format
    subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(app)], check=True)
    destination = ROOT / 'dist/app'
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.stage', dir=destination) as directory:
        archive = Path(directory) / filename
        if args.format == 'dmg':
            contents = Path(directory) / 'contents'
            contents.mkdir()
            copied = contents / 'Mirra.app'
            subprocess.run(['/usr/bin/ditto', str(app), str(copied)], check=True, timeout=180)
            subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(copied)], check=True)
            (contents / 'Applications').symlink_to('/Applications', target_is_directory=True)
            subprocess.run(['/usr/bin/hdiutil', 'create', '-srcfolder', str(contents),
                            '-volname', 'Mirra', '-fs', 'HFS+', '-format', 'UDZO',
                            '-nospotlight', '-quiet', str(archive)], check=True, timeout=180)
            subprocess.run(['/usr/bin/hdiutil', 'verify', '-quiet', str(archive)],
                           check=True, timeout=180)
        else:
            subprocess.run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent',
                            str(app), str(archive)], check=True, timeout=180)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        output = destination / checksum[:12] / filename
        output.parent.mkdir(exist_ok=True)
        if output.exists():
            if output.read_bytes() != archive.read_bytes():
                raise RuntimeError('App package destination contains different data')
        else:
            archive.rename(output)
    print(json.dumps({'archive': str(output), 'sha256': checksum, 'app': str(app),
                      'version': release['version'], 'build': release['build'], 'format': args.format,
                      'published': False, 'notarized': False,
                      'minimum_macos': deployment['minimum_macos'],
                      'verified_macos_binaries': len(deployment['binaries'])}, indent=2))


if __name__ == '__main__':
    main()
