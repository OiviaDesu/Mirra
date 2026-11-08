"""Bounded, read-only checks for native notifications and the phone-app catalog.

Neither settings nor catalog entries prove delivery or a successful app launch.
Reports contain only selected capability fields, not private app names, local
paths, device identifiers or notification contents.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def _probe(tool, mode):
    if not tool.is_file():
        return {'error': 'Build the checker first: make build/capabilities'}
    try:
        reply = subprocess.run([str(tool), mode], capture_output=True, text=True, timeout=15)
        if reply.returncode:
            return {'error': 'Capability helper failed; this framework version may be unsupported'}
        data = json.loads(reply.stdout)
        if not isinstance(data, dict) or data.get('schema_version') != 1:
            return {'error': 'Capability helper returned an unsupported report format'}
        return data
    except subprocess.TimeoutExpired:
        return {'error': 'Capability query timed out; the setting is unknown'}
    except (OSError, ValueError):
        return {'error': 'Capability helper could not return a valid report'}


def summarize(settings, catalog):
    notifications = {'setting': 'unknown', 'delivery_verified': None}
    # Interpret only Apple's own rendering plus the matching numeric value.
    # Unknown or inconsistent framework output must never become "enabled".
    raw = settings.get('remote_notifications_setting_raw')
    label = settings.get('remote_notifications_setting_label')
    labels = {0: ('NotSupported', 'not_supported'), 1: ('Disabled', 'disabled'),
              2: ('Enabled', 'enabled')}
    if (settings.get('notification_settings_signature_supported') is True
            and settings.get('notification_settings_returned') is True
            and type(raw) is int and raw in labels and label == labels[raw][0]):
        notifications.update(setting=labels[raw][1], raw=raw, apple_label=label)
    if 'error' in settings:
        notifications['query_error'] = settings['error']
    if notifications['setting'] == 'not_supported':
        notifications['next_check'] = ('macOS reports forwarding unsupported. Inspect native '
            'pairing/registration and eligibility; enabling a preference alone does not establish support.')
    elif notifications['setting'] == 'disabled':
        notifications['next_check'] = ('Check System Settings > Notifications > Allow notifications '
            'from iPhone, then verify a real phone notification with Mirroring closed.')
    elif notifications['setting'] == 'enabled':
        notifications['next_check'] = 'Verify a real phone notification with Mirroring closed.'
    else:
        notifications['next_check'] = 'Check the native notification setting manually; this query is inconclusive.'

    # Keep raw metadata out of the shareable report. Only exact typed fields
    # used in the diagnostic are selected; no arbitrary helper keys are copied.
    gates = {}
    for key in ('continuity_eligible', 'notification_gate_abi_supported',
                'mirroring_policy_explicit', 'mirroring_policy_allowed'):
        if type(settings.get(key)) is bool:
            gates[key] = settings[key]
    flags = settings.get('notification_feature_flags')
    if isinstance(flags, dict):
        gates['feature_flags'] = {key: flags[key] for key in
            ('UNCOneness', 'UNCDaemonEnabled', 'UNCRemoteServicesNeeded') if type(flags.get(key)) is bool}
    eligibility = settings.get('notification_eligibility')
    if isinstance(eligibility, dict) and all(type(eligibility.get(k)) is int
                                            for k in ('domain', 'status', 'answer_raw')):
        gates['eligibility'] = {k: eligibility[k] for k in ('domain', 'status', 'answer_raw')}
        if type(eligibility.get('gate_passes')) is bool:
            gates['eligibility']['gate_passes'] = eligibility['gate_passes']

    apps = {'catalog': 'unknown', 'launch_verified': None}
    count = catalog.get('remote_placeholder_count')
    capped = catalog.get('count_capped')
    if type(count) is int and 0 <= count <= 10000 and type(capped) is bool:
        apps.update(catalog='present' if count else 'empty', count=count, count_capped=capped)
        known = catalog.get('known_apple_apps')
        if isinstance(known, list):
            apps['iphone_clock_present'] = 'com.apple.mobiletimer' in known
            apps['iphone_calendar_present'] = 'com.apple.mobilecal' in known
    if 'error' in catalog:
        apps['query_error'] = catalog['error']
    example = ('iPhone Clock' if apps.get('iphone_clock_present') else
               'iPhone Calendar' if apps.get('iphone_calendar_present') else 'a listed iPhone app')
    apps['next_check'] = ('Use Spotlight > iPhone Apps to open ' + example + ' through the original '
                          'Mirroring app. A placeholder count does not verify launching.')
    return {'schema_version': 1, 'notifications': notifications, 'local_gate_observations': gates,
            'phone_apps': apps, 'daily_use_verified': None}


def inspect_native_features(root=ROOT):
    tool = Path(root) / 'build/capabilities'
    return summarize(_probe(tool, '--settings'), _probe(tool, '--remote-app-count'))
