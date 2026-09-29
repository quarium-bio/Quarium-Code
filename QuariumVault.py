"""Encrypts a workspace at rest, for data kept on a removable drive.

Every operation writes the new file, verifies it round-trips, and only then
removes the old one. A crash therefore leaves both copies rather than
neither, and a wrong passphrase is rejected before anything is touched.

This protects a drive that gets lost or stolen. It is not a backup: the
cloud copy stays unencrypted, so a forgotten passphrase costs you the local
files, not the data.
"""

import base64
import json
import os
import secrets

from QuariumPaths import CONFIG_FILES, DATA_FILES

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    Fernet = None
    InvalidToken = Exception

VAULT_FILE = 'vault.json'
ENC_SUFFIX = '.enc'
ITERATIONS = 480000

PROTECTED = DATA_FILES + CONFIG_FILES

STATE_OFF = 'off'          # encryption not configured
STATE_UNLOCKED = 'unlocked'  # configured, files readable
STATE_LOCKED = 'locked'      # files encrypted
STATE_MIXED = 'mixed'        # interrupted part way


class VaultError(Exception):
    pass


def _vault_path(workspace):
    return os.path.join(workspace, VAULT_FILE)


def read_config(workspace):
    try:
        with open(_vault_path(workspace), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def is_enabled(workspace):
    return bool(read_config(workspace))


def _derive(passphrase, salt):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode('utf-8')))


def enable(workspace, passphrase):
    """Turns encryption on and records a verifier, never the passphrase."""
    if not CRYPTO_AVAILABLE:
        raise VaultError("The cryptography library is not installed.")
    if not passphrase or len(passphrase) < 6:
        raise VaultError("Use a passphrase of at least 6 characters.")
    salt = secrets.token_bytes(16)
    key = _derive(passphrase, salt)
    # Encrypting a known value lets a wrong passphrase be rejected up front.
    verifier = Fernet(key).encrypt(b"quarium-vault-v1").decode('ascii')
    with open(_vault_path(workspace), 'w', encoding='utf-8') as f:
        json.dump({'salt': base64.b64encode(salt).decode('ascii'),
                   'verifier': verifier, 'iterations': ITERATIONS}, f, indent=2)


def disable(workspace):
    try:
        os.remove(_vault_path(workspace))
    except OSError:
        pass


def verify(workspace, passphrase):
    config = read_config(workspace)
    if not config or not CRYPTO_AVAILABLE:
        return False
    try:
        key = _derive(passphrase, base64.b64decode(config['salt']))
        return Fernet(key).decrypt(config['verifier'].encode('ascii')) == b"quarium-vault-v1"
    except (InvalidToken, KeyError, ValueError):
        return False


def state(workspace):
    if not is_enabled(workspace):
        return STATE_OFF
    encrypted = [n for n in PROTECTED if os.path.exists(os.path.join(workspace, n + ENC_SUFFIX))]
    plain = [n for n in PROTECTED if os.path.exists(os.path.join(workspace, n))]
    if encrypted and plain:
        return STATE_MIXED
    if encrypted:
        return STATE_LOCKED
    return STATE_UNLOCKED


def _key_for(workspace, passphrase):
    config = read_config(workspace)
    if not config:
        raise VaultError("Encryption is not configured for this folder.")
    if not verify(workspace, passphrase):
        raise VaultError("That passphrase does not match this folder.")
    return _derive(passphrase, base64.b64decode(config['salt']))


def lock(workspace, passphrase):
    """Encrypts the workspace. Returns the files protected."""
    if not CRYPTO_AVAILABLE:
        raise VaultError("The cryptography library is not installed.")
    fernet = Fernet(_key_for(workspace, passphrase))
    done = []
    for name in PROTECTED:
        source = os.path.join(workspace, name)
        if not os.path.exists(source):
            continue
        with open(source, 'rb') as f:
            original = f.read()
        target = source + ENC_SUFFIX
        temp = target + '.tmp'
        with open(temp, 'wb') as f:
            f.write(fernet.encrypt(original))
        # Prove it reverses before the only readable copy is removed.
        with open(temp, 'rb') as f:
            if fernet.decrypt(f.read()) != original:
                os.remove(temp)
                raise VaultError(f"{name} did not verify after encryption; nothing was removed.")
        os.replace(temp, target)
        os.remove(source)
        done.append(name)
    return done


def unlock(workspace, passphrase):
    """Decrypts the workspace. Returns the files restored."""
    if not CRYPTO_AVAILABLE:
        raise VaultError("The cryptography library is not installed.")
    fernet = Fernet(_key_for(workspace, passphrase))
    done = []
    for name in PROTECTED:
        source = os.path.join(workspace, name + ENC_SUFFIX)
        if not os.path.exists(source):
            continue
        with open(source, 'rb') as f:
            payload = f.read()
        try:
            plain = fernet.decrypt(payload)
        except InvalidToken:
            raise VaultError(f"{name} could not be decrypted; nothing was removed.")
        target = os.path.join(workspace, name)
        temp = target + '.tmp'
        with open(temp, 'wb') as f:
            f.write(plain)
        if os.path.getsize(temp) != len(plain):
            os.remove(temp)
            raise VaultError(f"{name} did not write completely; nothing was removed.")
        os.replace(temp, target)
        os.remove(source)
        done.append(name)
    return done


def recover(workspace, passphrase):
    """Brings an interrupted workspace back to a readable state.

    Duplicates are settled in favour of the readable copy, then whatever is
    still encrypted is decrypted, so a lock or unlock that stopped half way
    always finishes in the direction that leaves the data usable.
    """
    resolve_mixed(workspace)
    restored = []
    if state(workspace) in (STATE_LOCKED, STATE_MIXED):
        restored = unlock(workspace, passphrase)
    return restored


def resolve_mixed(workspace):
    """After an interrupted run both copies of a file can exist. The readable
    one is what the application was last using, so it wins and the stale
    ciphertext is discarded.

    This settles duplicates only; files that exist solely as ciphertext still
    need the passphrase, which is what recover() is for.
    """
    cleared = []
    for name in PROTECTED:
        plain = os.path.join(workspace, name)
        encrypted = plain + ENC_SUFFIX
        if os.path.exists(plain) and os.path.exists(encrypted):
            try:
                os.remove(encrypted)
                cleared.append(name)
            except OSError:
                pass
    for stray in os.listdir(workspace) if os.path.isdir(workspace) else []:
        if stray.endswith('.tmp'):
            try:
                os.remove(os.path.join(workspace, stray))
            except OSError:
                pass
    return cleared
