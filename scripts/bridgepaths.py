"""Launch native helpers from the bridge's actual filesystem namespace."""

ROOTHIDE_CODE_ROOT = '/usr/local/libexec/Mirra'
ROOTLESS_DEPLOYMENT_ROOT = '/var/jb/var/mobile/MirraCache'
ROOTHIDE_ENTITLEMENTS = (
    'platform-application',
    'com.apple.private.security.no-sandbox',
    'com.apple.private.security.storage.AppBundles',
    'com.apple.private.security.storage.AppDataContainers',
)

# RootHide's bootstrap tools use a virtual root. Verify the documented path
# converters rather than guessing a randomized jailbreak directory.
ENVIRONMENT_SCRIPT = '''
mirra_environment_error() {
    printf '%s\\n' '{"error":"roothide_path_translation_unavailable"}'
    exit 1
}
if command -v jbroot >/dev/null 2>&1 || command -v rootfs >/dev/null 2>&1; then
    command -v jbroot >/dev/null 2>&1 && command -v rootfs >/dev/null 2>&1 || mirra_environment_error
    mirra_bootstrap=$(jbroot /) || mirra_environment_error
    mirra_rootfs=$(rootfs /) || mirra_environment_error
    case "$mirra_bootstrap" in
        /) mirra_environment_error ;;
        /*) ;;
        *) mirra_environment_error ;;
    esac
    case "$mirra_rootfs" in
        /rootfs|/rootfs/) printf '%s\\n' '{"path_mode":"roothide","path_translation_verified":true}' ;;
        *) mirra_environment_error ;;
    esac
elif test -d /var/jb/usr/bin; then
    printf '%s\\n' '{"path_mode":"native","path_translation_verified":false,"rootless":true}'
else
    printf '%s\\n' '{"path_mode":"native","path_translation_verified":false}'
fi
'''


def helper_command(arguments, path_mode='native', *, observed=False):
    """Keep JSON stdout/stdin intact, optionally report the child's exit status.

    Paths in a RootHide profile stay relative to the bootstrap's virtual root.
    Its bootstrap shell translates executable paths through vroot; convert
    only paths passed as data to the native program. Repeating the conversion
    on an executable would prefix its jailbreak root twice. Resolve data paths
    at every launch so a changed root cannot stale a saved profile.
    """
    arguments = list(arguments)
    if path_mode not in ('native', 'roothide'):
        raise ValueError('Unsupported bridge path mode')
    if not arguments:
        raise ValueError('A helper command is required')
    if path_mode == 'native' and not observed:
        return arguments
    if len(arguments) == 2 and arguments[1] == '--capabilities':
        paths, stage = set(), 'capabilities'
    elif len(arguments) == 3 and arguments[1] in ('--export-certificate', '--issue-certificate'):
        paths, stage = {2}, arguments[1][2:].replace('-', '_')
    elif len(arguments) == 2 and arguments[1].startswith('/'):
        paths, stage = {1}, 'serve'
    else:
        raise ValueError('Unsupported helper arguments')
    script = []
    if path_mode == 'roothide':
        script.append('''mirra_path_error() {
    printf '%s\\n' '{"ready":false,"error":"roothide_path_translation_unavailable"}'
    exit 1
}
command -v jbroot >/dev/null 2>&1 || mirra_path_error
''')
    for index in range(len(arguments)):
        position = index + 1
        name = 'mirra_arg' + str(position)
        if path_mode == 'roothide' and index in paths:
            script.append(f'{name}=$(jbroot "${position}") || mirra_path_error\n')
            script.append(f'case "${name}" in /*) ;; *) mirra_path_error ;; esac\n')
        else:
            script.append(f'{name}=${position}\n')
    invocation = ' '.join('"$mirra_arg' + str(index + 1) + '"' for index in range(len(arguments)))
    if observed:
        script.append(f'''printf 'MIRRA_HELPER {{"stage":"{stage}","event":"launch"}}\\n' >&2
{invocation}
mirra_helper_status=$?
printf 'MIRRA_HELPER {{"stage":"{stage}","event":"exit","exit_status":%s}}\\n' "$mirra_helper_status" >&2
exit "$mirra_helper_status"
''')
    else:
        script.append('exec ' + invocation + '\n')
    return ['sh', '-c', ''.join(script), 'mirra-helper', *arguments]
