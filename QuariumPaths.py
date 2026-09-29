"""Where the application keeps its working data.

Data used to sit beside the program, which meant the databases lived inside
a OneDrive-synced folder and inside the git repository. Two sync engines
replicating the same open SQLite files is a hazard on its own, so the
working set now lives under %LOCALAPPDATA%.

Running from source gets a separate workspace, so testing a branch cannot
disturb the data the installed application uses.
"""

import json
import os
import shutil
import sys

APP_NAME = "Quarium"

# Everything the application reads or writes at runtime. Kept here so the
# first-run migration knows what to bring across.
DATA_FILES = [
    'stock.db', 'services.db', 'clients.db', 'projects.db', 'payees.db',
    'users.json', 'settings.json',
    'QLogo.png', 'EstimateLogo.png',
    'ContractTemplate_PF.docx', 'ContractTemplate_PJ.docx', 'ContractShell.docx',
    'ContractHeader_PF.docx', 'ContractHeader_PJ.docx',
]
CONFIG_FILES = ['local_config.json', 'credentials.json', 'token.json']
EXTRA_DIRS = ['stock_backups']

_cached_dir = None


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def program_dir():
    """Where the exe or the source tree lives."""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """The working directory for databases and settings, created on demand."""
    global _cached_dir
    if _cached_dir:
        return _cached_dir
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    path = os.path.join(base, APP_NAME)
    if not is_frozen():
        # Source runs are for development; keep them away from live data.
        path = os.path.join(path, 'dev')
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        # If AppData is unavailable for any reason, fall back to the old
        # behaviour rather than failing to start.
        path = program_dir()
    _cached_dir = path
    return path


def set_data_dir(path):
    """Overrides the location. Used by tests."""
    global _cached_dir
    _cached_dir = path
    return _cached_dir


def workspace_is_empty(target=None):
    target = target or data_dir()
    return not any(os.path.exists(os.path.join(target, name)) for name in DATA_FILES)


def migrate_if_needed(source=None, target=None, include_credentials=None):
    """Seeds an empty workspace from the program folder, once.

    Copies rather than moves: the originals stay where they are, so a failed
    migration can never be the reason data goes missing.

    A development workspace deliberately does not receive the credentials, so
    it starts offline and cannot sync to the shared Drive. The company profile
    is carried over with its credentials blanked, which is enough for the app
    to open straight into local mode.
    """
    source = source or program_dir()
    target = target or data_dir()
    if os.path.abspath(source) == os.path.abspath(target):
        return []
    if not workspace_is_empty(target):
        return []

    if include_credentials is None:
        include_credentials = is_frozen()

    # A fresh install has nothing beside the exe to copy from, so fall back to
    # the templates and logos bundled into the executable. Anything newer in
    # the cloud replaces them on the first sync.
    bundled = getattr(sys, '_MEIPASS', None)

    copied = []
    for name in DATA_FILES:
        for origin in (source, bundled):
            if not origin:
                continue
            src = os.path.join(origin, name)
            if not os.path.exists(src):
                continue
            try:
                shutil.copy2(src, os.path.join(target, name))
                copied.append(name)
            except OSError as e:
                print(f"Could not migrate {name}: {e}")
            break

    for name in CONFIG_FILES:
        src = os.path.join(source, name)
        if not os.path.exists(src):
            continue
        if include_credentials:
            try:
                shutil.copy2(src, os.path.join(target, name))
                copied.append(name)
            except OSError as e:
                print(f"Could not migrate {name}: {e}")
        elif name == 'local_config.json':
            # Keep the company profile so the app knows who it is, but strip the
            # secrets so it stays offline.
            try:
                with open(src, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                for profile in config.get('companies', {}).values():
                    if isinstance(profile, dict):
                        profile['credentials'] = ""
                        profile['token'] = ""
                config['workspace_company'] = None
                with open(os.path.join(target, name), 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2)
                copied.append(name + " (offline)")
            except Exception as e:
                print(f"Could not prepare an offline profile: {e}")

    for folder in EXTRA_DIRS:
        src = os.path.join(source, folder)
        dest = os.path.join(target, folder)
        if os.path.isdir(src) and not os.path.isdir(dest):
            try:
                shutil.copytree(src, dest)
                copied.append(folder + os.sep)
            except OSError as e:
                print(f"Could not migrate {folder}: {e}")

    return copied
