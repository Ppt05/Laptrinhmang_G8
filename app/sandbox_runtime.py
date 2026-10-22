"""Docker isolation flags shared by official judging and the local lab."""


def sandbox_args():
    # /tmp must be executable because the C++ artifact is started there.
    return ['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only',
            '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=32m,mode=1777',
            '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt',
            'no-new-privileges', '--memory', '256m', '--cpus', '1', '--pids-limit', '32']
