"""Who holds the right to edit, who is waiting, and how it changes hands.

One instance at a time may write to the shared databases. That instance holds
the lock; everyone else runs read-only. This module is the protocol for
passing it around, kept apart from the window and the network so the rules can
be reasoned about and tested on their own.

The lock is a single small JSON file in the Drive appdata folder, read and
written by every instance, so it is shaped for that: no transactions, no
server, and every field has to survive two clients writing in turn. Decisions
are therefore always made from a freshly read copy and written back whole.

When someone asks to edit, the holder chooses one of three things:

  keep      carry on editing, with a note back to the asker, who waits in the
            queue and is handed the lock when the holder closes
  hand_over save everything and give the lock away, then leave
  lend      save everything and give the lock away, but get it back when the
            borrower is done

Older clients only understand owner/last_active/request_by/response, so those
four fields keep their original meaning and are maintained alongside the rest.
An old client sees a plain held lock and a plain allow or deny, which is the
behaviour it had before.
"""

import time

# A heartbeat older than this means that instance is gone: crashed, killed, or
# off the network. Matches the window the dashboard has always used.
STALE_AFTER = 45

# While a holder is uploading, the asker must not give up: a large database on
# a slow connection takes far longer than a decision does.
HANDOVER_STALE_AFTER = 300

RESPONSE_ALLOWED = 'allowed'
RESPONSE_DENIED = 'denied'

KEEP = 'keep'
HAND_OVER = 'hand_over'
LEND = 'lend'


def new_lock():
    return {'version': 2, 'owner': None, 'last_active': 0.0, 'request_by': None,
            'response': None, 'queue': [], 'presence': {}, 'messages': {},
            'handover': None, 'return_to': None}


def _normalise(lock):
    """Fills in anything a version 1 lock, or a missing file, does not carry."""
    base = new_lock()
    if lock:
        base.update({k: v for k, v in lock.items() if v is not None or k in base})
        for key, default in new_lock().items():
            if base.get(key) is None and isinstance(default, (list, dict)):
                base[key] = default
    # A v1 lock records a pending request but has no queue; treat the asker as
    # the only person waiting, so an upgraded client does not lose them.
    if base['request_by'] and base['request_by'] not in base['queue']:
        base['queue'] = [base['request_by']] + list(base['queue'])
    # A v1 client writes no presence at all. Its asker is as live as the lock
    # itself, which is the most that protocol ever knew, so take that rather
    # than treat them as gone and skip them.
    if base['request_by'] and base['request_by'] not in base['presence']:
        base['presence'][base['request_by']] = base['last_active'] or 0.0
    return base


def held_by(lock, now=None):
    """The user currently entitled to edit, or None if the lock is free."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    if lock['owner'] and (now - (lock['last_active'] or 0)) < STALE_AFTER:
        return lock['owner']
    return None


def is_present(lock, user, now=None):
    """Whether that user's application is still running and reachable."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    if user and user == lock['owner'] and (now - (lock['last_active'] or 0)) < STALE_AFTER:
        return True
    seen = lock['presence'].get(user)
    return bool(seen and (now - seen) < STALE_AFTER)


def mark_present(lock, user, now=None):
    """Records that this instance is alive. Every instance does this, not just
    the holder, so the queue can tell who is still there to be handed to."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    lock['presence'][user] = now
    if lock['owner'] == user:
        lock['last_active'] = now
    # Forget anyone long gone, or the file grows without bound.
    lock['presence'] = {u: t for u, t in lock['presence'].items()
                        if (now - t) < STALE_AFTER * 20}
    return lock


def request(lock, user, now=None):
    """Joins the queue. Asking twice keeps the original place in line."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    if user == lock['owner']:
        return lock
    if user not in lock['queue']:
        lock['queue'].append(user)
    lock['presence'][user] = now
    lock['response'] = None
    lock['request_by'] = lock['queue'][0] if lock['queue'] else None
    return lock


def pending_request(lock, now=None):
    """The person the holder should be asked about, if any."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    for user in lock['queue']:
        if is_present(lock, user, now):
            return user
    return None


def keep(lock, requester, message="", now=None):
    """Carry on editing. The asker stays queued and is told why."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    lock['response'] = RESPONSE_DENIED
    if message:
        lock['messages'][requester] = {'from': lock['owner'], 'text': message, 'at': now}
    # Deliberately left in the queue: keeping the lock now is not a refusal to
    # ever give it up, and on close it goes to whoever has waited longest.
    if requester not in lock['queue']:
        lock['queue'].append(requester)
    lock['request_by'] = None
    return lock


def begin_handover(lock, to_user, message="", now=None):
    """Marks an upload as under way, so the waiter holds on rather than
    timing out while the databases go up."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    lock['handover'] = {'to': to_user, 'from': lock['owner'], 'state': 'uploading',
                        'at': now, 'text': message}
    lock['response'] = None
    return lock


def handover_in_progress(lock, user, now=None):
    """Whether this user is the one being handed to, and should keep waiting."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    hand = lock['handover']
    return bool(hand and hand.get('to') == user and hand.get('state') == 'uploading'
                and (now - hand.get('at', 0)) < HANDOVER_STALE_AFTER)


def complete_handover(lock, to_user, lend_back_to=None, now=None):
    """Gives the lock away. lend_back_to asks for it back when they finish."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    lock['owner'] = to_user
    lock['last_active'] = now
    lock['queue'] = [u for u in lock['queue'] if u != to_user]
    lock['response'] = RESPONSE_ALLOWED
    lock['request_by'] = None
    lock['return_to'] = lend_back_to
    lock['handover'] = {'to': to_user, 'from': lock['handover'].get('from') if lock['handover'] else None,
                        'state': 'done', 'at': now}
    lock['presence'][to_user] = now
    return lock


def release(lock, closing_user, now=None):
    """Hands the lock on as the holder leaves, or lets it go.

    A lock that was lent goes back to whoever lent it, ahead of the queue --
    they gave it up on the promise of getting it back. If that person is no
    longer running, it falls through to the longest waiter who still is.

    Returns (lock, granted_to), granted_to being None when nobody was there.
    """
    lock = _normalise(lock)
    now = time.time() if now is None else now
    if lock['owner'] != closing_user:
        return lock, None

    candidates = []
    if lock['return_to'] and lock['return_to'] != closing_user:
        candidates.append(lock['return_to'])
    candidates += [u for u in lock['queue'] if u != closing_user]

    for user in candidates:
        if is_present(lock, user, now):
            lock['owner'] = user
            lock['last_active'] = now
            lock['queue'] = [u for u in lock['queue'] if u != user]
            lock['return_to'] = None
            lock['response'] = RESPONSE_ALLOWED
            lock['request_by'] = None
            # Not claimed yet: the new holder is asked before it takes effect,
            # and until then the heartbeat is what keeps it from going stale.
            lock['handover'] = {'to': user, 'from': closing_user, 'state': 'offered',
                                'at': now}
            return lock, user

    lock['owner'] = None
    lock['return_to'] = None
    lock['request_by'] = None
    lock['response'] = None
    lock['handover'] = None
    lock['presence'].pop(closing_user, None)
    return lock, None


def offered_to(lock, user, now=None):
    """Whether this user has been handed the lock and not yet taken it up."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    hand = lock['handover']
    return bool(lock['owner'] == user and hand and hand.get('to') == user
                and hand.get('state') == 'offered')


def claim(lock, user, now=None):
    """Takes up a lock that was offered."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    lock['owner'] = user
    lock['last_active'] = now
    lock['queue'] = [u for u in lock['queue'] if u != user]
    lock['handover'] = {'to': user, 'from': (lock['handover'] or {}).get('from'),
                        'state': 'done', 'at': now}
    lock['presence'][user] = now
    return lock


def decline(lock, user, now=None):
    """Turns down an offered lock so it can go to the next person."""
    lock = _normalise(lock)
    return release(lock, user, now)


def take_free_lock(lock, user, now=None):
    """Claims a lock nobody holds."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    lock['owner'] = user
    lock['last_active'] = now
    lock['queue'] = [u for u in lock['queue'] if u != user]
    lock['request_by'] = None
    lock['response'] = None
    lock['handover'] = None
    lock['presence'][user] = now
    return lock


def message_for(lock, user):
    """A note left for this user by the holder, if there is one."""
    return _normalise(lock)['messages'].get(user)


def clear_message(lock, user):
    lock = _normalise(lock)
    lock['messages'].pop(user, None)
    return lock


def queue_position(lock, user, now=None):
    """1 for next in line, 0 if not waiting. Users no longer running are
    skipped, so the number means what it says."""
    lock = _normalise(lock)
    now = time.time() if now is None else now
    waiting = [u for u in lock['queue'] if is_present(lock, u, now)]
    return waiting.index(user) + 1 if user in waiting else 0
