import os
import io
import json
import threading
from datetime import datetime
import time
import ssl

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.auth.exceptions import RefreshError
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload, MediaIoBaseUpload
    GOOGLE_API_AVAILABLE = True
except ImportError:
    from unittest.mock import MagicMock
    GOOGLE_API_AVAILABLE = False
    Credentials = MagicMock()
    InstalledAppFlow = MagicMock()
    Request = MagicMock()
    RefreshError = Exception
    build = MagicMock()
    MediaIoBaseDownload = MagicMock()
    MediaFileUpload = MagicMock()
    MediaIoBaseUpload = MagicMock()

SCOPES = ['https://www.googleapis.com/auth/drive.appdata']


def has_client_secrets(path='credentials.json'):
    """True when the file holds a usable OAuth client ID.

    Existence alone is not enough. A company profile saved without credentials
    used to leave a zero-byte file here, which passed an exists() check and
    then failed with a parse error deep inside the OAuth flow.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    section = data.get('installed') or data.get('web') or {}
    return bool(section.get('client_id'))

class DriveSyncManager:
    def __init__(self):
        self.creds = None
        self.service = None
        self.file_versions = {}
        self.api_lock = threading.RLock()
        # Set while another user holds the edit lock. An instance in that state
        # must never replace a canonical file in the cloud: the copy it holds
        # is one it was told not to change, and the owner has been editing
        # theirs since. Anything it has to save goes up as a conflict copy.
        self.read_only = False
        if not GOOGLE_API_AVAILABLE:
            raise ImportError("Google API client libraries are not installed. Please install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        self.authenticate()

    def authenticate(self):
        with self.api_lock:
            if os.path.exists('token.json'):
                try:
                    self.creds = Credentials.from_authorized_user_file('token.json', SCOPES)
                except (OSError, ValueError) as e:
                    # An empty or truncated token is not a reason to fail the
                    # whole start-up; discard it and sign in again.
                    print(f"Ignoring unreadable token.json: {e}")
                    self.creds = None
                    try:
                        os.remove('token.json')
                    except OSError:
                        pass
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    try:
                        self.creds.refresh(Request())
                    except RefreshError:
                        self.creds = None
                        if os.path.exists('token.json'):
                            os.remove('token.json')
                
                if not self.creds or not self.creds.valid:
                    if not has_client_secrets('credentials.json'):
                        raise FileNotFoundError(
                            "No Google credentials are set up for this workspace.\n\n"
                            "Use Load New credentials.json in the Connection Manager, "
                            "with an OAuth 2.0 client ID from the Google Cloud Console.")
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    self.creds = flow.run_local_server(port=0)
                    with open('token.json', 'w') as token:
                        token.write(self.creds.to_json())
            self.service = build('drive', 'v3', credentials=self.creds)

    def list_appdata_files(self):
        results = self.service.files().list(  # type: ignore
            spaces='appDataFolder', fields='nextPageToken, files(id, name, modifiedTime)').execute()
        return {f['name']: {'id': f['id'], 'modifiedTime': f.get('modifiedTime')} for f in results.get('files', [])}

    def list_conflict_files(self):
        with self.api_lock:
            files = self.list_appdata_files()
            return {name: data for name, data in files.items() if '_conflict_' in name}
            
    def delete_file(self, file_id):
        with self.api_lock:
            try: self.service.files().delete(fileId=file_id).execute()  # type: ignore
            except Exception as e: print("Delete file error:", e)

    def read_lock(self):
        with self.api_lock:
            try:
                files = self.list_appdata_files()
                if 'lock.json' in files:
                    file_id = files['lock.json']['id']
                    content = self._download_to_memory(file_id)
                    if content:
                        return json.loads(content.decode('utf-8'))
            except Exception as e: print("Read lock error:", e)
            return None

    def write_lock(self, lock_data):
        with self.api_lock:
            try:
                # Use an in-memory file to avoid WinError 32 on os.remove
                fh = io.BytesIO(json.dumps(lock_data).encode('utf-8'))
                media = MediaIoBaseUpload(fh, mimetype='application/json', resumable=True)

                files = self.list_appdata_files()
                if 'lock.json' in files:
                    file_id = files['lock.json']['id']
                    self.service.files().update(fileId=file_id, media_body=media).execute()  # type: ignore
                else:
                    file_metadata = {'name': 'lock.json', 'parents': ['appDataFolder']}
                    self.service.files().create(body=file_metadata, media_body=media, fields='id').execute()  # type: ignore
            except Exception as e: print("Write lock error:", e)

    def _download_to_memory(self, file_id):
        """Downloads a file's content into memory with retry logic for SSL errors."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                request = self.service.files().get_media(fileId=file_id)  # type: ignore
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                return fh.getvalue()
            except ssl.SSLError as e:
                print(f"SSL Error during download (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    sleep_time = 2 ** attempt
                    print(f"Retrying in {sleep_time} seconds...")
                    time.sleep(sleep_time)
                else:
                    print("Download failed after multiple retries due to SSL errors.")
                    raise
        return None

    def download_file(self, file_id, file_path):
        content = self._download_to_memory(file_id)
        if content:
            with open(file_path, 'wb') as f:
                f.write(content)

    def upload_file(self, file_path, file_name, file_id=None):
        media = MediaFileUpload(file_path, resumable=True)
        if file_id:
            self.service.files().update(fileId=file_id, media_body=media).execute()  # type: ignore
        else:
            file_metadata = {'name': file_name, 'parents': ['appDataFolder']}
            self.service.files().create(body=file_metadata, media_body=media, fields='id').execute()  # type: ignore

    def sync_down(self, filenames):
        with self.api_lock:
            files_in_drive = self.list_appdata_files()
            for name in filenames:
                if name in files_in_drive:
                    self.download_file(files_in_drive[name]['id'], name)
                    self.file_versions[name] = files_in_drive[name]['modifiedTime']

    def sync_up(self, filenames, current_user="Unknown"):
        """Sends files to the cloud. Returns what actually happened.

        {'updated': [...], 'conflicts': [...], 'read_only': bool}

        A file is only replaced when this instance holds the edit lock and the
        cloud copy has not moved since it was last read. Otherwise the work is
        preserved as a conflict copy beside it, so nothing is lost and nobody
        else's file is overwritten. Callers must not report an upload without
        checking what came back.
        """
        with self.api_lock:
            files_in_drive = self.list_appdata_files()
            conflicts = []
            updated = []
            for name in filenames:
                if os.path.exists(name):
                    drive_file = files_in_drive.get(name)
                    if drive_file:
                        cloud_time = drive_file.get('modifiedTime')
                        known_time = self.file_versions.get(name)
                        diverged = bool(known_time and cloud_time and cloud_time != known_time)
                        # Read-only is treated exactly like a diverged file:
                        # the canonical copy belongs to whoever holds the lock.
                        if diverged or self.read_only:
                            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                            clean_user = "".join(c for c in current_user if c.isalnum()) or "Unknown"
                            conflict_name = f"{name.split('.')[0]}_conflict_{clean_user}_{timestamp}.{name.split('.')[-1]}"
                            self.upload_file(name, conflict_name, None)
                            conflicts.append(name)
                            continue
                        self.upload_file(name, name, drive_file['id'])
                        updated.append(name)
                    elif self.read_only:
                        # Nothing to overwrite, but this instance is still not
                        # the one that gets to say what the canonical file is.
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        clean_user = "".join(c for c in current_user if c.isalnum()) or "Unknown"
                        conflict_name = f"{name.split('.')[0]}_conflict_{clean_user}_{timestamp}.{name.split('.')[-1]}"
                        self.upload_file(name, conflict_name, None)
                        conflicts.append(name)
                    else:
                        self.upload_file(name, name, None)
                        updated.append(name)
            return {'updated': updated, 'conflicts': conflicts, 'read_only': self.read_only}
            return conflicts