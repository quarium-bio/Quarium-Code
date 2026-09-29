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

# Records that a folder is a Quarium workspace, so pointing the app at a
# directory can tell "empty", "ours already" and "someone else's files" apart.
MARKER_FILE = '.quarium-workspace'
# Remembers a relocated workspace. Deliberately kept in the default location,
# never in the workspace itself, or it could not be found again.
POINTER_FILE = 'workspace.json'

_cached_dir = None


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def program_dir():
    """Where the exe or the source tree lives."""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def default_dir():
    """The built-in location, used unless the user has chosen another."""
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    path = os.path.join(base, APP_NAME)
    if not is_frozen():
        # Source runs are for development; keep them away from live data.
        path = os.path.join(path, 'dev')
    return path


def _pointer_path():
    return os.path.join(default_dir(), POINTER_FILE)


def configured_dir():
    """The workspace the user has chosen, or the default. Not validated."""
    try:
        with open(_pointer_path(), 'r', encoding='utf-8') as f:
            chosen = json.load(f).get('path')
        if chosen:
            return os.path.abspath(chosen)
    except (OSError, ValueError):
        pass
    return default_dir()


def set_workspace(path):
    """Points the application at another folder from now on."""
    os.makedirs(default_dir(), exist_ok=True)
    with open(_pointer_path(), 'w', encoding='utf-8') as f:
        json.dump({'path': os.path.abspath(path)}, f, indent=2)
    return set_data_dir(os.path.abspath(path))


def clear_workspace():
    """Returns to the default location."""
    try:
        os.remove(_pointer_path())
    except OSError:
        pass
    return set_data_dir(default_dir())


def is_relocated():
    return os.path.abspath(configured_dir()) != os.path.abspath(default_dir())


def data_dir():
    """The active working directory, created on demand."""
    global _cached_dir
    if _cached_dir:
        return _cached_dir
    path = configured_dir()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        # A relocated workspace may be on a drive that is not attached. Leave
        # the caller to notice; creating a stand-in here would silently split
        # the data across two places.
        if os.path.abspath(path) == os.path.abspath(default_dir()):
            path = program_dir()
    _cached_dir = path
    return path


def write_marker(path):
    import datetime
    try:
        with open(os.path.join(path, MARKER_FILE), 'w', encoding='utf-8') as f:
            json.dump({'app': APP_NAME,
                       'created': datetime.datetime.now().isoformat(timespec='seconds'),
                       'machine': os.environ.get('COMPUTERNAME', '')}, f, indent=2)
    except OSError as e:
        print(f"Could not mark the workspace: {e}")


def read_marker(path):
    try:
        with open(os.path.join(path, MARKER_FILE), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def inspect_folder(path):
    """Describes a candidate workspace so the user is never asked to guess.

    status is one of:
      missing    - the folder, or its drive, is not there
      empty      - nothing in it
      workspace  - already a Quarium workspace
      data       - holds Quarium data files but no marker, e.g. an old copy
      occupied   - holds unrelated files
    """
    result = {'path': os.path.abspath(path), 'status': 'missing',
              'entries': [], 'data_files': [], 'marker': None, 'writable': False}
    if not os.path.isdir(path):
        return result
    try:
        entries = [e for e in os.listdir(path) if e != MARKER_FILE]
    except OSError:
        return result

    result['entries'] = entries
    result['marker'] = read_marker(path)
    result['data_files'] = [n for n in DATA_FILES + CONFIG_FILES if n in entries]
    result['writable'] = os.access(path, os.W_OK)

    if result['marker']:
        result['status'] = 'workspace'
    elif result['data_files']:
        result['status'] = 'data'
    elif not entries:
        result['status'] = 'empty'
    else:
        result['status'] = 'occupied'
    return result


def copy_workspace(source, target, move=False):
    """Copies a workspace to a new folder, verifying each file before any
    original is removed."""
    os.makedirs(target, exist_ok=True)
    moved = []
    for name in DATA_FILES + CONFIG_FILES + [POINTER_FILE]:
        if name == POINTER_FILE:
            continue
        src = os.path.join(source, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(target, name)
        shutil.copy2(src, dst)
        if os.path.getsize(dst) != os.path.getsize(src):
            raise IOError(f"{name} did not copy completely; nothing was removed.")
        moved.append(name)

    for folder in EXTRA_DIRS:
        src = os.path.join(source, folder)
        dst = os.path.join(target, folder)
        if os.path.isdir(src) and not os.path.isdir(dst):
            shutil.copytree(src, dst)
            moved.append(folder + os.sep)

    write_marker(target)

    if move:
        # Only now that every file is verified at the destination.
        for name in moved:
            victim = os.path.join(source, name.rstrip(os.sep))
            try:
                if os.path.isdir(victim):
                    shutil.rmtree(victim)
                elif os.path.exists(victim):
                    os.remove(victim)
            except OSError as e:
                print(f"Copied {name} but could not remove the original: {e}")
    return moved


def resolve_active_dir(interactive=False, parent=None):
    """Settles which workspace to use before anything reads from it.

    Returns (path, action). When the configured workspace is unreachable --
    an external drive that is not plugged in -- and interactive is set, the
    user is asked whether to retry, quit, or start again in the default
    location.
    """
    global _cached_dir
    target = configured_dir()
    if not is_relocated() or os.path.isdir(target):
        _cached_dir = None
        return data_dir(), 'ok'

    if not interactive:
        _cached_dir = None
        return target, 'missing'

    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        while True:
            choice = messagebox.askyesnocancel(
                "Workspace Not Found",
                f"The chosen data folder is not available:\n\n{target}\n\n"
                "If it is on an external drive, reconnect it and choose Yes to try again.\n\n"
                "Yes  -  try again\n"
                "No   -  use the default folder and download fresh copies\n"
                "Cancel  -  close the application",
                parent=root)
            if choice is None:
                return target, 'cancelled'
            if choice:
                if os.path.isdir(target):
                    _cached_dir = None
                    return data_dir(), 'ok'
                continue
            clear_workspace()
            _cached_dir = None
            path = data_dir()
            messagebox.showinfo(
                "Default Folder",
                f"Now using:\n\n{path}\n\nYour data will be downloaded from the cloud.",
                parent=root)
            return path, 'reset'
    finally:
        root.destroy()


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
