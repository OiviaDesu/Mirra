"""Run the native UI checks as a foreground app registered by LaunchServices."""
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

from release import release_info

ROOT = Path(__file__).resolve().parents[1]


def main():
    parent = ROOT / 'build/interface-checks.noindex'
    parent.mkdir(parents=True, exist_ok=True)
    # A regular application launch gives AppKit keyboard tests a foreground
    # window. A raw CLI process may be refused activation by WindowServer.
    # Keep test bundles out of Spotlight and remove them after the check.
    with tempfile.TemporaryDirectory(prefix='run-', dir=parent) as directory:
        stage = Path(directory)
        app = stage / 'Mirra UI Checks.app'
        contents = app / 'Contents'
        executable = contents / 'MacOS/InterfaceChecks'
        executable.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'build/interface', executable)
        executable.chmod(0o700)
        release = release_info(ROOT)
        (contents / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable': executable.name, 'CFBundleIdentifier': 'local.mirra.interface-tests',
            'CFBundleName': 'Mirra UI Checks', 'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': release['version'], 'CFBundleVersion': release['build'],
            'NSHighResolutionCapable': True,
        }))
        subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True, timeout=15)
        log = stage / 'result.log'
        try:
            opened = subprocess.run(['/usr/bin/open', '-n', '-W', '--stdout', str(log), '--stderr', str(log), str(app)],
                                    capture_output=True, text=True, timeout=60)
        finally:
            registry = Path('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister')
            if registry.is_file():
                subprocess.run([str(registry), '-u', str(app)], capture_output=True, timeout=10)
        output = log.read_text(errors='replace') if log.is_file() else ''
        print(output, end='')
        if opened.returncode:
            raise RuntimeError('Native test application could not launch: ' + opened.stderr.strip())
        # `open -W` does not forward the application's exit status. Require the
        # final success record written only after all actual assertions finish.
        passed = False
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if (isinstance(value, dict) and value.get('passed') is True
                    and type(value.get('native_ui_checks')) is int and value['native_ui_checks'] > 0
                    and value.get('external_actions_performed') is False):
                passed = True
        if not passed:
            raise RuntimeError('Native UI checks did not finish successfully; see the output above')


if __name__ == '__main__':
    main()
