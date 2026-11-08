"""Read the host ABI probe shared with the media adapter before starting support."""
import json
from pathlib import Path
import subprocess

from configuration import ROOT


def inspect_media_profile(root=ROOT):
    tool = Path(root) / 'build/inspector'
    if not tool.is_file():
        raise RuntimeError('Build the media profile checker first: make build')
    reply = subprocess.run([str(tool)], capture_output=True, text=True, timeout=15)
    if reply.returncode not in (0, 1):
        raise RuntimeError('Media profile checker failed; run build/inspector for diagnostics')
    try:
        report = json.loads(reply.stdout)
    except ValueError as error:
        raise RuntimeError('Media profile checker returned invalid JSON') from error
    if (not isinstance(report, dict) or report.get('schema_version') != 1
            or report.get('profile') != 'awdl-single-tile'
            or type(report.get('compatible')) is not bool
            or report['compatible'] != (reply.returncode == 0)):
        raise RuntimeError('Media profile checker returned inconsistent compatibility data')
    return report


def require_media_profile(root=ROOT):
    report = inspect_media_profile(root)
    if not report['compatible']:
        raise RuntimeError('Media profile is unsupported on this host ('
            + str(report.get('architecture')) + ', ' + str(report.get('os_build'))
            + '). Inspect build/inspector; the supported reference is Intel build '
            + str(report.get('reference_build')) + ' with matching framework methods')
    return report
