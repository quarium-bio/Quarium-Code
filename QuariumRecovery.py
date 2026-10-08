"""Work that never reached the cloud, and what can be done about it.

SQLite commits as you go, so a power cut does not lose the session: it is
sitting in the local database files. What loses it is the next launch, which
downloads the cloud copy over the top when someone else has edited since.

Two things were missing to handle that. There was no durable record of what
the last successful sync looked like -- it lived in memory and died with the
process -- so after a crash the application could not tell an unsaved session
from a clean one, and could not tell whether the cloud had moved. And with no
such record, the conflict detection in sync_up has no baseline either, so a
recovered session would overwrite whoever had been editing in the meantime.

This module supplies that record, and what to do with it:

  clean        local matches the last sync. Nothing happened.
  local_only   local changed, the cloud did not. Nobody else has edited, so
               the work is simply sent up.
  both_changed both changed. The local copy is set aside before anything is
               downloaded, and compared afterwards so the difference can be
               described in business terms.

Nothing is merged automatically. These are relational databases whose primary
keys are handed out locally, so two offline sessions each produce a project 79
meaning different things; merging them would silently corrupt every reference
pointing at either. Rows that stand on their own -- a client, a payee, a
company, a stock item -- can be re-inserted one at a time, on request, after
the user has had a chance to look and see whether someone else already added
the same thing. Everything else is described and left to them.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime

STATE_FILE = 'sync_state.json'
RECOVERY_DIR = 'recovery'
PENDING_FILE = 'pending.json'

CLEAN = 'clean'
LOCAL_ONLY = 'local_only'
BOTH_CHANGED = 'both_changed'
NEW_LOCAL = 'new_local'

# What a recovered session can be compared on. A natural key, never the row
# id: ids are assigned locally and two machines hand out the same ones.
#
# insertable marks rows that carry no reference another row has to agree
# with, so putting one back cannot leave a dangling pointer. The rest are
# described for the user to re-enter, because re-creating them means
# re-creating the things they point at too.
RECOVERABLE = {
    'clients.db': [
        {'table': 'clients', 'key': ['name'], 'label': 'Client',
         'shows': ['email', 'phone', 'cpf_cnpj'], 'insertable': True,
         'remap': {'company_id': ('companies', 'name')}},
        {'table': 'companies', 'key': ['name'], 'label': 'Company',
         'shows': ['code', 'cnpj'], 'insertable': True},
    ],
    'payees.db': [
        {'table': 'payees', 'key': ['name'], 'label': 'Payee',
         'shows': ['kind', 'cpf_cnpj', 'email'], 'insertable': True},
    ],
    'stock.db': [
        {'table': 'stock', 'key': ['name'], 'label': 'Stock item',
         'shows': ['price', 'container_size', 'unit', 'brand'], 'insertable': True},
    ],
    'projects.db': [
        {'table': 'projects', 'key': ['estimate_number'], 'label': 'Estimate',
         'shows': ['total_samples', 'final_cost', 'responsible_user'],
         'insertable': False},
    ],
    'services.db': [
        {'table': 'services', 'key': ['name'], 'label': 'Service',
         'shows': ['code', 'description'], 'insertable': False},
    ],
}

# Bookkeeping that changes on its own and would otherwise be reported as a
# difference the user is expected to act on.
IGNORED_FIELDS = {'id', 'updated_at', 'created_at', 'last_updated', 'updated_by',
                  'created_by'}


# ----------------------------------------------------------------- the record

def fingerprint(path):
    """Enough to tell whether a file changed. Hashed, not timestamped: a
    restored backup or a clock adjustment moves an mtime without the contents
    differing, and the whole point here is to be sure."""
    if not os.path.exists(path):
        return None
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return {'size': os.path.getsize(path), 'sha256': digest.hexdigest()}


def load_state(base_dir):
    try:
        with open(os.path.join(base_dir, STATE_FILE), 'r', encoding='utf-8') as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(base_dir, state):
    """Written through a temporary file: a half-written state record is worse
    than none, because it would be trusted."""
    path = os.path.join(base_dir, STATE_FILE)
    temp = path + '.tmp'
    try:
        with open(temp, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except OSError as e:
        print(f"Could not record the sync state: {e}")
        try:
            os.remove(temp)
        except OSError:
            pass


def record_sync(base_dir, filenames, cloud_times):
    """Notes what each file looked like, and which cloud version it matched,
    immediately after a successful sync."""
    state = load_state(base_dir)
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for name in filenames:
        print_ = fingerprint(os.path.join(base_dir, name))
        if print_ is None:
            continue
        state[name] = {'cloud_time': cloud_times.get(name), 'at': stamp, **print_}
    save_state(base_dir, state)
    return state


def classify(base_dir, filenames, cloud_times, state=None):
    """What happened to each file since the last sync."""
    state = load_state(base_dir) if state is None else state
    verdict = {}
    for name in filenames:
        path = os.path.join(base_dir, name)
        if not os.path.exists(path):
            continue
        known = state.get(name)
        if not known:
            # Never recorded: either a first run, or an upgrade from a version
            # that kept no record. Nothing can be claimed about it.
            verdict[name] = NEW_LOCAL
            continue
        now = fingerprint(path)
        local_changed = not (now and now['sha256'] == known.get('sha256'))
        cloud_changed = (cloud_times.get(name) or None) != (known.get('cloud_time') or None)
        if not local_changed:
            verdict[name] = CLEAN
        elif not cloud_changed:
            verdict[name] = LOCAL_ONLY
        else:
            verdict[name] = BOTH_CHANGED
    return verdict


# ---------------------------------------------------------------- stashing

def stash(base_dir, name, when=None):
    """Puts the unsaved copy somewhere safe before anything overwrites it."""
    folder = os.path.join(base_dir, RECOVERY_DIR)
    os.makedirs(folder, exist_ok=True)
    stamp = when or datetime.now().strftime('%Y%m%d_%H%M%S')
    target = os.path.join(folder, f"{os.path.splitext(name)[0]}_{stamp}{os.path.splitext(name)[1]}")
    shutil.copy2(os.path.join(base_dir, name), target)
    return target


# ---------------------------------------------------------------- comparing

def _rows(path, table):
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _key_of(row, key_fields):
    return tuple(str(row.get(f) or '').strip().lower() for f in key_fields)


def compare(stashed_path, live_path, specs):
    """What the unsaved copy has that the live one does not.

    Returns a list of findings: rows only the stashed copy knows about, and
    rows both have but disagree on.
    """
    findings = []
    for spec in specs:
        stashed = _rows(stashed_path, spec['table'])
        live = _rows(live_path, spec['table'])
        if stashed is None or live is None:
            continue
        live_by_key = {_key_of(r, spec['key']): r for r in live}
        for row in stashed:
            key = _key_of(row, spec['key'])
            if not any(part for part in key):
                continue
            name = ' / '.join(str(row.get(f) or '') for f in spec['key'])
            if key not in live_by_key:
                findings.append({
                    'kind': 'added', 'table': spec['table'], 'label': spec['label'],
                    'name': name, 'insertable': spec['insertable'],
                    'detail': {f: row.get(f) for f in spec.get('shows', []) if row.get(f) not in (None, '')},
                    'row': row, 'key': spec['key'], 'remap': spec.get('remap', {}),
                })
                continue
            other = live_by_key[key]
            changes = {}
            for field, value in row.items():
                if field in IGNORED_FIELDS:
                    continue
                if field in other and other[field] != value:
                    changes[field] = {'theirs': other[field], 'yours': value}
            if changes:
                findings.append({
                    'kind': 'changed', 'table': spec['table'], 'label': spec['label'],
                    'name': name, 'insertable': False, 'changes': changes,
                })
    return findings


def compare_workspace(stashes, base_dir):
    """Every stashed database against its live counterpart.

    stashes maps a database name to the copy set aside for it.
    """
    findings = []
    for name, stashed_path in stashes.items():
        specs = RECOVERABLE.get(name)
        if not specs:
            continue
        for item in compare(stashed_path, os.path.join(base_dir, name), specs):
            item['database'] = name
            item['stash'] = stashed_path
            findings.append(item)
    return findings


# ------------------------------------------------------------- the pending list

def _pending_path(base_dir):
    return os.path.join(base_dir, RECOVERY_DIR, PENDING_FILE)


def load_pending(base_dir):
    try:
        with open(_pending_path(base_dir), 'r', encoding='utf-8') as f:
            items = json.load(f)
        return items if isinstance(items, list) else []
    except (OSError, ValueError):
        return []


def save_pending(base_dir, items):
    os.makedirs(os.path.join(base_dir, RECOVERY_DIR), exist_ok=True)
    path = _pending_path(base_dir)
    temp = path + '.tmp'
    try:
        with open(temp, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2, default=str)
        os.replace(temp, path)
    except OSError as e:
        print(f"Could not save the recovery list: {e}")


def add_pending(base_dir, findings):
    """Keeps findings for later. They survive restarts on purpose: the point
    is to let someone look around first and decide in their own time."""
    items = load_pending(base_dir)
    existing = {(i.get('database'), i.get('table'), i.get('name'), i.get('kind')) for i in items}
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for finding in findings:
        signature = (finding.get('database'), finding.get('table'),
                     finding.get('name'), finding.get('kind'))
        if signature in existing:
            continue
        item = dict(finding)
        item['found_at'] = stamp
        item['id'] = hashlib.sha1(
            f"{signature}{stamp}{time.time()}".encode()).hexdigest()[:12]
        items.append(item)
        existing.add(signature)
    save_pending(base_dir, items)
    return items


def resolve_pending(base_dir, item_id):
    items = [i for i in load_pending(base_dir) if i.get('id') != item_id]
    save_pending(base_dir, items)
    return items


def already_present(base_dir, item):
    """Whether the live data now has something under this name.

    The whole reason the list waits is that somebody else may have added the
    same thing in the meantime, so this is checked when the list is shown, not
    when it was built.
    """
    rows = _rows(os.path.join(base_dir, item['database']), item['table'])
    if rows is None:
        return None
    key_fields = item.get('key') or ['name']
    wanted = tuple(part.strip().lower() for part in str(item['name']).split(' / '))
    for row in rows:
        if _key_of(row, key_fields) == wanted:
            return row
    return None


def reinsert(base_dir, item):
    """Puts a self-contained row back, giving it a fresh id.

    References that matter are remapped by name, because the id the row
    carried belonged to the database it came from. If the thing it pointed at
    is not here, the reference is dropped rather than left pointing at
    whatever happens to hold that id now.
    """
    if not item.get('insertable'):
        raise ValueError("That entry has to be re-entered by hand.")
    if already_present(base_dir, item):
        raise ValueError(f"'{item['name']}' is already there.")

    row = dict(item['row'])
    row.pop('id', None)
    path = os.path.join(base_dir, item['database'])
    conn = sqlite3.connect(path)
    try:
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({item['table']})")}
        for field, (ref_table, ref_key) in (item.get('remap') or {}).items():
            if field not in row or row.get(field) is None:
                continue
            original = _lookup_name(item.get('stash'), ref_table, ref_key, row[field])
            row[field] = _lookup_id(conn, ref_table, ref_key, original) if original else None
        row = {k: v for k, v in row.items() if k in columns}
        if not row:
            raise ValueError("Nothing in that entry fits the current database.")
        placeholders = ', '.join('?' * len(row))
        conn.execute(f"INSERT INTO {item['table']} ({', '.join(row)}) "
                     f"VALUES ({placeholders})", list(row.values()))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def _lookup_name(stash_path, table, key_field, row_id):
    if not stash_path or not os.path.exists(stash_path):
        return None
    rows = _rows(stash_path, table)
    for row in rows or []:
        if row.get('id') == row_id:
            return row.get(key_field)
    return None


def _lookup_id(conn, table, key_field, value):
    if value is None:
        return None
    try:
        found = conn.execute(f"SELECT id FROM {table} WHERE {key_field} = ?",
                             (value,)).fetchone()
    except sqlite3.Error:
        return None
    return found[0] if found else None


def open_stashed_estimate(base_dir, item):
    """A cursor over the set-aside projects.db, ready to render an estimate.

    The estimate's services and client live in other databases, which may not
    have been set aside: only a database that changed on both sides is. The
    live ones stand in where there is no stashed copy, which is right as well
    as convenient, since a service's name and a client's details are what
    they are now, not what they were at the moment of the crash.

    Returns (connection, cursor, project_id), or (None, None, None).
    """
    stash_path = item.get('stash')
    if not stash_path or not os.path.exists(stash_path):
        return None, None, None
    conn = sqlite3.connect(f"file:{stash_path}?mode=ro", uri=True)
    cur = conn.cursor()
    folder = os.path.dirname(stash_path)
    for alias, name in (('clients_db', 'clients.db'), ('services_db', 'services.db')):
        candidate = os.path.join(base_dir, name)
        # Prefer a stashed copy of the same vintage if one was taken.
        same_vintage = [f for f in sorted(os.listdir(folder))
                        if f.startswith(os.path.splitext(name)[0] + '_') and f.endswith('.db')]
        if same_vintage:
            candidate = os.path.join(folder, same_vintage[-1])
        try:
            cur.execute(f"ATTACH DATABASE ? AS {alias}", (candidate,))
        except sqlite3.Error as e:
            print(f"Could not attach {name} for the recovered estimate: {e}")
    row = cur.execute("SELECT id FROM projects WHERE estimate_number = ?",
                      (item['name'],)).fetchone()
    if not row:
        conn.close()
        return None, None, None
    return conn, cur, row[0]


def describe(item):
    """One line a person can act on, in business terms rather than row ids."""
    if item['kind'] == 'added':
        extra = ", ".join(f"{k}: {v}" for k, v in (item.get('detail') or {}).items())
        return f"{item['label']} '{item['name']}'" + (f"  ({extra})" if extra else "")
    parts = []
    for field, pair in (item.get('changes') or {}).items():
        parts.append(f"{field}: {pair['theirs']} → {pair['yours']}")
    return f"{item['label']} '{item['name']}' — " + "; ".join(parts)
