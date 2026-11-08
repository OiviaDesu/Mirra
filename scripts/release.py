"""One marketing version for the app, archives, and release notes."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def release_info(root=ROOT):
    root = Path(root)
    version = (root / 'VERSION').read_text().strip()
    settings = json.loads((root / 'config/release.json').read_text())
    build = settings.get('build')
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must contain a release version such as 0.1.0')
    if not isinstance(build, str) or not re.fullmatch(r'[1-9]\d*', build):
        raise ValueError('The release build must be a positive integer string')
    return {'version': version, 'build': build, 'tag': 'v' + version,
            'repository': settings['repository']}
