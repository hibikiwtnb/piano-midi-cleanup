import os
import shutil


NODE_CANDIDATES = (
    'node',
    'node.exe',
    '/opt/homebrew/bin/node',
    '/usr/local/bin/node',
    '/Applications/Codex.app/Contents/Resources/node',
)
if os.name == 'nt':
    NODE_CANDIDATES += (
        os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'nodejs', 'node.exe'),
    )


def node_executable(requirement='Tonal.js chord detection'):
    configured = os.environ.get('PIANOTRANS_NODE')
    if configured:
        return configured

    for candidate in NODE_CANDIDATES:
        resolved = shutil.which(candidate) if os.path.basename(candidate) == candidate else candidate
        if resolved and os.path.exists(resolved) and os.access(resolved, os.X_OK):
            return resolved

    raise RuntimeError(
        'Node.js is required for {}. Install Node.js or set PIANOTRANS_NODE.'.format(requirement)
    )
