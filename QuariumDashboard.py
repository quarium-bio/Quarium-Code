import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import os
import sys
import json
import base64
import calendar
import secrets
import zipfile
import shutil
import threading
import time
from tkinter import filedialog
import sqlite3
import urllib.request
import urllib.error
import re
import secrets

# Working data lives under %LOCALAPPDATA%, not beside the program: keeping
# live SQLite files inside the OneDrive-synced project folder meant two sync
# engines replicating the same open databases. Source runs get a separate
# workspace so testing cannot disturb live data.
import QuariumPaths
from QuariumPaths import data_dir

# Settle which workspace to use before the managers below import, because each
# captures _BASE_DIR at import time. If a relocated folder is unreachable --
# an external drive that is not plugged in -- this is where the user is asked,
# before anything tries to read from it.
if __name__ == "__main__":
    _resolved_dir, _resolve_action = QuariumPaths.resolve_active_dir(interactive=True)
    if _resolve_action == 'cancelled':
        sys.exit(0)

_BASE_DIR = data_dir()


def asset_path(name):
    """Finds a logo or template wherever this build keeps it.

    The workspace copy wins, but it only arrives with the first sync. Before
    that -- a fresh install, or the launch right after a profile switch has
    cleared the workspace -- fall back to whatever ships with the program, so
    the splash screen is not left blank.
    """
    candidates = [os.path.join(_BASE_DIR, name)]
    bundled = getattr(sys, '_MEIPASS', None)
    if bundled:
        candidates.append(os.path.join(bundled, name))
    candidates.append(os.path.join(QuariumPaths.program_dir(), name))
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

# Import the application classes
from QuariumClientManager import ClientManager
from QuariumServiceManager import ServiceManager
from QuariumProjectManager import ProjectManager # New import
from CompositeStockManager import CompositeStockManager
from QuariumSM import StockManager
from QuariumProjectFlow import ProjectFlowManager
from QuariumContractManager import ContractManager # New import
from QuariumFinanceManager import FinanceManager
from QuariumDebts import DebtsManager
import QuariumLock as QL
import QuariumRecovery as QR
from QuariumPayeeManager import PayeeManager

try:
    from QuariumDriveSync import DriveSyncManager
except ImportError:
    DriveSyncManager = None

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    from unittest.mock import MagicMock
    CRYPTO_AVAILABLE = False
    Fernet = MagicMock()
    InvalidToken = Exception
    hashes = MagicMock()
    PBKDF2HMAC = MagicMock()
    # Add this line to prevent errors if crypto is missing
    os.urandom = lambda x: b'x' * x 

CURRENT_VERSION = "2.2.0"
UPDATE_URL = "https://raw.githubusercontent.com/quarium-bio/Quarium-Code/main/version.json" # Change to your actual raw URL

class SectionNav(ttk.Frame):
    """Side navigation that stands in for a ttk.Notebook.

    Exposes the same add(child, text=...) call, so sections built for a
    notebook work unchanged, but shows them from a grouped list on the left
    instead of a row of tabs that runs out of width.
    """

    def __init__(self, parent, nav_width=210, groups=(), **kwargs):
        super().__init__(parent, **kwargs)
        self.tree = ttk.Treeview(self, show="tree", selectmode="browse", height=18)
        self.tree.column("#0", width=nav_width, stretch=False)
        self.tree.pack(side="left", fill="y")
        ttk.Separator(self, orient="vertical").pack(side="left", fill="y", padx=(10, 14))
        self.body = ttk.Frame(self)
        self.body.pack(side="left", fill="both", expand=True)

        self._sections = {}
        self._groups = {}
        self._current = None
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # Declared up front so the sidebar reads in a deliberate order rather
        # than whatever order the sections happen to be built in.
        for name in groups:
            self._groups[name] = self.tree.insert("", "end", text=name, open=True)

    def add(self, child, text="", group=None):
        parent = ""
        if group:
            if group not in self._groups:
                self._groups[group] = self.tree.insert("", "end", text=group, open=True)
            parent = self._groups[group]
        iid = self.tree.insert(parent, "end", text="   " + text)
        self._sections[iid] = child
        if len(self._sections) == 1:
            self.tree.selection_set(iid)
            self._show(iid)
        return iid

    def _on_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        if iid not in self._sections:
            # A group heading was clicked; fall through to its first section.
            children = self.tree.get_children(iid)
            if children:
                self.tree.selection_set(children[0])
            return
        self._show(iid)

    def _show(self, iid):
        if self._current == iid:
            return
        for section in self._sections.values():
            section.pack_forget()
        self._sections[iid].pack(in_=self.body, fill="both", expand=True)
        self._current = iid


class QuariumDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Quarium Dashboard")
        self.root.withdraw() # Hide until authenticated

        self.apps = {}
        self.frames = {}
        self.current_user = None
        self.drive_sync = None  # Initialize to None
        self.vault_passphrase = None  # set by the launcher when the folder is encrypted
        self.local_file_mod_times = {} # To track local file changes
        # payees.db must sync: project_cost_splits and payee_settlements live in
        # projects.db and reference payee ids, so without it another machine
        # would hold splits pointing at payees it has never heard of.
        self.db_files = ['stock.db', 'services.db', 'clients.db', 'projects.db', 'payees.db', 'users.json', 'settings.json', 'QLogo.png', 'EstimateLogo.png', 'ContractTemplate_PF.docx', 'ContractTemplate_PJ.docx', 'ContractShell.docx', 'ContractHeader_PF.docx', 'ContractHeader_PJ.docx']
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.create_splash_screen()
        # Run the startup sequence in a separate thread to keep the splash screen responsive
        threading.Thread(target=self.startup_sequence, daemon=True).start()

    def _hash_password(self, password: str, salt: bytes) -> str:
        """Hashes a password with the given salt."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        key = kdf.derive(password.encode())
        return base64.urlsafe_b64encode(key).decode('utf-8')

    def _verify_password(self, stored_hash: str, salt_b64: str, provided_password: str) -> bool:
        """Verifies a provided password against a stored hash and salt."""
        salt = base64.urlsafe_b64decode(salt_b64.encode('utf-8'))
        return self._hash_password(provided_password, salt) == stored_hash

    def _claim_taskbar_button(self, window):
        """Gives a frameless window its own taskbar button and alt-tab entry.

        A window with no title bar is a plain popup as far as Windows is
        concerned, so it gets neither. That matters during startup: the main
        window is withdrawn until the user has logged in, so the splash is the
        only thing on screen, and without this it cannot be brought back once
        another window covers it. Startup can take a while when the sync has
        work to do, which is exactly when someone switches away.

        Best effort. A window that keeps working but sits behind something is
        better than no window at all, so failure here is reported and ignored.
        """
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000

            window.update_idletasks()
            hwnd = int(window.wm_frame(), 16)
            user32 = ctypes.windll.user32
            # The Ptr forms exist only on 64-bit; the plain ones truncate a
            # style word there, so pick the right pair rather than assume.
            get_style = getattr(user32, 'GetWindowLongPtrW', None) or user32.GetWindowLongW
            set_style = getattr(user32, 'SetWindowLongPtrW', None) or user32.SetWindowLongW
            get_style.restype = ctypes.c_ssize_t
            get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
            set_style.restype = ctypes.c_ssize_t
            set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]

            style = get_style(hwnd, GWL_EXSTYLE)
            set_style(hwnd, GWL_EXSTYLE, (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
            # The taskbar only reads the style when the window is shown, so it
            # has to be taken down and put back for the change to register.
            window.withdraw()
            window.deiconify()
        except Exception as e:
            print("Could not give the splash its own taskbar button:", e)

    def create_splash_screen(self):
        self.splash = tk.Toplevel(self.root)
        self.splash.overrideredirect(True) # No title bar
        # Not drawn while there is no title bar, but it is what the taskbar
        # button and the alt-tab entry are labelled with.
        self.splash.title("Quarium Dashboard")

        width, height = 450, 300
        screen_width = self.splash.winfo_screenwidth()
        screen_height = self.splash.winfo_screenheight()
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        self.splash.geometry(f'{width}x{height}+{x}+{y}')
        
        splash_frame = ttk.Frame(self.splash, style="TFrame", relief="solid", borderwidth=1)
        splash_frame.pack(fill="both", expand=True)

        logo_path = asset_path('QLogo.png')
        if logo_path:
            try:
                from PIL import Image, ImageTk
                img = Image.open(logo_path)
                img.thumbnail((150, 150)) # Resize to max 150x150, preserving aspect ratio
                self.splash_logo_img = ImageTk.PhotoImage(img)
            except ImportError:
                # Fallback for when Pillow is not installed
                self.splash_logo_img = tk.PhotoImage(file=logo_path)
                if self.splash_logo_img.height() > 150:
                    factor = self.splash_logo_img.height() // 150
                    self.splash_logo_img = self.splash_logo_img.subsample(factor, factor)
            
            logo_label = ttk.Label(splash_frame, image=self.splash_logo_img)
            logo_label.image = self.splash_logo_img # Keep a reference
            logo_label.pack(pady=(20, 10))

        ttk.Label(splash_frame, text="Quarium Dashboard", font=('Helvetica', 16, 'bold')).pack()

        # The status text changes at every step, and a label that resizes with
        # its text leaves the strip it used to occupy unpainted. There is no
        # title bar here, so no window manager forces a repaint, and the old
        # messages stay on screen stacked under the new one. Pin the strip to a
        # constant size and give it an opaque background of its own, so each
        # redraw covers everything drawn before it.
        status_bg = ttk.Style().lookup('TFrame', 'background') or 'SystemButtonFace'
        status_holder = tk.Frame(splash_frame, height=24, background=status_bg)
        status_holder.pack(fill="x", padx=20, pady=(20, 5))
        status_holder.pack_propagate(False)
        self.splash_status_label = tk.Label(status_holder, text="Initializing...",
                                            font=('Helvetica', 10), background=status_bg,
                                            anchor="center")
        self.splash_status_label.pack(fill="both", expand=True)
        
        self.splash_progress = ttk.Progressbar(splash_frame, orient="horizontal", length=300, mode='determinate')
        self.splash_progress.pack(pady=10)

        # Once the contents exist, so the window is its final size when the
        # taskbar picks it up.
        self._claim_taskbar_button(self.splash)

    def update_splash(self, text, value):
        # Ensure UI updates are done on the main thread
        def do_update():
            if hasattr(self, 'splash') and self.splash.winfo_exists():
                self.splash_status_label.config(text=text)
                self.splash_progress['value'] = value
                # Repaint now. The UI build runs straight through on the main
                # thread without the loop ever going idle, so queueing this
                # would show nothing until the splash had already gone.
                try:
                    self.splash.update_idletasks()
                except tk.TclError:
                    pass
        if threading.current_thread() is threading.main_thread():
            do_update()
        else:
            self.root.after(0, do_update)
        
    def check_updates(self):
        try:
            req = urllib.request.Request(UPDATE_URL, headers={'User-Agent': 'QuariumApp/1.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
            
            latest_version = data.get("version", CURRENT_VERSION)
            level = data.get("level", "Patch")
            dl_url = data.get("download_url", "https://github.com")
            
            def v_tuple(v): return tuple(map(int, (v.split('.'))))
            
            if v_tuple(latest_version) > v_tuple(CURRENT_VERSION):
                msg = f"A new {level} update is available!\n\nCurrent Version: {CURRENT_VERSION}\nNew Version: {latest_version}\n\nDo you want to visit the download page?"
                if messagebox.askyesno("Update Available", msg):
                    import webbrowser
                    webbrowser.open(dl_url)
                    if level == "Critical":
                        self.root.destroy()
                        return False
                else:
                    if level == "Critical":
                        messagebox.showwarning("Critical Update Declined", "You have declined a Critical update. The software may become unstable or fail to sync correctly.")
        except Exception as e:
            print("Update check failed (this is normal if offline or URL is invalid):", e)
        return True

    def load_local_config(self):
        if os.path.exists('local_config.json'):
            try:
                with open('local_config.json', 'r') as f:
                    return json.load(f)
            except Exception: pass
        return {"active_company": None, "companies": {}}

    def save_local_config(self, config):
        with open('local_config.json', 'w') as f:
            json.dump(config, f)

    def derive_company_name(self, credentials_json):
        """Names an adopted connection without stopping to ask for one.

        Prefers the company already configured for contracts and estimates,
        since that is the name the user would have typed anyway, and falls
        back to the Google project the credentials were issued for.
        """
        try:
            with open('settings.json', 'r', encoding='utf-8') as f:
                configured = json.load(f).get('contract_info', {}).get('company_name', '')
            words = str(configured).strip().split()
            if words:
                return words[0]
        except (OSError, ValueError, AttributeError):
            pass
        try:
            for block in json.loads(credentials_json).values():
                if isinstance(block, dict) and block.get('project_id'):
                    return str(block['project_id'])
        except (ValueError, AttributeError):
            pass
        return "Default Company"

    def save_current_tokens_to_profile(self):
        config = self.load_local_config()
        active = config.get("active_company")
        if active and active in config["companies"]:
            if os.path.exists("token.json"):
                with open("token.json", "r") as f:
                    config["companies"][active]["token"] = f.read()
            self.save_local_config(config)

    def clean_local_workspace(self):
        for f in self.db_files:
            if os.path.exists(f):
                try: os.remove(f)
                except Exception: pass

    def apply_profile(self, comp_name, config):
        # The wipe exists so switching company profiles cannot leave the
        # previous company's data behind. Running it on every start also
        # deleted files that were already up to date, which left
        # _conditional_sync_down with nothing on disk to compare against and
        # forced a full re-download of all synced files each launch.
        if config.get("workspace_company") != comp_name:
            self.clean_local_workspace()
            config["workspace_company"] = comp_name
            self.save_local_config(config)
        prof = config["companies"].get(comp_name)
        if not prof: return
        creds = (prof.get("credentials") or "").strip()
        if creds:
            with open("credentials.json", "w") as f:
                f.write(creds)
        elif os.path.exists("credentials.json"):
            # Writing an empty file here is worse than writing nothing: the sync
            # manager only checks that the path exists, so a blank file reads as
            # a configured connection and then fails inside the OAuth flow.
            os.remove("credentials.json")
        if prof.get("token"):
            with open("token.json", "w") as f:
                f.write(prof["token"])
        else:
            if os.path.exists("token.json"):
                os.remove("token.json")

    def startup_sequence(self):
        self.update_splash("Checking for updates...", 10)
        if not self.check_updates():
            self.root.after(0, self.root.destroy)
            return
            
        self.update_splash("Loading local configuration...", 20)
        config = self.load_local_config()
        
        # Adopts a connection that predates company profiles. A workspace
        # seeded from an older install brings credentials.json across without
        # a local_config.json, so this now runs on an ordinary first start and
        # has to be silent about it: the name is derived rather than asked for,
        # and the Connection Manager can change it afterwards.
        #
        # It used to ask here with simpledialog, from this worker thread. The
        # dialog belongs to the main loop, so waiting on it never returned and
        # the splash sat on "Loading local configuration..." for good.
        if os.path.exists("credentials.json") and not config["companies"]:
            with open("credentials.json", "r") as f: creds = f.read()
            token_data = ""
            if os.path.exists("token.json"):
                with open("token.json", "r") as f: token_data = f.read()

            comp_name = self.derive_company_name(creds)
            config["companies"][comp_name] = {"credentials": creds, "token": token_data}
            config["active_company"] = comp_name
            self.save_local_config(config)
            
        active = config.get("active_company")
        profile = config["companies"].get(active) if active else None
        # A source checkout is seeded with its credentials stripped, so that
        # testing a branch cannot reach the live Drive by accident. That profile
        # is not something to connect with: fall through to the Connection
        # Manager, where a credentials.json can be loaded deliberately.
        has_credentials = bool(profile and (profile.get("credentials") or "").strip())
        if has_credentials and not getattr(self, 'force_disconnect', False):
            self.update_splash(f"Connecting to '{active}'...", 30)
            self.apply_profile(active, config)
            self.authenticate_and_sync()
        else:
            self.update_splash("Awaiting connection setup...", 30)
            self.root.after(0, self.show_connection_manager)

    def continue_startup_after_conn_manager(self):
        # authenticate_and_sync is written for a worker thread: it marshals
        # every dialog back with after(). Running it on the main thread instead
        # froze the window for the whole OAuth round trip and the first sync,
        # so nothing was drawn while the browser waited for consent.
        threading.Thread(target=self.authenticate_and_sync, daemon=True).start()

    def show_connection_manager(self):
        self.root.withdraw()
        # Distinguishes "the user closed this window without connecting" from
        # "a connection is under way". current_user is not set until the login
        # dialog, long after this window has gone, so it cannot answer that.
        self.connection_started = False
        conn_win = tk.Toplevel(self.root)
        conn_win.title("Connection Manager")
        conn_win.geometry("450x420")
        conn_win.grab_set()
        
        config = self.load_local_config()
        
        ttk.Label(conn_win, text="Welcome to Quarium", font=("Helvetica", 14, "bold")).pack(pady=10)
        ttk.Label(conn_win, text="Select an existing company profile or load a new credentials.json file to connect.", wraplength=400, justify="center").pack(pady=10)
        
        # Only profiles that actually carry credentials can be connected with.
        # Offering one that cannot would just fail later in the OAuth flow.
        connectable = [name for name, prof in config["companies"].items()
                       if (prof.get("credentials") or "").strip()]
        if connectable:
            ttk.Label(conn_win, text="Saved Companies:").pack(pady=(10,0))
            comp_var = tk.StringVar()
            cb = ttk.Combobox(conn_win, textvariable=comp_var, values=connectable, state="readonly", width=30)
            cb.pack(pady=5)
            if config.get("active_company") in connectable:
                cb.set(config["active_company"])
            
            def connect_existing():
                sel = comp_var.get()
                if sel:
                    config["active_company"] = sel
                    self.save_local_config(config)
                    self.apply_profile(sel, config)
                    self.connection_started = True
                    conn_win.destroy()
                    self.continue_startup_after_conn_manager()

            # An adopted connection is named without asking, so there has to be
            # somewhere to correct it. Safe to prompt from here: this is a
            # button callback on the main thread, not the startup worker.
            def rename_existing():
                sel = comp_var.get()
                if not sel:
                    return
                new_name = simpledialog.askstring(
                    "Rename Connection", "Name for this connection:",
                    initialvalue=sel, parent=conn_win)
                new_name = (new_name or "").strip()
                if not new_name or new_name == sel:
                    return
                if new_name in config["companies"]:
                    messagebox.showerror(
                        "Rename Connection",
                        f"There is already a connection called '{new_name}'.",
                        parent=conn_win)
                    return
                config["companies"][new_name] = config["companies"].pop(sel)
                if config.get("active_company") == sel:
                    config["active_company"] = new_name
                self.save_local_config(config)
                connectable[connectable.index(sel)] = new_name
                cb.config(values=connectable)
                cb.set(new_name)

            buttons = ttk.Frame(conn_win)
            buttons.pack(pady=5)
            ttk.Button(buttons, text="Connect", command=connect_existing,
                       style="Accent.TButton").pack(side="left", padx=4)
            ttk.Button(buttons, text="Rename", command=rename_existing).pack(side="left", padx=4)
        
        ttk.Separator(conn_win, orient="horizontal").pack(fill="x", pady=15, padx=20)
        
        def load_new():
            filepath = filedialog.askopenfilename(title="Select credentials.json", filetypes=[("JSON Files", "*.json")])
            if not filepath: return
            
            comp_name = simpledialog.askstring("New Connection", "Enter the Company Name for this connection:", parent=conn_win)
            if not comp_name: return
            
            try:
                with open(filepath, "r") as f: creds_data = f.read()
                if "client_id" not in creds_data:
                    raise ValueError("Invalid credentials.json format")
                    
                config["companies"][comp_name] = {"credentials": creds_data, "token": ""}
                config["active_company"] = comp_name
                self.save_local_config(config)
                self.apply_profile(comp_name, config)
                self.update_splash(f"Connecting to '{comp_name}'...", 40)
                self.connection_started = True
                conn_win.destroy()
                self.continue_startup_after_conn_manager()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load credentials: {e}", parent=conn_win)
            
        ttk.Button(conn_win, text="Load New credentials.json", command=load_new).pack(pady=5)
        ttk.Button(conn_win, text="How to create credentials.json (Tutorial)", command=self.show_tutorial).pack(pady=5)
        
        self.root.wait_window(conn_win)
        # Close only if the window was dismissed without starting a connection.
        # Testing current_user here used to queue root.destroy even on success,
        # and because that ran before the login dialog it tore the application
        # down just as authentication finished.
        if not getattr(self, 'connection_started', False) and not getattr(self, 'current_user', None):
            self.root.after(0, self.root.destroy)

    def authenticate_and_sync(self):
        # Pre-fetch lock info to see who is online for the login dialog
        online_owner = None
        if self.drive_sync:
            try:
                lock_data = self.drive_sync.read_lock()
                if lock_data and lock_data.get('owner') and (time.time() - lock_data.get('last_active', 0) < 45):
                    online_owner = lock_data.get('owner')
            except Exception: pass
        self.update_splash("Authenticating with Google Drive...", 45)
        if DriveSyncManager:
            try:
                self.drive_sync = DriveSyncManager()
            except ImportError as e:
                detail = str(e)
                self.root.after(0, lambda: messagebox.showwarning("Sync Dependencies Missing", f"{detail}\n\nOperating in local mode."))
                self.drive_sync = None
            except Exception as e:
                # Format the message now. `e` is unbound once the except block
                # ends, so a lambda that reads it later dies with NameError and
                # hides the very failure it was meant to report.
                detail = f"{type(e).__name__}: {e}"
                print("Drive sync failed:", detail)
                self.root.after(0, lambda: messagebox.showerror("Sync Error", f"An unexpected error occurred during Google Drive sync:\n\n{detail}\n\nOperating in local mode."))
                self.drive_sync = None
        else:
            self.root.after(0, lambda: messagebox.showinfo("Local Mode", "QuariumDriveSync not found or Google API libraries missing. Operating locally."))
            self.drive_sync = None

        self.update_splash("Syncing user data...", 55)
        # **Fix:** Explicitly download the users.json file before showing the login dialog.
        # This ensures that even on a fresh install, the user list is populated from the cloud.
        if self.drive_sync:
            try:
                self.drive_sync.sync_down(['users.json'])
            except Exception as e:
                self.update_splash("Retrying user data sync...", 60)
                print(f"Could not pre-sync users.json on first attempt: {e}. Retrying...") # Keep for debug
                time.sleep(2) # Wait 2 seconds before retrying
                try:
                    self.drive_sync.sync_down(['users.json'])
                except Exception as e2:
                    print(f"Could not pre-sync users.json on second attempt: {e2}")
            
        self.update_splash("Awaiting user login...", 70)
        self.root.after(0, lambda: self.show_login_dialog(online_owner))
        self.save_current_tokens_to_profile()

    def show_tutorial(self):
        tut_win = tk.Toplevel(self.root)
        tut_win.title("Tutorial: credentials.json")
        tut_win.geometry("600x500")
        
        txt = tk.Text(tut_win, wrap="word", padx=15, pady=15, font=("Helvetica", 10))
        txt.pack(fill="both", expand=True)
        
        tutorial_content = """**AVISO: Este tutorial foi escrito por Inteligência Artificial e a interface do Google Cloud pode ter sofrido pequenas alterações.**\n\nPASSO A PASSO PARA OBTER O credentials.json:\n\n1. Acesse o Google Cloud Console (https://console.cloud.google.com/) e faça login com a conta Google da empresa.\n2. No topo, clique em "Selecione um projeto" (ou no nome do projeto atual) e depois em "Novo Projeto". Dê um nome (ex: QuariumApp) e clique em "Criar".\n3. Com o projeto selecionado, acesse o menu lateral (três linhas) > "APIs e Serviços" > "Biblioteca".\n4. Pesquise por "Google Drive API", clique nela e depois em "Ativar".\n5. Volte para "APIs e Serviços" e clique em "Tela de consentimento OAuth".\n6. Escolha "Externo" (ou Interno se tiver Google Workspace) e clique em "Criar".\n7. Preencha os campos obrigatórios (Nome do app, email de suporte, dados do desenvolvedor) e clique em "Salvar e Continuar". Você pode pular a aba de Escopos. Na aba "Usuários de teste", adicione os emails das pessoas que farão login no app.\n8. Após finalizar, vá em "APIs e Serviços" > "Credenciais".\n9. Clique em "Criar Credenciais" > "ID do cliente OAuth".\n10. Tipo de aplicativo: escolha "App para computador" (Desktop app) e dê um nome. Clique em "Criar".\n11. Uma janela aparecerá. Clique no botão de DOWNLOAD (arquivo JSON) para baixá-lo.\n12. Carregue este arquivo baixado através do botão "Load New credentials.json" no Quarium!"""

        txt.insert("1.0", tutorial_content)
        txt.config(state="disabled")

    def show_login_dialog(self, online_owner=None):
        login_win = tk.Toplevel(self.root)
        login_win.title("User Login")
        login_win.geometry("350x280")
        login_win.grab_set()
        login_win.focus_force() # Make sure it appears on top

        # Center the window
        login_win.update_idletasks()
        width = login_win.winfo_width()
        height = login_win.winfo_height()
        x = (login_win.winfo_screenwidth() // 2) - (width // 2)
        y = (login_win.winfo_screenheight() // 2) - (height // 2)
        login_win.geometry(f'{width}x{height}+{x}+{y}')

        users = {}
        if os.path.exists('users.json'):
            with open('users.json', 'r') as f:
                users = json.load(f)
        
        ttk.Label(login_win, text="Select User:").pack(pady=(10, 2))
        
        user_listbox = tk.Listbox(login_win, height=5, exportselection=False)
        user_listbox.pack(pady=5, padx=10, fill="x")
        
        user_keys = sorted(users.keys())
        for user in user_keys:
            user_listbox.insert(tk.END, user)
            if user == online_owner:
                user_listbox.itemconfig(tk.END, {'fg': 'grey'})

        ttk.Label(login_win, text="Password:").pack(pady=(10, 2))
        password_var = tk.StringVar()
        password_entry = ttk.Entry(login_win, textvariable=password_var, show="*")
        password_entry.pack()
        password_entry.bind('<Return>', lambda event: login())
        
        def login():
            selection = user_listbox.curselection()
            if not selection:
                messagebox.showwarning("Warning", "Please select a user", parent=login_win)
                return

            selected_index = selection[0]
            selected = user_listbox.get(selected_index)
            
            password = password_var.get()
            if not password:
                messagebox.showwarning("Warning", "Password is required.", parent=login_win)
                return

            if selected:
                user_data = users.get(selected)
                
                # Handle migration from old format (string) to new format (dict)
                if isinstance(user_data, str):
                    messagebox.showinfo("New Security System", "As part of a security upgrade, you need to create a password for your account.", parent=login_win)
                    new_pass = simpledialog.askstring("Create Password", "Enter new password:", show='*', parent=login_win)
                    if not new_pass: return
                    confirm_pass = simpledialog.askstring("Create Password", "Confirm new password:", show='*', parent=login_win)
                    if new_pass != confirm_pass:
                        messagebox.showerror("Error", "Passwords do not match.", parent=login_win)
                        return
                    
                    salt = os.urandom(16)
                    salt_b64 = base64.urlsafe_b64encode(salt).decode('utf-8')
                    hashed_password = self._hash_password(new_pass, salt)
                    
                    users[selected] = {"full_name": user_data, "salt": salt_b64, "hash": hashed_password}
                    _save_users_and_sync(users)
                    messagebox.showinfo("Success", "Password created successfully. Please log in again.", parent=login_win)
                    password_var.set("")
                    return

                # Standard password verification
                stored_hash = user_data.get("hash")
                stored_salt = user_data.get("salt")
                if not self._verify_password(stored_hash, stored_salt, provided_password=password):
                    messagebox.showerror("Login Failed", "Invalid username or password.", parent=login_win)
                    return

                if selected == online_owner:
                    if messagebox.askyesno("User Active", f"User '{selected}' is currently active elsewhere.\n\nDo you want to request editing permissions from them?", parent=login_win):
                        login_win.destroy()
                        self.current_user = selected
                        self.update_splash("Requesting edit access...", 75)
                        self.request_lock(online_owner)
                    return # Don't proceed with normal login
                else:
                    self.current_user = selected
                    self.update_splash("Login successful. Checking database status...", 75)
                    # Defer the rest of the startup to allow the splash screen to update
                    login_win.after(100, lambda: [login_win.destroy(), self.check_and_acquire_lock()])
            else:
                messagebox.showwarning("Warning", "Invalid username or password.", parent=login_win)

        def _save_users_and_sync(users_dict):
            with open('users.json', 'w') as f:
                json.dump(users_dict, f, indent=2)
            if self.drive_sync:
                try: self.drive_sync.sync_up(['users.json'])
                except Exception as e: print("Error syncing users file:", e)

        def new_user():
            name = simpledialog.askstring("New User", "Enter your Full Name:", parent=login_win)
            if name:
                username = simpledialog.askstring("New User", "Enter Username:", parent=login_win)
                if username:
                    if not re.match("^[a-zA-Z0-9_.-]+$", username):
                        messagebox.showerror("Invalid Username", "Username can only contain letters, numbers, and . - _", parent=login_win)
                        return
                    if username in users:
                        messagebox.showwarning("Warning", "Username already exists", parent=login_win)
                        return

                    new_pass = simpledialog.askstring("Create Password", "Enter new password:", show='*', parent=login_win)
                    if not new_pass: return
                    confirm_pass = simpledialog.askstring("Create Password", "Confirm new password:", show='*', parent=login_win)
                    if new_pass != confirm_pass:
                        messagebox.showerror("Error", "Passwords do not match.", parent=login_win)
                        return

                    salt = os.urandom(16)
                    salt_b64 = base64.urlsafe_b64encode(salt).decode('utf-8')
                    hashed_password = self._hash_password(new_pass, salt)

                    if not users: # First user is owner
                        users[username] = {"full_name": name, "salt": salt_b64, "hash": hashed_password, "is_owner": True}
                    else:
                        users[username] = {"full_name": name, "salt": salt_b64, "hash": hashed_password}
                    _save_users_and_sync(users)
                    user_listbox.insert(tk.END, username)
        
        ttk.Button(login_win, text="Login", command=login).pack(pady=(10, 5))
        ttk.Button(login_win, text="Create New User", command=new_user).pack(pady=5)

        self.root.wait_window(login_win)
        
        if not self.current_user:
            self.root.after(0, self.root.destroy)
            
    def check_and_acquire_lock(self):
        if not self.drive_sync:
            self.root.after(0, self.finish_init)
            return
            
        self.update_splash("Checking online database status...", 80)
        lock_data = self.drive_sync.read_lock()  # type: ignore
        
        now = time.time()
        if lock_data and lock_data.get('owner') and (now - lock_data.get('last_active', 0) < 45):
            owner = lock_data['owner']
            if owner == self.current_user:
                self.do_sync_down_and_finish(read_only=False)
                return
                
            res = self.ask_on_main_thread(messagebox.askyesnocancel, "Database in Use", 
                f"User '{owner}' is currently editing the database.\n\n"
                "Do you want to request editing permissions? (They will have 15 seconds to respond).\n\n"
                "Select 'No' to immediately open a Read-Only copy.")
            
            if res is True:
                self.request_lock(owner)
            elif res is False:
                self.do_sync_down_and_finish(read_only=True)
            else:
                self.root.after(0, self.root.destroy)
        else:
            self.do_sync_down_and_finish(read_only=False)
            # **Fix:** Implement a "write-then-verify" strategy to prevent race conditions.
            # After acquiring the lock, wait a moment and check if another user also acquired it.
            # If so, the user with the lexicographically smaller name backs off.
            time.sleep(secrets.randbelow(2000) / 1000.0 + 1.0) # Wait 1-3 seconds
            
            lock_data = self.drive_sync.read_lock() # type: ignore
            if lock_data and lock_data.get('owner') and lock_data.get('owner') != self.current_user:
                # Another user grabbed the lock in the small window. We must resolve who keeps it.
                other_user = lock_data.get('owner')
                if self.current_user > other_user:
                    # This user's name comes later alphabetically, so they back off.
                    self.is_owner = False
                    self.stop_poller = True
                    self.root.after(0, self._notify_lock_lost)
                    messagebox.showwarning("Conflict Detected", 
                        f"Both you and '{other_user}' tried to edit at the same time.\n\n"
                        "To prevent data loss, you have been placed in Read-Only mode.")
                    return
            
    def _build_recovery_tab(self, parent):
        """Work from a session that ended before saving, waiting to be dealt
        with. Deliberately not a startup prompt: whether an entry is still
        needed depends on what everyone else did, which takes looking around
        to find out."""
        ttk.Label(parent, text="Work recovered from an interrupted session",
                  font=("Helvetica", 11, "bold")).pack(anchor="w")
        ttk.Label(parent, wraplength=560, justify="left", foreground="#4B5563",
                  text="These came from a session that could not save before it ended, while "
                       "someone else was editing. They could not be combined automatically, so "
                       "nothing has been applied. Check whether each one is already in the "
                       "system, then add it back or dismiss it.").pack(anchor="w", pady=(2, 10))

        self.recovery_list = ttk.Treeview(
            parent, columns=("What", "Status"), show="tree headings", height=10)
        self.recovery_list.heading("#0", text="Item")
        self.recovery_list.heading("What", text="Details")
        self.recovery_list.heading("Status", text="In the system now")
        self.recovery_list.column("#0", width=210)
        self.recovery_list.column("What", width=250)
        self.recovery_list.column("Status", width=160)
        self.recovery_list.pack(fill="both", expand=True)

        self.recovery_note = ttk.Label(parent, text="", wraplength=560, justify="left",
                                       foreground="#4B5563")
        self.recovery_note.pack(anchor="w", pady=(8, 0))

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(10, 0))
        self.recovery_add_btn = ttk.Button(buttons, text="Add This Back",
                                           command=self._recovery_add, state="disabled")
        self.recovery_add_btn.pack(side="left")
        ttk.Button(buttons, text="Dismiss", command=self._recovery_dismiss).pack(side="left", padx=6)
        ttk.Button(buttons, text="Refresh", command=self._refresh_recovery_tab).pack(side="right")

        self.recovery_list.bind("<<TreeviewSelect>>", lambda _e: self._recovery_selected())
        self._refresh_recovery_tab()

    def _refresh_recovery_tab(self):
        if not hasattr(self, 'recovery_list') or not self.recovery_list.winfo_exists():
            return
        self.recovery_list.delete(*self.recovery_list.get_children())
        self._recovery_items = QR.load_pending(_BASE_DIR)
        for item in self._recovery_items:
            # Checked now rather than when the entry was made: somebody may
            # have added the same thing since, which is the point of waiting.
            present = QR.already_present(_BASE_DIR, item) if item.get('kind') == 'added' else None
            if item.get('kind') == 'changed':
                status = "value differs"
            elif present:
                status = "already added"
            else:
                status = "missing"
            detail = ", ".join(f"{k}: {v}" for k, v in (item.get('detail') or {}).items())
            if item.get('kind') == 'changed':
                detail = "; ".join(f"{f}: {p['theirs']} \u2192 {p['yours']}"
                                   for f, p in (item.get('changes') or {}).items())
            self.recovery_list.insert(
                "", "end", iid=item['id'],
                text=f"{item.get('label', '?')}  {item.get('name', '')}",
                values=(detail[:70], status))
        if not self._recovery_items:
            self.recovery_note.config(
                text="Nothing is waiting. Anything recovered from an interrupted "
                     "session would be listed here.")
        else:
            self.recovery_note.config(
                text=f"{len(self._recovery_items)} item(s) waiting. Your original copy is "
                     f"kept in the 'recovery' folder of the data directory.")
        self._recovery_selected()

    def _recovery_current(self):
        selection = self.recovery_list.selection() if hasattr(self, 'recovery_list') else ()
        if not selection:
            return None
        return next((i for i in getattr(self, '_recovery_items', [])
                     if i['id'] == selection[0]), None)

    def _recovery_selected(self):
        item = self._recovery_current()
        if not item:
            self.recovery_add_btn.config(state="disabled")
            return
        present = QR.already_present(_BASE_DIR, item) if item.get('kind') == 'added' else None
        can_add = bool(item.get('insertable')) and not present and getattr(self, 'is_owner', True)
        self.recovery_add_btn.config(state="normal" if can_add else "disabled")
        if item.get('kind') == 'changed':
            self.recovery_note.config(
                text=f"{QR.describe(item)}\n\nA changed value cannot be put back "
                     f"automatically, because the other version may be the newer one. "
                     f"Change it by hand if yours is the one that should stand.")
        elif present:
            self.recovery_note.config(
                text=f"'{item['name']}' is already in the system. Compare it with your "
                     f"version before dismissing this.")
        elif not item.get('insertable'):
            self.recovery_note.config(
                text=f"{QR.describe(item)}\n\nThis one has to be re-entered by hand: it "
                     f"refers to other records by number, and those numbers mean "
                     f"something different in the copy that was kept.")
        elif not getattr(self, 'is_owner', True):
            self.recovery_note.config(
                text="Another user is editing, so nothing can be added back right now.")
        else:
            self.recovery_note.config(text=QR.describe(item))

    def _recovery_add(self):
        item = self._recovery_current()
        if not item:
            return
        if not messagebox.askyesno(
                "Add This Back",
                f"Add {item.get('label', 'this')} '{item['name']}' back into the system?",
                parent=self.root):
            return
        try:
            QR.reinsert(_BASE_DIR, item)
        except Exception as e:
            messagebox.showerror("Could Not Add", str(e), parent=self.root)
            self._refresh_recovery_tab()
            return
        QR.resolve_pending(_BASE_DIR, item['id'])
        for app in self.apps.values():
            for method in ('load_clients', 'load_payees', 'refresh_tree'):
                if hasattr(app, method):
                    try:
                        getattr(app, method)()
                    except Exception:
                        pass
        self._refresh_recovery_tab()
        messagebox.showinfo("Added", f"'{item['name']}' is back in the system.",
                            parent=self.root)

    def _recovery_dismiss(self):
        item = self._recovery_current()
        if not item:
            return
        if not messagebox.askyesno(
                "Dismiss",
                f"Remove '{item['name']}' from this list?\n\n"
                "The copy kept in the recovery folder is not deleted.",
                parent=self.root):
            return
        QR.resolve_pending(_BASE_DIR, item['id'])
        self._refresh_recovery_tab()

    def _record_sync_state(self):
        """Writes down what each database looks like now, and which cloud
        version it matches. Survives a crash, which is the whole point: the
        in-memory copy of this dies with the process."""
        if not self.drive_sync:
            return
        try:
            in_drive = self.drive_sync.list_appdata_files()      # type: ignore
            QR.record_sync(_BASE_DIR, self.db_files,
                           {n: v.get('modifiedTime') for n, v in in_drive.items()})
        except Exception as e:
            print("Could not record the sync state:", e)

    def _recover_unsaved_work(self):
        """Deals with a session that ended before it saved.

        Returns the databases whose local copy was set aside, which are the
        ones that have to be downloaded over regardless of timestamps.
        """
        if not self.drive_sync:
            return {}
        try:
            in_drive = self.drive_sync.list_appdata_files()      # type: ignore
            cloud_times = {n: v.get('modifiedTime') for n, v in in_drive.items()}
            verdict = QR.classify(_BASE_DIR, self.db_files, cloud_times)
        except Exception as e:
            print("Could not check for unsaved work:", e)
            return {}

        alone = [n for n, v in verdict.items() if v == QR.LOCAL_ONLY]
        contested = [n for n, v in verdict.items() if v == QR.BOTH_CHANGED]

        if alone:
            # Nobody else has touched these since, so there is nothing to
            # weigh up and nobody to consult: send the work and move on.
            self.update_splash(f"Restoring {len(alone)} unsaved file(s)...", 86)
            try:
                self.drive_sync.sync_up(alone, self.current_user)     # type: ignore
                print(f"Recovered an unsaved session: uploaded {', '.join(alone)}")
            except Exception as e:
                print("Could not upload the unsaved work:", e)

        stashes = {}
        for name in contested:
            try:
                stashes[name] = QR.stash(_BASE_DIR, name)
            except OSError as e:
                print(f"Could not set aside the unsaved {name}: {e}")
        if stashes:
            self.update_splash("Keeping your unsaved work aside...", 88)
        return stashes

    def _finish_recovery(self, stashes):
        """Compares what was set aside against what was downloaded."""
        if not stashes:
            return
        try:
            findings = QR.compare_workspace(stashes, _BASE_DIR)
        except Exception as e:
            print("Could not compare the unsaved work:", e)
            return
        if not findings:
            return
        QR.add_pending(_BASE_DIR, findings)
        count = len(findings)
        self.root.after(1200, lambda: messagebox.showwarning(
            "Unsaved Work Recovered",
            f"Your last session ended before it could save, and someone else "
            f"edited in the meantime, so the two could not be combined "
            f"automatically.\n\n{count} item(s) from your session are waiting "
            f"in Settings > Recovered Work.\n\nNothing has been lost: your copy "
            f"was kept. Look through the data first, then add back anything "
            f"that is still missing."))

    def _record_local_file_mod_times(self):
        """Records the modification times of local database files after a sync."""
        self.local_file_mod_times.clear()
        for f in self.db_files:
            if os.path.exists(f):
                try:
                    self.local_file_mod_times[f] = os.path.getmtime(f)
                except OSError:
                    pass

    @staticmethod
    def _parse_drive_time(value):
        """Drive stamps are RFC3339 in UTC.

        time.mktime() would read them as local time, which west of UTC makes
        every cloud file look newer than the local copy and re-downloads the
        whole synced set on every launch. calendar.timegm() reads them as the
        UTC they actually are.
        """
        if not value:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return calendar.timegm(time.strptime(value, fmt))
            except ValueError:
                continue
        return None

    def _conditional_sync_down(self, files_to_sync, force=()):
        """Downloads files only if the cloud version is newer than the local version."""
        if not self.drive_sync:
            return

        files_in_drive = self.drive_sync.list_appdata_files()
        for name in files_to_sync:
            if name in files_in_drive:
                cloud_mod_time_str = files_in_drive[name].get('modifiedTime')
                if not cloud_mod_time_str:
                    self.drive_sync.download_file(files_in_drive[name]['id'], name)
                    continue

                # A file whose local copy has been set aside is downloaded
                # whatever the timestamps say: the cloud copy is the one
                # everybody else has been working from.
                if name in force:
                    self.drive_sync.download_file(files_in_drive[name]['id'], name)
                    self.drive_sync.file_versions[name] = cloud_mod_time_str
                    continue

                # Compare modification times
                if os.path.exists(name):
                    local_mod_time = os.path.getmtime(name)
                    cloud_mod_time = self._parse_drive_time(cloud_mod_time_str)

                    # If cloud is newer by more than a small margin (e.g., 2 seconds)
                    if cloud_mod_time is None or cloud_mod_time > local_mod_time + 2:
                        self.drive_sync.download_file(files_in_drive[name]['id'], name)
                else: # File doesn't exist locally, so download it
                    self.drive_sync.download_file(files_in_drive[name]['id'], name)

                # Record the cloud version this local copy is now known to match, so
                # sync_up() can detect if another instance changes the cloud file
                # before we save, instead of silently overwriting it.
                self.drive_sync.file_versions[name] = cloud_mod_time_str

    def do_sync_down_and_finish(self, read_only=False):
        ui_exists = bool(self.frames)
        if ui_exists:
            for app in self.apps.values():
                try:
                    if hasattr(app, 'conn'): app.conn.close()
                except Exception: pass

        # Before a download can overwrite anything, work out whether the
        # last session ended without saving, and protect what it left.
        stashes = self._recover_unsaved_work()

        self.update_splash("Downloading latest databases...", 90)
        try: self._conditional_sync_down(self.db_files, force=set(stashes))
        except Exception as e: print("Sync down error:", e)
        self._finish_recovery(stashes)
        
        # Record the state of files after download to check for changes later
        self._record_local_file_mod_times()
        self._record_sync_state()
        
        if ui_exists:
            old_view = self.current_view.get()
            for widget in self.root.winfo_children():
                if not isinstance(widget, tk.Toplevel):
                    widget.destroy()
            self.apps.clear()
            self.frames.clear()
            self.create_ui()
            self.current_view.set(old_view)
            self.switch_view()
            
        if not read_only:
            self.acquire_lock()
        else:
            if not self.frames: self.root.after(0, self.finish_init)
            self.enforce_read_only_mode()
            # Waiting users must check in, or the queue decides they have gone
            # home and skips past them. This also notices being offered a turn.
            self.start_presence_poller()
            if not ui_exists:
                self.root.after(0, lambda: messagebox.showinfo("Read-Only", "You are now in Read-Only mode. Edits cannot be saved."))

    def ask_on_main_thread(self, func, *args, **kwargs):
        """Helper to run a messagebox from a background thread."""
        result = [None]
        event = threading.Event()
        def run_it():
            result[0] = func(*args, **kwargs)
            event.set()
        self.root.after(0, run_it)
        event.wait()
        return result[0]

    def _notify_lock_lost(self):
        self.enforce_read_only_mode()
        messagebox.showwarning("Session Expired", "You lost your connection to the server and another user took over editing permissions.\n\nYou have been placed in Read-Only mode to prevent data conflicts. Any work done while offline will be safely backed up as a conflict file when you close the app.")

    def acquire_lock(self):
        if self.drive_sync:
            self.drive_sync.write_lock(                          # type: ignore
                QL.take_free_lock(self.drive_sync.read_lock(), self.current_user))
        self.is_owner = True
        # On the first run the UI does not exist yet, so enable_read_write_mode
        # is not the one to set this.
        self._set_cloud_writable(True)
        if not self.frames: self.root.after(0, self.finish_init)
        else: self.enable_read_write_mode()
        self.start_lock_poller()

    def request_lock(self, owner):
        """Joins the queue and waits for the holder to decide."""
        if not self.drive_sync: return
        self._show_progress_dialog("Requesting Access",
                                   f"Asking {owner} for editing access...")
        try:
            self.drive_sync.write_lock(                          # type: ignore
                QL.request(self.drive_sync.read_lock(), self.current_user))
        except Exception as e:
            print("Could not join the queue:", e)

        def poll_response():
            start = time.time()
            while True:
                time.sleep(2)
                try:
                    data = self.drive_sync.read_lock()           # type: ignore
                except Exception as e:
                    print("Error while waiting for a response:", e)
                    data = None
                # An upload is running on their side. However long it takes,
                # giving up now would mean opening a copy about to be replaced.
                if QL.handover_in_progress(data, self.current_user):
                    self.root.after(0, self._update_progress_dialog,
                                    f"{owner} is saving their work. This may take a moment...", 50)
                    start = time.time()
                    continue
                if data and data.get('owner') == self.current_user:
                    self.root.after(0, self._on_request_allowed)
                    return
                if data and data.get('response') == QL.RESPONSE_DENIED:
                    self.root.after(0, self._on_request_denied, owner, data)
                    return
                if time.time() - start >= 25:
                    self.root.after(0, self._on_request_timeout, owner)
                    return

        threading.Thread(target=poll_response, daemon=True).start()

    def _on_request_allowed(self):
        self._hide_progress_dialog()
        messagebox.showinfo("Access Granted", "Editing permissions transferred to you! Downloading latest data...")
        self.do_sync_down_and_finish(read_only=False)

    def _on_request_denied(self, owner, lock=None):
        self._hide_progress_dialog()
        note = QL.message_for(lock, self.current_user) if lock else None
        place = QL.queue_position(lock, self.current_user) if lock else 0
        said = f"\n\n{owner} says:\n\n\u201c{note['text']}\u201d" if note and note.get('text') else ""
        waiting = ("\n\nYou are next in line: editing passes to you when they close."
                   if place == 1 else
                   f"\n\nYou are number {place} in the queue." if place else "")
        messagebox.showinfo("Still Editing",
                            f"{owner} is still working, so Quarium will open Read-Only."
                            f"{said}{waiting}")
        self.do_sync_down_and_finish(read_only=True)

    def _on_request_timeout(self, owner):
        self._hide_progress_dialog()
        messagebox.showwarning("No Response",
                               f"{owner} did not respond. Opening in Read-Only mode.\n\n"
                               "You are in the queue, and editing passes to you when "
                               "they close.")
        self.do_sync_down_and_finish(read_only=True)

    def start_lock_poller(self):
        if not self.drive_sync: return
        self.stop_poller = False
        def poll():
            while not getattr(self, 'stop_poller', False) and getattr(self, 'is_owner', False):
                time.sleep(10)
                if getattr(self, 'stop_poller', False): break
                try:
                    ld = self.drive_sync.read_lock()  # type: ignore
                    if not ld or ld.get('owner') != self.current_user:
                            self.is_owner = False
                            self.root.after(0, self._notify_lock_lost)
                            break
                    waiting = QL.pending_request(ld)
                    if waiting and not ld.get('response'):
                        self.root.after(0, self.handle_lock_request, waiting)
                        continue
                    self.drive_sync.write_lock(                  # type: ignore
                        QL.mark_present(ld, self.current_user))
                except Exception as e: print("Poller error:", e)
        threading.Thread(target=poll, daemon=True).start()
        
    def handle_lock_request(self, requester):
        """Asks the holder what to do about someone waiting to edit.

        Three answers, and no answer is one of them: walking away from this
        dialog hands the lock over, because the alternative is one unattended
        session blocking everybody else indefinitely.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Request")
        dialog.geometry("470x350")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 235
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 175
        dialog.geometry(f"+{x}+{y}")

        ttk.Label(dialog, text=f"{requester} wants to edit",
                  font=('Helvetica', 12, 'bold')).pack(pady=(14, 2))
        ttk.Label(dialog, text="If you do nothing, your work is saved and they get to\n"
                               "edit while you wait to take it back.",
                  justify="center", font=('Helvetica', 9)).pack()

        ttk.Label(dialog, text="Message to send them (optional):",
                  font=('Helvetica', 9)).pack(anchor="w", padx=18, pady=(12, 2))
        note = tk.Text(dialog, height=3, width=48, wrap="word", relief="solid", borderwidth=1)
        note.pack(padx=18, fill="x")

        time_left = tk.IntVar(value=20)
        countdown = ttk.Label(dialog, text="", font=('Helvetica', 9))
        countdown.pack(pady=(8, 0))

        choice = [None]
        typed = [""]

        def pick(value):
            if dialog.winfo_exists():
                typed[0] = note.get('1.0', 'end-1c').strip()
            choice[0] = value
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(pady=12, padx=18, fill="x")
        ttk.Button(buttons, text="Keep editing  -  they wait, and get it when I close",
                   command=lambda: pick(QL.KEEP)).pack(fill="x", pady=2)
        ttk.Button(buttons, text="Save and hand over  -  take it back when they finish",
                   command=lambda: pick(QL.LEND), style="Accent.TButton").pack(fill="x", pady=2)
        ttk.Button(buttons, text="Save, hand over and close",
                   command=lambda: pick(QL.HAND_OVER)).pack(fill="x", pady=2)

        def update_timer():
            if not dialog.winfo_exists():
                return
            value = time_left.get()
            if value > 0:
                countdown.config(text=f"Handing over automatically in {value}s")
                time_left.set(value - 1)
                dialog.after(1000, update_timer)
            else:
                pick(QL.LEND)

        dialog.after(1000, update_timer)
        self.root.wait_window(dialog)
        self._answer_lock_request(requester, choice[0] or QL.LEND, typed[0])

    def _answer_lock_request(self, requester, decision, message):
        """Writes the holder's answer, saving first where the answer needs it."""
        if not self.drive_sync:
            return

        if decision == QL.KEEP:
            try:
                self.drive_sync.write_lock(                      # type: ignore
                    QL.keep(self.drive_sync.read_lock(), requester, message))
            except Exception as e:
                print("Could not answer the edit request:", e)
            self._show_waiting_count()
            return

        # The other two answers upload first. Say so in the lock before
        # starting, so the person waiting holds on instead of timing out
        # halfway through a large database.
        lend = decision == QL.LEND
        self._show_progress_dialog("Handing Over", "Saving your work to the cloud...")
        try:
            self.drive_sync.write_lock(                          # type: ignore
                QL.begin_handover(self.drive_sync.read_lock(), requester, message))
            self.drive_sync.sync_up(self.db_files, self.current_user)   # type: ignore
            lock = QL.complete_handover(self.drive_sync.read_lock(), requester,
                                        lend_back_to=self.current_user if lend else None)
            if message:
                lock['messages'][requester] = {'from': self.current_user,
                                               'text': message, 'at': time.time()}
            self.drive_sync.write_lock(lock)                     # type: ignore
        except Exception as e:
            print("Error handing over the lock:", e)
            self._hide_progress_dialog()
            messagebox.showerror(
                "Handover Failed",
                f"Your work could not be saved to the cloud:\n\n{e}\n\n"
                "You still have edit access, so nothing has been given away.")
            return

        self._hide_progress_dialog()
        self.enforce_read_only_mode()

        if lend:
            self.start_presence_poller()
            messagebox.showinfo(
                "Handed Over",
                f"Your work is saved and {requester} can now edit.\n\n"
                "You are in Read-Only mode. When they finish, you will be asked "
                "whether to take editing back.")
        else:
            messagebox.showinfo(
                "Handed Over",
                f"Your work is saved and {requester} can now edit.\n\n"
                "Quarium will now close.")
            self.root.after(100, self.on_closing)

    def _show_waiting_count(self):
        """Keeps the sidebar honest about people waiting for the lock."""
        if not (hasattr(self, 'status_label') and self.status_label.winfo_exists()):
            return
        waiting = 0
        try:
            lock = self.drive_sync.read_lock() if self.drive_sync else None
            waiting = len([u for u in QL._normalise(lock)['queue']
                           if QL.is_present(lock, u)])
        except Exception:
            pass
        suffix = f" ({waiting} waiting)" if waiting else ""
        self.status_label.config(text=f"● EDITING{suffix}", fg="#2E7D32")

    def start_presence_poller(self):
        """Checks in while read-only, and notices being offered the lock.

        A waiting instance has to keep a heartbeat of its own: the queue skips
        anyone who has gone home, and without this every waiter would look
        like they had.
        """
        if not self.drive_sync or getattr(self, 'presence_thread_running', False):
            return
        self.presence_thread_running = True

        def poll():
            while not getattr(self, 'stop_poller', False) and not getattr(self, 'is_owner', False):
                try:
                    lock = self.drive_sync.read_lock()           # type: ignore
                    if QL.offered_to(lock, self.current_user):
                        self.root.after(0, self._offer_edit_access, lock)
                        break
                    self.drive_sync.write_lock(                  # type: ignore
                        QL.mark_present(lock, self.current_user))
                except Exception as e:
                    print("Presence poller error:", e)
                time.sleep(10)
            self.presence_thread_running = False

        threading.Thread(target=poll, daemon=True).start()

    def _offer_edit_access(self, lock):
        """Someone finished and the lock came to us. Ask before taking it."""
        handed_from = (lock.get('handover') or {}).get('from') or "The previous editor"
        take = messagebox.askyesno(
            "Editing Available",
            f"{handed_from} has finished, and editing is now yours if you want it.\n\n"
            "Taking it will refresh your data with their latest work first.\n\n"
            "Take over editing?")
        if not take:
            try:
                updated, passed_to = QL.decline(self.drive_sync.read_lock(), self.current_user)
                self.drive_sync.write_lock(updated)              # type: ignore
            except Exception as e:
                print("Could not pass the lock on:", e)
            self.start_presence_poller()
            return
        self._show_progress_dialog("Taking Over", "Downloading the latest data...")
        try:
            self.drive_sync.sync_down(self.db_files)             # type: ignore
            self._record_local_file_mod_times()
            self.drive_sync.write_lock(                          # type: ignore
                QL.claim(self.drive_sync.read_lock(), self.current_user))
        except Exception as e:
            self._hide_progress_dialog()
            messagebox.showerror("Could Not Take Over",
                                 f"The latest data could not be downloaded:\n\n{e}\n\n"
                                 "Staying in Read-Only mode.")
            self.start_presence_poller()
            return
        self._hide_progress_dialog()
        for app in self.apps.values():
            for method in ('load_clients', 'load_services', 'load_all_data', 'load_data',
                           'refresh_tree', 'load_payees', 'load_approved_projects'):
                if hasattr(app, method):
                    try:
                        getattr(app, method)()
                    except Exception:
                        pass
        self.enable_read_write_mode()
        self.start_lock_poller()
        messagebox.showinfo("Editing", "You now have editing access, with the latest data.")

    def _set_cloud_writable(self, writable):
        """Keeps the sync's idea of read-only in step with this window's.

        Both mode switches run through here, so an upload cannot be left
        enabled by a path that changed is_owner without changing the UI.
        """
        if self.drive_sync:
            self.drive_sync.read_only = not writable

    def enforce_read_only_mode(self):
        self.is_owner = False
        self._set_cloud_writable(False)
        self.root.title("Quarium Dashboard [READ-ONLY MODE]")
        if hasattr(self, 'status_label') and self.status_label.winfo_exists():
            self.status_label.config(text="● READ-ONLY", fg="#D32F2F")
        if hasattr(self, 'request_edit_btn') and self.request_edit_btn.winfo_exists():
            self.request_edit_btn.pack(after=self.status_label, fill="x", pady=(0, 15), ipady=3)
        for app in self.apps.values():
            try:
                if hasattr(app, 'cursor'): app.cursor.execute("PRAGMA query_only = ON;")
            except Exception: pass

    def enable_read_write_mode(self):
        self.is_owner = True
        self._set_cloud_writable(True)
        self.root.title("Quarium Dashboard")
        if hasattr(self, 'status_label') and self.status_label.winfo_exists():
            self.status_label.config(text="● EDITING", fg="#2E7D32")
        if hasattr(self, 'request_edit_btn') and self.request_edit_btn.winfo_exists():
            self.request_edit_btn.pack_forget()
        for app in self.apps.values():
            try:
                if hasattr(app, 'cursor'): app.cursor.execute("PRAGMA query_only = OFF;")
            except Exception: pass
            
    def manual_request_edit(self):
        if not self.drive_sync: return
        self._show_progress_dialog("Checking Status", "Checking online database status...")
        lock_data = self.drive_sync.read_lock()  # type: ignore
        self._hide_progress_dialog()
        
        now = time.time()
        if lock_data and lock_data.get('owner') and (now - lock_data.get('last_active', 0) < 45):
            owner = lock_data['owner']
            if owner == self.current_user:
                self.do_sync_down_and_finish(read_only=False)
                return
                
            res = messagebox.askyesno("Database in Use", 
                f"User '{owner}' is currently editing the database.\n\n"
                "Do you want to request editing permissions? (They will have 15 seconds to respond).")
            
            if res:
                self.request_lock(owner)
        else:
            self.do_sync_down_and_finish(read_only=False)

    def finish_init(self):
        # Build the interface while the splash is still up. Tearing it down
        # first left a bare desktop for the several seconds the nine managers
        # take to construct, with the login window already gone -- which reads
        # as the application having crashed.
        self.update_splash("Loading modules...", 78)
        self.create_ui()
        self.update_splash("Ready", 100)

        if hasattr(self, 'splash') and self.splash.winfo_exists():
            self.splash.destroy()

        window_width = 1200
        window_height = 800
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        center_x = int((screen_width / 2) - (window_width / 2))
        center_y = int((screen_height / 2) - (window_height / 2))
        self.root.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

        self.root.deiconify() # Show main window, now that it is built

        try:
            self.root.state('zoomed') # Maximize the window on Windows
        except tk.TclError:
            pass

        logo_path = asset_path('QLogo.png')
        if logo_path:
            try:
                icon_img = tk.PhotoImage(file=logo_path)
                self.root.iconphoto(True, icon_img)
            except Exception: pass
        
    def create_ui(self):
        # Configure grid for the main window
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        
        # --- Define custom theme ---
        style = ttk.Style(self.root)
        
        # Define colors
        COLOR_PRIMARY = "#285D80"
        COLOR_ACCENT = "#FF6A7E"
        COLOR_WHITE = "#FFFFFF"
        COLOR_LIGHT_GRAY = "#F0F0F0"
        COLOR_TEXT = "#000000"
        COLOR_PRIMARY_LIGHT = "#3E84B3" # Lighter shade for hover

        # Use 'clam' as a base theme for better customization
        style.theme_use("clam")

        # --- Configure widget styles ---
        style.configure(".",
                        background=COLOR_WHITE,
                        foreground=COLOR_TEXT,
                        fieldbackground=COLOR_WHITE,
                        font=('Helvetica', 10))

        style.configure("TFrame", background=COLOR_WHITE)
        style.configure("TLabel", background=COLOR_WHITE)
        style.configure("TCheckbutton", background=COLOR_WHITE)

        # Buttons
        style.configure("TButton",
                        background=COLOR_PRIMARY,
                        foreground=COLOR_WHITE,
                        font=('Helvetica', 10, 'bold'),
                        padding=5,
                        borderwidth=0)
        style.map("TButton",
                  background=[('active', COLOR_PRIMARY_LIGHT)])

        # Accent Button for special actions
        style.configure("Accent.TButton",
                        background=COLOR_ACCENT,
                        foreground=COLOR_WHITE)
        style.map("Accent.TButton",
                  background=[('active', '#FF8C9D')]) # Lighter accent

        # Treeview
        style.configure("Treeview",
                        rowheight=25,
                        fieldbackground=COLOR_WHITE)
        style.configure("Treeview.Heading",
                        background=COLOR_PRIMARY,
                        foreground=COLOR_WHITE,
                        font=('Helvetica', 10, 'bold'))
        style.map("Treeview.Heading", background=[('active', COLOR_PRIMARY_LIGHT)])
        style.map("Treeview",
                  background=[('selected', COLOR_PRIMARY)],
                  foreground=[('selected', COLOR_WHITE)])

        # Sidebar navigation buttons
        # Nine sections plus Settings, Log Out and Quit have to share the
        # column, so the vertical padding stays tight: at 10 all round the
        # stack outgrew a 1080p screen and the bottom buttons were cut off.
        style.configure("Toolbutton",
                        background=COLOR_WHITE,
                        foreground=COLOR_PRIMARY,
                        font=('Helvetica', 10),
                        padding=(10, 4),
                        borderwidth=0,
                        anchor="w")
        style.map("Toolbutton",
                  background=[('selected', COLOR_PRIMARY), ('active', COLOR_LIGHT_GRAY)],
                  foreground=[('selected', COLOR_WHITE)])

        # The Settings / Log Out / Quit group, matched to the nav buttons.
        style.configure("Sidebar.TButton", font=('Helvetica', 10), padding=(10, 4))

        # Entry and Combobox
        style.configure("TEntry", fieldbackground=COLOR_LIGHT_GRAY, borderwidth=1, relief="flat")
        style.map("TEntry", fieldbackground=[('focus', COLOR_WHITE)])
        style.configure("TCombobox", fieldbackground=COLOR_LIGHT_GRAY, arrowcolor=COLOR_PRIMARY, relief="flat")
        style.map("TCombobox", fieldbackground=[('readonly', COLOR_LIGHT_GRAY), ('focus', COLOR_WHITE)])

        # Notebook (Tabs)
        style.configure("TNotebook", background=COLOR_WHITE, borderwidth=0)
        style.configure("TNotebook.Tab", background=COLOR_LIGHT_GRAY, foreground=COLOR_TEXT, padding=[10, 5], borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", COLOR_PRIMARY)], foreground=[("selected", COLOR_WHITE)])
        
        # LabelFrame
        style.configure("TLabelframe", background=COLOR_WHITE, borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=COLOR_WHITE, foreground=COLOR_PRIMARY, font=('Helvetica', 11, 'bold'))

        # Sidebar frame
        sidebar = ttk.Frame(self.root, padding=10, style="TFrame")
        sidebar.grid(row=0, column=0, sticky="ns")
        
        # Load and display logo
        logo_path = asset_path('QLogo.png')
        if logo_path:
            try:
                from PIL import Image, ImageTk
                img = Image.open(logo_path)
                img.thumbnail((80, 80))  # Small: the column has 12 controls to fit as well
                self.logo_img = ImageTk.PhotoImage(img)
            except ImportError:
                messagebox.showwarning("Optional Dependency Missing", "The 'Pillow' library is not installed. Logo image quality may be reduced.\n\nInstall it with: pip install Pillow")
                # Fallback to standard Tkinter PhotoImage if Pillow is not installed
                self.logo_img = tk.PhotoImage(file=logo_path)
                if self.logo_img.width() > 150:
                    factor = max(1, self.logo_img.width() // 120)
                    self.logo_img = self.logo_img.subsample(factor, factor)
            sidebar_logo_label = ttk.Label(sidebar, image=self.logo_img)
            sidebar_logo_label.image = self.logo_img # Keep a reference
            sidebar_logo_label.pack(pady=(10, 5))
        
        # Application title in sidebar
        ttk.Label(sidebar, text="Quarium\nDashboard", font=('Helvetica', 13, 'bold'), justify="center").pack(pady=(0, 6))

        self.status_label = tk.Label(sidebar, text="● EDITING", font=('Helvetica', 10, 'bold'), bg="#FFFFFF", fg="#2E7D32")
        self.status_label.pack(pady=(0, 6))

        self.request_edit_btn = ttk.Button(sidebar, text="Request Edit Access", command=self.manual_request_edit, style="Accent.TButton")

        # Packed before the section buttons on purpose. Pack hands out space in
        # call order, so claiming the bottom first means a long section list
        # can never push Settings, Log Out and Quit off the screen.
        ttk.Button(sidebar, text="Quit", command=self.on_closing,
                   style="Sidebar.TButton").pack(side="bottom", fill="x", pady=(2, 0))
        ttk.Button(sidebar, text="Log Out", command=self.logout,
                   style="Sidebar.TButton").pack(side="bottom", fill="x", pady=2)
        ttk.Button(sidebar, text="Settings", command=self.open_settings,
                   style="Sidebar.TButton").pack(side="bottom", fill="x", pady=(8, 2))

        # The section buttons live in their own frame, packed last and allowed
        # to take whatever is left. Any shortage is then absorbed here rather
        # than by the fixed controls above and below it.
        nav_holder = ttk.Frame(sidebar)
        nav_holder.pack(side="top", fill="both", expand=True)
        
        # Main content area
        self.content_area = ttk.Frame(self.root)
        self.content_area.grid(row=0, column=1, sticky="nsew")
        self.content_area.rowconfigure(0, weight=1)
        self.content_area.columnconfigure(0, weight=1)
        
        # Define apps to load
        app_definitions = [
            ("Projects", "Estimate Manager", ProjectManager, {'current_user': self.current_user}),
            ("Flow", "Project Flow", ProjectFlowManager, {'current_user': self.current_user, 'drive_sync': self.drive_sync}),
            ("Finances", "Project Finances", FinanceManager, {'current_user': self.current_user}),
            ("Debts", "Debts and Credits", DebtsManager, {'current_user': self.current_user}),
            ("Payees", "Payee Manager", PayeeManager, {'current_user': self.current_user}),
            ("Contracts", "Contract Generator", ContractManager, {'current_user': self.current_user, 'drive_sync': self.drive_sync}),
            ("Clients", "Client Manager", ClientManager, {'current_user': self.current_user}),
            ("Services", "Service Manager", ServiceManager, {'current_user': self.current_user}),
            ("Stock", "Stock Manager", StockManager, {'on_edit_composite': self.open_composite_editor, 'current_user': self.current_user}),
            ("Composites", "Composite Creator", CompositeStockManager, {'is_embedded': True, 'current_user': self.current_user}),
        ]
        
        self.current_view = tk.StringVar(value="Projects")
        
        # Create navigation buttons and app frames
        for position, (app_id, title, app_class, kwargs) in enumerate(app_definitions):
            self.update_splash(f"Loading {title}...",
                               78 + int(20 * position / len(app_definitions)))
            # Navigation button (acting like a tab using the Toolbutton style)
            btn = ttk.Radiobutton(
                nav_holder,
                text=title, 
                variable=self.current_view, 
                value=app_id,
                style="Toolbutton",
                command=self.switch_view
            )
            btn.pack(fill="x", pady=1)
            
            # App frame
            frame = ttk.Frame(self.content_area)
            frame.grid(row=0, column=0, sticky="nsew")
            self.frames[app_id] = frame
            
            # Initialize the app inside its frame
            if kwargs:
                self.apps[app_id] = app_class(frame, **kwargs)
            else:
                self.apps[app_id] = app_class(frame)
            
        # Show initial view
        self.switch_view()
        
    def open_composite_editor(self, composite_name):
        self.current_view.set("Composites")
        self.switch_view()
        comp_app = self.apps["Composites"]
        if hasattr(comp_app, 'load_composite'):
            comp_app.load_composite(composite_name)
            
    def _show_progress_dialog(self, title, message, mode='indeterminate', max_value=100):
        self.progress_dialog = tk.Toplevel(self.root)
        self.progress_dialog.title(title)
        self.progress_dialog.transient(self.root)
        self.progress_dialog.grab_set()
        self.progress_dialog.resizable(False, False)
        self.progress_dialog.minsize(400, 120) # Increased minsize for more text room

        # Use a frame to better contain the widgets
        container = ttk.Frame(self.progress_dialog, padding=10)
        container.pack(fill="both", expand=True)
        
        # Increased wraplength to match new minsize width
        self.progress_dialog_label = ttk.Label(container, text=message, wraplength=350, justify="center")
        self.progress_dialog_label.pack(pady=(5, 10))
        self.progress_bar = ttk.Progressbar(container, mode=mode, length=350, maximum=max_value)
        self.progress_bar.pack(pady=(10, 5))
        if mode == 'indeterminate':
            self.progress_bar.start()

        self.progress_dialog.update_idletasks()
        # Center the dialog
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (self.progress_dialog.winfo_width() // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (self.progress_dialog.winfo_height() // 2)
        self.progress_dialog.geometry(f"+{x}+{y}")
        self.root.update_idletasks()

    def _update_progress_dialog(self, text, value):
        if hasattr(self, 'progress_dialog') and self.progress_dialog.winfo_exists():
            self.progress_dialog_label.config(text=text)
            self.progress_bar['value'] = value
            self.progress_dialog.update_idletasks()
            self.progress_dialog.update() # Force a redraw

    def _hide_progress_dialog(self):
        if hasattr(self, 'progress_dialog') and self.progress_dialog.winfo_exists():
            if self.progress_bar['mode'] == 'indeterminate':
                self.progress_bar.stop()
            self.progress_dialog.destroy()

    def switch_view(self):
        old_view = getattr(self, '_last_view', None)
        view_id = self.current_view.get()
        
        if old_view and old_view in self.apps:
            app = self.apps[old_view]
            if hasattr(app, 'check_unsaved_changes'):
                if not app.check_unsaved_changes():
                    self.current_view.set(old_view) # Revert radiobutton state
                    return
                    
        self._last_view = view_id
        frame = self.frames[view_id]
        frame.tkraise()
        
        # Refresh data when switching to a tab to ensure it is up to date
        app = self.apps[view_id]
        if view_id == "Clients":
            app.load_clients()
        elif view_id == "Services":
            app.load_services()
            app.load_stock_items()
        elif view_id == "Projects":
            app.load_all_data()
        elif view_id == "Flow":
            app.load_data()
        elif view_id == "Contracts":
            app.load_approved_projects()
        elif view_id == "Stock":
            app.refresh_tree()
        elif view_id == "Payees":
            # A payee can be created from the attribution editor on another
            # tab, so this list is stale as soon as that happens.
            app.load_payees()
        elif view_id == "Composites":
            app.load_items()
            app.update_summary()

    def logout_no_sync(self):
        for app in self.apps.values():
            if hasattr(app, 'on_closing'): app.on_closing()
            elif hasattr(app, 'close'): app.close()

        self.stop_poller = True
        if getattr(self, 'is_owner', False) and self.drive_sync:
            try:
                # Pass it to whoever has waited longest rather than dropping
                # it, so someone sitting in read-only is not left refreshing.
                ld, handed_to = QL.release(self.drive_sync.read_lock(),  # type: ignore
                                           self.current_user)
                self.drive_sync.write_lock(ld)  # type: ignore
                if handed_to:
                    self._update_progress_dialog(f"Passing editing to {handed_to}...", 45)
            except Exception as e:
                print("Could not pass on the edit lock:", e)

        self.current_user = None
        self.drive_sync = None
        for widget in self.root.winfo_children():
            widget.destroy()
        self.apps.clear()
        self.frames.clear()
        self.root.withdraw()
        self.show_connection_manager()

    def _perform_logout(self):
        # Gracefully close all embedded apps
        for app in self.apps.values():
            if hasattr(app, 'on_closing'):
                app.on_closing()
            elif hasattr(app, 'close'):
                app.close()

        self.stop_poller = True
        if getattr(self, 'is_owner', False) and self.drive_sync:
            try:
                ld = self.drive_sync.read_lock() or {}  # type: ignore
                if ld.get('owner') == self.current_user:
                    ld['owner'] = None
                    self.drive_sync.write_lock(ld)  # type: ignore
            except Exception: pass

        # Upload databases back to drive
        if self.drive_sync:
            try:
                self.drive_sync.sync_up(self.db_files)  # type: ignore
            except Exception as e:
                print("Could not sync databases back to Google Drive:", e)

        self._hide_progress_dialog()

        # Clear current session state and UI
        self.current_user = None
        for widget in self.root.winfo_children():
            widget.destroy()
        self.apps.clear()
        self.frames.clear()

        self.root.withdraw()
        
        if getattr(self, 'force_disconnect', False):
            self.force_disconnect = False
            self.show_connection_manager()
        else:
            self.show_login_dialog()

    def logout(self):
        self._show_progress_dialog("Logging Out", "Logging out and saving data...")
        self.root.after(100, self._perform_logout)

    def _perform_closing(self):
        self._update_progress_dialog("Closing application modules...", 20)
        # Gracefully close all embedded apps
        for app in self.apps.values():
            if hasattr(app, 'on_closing'):
                app.on_closing()
            elif hasattr(app, 'close'):
                app.close()
        self.root.after(50) # Give UI a moment to process

        self.stop_poller = True
        if getattr(self, 'is_owner', False) and self.drive_sync:
            self._update_progress_dialog("Releasing online lock...", 40)
            try:
                ld = self.drive_sync.read_lock() or {}  # type: ignore
                if ld.get('owner') == self.current_user:
                    ld['owner'] = None
                    self.drive_sync.write_lock(ld)  # type: ignore
            except Exception: pass
        self.root.after(50)

        # Upload databases back to drive
        if self.drive_sync:
            files_to_upload = []
            for f in self.db_files:
                if os.path.exists(f):
                    try:
                        current_mtime = os.path.getmtime(f)
                        if self.local_file_mod_times.get(f) != current_mtime:
                            files_to_upload.append(f)
                    except OSError:
                        # If we can't get mtime, assume it needs upload
                        files_to_upload.append(f)
            
            if files_to_upload:
                # A read-only window still saves its work, but as conflict
                # copies: the canonical files belong to whoever holds the lock.
                read_only = not getattr(self, 'is_owner', True)
                self._update_progress_dialog(
                    (f"Saving {len(files_to_upload)} changed file(s) as conflict copies..."
                     if read_only else
                     f"Syncing {len(files_to_upload)} changed file(s) to cloud..."), 60)
                try:
                    result = self.drive_sync.sync_up(files_to_upload, self.current_user)  # type: ignore
                    self._record_sync_state()
                    if result and result['conflicts']:
                        self._update_progress_dialog(
                            f"{len(result['conflicts'])} file(s) kept as conflict copies.", 80)
                except Exception as e:
                    print("Could not sync databases back to Google Drive:", e)
            else:
                self._update_progress_dialog("No local changes detected. Skipping cloud sync.", 80)
                time.sleep(1) # Give user time to read the message
        self.root.after(50)

        self._update_progress_dialog("Finishing...", 100)
        self._hide_progress_dialog()
        self.root.destroy()

    def on_closing(self):
        self._show_progress_dialog("Closing Application", "Saving data and closing connections...", mode='determinate')
        self.root.after(100, self._perform_closing)

    def open_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("1000x680")
        dialog.minsize(860, 560)
        dialog.transient(self.root)
        dialog.grab_set()

        # Nine sections across the top of a notebook left no room to breathe;
        # a grouped list down the side gives each one the full width.
        notebook = SectionNav(dialog, groups=("Appearance", "Users and Access", "Estimates",
                                              "Contracts", "Sync and Data"))
        notebook.pack(fill="both", expand=True, padx=14, pady=14)

        settings_path = 'settings.json'
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    settings = json.load(f)
            except Exception: pass

        gen_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(gen_frame, text="General", group="Appearance")

        ttk.Label(gen_frame, text="Dashboard Logo (QLogo.png):").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Button(gen_frame, text="Select New Image", command=lambda: self._select_image('QLogo.png')).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(gen_frame, text="Estimate Logo (EstimateLogo.png):").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Button(gen_frame, text="Select New Image", command=lambda: self._select_image('EstimateLogo.png')).grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(gen_frame, text="Estimate Footer Text:").grid(row=2, column=0, sticky="nw", pady=5)
        footer_text = tk.Text(gen_frame, width=50, height=4)
        footer_text.grid(row=2, column=1, padx=5, pady=5)
        footer_text.insert("1.0", settings.get("footer_text", "Quarium Consultoria em Biologia Analítica, Ltda. | Campinas, SP | Email: quarium.bio@gmail.com"))

        def save_general():
            settings["footer_text"] = footer_text.get("1.0", tk.END).strip()
            settings["estimate_logo"] = "EstimateLogo.png" if os.path.exists("EstimateLogo.png") else "QLogo.png"
            with open(settings_path, 'w') as f:
                json.dump(settings, f)
            if self.drive_sync:
                try: self.drive_sync.sync_up(['settings.json'])
                except Exception: pass
            messagebox.showinfo("Saved", "General settings saved.", parent=dialog)

        ttk.Button(gen_frame, text="Save General Settings", command=save_general).grid(row=3, column=0, columnspan=2, pady=15)

        users_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(users_frame, text="Users", group="Users and Access")

        pass_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(pass_frame, text="Passwords", group="Users and Access")

        # --- Users Tab ---
        user_tree = ttk.Treeview(users_frame, columns=("Full Name",), height=8)
        user_tree.heading("#0", text="Username")
        user_tree.heading("Full Name", text="Full Name")
        user_tree.column("#0", width=150)
        user_tree.column("Full Name", width=250)
        user_tree.pack(fill="x", pady=5)

        users_dict = {}
        if os.path.exists('users.json'):
            try:
                with open('users.json', 'r') as f:
                    users_dict = json.load(f)
                for uname, udata in users_dict.items():
                    fname = udata.get("full_name", udata) if isinstance(udata, dict) else udata
                    user_tree.insert("", "end", text=uname, values=(fname,))
            except Exception: pass

        def _save_users(u_dict):
            with open('users.json', 'w') as f:
                json.dump(u_dict, f, indent=2)
            if self.drive_sync:
                try: self.drive_sync.sync_up(['users.json'])
                except Exception: pass
            for item in user_tree.get_children():
                user_tree.delete(item)
            for uname, udata in u_dict.items():
                fname = udata.get("full_name", udata) if isinstance(udata, dict) else udata
                user_tree.insert("", "end", text=uname, values=(fname,))
            messagebox.showinfo("Saved", "Users updated.", parent=dialog)

        def edit_user():
            sel = user_tree.selection()
            if not sel: return
            old_uname = user_tree.item(sel[0], "text")
            old_fname = users_dict[old_uname].get("full_name", users_dict[old_uname])
            
            new_fname = simpledialog.askstring("Edit User", "Full Name:", initialvalue=old_fname, parent=dialog)
            if new_fname is None: return
            new_uname = simpledialog.askstring("Edit User", "Username:", initialvalue=old_uname, parent=dialog)
            if new_uname is None: return
            
            if new_uname != old_uname and new_uname in users_dict:
                messagebox.showerror("Error", "Username already exists!", parent=dialog)
                return
                
            udata = users_dict[old_uname]
            del users_dict[old_uname]
            if isinstance(udata, dict):
                udata["full_name"] = new_fname
                users_dict[new_uname] = udata
            else: # Handle migration of old format if somehow missed
                users_dict[new_uname] = new_fname
            _save_users(users_dict)

        def add_user():
            new_fname = simpledialog.askstring("Add User", "Full Name:", parent=dialog)
            if not new_fname: return
            new_uname = simpledialog.askstring("Add User", "Username:", parent=dialog)
            if not new_uname: return
            if new_uname in users_dict:
                messagebox.showerror("Error", "Username already exists!", parent=dialog)
                return
            
            messagebox.showinfo("Password Required", "A password must be set for the new user.", parent=dialog)
            new_pass = simpledialog.askstring("Create Password", "Enter new password:", show='*', parent=dialog)
            if not new_pass: return
            confirm_pass = simpledialog.askstring("Create Password", "Confirm new password:", show='*', parent=dialog)
            if new_pass != confirm_pass:
                messagebox.showerror("Error", "Passwords do not match.", parent=dialog)
                return

            salt = os.urandom(16)
            salt_b64 = base64.urlsafe_b64encode(salt).decode('utf-8')
            hashed_password = self._hash_password(new_pass, salt)
            users_dict[new_uname] = {"full_name": new_fname, "salt": salt_b64, "hash": hashed_password}
            _save_users(users_dict)

        u_btn_frame = ttk.Frame(users_frame)
        u_btn_frame.pack(fill="x", pady=5)
        ttk.Button(u_btn_frame, text="Add User", command=add_user).pack(side="left", padx=5)
        ttk.Button(u_btn_frame, text="Edit Selected", command=edit_user).pack(side="left", padx=5)

        # --- Passwords Tab ---
        my_pass_frame = ttk.LabelFrame(pass_frame, text="Change My Password", padding=10)
        my_pass_frame.pack(fill="x", pady=5)
        
        ttk.Label(my_pass_frame, text="Current Password:").grid(row=0, column=0, sticky="w", pady=2)
        current_pass_var = tk.StringVar()
        ttk.Entry(my_pass_frame, textvariable=current_pass_var, show="*").grid(row=0, column=1, padx=5, pady=2)
        
        ttk.Label(my_pass_frame, text="New Password:").grid(row=1, column=0, sticky="w", pady=2)
        new_pass_var = tk.StringVar()
        ttk.Entry(my_pass_frame, textvariable=new_pass_var, show="*").grid(row=1, column=1, padx=5, pady=2)
        
        ttk.Label(my_pass_frame, text="Confirm New Password:").grid(row=2, column=0, sticky="w", pady=2)
        confirm_pass_var = tk.StringVar()
        ttk.Entry(my_pass_frame, textvariable=confirm_pass_var, show="*").grid(row=2, column=1, padx=5, pady=2)

        def change_my_password():
            current_pass = current_pass_var.get()
            new_pass = new_pass_var.get()
            confirm_pass = confirm_pass_var.get()
            
            my_data = users_dict.get(self.current_user)
            if not my_data or not isinstance(my_data, dict) or not self._verify_password(my_data.get("hash"), my_data.get("salt"), current_pass):
                messagebox.showerror("Error", "Current password is incorrect.", parent=dialog)
                return
            if not new_pass or new_pass != confirm_pass:
                messagebox.showerror("Error", "New passwords do not match.", parent=dialog)
                return
            
            salt = os.urandom(16)
            my_data["salt"] = base64.urlsafe_b64encode(salt).decode('utf-8')
            my_data["hash"] = self._hash_password(new_pass, salt)
            _save_users(users_dict)
            current_pass_var.set(""); new_pass_var.set(""); confirm_pass_var.set("")
            messagebox.showinfo("Success", "Your password has been changed.", parent=dialog)

        ttk.Button(my_pass_frame, text="Change Password", command=change_my_password).grid(row=3, column=1, sticky="e", pady=10)

        # --- Admin Reset Frame (only visible to owner) ---
        my_user_data = users_dict.get(self.current_user, {})
        if my_user_data.get("is_owner"):
            admin_frame = ttk.LabelFrame(pass_frame, text="Reset User Password (Owner)", padding=10)
            admin_frame.pack(fill="x", pady=15)
            
            ttk.Label(admin_frame, text="Select user to reset:").pack(anchor="w")
            other_users = [u for u in users_dict.keys() if u != self.current_user]
            reset_user_var = tk.StringVar()
            reset_combo = ttk.Combobox(admin_frame, textvariable=reset_user_var, values=other_users, state="readonly")
            reset_combo.pack(anchor="w", pady=5)
            
            def reset_password():
                user_to_reset = reset_user_var.get()
                if not user_to_reset: return
                
                temp_pass = secrets.token_urlsafe(8)
                
                salt = os.urandom(16)
                udata = users_dict[user_to_reset]
                if not isinstance(udata, dict):
                    messagebox.showerror("Error", "Cannot reset password for a user who has not set one up yet.", parent=dialog)
                    return

                udata["salt"] = base64.urlsafe_b64encode(salt).decode('utf-8')
                udata["hash"] = self._hash_password(temp_pass, salt)
                _save_users(users_dict)
                
                messagebox.showinfo("Password Reset", f"Password for '{user_to_reset}' has been reset.\n\nTheir new temporary password is:\n\n{temp_pass}\n\nPlease share this with them securely.", parent=dialog)
                reset_user_var.set("")

            ttk.Button(admin_frame, text="Reset Password", command=reset_password).pack(anchor="w", pady=10)

        taxes_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(taxes_frame, text="Taxes", group="Estimates")

        ttk.Label(taxes_frame, text="Profit Margin (%):").grid(row=0, column=0, sticky="w", pady=5)
        profit_var = tk.StringVar(value=str(settings.get("profit_margin", 0.0)))
        ttk.Entry(taxes_frame, textvariable=profit_var, width=15).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(taxes_frame, text="Taxes and Fees (%):").grid(row=1, column=0, sticky="w", pady=5)
        taxes_var = tk.StringVar(value=str(settings.get("taxes_and_fees", 0.0)))
        ttk.Entry(taxes_frame, textvariable=taxes_var, width=15).grid(row=1, column=1, padx=5, pady=5)

        def save_taxes():
            try:
                settings["profit_margin"] = float(profit_var.get().replace(',', '.'))
                settings["taxes_and_fees"] = float(taxes_var.get().replace(',', '.'))
                with open(settings_path, 'w') as f:
                    json.dump(settings, f)
                if self.drive_sync:
                    try: self.drive_sync.sync_up(['settings.json'])
                    except Exception: pass
                messagebox.showinfo("Saved", "Taxes and fees settings saved.", parent=dialog)
            except ValueError:
                messagebox.showerror("Error", "Please enter valid numbers.", parent=dialog)

        ttk.Button(taxes_frame, text="Save Taxes Settings", command=save_taxes).grid(row=2, column=0, columnspan=2, pady=15)

        obs_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(obs_frame, text="Default Observations", group="Estimates")

        ttk.Label(obs_frame, text="Saved Observations:").pack(anchor="w")
        obs_listbox = tk.Listbox(obs_frame, height=8)
        obs_listbox.pack(fill="x", pady=5)

        ttk.Label(obs_frame, text="Observation Text:").pack(anchor="w", pady=(10, 0))
        obs_text = tk.Text(obs_frame, height=5)
        obs_text.pack(fill="x", pady=5)

        obs_list = settings.get("default_observations", [])
        for obs in obs_list:
            preview = obs.replace('\n', ' ')
            preview = preview[:60] + ("..." if len(preview) > 60 else "")
            obs_listbox.insert(tk.END, preview)

        def on_obs_select(evt):
            sel = obs_listbox.curselection()
            if sel:
                obs_text.delete("1.0", tk.END)
                obs_text.insert("1.0", obs_list[sel[0]])

        obs_listbox.bind("<<ListboxSelect>>", on_obs_select)

        def add_obs():
            text = obs_text.get("1.0", tk.END).strip()
            if text:
                obs_list.append(text)
                preview = text.replace('\n', ' ')
                preview = preview[:60] + ("..." if len(preview) > 60 else "")
                obs_listbox.insert(tk.END, preview)
                obs_text.delete("1.0", tk.END)

        def update_obs():
            sel = obs_listbox.curselection()
            text = obs_text.get("1.0", tk.END).strip()
            if sel and text:
                obs_list[sel[0]] = text
                preview = text.replace('\n', ' ')
                preview = preview[:60] + ("..." if len(preview) > 60 else "")
                obs_listbox.delete(sel[0])
                obs_listbox.insert(sel[0], preview)
                obs_listbox.selection_set(sel[0])

        def delete_obs():
            sel = obs_listbox.curselection()
            if sel:
                obs_list.pop(sel[0])
                obs_listbox.delete(sel[0])
                obs_text.delete("1.0", tk.END)

        def move_obs_up():
            sel = obs_listbox.curselection()
            if not sel: return
            idx = sel[0]
            if idx == 0: return
            obs_list[idx], obs_list[idx-1] = obs_list[idx-1], obs_list[idx]
            val = obs_listbox.get(idx)
            obs_listbox.delete(idx)
            obs_listbox.insert(idx-1, val)
            obs_listbox.selection_set(idx-1)

        def move_obs_down():
            sel = obs_listbox.curselection()
            if not sel: return
            idx = sel[0]
            if idx == len(obs_list) - 1: return
            obs_list[idx], obs_list[idx+1] = obs_list[idx+1], obs_list[idx]
            val = obs_listbox.get(idx)
            obs_listbox.delete(idx)
            obs_listbox.insert(idx+1, val)
            obs_listbox.selection_set(idx+1)

        btn_frame_obs = ttk.Frame(obs_frame)
        btn_frame_obs.pack(fill="x", pady=5)
        ttk.Button(btn_frame_obs, text="Add New", command=add_obs).pack(side="left", padx=5)
        ttk.Button(btn_frame_obs, text="Update Selected", command=update_obs).pack(side="left", padx=5)
        ttk.Button(btn_frame_obs, text="Delete Selected", command=delete_obs).pack(side="left", padx=5)
        ttk.Button(btn_frame_obs, text="▲", width=3, command=move_obs_up).pack(side="left", padx=2)
        ttk.Button(btn_frame_obs, text="▼", width=3, command=move_obs_down).pack(side="left", padx=2)

        def save_obs_settings():
            settings["default_observations"] = obs_list
            with open(settings_path, 'w') as f:
                json.dump(settings, f)
            if self.drive_sync:
                try: self.drive_sync.sync_up(['settings.json'])
                except Exception: pass
            messagebox.showinfo("Saved", "Default observations saved.", parent=dialog)

        ttk.Button(obs_frame, text="Save Observations", command=save_obs_settings).pack(pady=15)

        conn_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(conn_frame, text="Connection", group="Sync and Data")
        
        ttk.Label(conn_frame, text="Disconnecting will log you out and allow you to load a different company's credentials.json file. Local files will be cleared to prevent data mixing.", wraplength=500).pack(pady=10)
        
        def do_disconnect():
            if messagebox.askyesno("Disconnect", "Do you want to synchronize your current changes before disconnecting?", parent=dialog):
                self._show_progress_dialog("Syncing", "Synchronizing databases before disconnect...")
                if self.drive_sync:
                    try: self.drive_sync.sync_up(self.db_files, self.current_user or "Unknown")  # type: ignore
                    except: pass
                self._hide_progress_dialog()
                
            self.save_current_tokens_to_profile()
            
            config = self.load_local_config()
            config["active_company"] = None
            config["workspace_company"] = None
            self.save_local_config(config)
            
            self.force_disconnect = True
            dialog.destroy()
            
            if os.path.exists("credentials.json"): os.remove("credentials.json")
            if os.path.exists("token.json"): os.remove("token.json")
            self.clean_local_workspace()
            
            self.logout_no_sync()
            
        ttk.Button(conn_frame, text="Disconnect from Company", command=do_disconnect, style="Accent.TButton").pack(pady=10)

        # --- Contract Info Tab ---
        contract_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(contract_frame, text="Contract Info", group="Contracts")

        ttk.Label(contract_frame, text="Enter your company's information for contract generation.", wraplength=500).pack(pady=(0, 10), anchor="w")

        contract_info = settings.get("contract_info", {})
        contract_vars = {}

        fields = [
            ("Company Full Name", "company_name", "Quarium Consultoria em Biologia Analítica LTDA"),
            ("Company CNPJ", "company_cnpj", "53.429.415/0001-41"),
            ("Company Address", "company_address", "Rua Maria Bicego, 323, Vila Santa Isabel, Campinas, SP. CEP: 13084-639"),
            ("Legal Representative Name", "rep_name", "Lícia Carla da Silva Costa"),
            ("Representative Nationality", "rep_nationality", "Brasileira"),
            ("Representative Marital Status", "rep_marital_status", "Solteira"),
            ("Representative Profession", "rep_profession", "Bióloga"),
            ("Representative ID (RG)", "rep_id", "56.727.423-8"),
            ("Representative ID Issuer", "rep_id_issuer", "SSP/SP"),
            ("Representative CPF", "rep_cpf", "086.388.957-39")
        ]

        form_frame = ttk.Frame(contract_frame)
        form_frame.pack(fill="x")

        for i, (label, key, default) in enumerate(fields):
            ttk.Label(form_frame, text=f"{label}:").grid(row=i, column=0, sticky="w", pady=2, padx=5)
            var = tk.StringVar(value=contract_info.get(key, default))
            ttk.Entry(form_frame, textvariable=var, width=50).grid(row=i, column=1, sticky="ew", pady=2, padx=5)
            contract_vars[key] = var

        form_frame.columnconfigure(1, weight=1)

        def save_contract_info():
            new_info = {}
            for key, var in contract_vars.items():
                new_info[key] = var.get()
            
            settings["contract_info"] = new_info
            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=2)
            
            if self.drive_sync:
                try:
                    self.drive_sync.sync_up(['settings.json'])
                except Exception as e:
                    messagebox.showwarning("Sync Warning", f"Could not sync settings.json to cloud: {e}", parent=dialog)
            
            messagebox.showinfo("Saved", "Contract information saved successfully.", parent=dialog)
            
            # Refresh the contract manager if it's open
            if "Contracts" in self.apps:
                self.apps["Contracts"].load_settings()

        ttk.Button(contract_frame, text="Save Contract Info", command=save_contract_info).pack(pady=15)

        workspace_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(workspace_frame, text="Data Location", group="Sync and Data")
        self._build_workspace_tab(workspace_frame, dialog)

        conflicts_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(conflicts_frame, text="Sync Conflicts", group="Sync and Data")

        recovery_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(recovery_frame, text="Recovered Work", group="Sync and Data")
        self._build_recovery_tab(recovery_frame)
        
        ttk.Label(conflicts_frame, text="Conflict files are generated when two users edit the database simultaneously, or if someone works offline. Download them here to manually inspect the changes, then delete them from the cloud when resolved.", wraplength=500).pack(pady=(0, 10), anchor="w")
        
        conflict_listbox = tk.Listbox(conflicts_frame, height=8)
        conflict_listbox.pack(fill="x", pady=5)
        
        conflict_files = {}
        if self.drive_sync:
            try:
                conflict_files = self.drive_sync.list_conflict_files()
                for name in conflict_files:
                    conflict_listbox.insert(tk.END, name)
            except Exception as e:
                conflict_listbox.insert(tk.END, f"Error loading conflicts: {e}")
                
        def download_conflict():
            sel = conflict_listbox.curselection()
            if not sel: return
            name = conflict_listbox.get(sel[0])
            if name not in conflict_files: return
            file_id = conflict_files[name]['id']
            save_path = filedialog.asksaveasfilename(initialfile=name, title="Save Conflict File")
            if save_path:
                try:
                    self.drive_sync.download_file(file_id, save_path)
                    messagebox.showinfo("Success", f"Downloaded successfully to:\n{save_path}", parent=dialog)
                except Exception as e:
                    messagebox.showerror("Error", f"Could not download: {e}", parent=dialog)
                    
        def delete_conflict():
            sel = conflict_listbox.curselection()
            if not sel: return
            name = conflict_listbox.get(sel[0])
            if name not in conflict_files: return
            if messagebox.askyesno("Confirm Delete", f"Are you sure you want to permanently delete '{name}' from the cloud?", parent=dialog):
                self.drive_sync.delete_file(conflict_files[name]['id'])
                conflict_listbox.delete(sel[0])
                del conflict_files[name]
                
        def resolve_conflict():
            sel = conflict_listbox.curselection()
            if not sel: return
            name = conflict_listbox.get(sel[0])
            if name not in conflict_files: return
            file_id = conflict_files[name]['id']

            base_db = None
            for db in ['stock.db', 'services.db', 'clients.db', 'projects.db']:
                if name.startswith(db.split('.')[0]):
                    base_db = db
                    break
            
            if not base_db:
                messagebox.showerror("Error", "Cannot determine base database for this conflict file.", parent=dialog)
                return

            self._show_progress_dialog("Downloading", "Downloading conflict file for analysis...")
            temp_db = "temp_conflict_resolve.db"
            if os.path.exists(temp_db):
                try: os.remove(temp_db)
                except: pass
                
            try:
                self.drive_sync.download_file(file_id, temp_db)
            except Exception as e:
                self._hide_progress_dialog()
                messagebox.showerror("Error", f"Could not download: {e}", parent=dialog)
                return
            self._hide_progress_dialog()

            try:
                conn_live = sqlite3.connect(base_db)
                conn_conf = sqlite3.connect(temp_db)
                c_live = conn_live.cursor()
                c_conf = conn_conf.cursor()
                
                c_live.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in c_live.fetchall() if r[0] != 'sqlite_sequence']

                differences = []

                for table in tables:
                    c_live.execute(f"PRAGMA table_info({table})")
                    cols = [r[1] for r in c_live.fetchall()]
                    if 'id' not in cols: continue 
                    
                    col_names = [c for c in cols if c != 'id']
                    
                    c_live.execute(f"SELECT {','.join(col_names)} FROM {table}")
                    live_rows = set(c_live.fetchall())
                    
                    try:
                        c_conf.execute(f"SELECT {','.join(col_names)} FROM {table}")
                        conf_rows = set(c_conf.fetchall())
                    except sqlite3.OperationalError:
                        continue # Table might not exist in an older conflict db
                    
                    new_in_conf = conf_rows - live_rows
                    for row in new_in_conf:
                        differences.append((table, col_names, row))
                        
                conn_live.close()
                conn_conf.close()
            except Exception as e:
                messagebox.showerror("Error", f"Error analyzing databases: {e}", parent=dialog)
                if os.path.exists(temp_db): os.remove(temp_db)
                return

            if not differences:
                messagebox.showinfo("No Differences", "No new or modified rows were found in this conflict file compared to the live database.", parent=dialog)
                if os.path.exists(temp_db): os.remove(temp_db)
                return

            dialog_res = tk.Toplevel(dialog)
            dialog_res.title("Resolve Conflict")
            dialog_res.geometry("950x550")
            dialog_res.transient(dialog)
            dialog_res.grab_set()

            msg = (f"Found {len(differences)} new/modified records in '{name}'.\n"
                   f"Select the records you wish to automatically merge into your live '{base_db}'.\n"
                   "WARNING: Foreign keys (like Company ID or Category ID) are copied exactly as they were offline. If those parent items were also newly created, their IDs may have changed and you will need to manually re-link them in the UI after merging.")
            ttk.Label(dialog_res, text=msg, wraplength=900, font=('Helvetica', 10, 'bold'), foreground="#D32F2F").pack(pady=10, padx=10)

            tree_frame = ttk.Frame(dialog_res)
            tree_frame.pack(fill="both", expand=True, padx=10, pady=5)

            tree = ttk.Treeview(tree_frame, columns=("Merge", "Table", "Data"), show="headings")
            tree.heading("Merge", text="Merge?")
            tree.heading("Table", text="Table")
            tree.heading("Data", text="Record Data Summary")
            tree.column("Merge", width=80, anchor="center")
            tree.column("Table", width=150)
            tree.column("Data", width=650)
            tree.pack(side="left", fill="both", expand=True)

            scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
            scroll.pack(side="right", fill="y")
            tree.configure(yscrollcommand=scroll.set)

            for i, (tbl, cols, row) in enumerate(differences):
                summary = " | ".join([f"{c}: {v}" for c, v in zip(cols, row) if v is not None and v != ""])
                tree.insert("", "end", values=("☑ YES", tbl, summary), tags=(str(i),))

            def toggle_check(event):
                item = tree.identify_row(event.y)
                if not item: return
                col = tree.identify_column(event.x)
                if col == '#1': 
                    vals = list(tree.item(item, "values"))
                    vals[0] = "☐ NO" if vals[0] == "☑ YES" else "☑ YES"
                    tree.item(item, values=tuple(vals))

            tree.bind("<Button-1>", toggle_check)

            def apply_merge():
                selected_diffs = []
                for item in tree.get_children():
                    vals = tree.item(item, "values")
                    if vals[0] == "☑ YES":
                        idx = int(tree.item(item, "tags")[0])
                        selected_diffs.append(differences[idx])
                
                if not selected_diffs:
                    messagebox.showinfo("No Selection", "No records selected for merging.", parent=dialog_res)
                    return
                    
                if not messagebox.askyesno("Confirm Merge", f"Merge {len(selected_diffs)} records into '{base_db}'?\nThis action cannot be undone.", parent=dialog_res):
                    return
                    
                try:
                    conn = sqlite3.connect(base_db)
                    c = conn.cursor()
                    for tbl, cols, row in selected_diffs:
                        placeholders = ",".join(["?" for _ in cols])
                        col_str = ",".join(cols)
                        c.execute(f"INSERT INTO {tbl} ({col_str}) VALUES ({placeholders})", row)
                    conn.commit()
                    conn.close()
                    
                    messagebox.showinfo("Success", "Merge successful! The changes have been applied to your live database.", parent=dialog_res)
                    dialog_res.destroy()
                    
                    if os.path.exists(temp_db): os.remove(temp_db)
                    
                    if messagebox.askyesno("Cleanup", "Do you want to permanently delete this conflict file from the cloud now?", parent=dialog):
                        self.drive_sync.delete_file(file_id)
                        conflict_listbox.delete(sel[0])
                        del conflict_files[name]
                        
                    self.switch_view() # Refresh UI to show new data
                    
                except Exception as e:
                    messagebox.showerror("Merge Error", f"Failed to apply merge: {e}", parent=dialog_res)

            btn_frame = ttk.Frame(dialog_res)
            btn_frame.pack(pady=10)
            ttk.Button(btn_frame, text="Apply Selected Changes", command=apply_merge, style="Accent.TButton").pack(side="left", padx=10)
            ttk.Button(btn_frame, text="Cancel", command=dialog_res.destroy).pack(side="left", padx=10)

        c_btn_frame = ttk.Frame(conflicts_frame)
        c_btn_frame.pack(fill="x", pady=5)
        ttk.Button(c_btn_frame, text="Download Selected", command=download_conflict).pack(side="left", padx=5)
        ttk.Button(c_btn_frame, text="Resolve Selected", command=resolve_conflict).pack(side="left", padx=5)
        ttk.Button(c_btn_frame, text="Delete from Cloud", command=delete_conflict).pack(side="left", padx=5)

        backup_frame = ttk.Frame(notebook, padding=(18, 14))
        notebook.add(backup_frame, text="Data Backup", group="Sync and Data")
        
        ttk.Label(backup_frame, text="Create or restore an encrypted backup of all system databases and images.", wraplength=500).pack(pady=10)
        
        if CRYPTO_AVAILABLE:
            ttk.Button(backup_frame, text="Export Encrypted Backup", command=self._export_backup).pack(pady=10)
            ttk.Button(backup_frame, text="Import Encrypted Backup", command=self._import_backup).pack(pady=10)
        else:
            ttk.Label(backup_frame, text="Backup feature requires the 'cryptography' library.\nPlease install it via terminal: pip install cryptography", foreground="red").pack(pady=10)

    def _build_workspace_tab(self, parent, dialog):
        """Lets the data folder be moved elsewhere, e.g. to an external drive."""
        ttk.Label(parent,
                  text="Where this computer keeps its databases and settings. The cloud copy "
                       "is unaffected by this choice.",
                  wraplength=560).pack(anchor="w", pady=(0, 10))

        current_var = tk.StringVar()
        status_var = tk.StringVar()
        ttk.Label(parent, text="Current folder:").pack(anchor="w")
        path_label = ttk.Label(parent, textvariable=current_var, foreground="#285D80",
                               wraplength=560)
        path_label.pack(anchor="w", pady=(0, 2))
        ttk.Label(parent, textvariable=status_var, foreground="#666").pack(anchor="w",
                                                                           pady=(0, 12))

        def refresh():
            active = QuariumPaths.data_dir()
            current_var.set(active)
            if QuariumPaths.is_relocated():
                where = "Custom location"
            else:
                where = "Default location"
            size = 0
            for name in QuariumPaths.DATA_FILES:
                p = os.path.join(active, name)
                if os.path.exists(p):
                    size += os.path.getsize(p)
            status_var.set(f"{where}  ·  {size / 1024 / 1024:.1f} MB of data"
                           + ("" if os.path.isdir(active) else "  ·  NOT REACHABLE"))

        def choose():
            chosen = filedialog.askdirectory(title="Choose a folder for Quarium's data",
                                             parent=dialog)
            if not chosen:
                return
            chosen = os.path.abspath(chosen)
            if os.path.abspath(chosen) == os.path.abspath(QuariumPaths.data_dir()):
                messagebox.showinfo("No change", "That is already the current folder.",
                                    parent=dialog)
                return

            report = QuariumPaths.inspect_folder(chosen)
            if not report['writable'] and report['status'] != 'missing':
                messagebox.showerror("Not Writable",
                                     f"Quarium cannot write to:\n\n{chosen}", parent=dialog)
                return

            if report['status'] == 'occupied':
                if not messagebox.askyesno(
                        "Folder Is Not Empty",
                        f"{chosen}\n\nalready contains {len(report['entries'])} item(s) that do "
                        "not look like Quarium data, for example:\n\n  "
                        + "\n  ".join(report['entries'][:5])
                        + "\n\nQuarium will add its files alongside them. Continue?",
                        parent=dialog):
                    return
            elif report['status'] in ('workspace', 'data'):
                described = ("an existing Quarium workspace" if report['status'] == 'workspace'
                             else "Quarium data files without a workspace marker")
                answer = messagebox.askyesno(
                    "Existing Data Found",
                    f"{chosen}\n\nalready holds {described} "
                    f"({len(report['data_files'])} file(s)).\n\n"
                    "Yes  -  use what is already there, and leave this computer's current "
                    "data where it is\n"
                    "No   -  cancel\n\n"
                    "Nothing there will be overwritten either way.",
                    parent=dialog)
                if not answer:
                    return
                QuariumPaths.set_workspace(chosen)
                QuariumPaths.write_marker(chosen)
                self._workspace_changed(chosen, dialog, copied=False)
                refresh()
                return

            move = messagebox.askyesno(
                "Move or Copy",
                f"Put this computer's data in:\n\n{chosen}\n\n"
                "Yes  -  move it (the current folder is emptied once every file is verified)\n"
                "No   -  copy it (the current folder keeps a copy)",
                parent=dialog)
            source = QuariumPaths.data_dir()
            try:
                copied = QuariumPaths.copy_workspace(source, chosen, move=move)
            except Exception as e:
                messagebox.showerror("Could Not Move",
                                     f"Nothing was removed. The error was:\n\n{e}", parent=dialog)
                return
            QuariumPaths.set_workspace(chosen)
            self._workspace_changed(chosen, dialog, copied=len(copied))
            refresh()

        def reset():
            if QuariumPaths.is_relocated() and messagebox.askyesno(
                    "Use Default Folder",
                    "Go back to the default folder?\n\nThe files in the custom folder are left "
                    "where they are; nothing is deleted.", parent=dialog):
                QuariumPaths.clear_workspace()
                self._workspace_changed(QuariumPaths.data_dir(), dialog, copied=False)
                refresh()

        buttons = ttk.Frame(parent)
        buttons.pack(anchor="w")
        ttk.Button(buttons, text="Change Folder...", command=choose).pack(side="left")
        ttk.Button(buttons, text="Open Folder",
                   command=lambda: os.startfile(QuariumPaths.data_dir())
                   if os.path.isdir(QuariumPaths.data_dir()) else None).pack(side="left", padx=6)
        ttk.Button(buttons, text="Use Default", command=reset).pack(side="left")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=16)

        ttk.Label(parent, text="Encryption at rest",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(parent,
                  text="Worth turning on for a removable drive: the folder is encrypted when "
                       "you close the application and decrypted when you reopen it. The cloud "
                       "copy stays unencrypted, so a forgotten passphrase costs you the local "
                       "files, not the data.",
                  wraplength=560, foreground="#666").pack(anchor="w", pady=(2, 8))

        vault_status = tk.StringVar()

        def refresh_vault():
            try:
                import QuariumVault
            except ImportError:
                vault_status.set("Encryption unavailable: QuariumVault.py not found.")
                return None
            if not QuariumVault.CRYPTO_AVAILABLE:
                vault_status.set("Encryption unavailable: the cryptography library is missing.")
                return QuariumVault
            active = QuariumPaths.data_dir()
            if QuariumVault.is_enabled(active):
                vault_status.set(f"On  ·  the folder is encrypted whenever the app is closed "
                                 f"(currently {QuariumVault.state(active)})")
            else:
                vault_status.set("Off  ·  files are stored in the clear")
            return QuariumVault

        ttk.Label(parent, textvariable=vault_status, foreground="#285D80").pack(anchor="w")

        def enable_encryption():
            vault = refresh_vault()
            if not vault or not vault.CRYPTO_AVAILABLE:
                return
            active = QuariumPaths.data_dir()
            if vault.is_enabled(active):
                messagebox.showinfo("Already On", "Encryption is already set up for this folder.",
                                    parent=dialog)
                return
            first = simpledialog.askstring("Set Passphrase",
                                           "Choose a passphrase for this data folder:",
                                           show='*', parent=dialog)
            if not first:
                return
            again = simpledialog.askstring("Set Passphrase", "Enter it again:",
                                           show='*', parent=dialog)
            if first != again:
                messagebox.showerror("Mismatch", "The two entries do not match.", parent=dialog)
                return
            try:
                vault.enable(active, first)
            except Exception as e:
                messagebox.showerror("Could Not Enable", str(e), parent=dialog)
                return
            self.vault_passphrase = first
            messagebox.showwarning(
                "Encryption On",
                "The folder will be encrypted when you close the application.\n\n"
                "Keep this passphrase safe. If it is lost, point Quarium at the default "
                "folder and download a fresh copy from the cloud.",
                parent=dialog)
            refresh_vault()

        def disable_encryption():
            vault = refresh_vault()
            if not vault:
                return
            active = QuariumPaths.data_dir()
            if not vault.is_enabled(active):
                return
            passphrase = simpledialog.askstring("Turn Off Encryption",
                                                "Enter the current passphrase:",
                                                show='*', parent=dialog)
            if not passphrase:
                return
            if not vault.verify(active, passphrase):
                messagebox.showerror("Wrong Passphrase", "That passphrase does not match.",
                                     parent=dialog)
                return
            try:
                vault.recover(active, passphrase)
                vault.disable(active)
            except Exception as e:
                messagebox.showerror("Could Not Turn Off", f"{e}\n\nNothing was deleted.",
                                     parent=dialog)
                return
            self.vault_passphrase = None
            messagebox.showinfo("Encryption Off", "The folder is stored in the clear again.",
                                parent=dialog)
            refresh_vault()

        vault_buttons = ttk.Frame(parent)
        vault_buttons.pack(anchor="w", pady=(8, 0))
        ttk.Button(vault_buttons, text="Turn On Encryption...",
                   command=enable_encryption).pack(side="left")
        ttk.Button(vault_buttons, text="Turn Off...",
                   command=disable_encryption).pack(side="left", padx=6)

        refresh()
        refresh_vault()

    def _workspace_changed(self, path, dialog, copied):
        detail = f"{copied} file(s) transferred.\n\n" if copied else ""
        messagebox.showinfo(
            "Data Location Changed",
            f"{detail}Quarium will use:\n\n{path}\n\n"
            "Close and reopen the application for this to take effect.",
            parent=dialog)

    def _select_image(self, target_filename):
        # Replacing a logo is an edit, and it is shared: without this a
        # read-only window reported the change as done while the file it
        # actually sent was a conflict copy nobody would look at.
        if not getattr(self, 'is_owner', True):
            messagebox.showwarning(
                "Read-Only",
                "Another user is editing, so images cannot be changed right now.\n\n"
                "Use 'Request Edit Access' and try again once they hand over.")
            return
        file_path = filedialog.askopenfilename(filetypes=[("Image Files", "*.png *.jpg *.jpeg")])
        if file_path:
            try:
                shutil.copy(file_path, target_filename)
                result = self.drive_sync.sync_up([target_filename]) if self.drive_sync else None
                if result and result['conflicts']:
                    messagebox.showwarning(
                        "Saved Locally",
                        f"{target_filename} was changed on this computer, but the copy in "
                        "the cloud had already moved on, so your version was kept beside "
                        "it as a conflict file rather than replacing it.\n\n"
                        "See Settings > Sync Conflicts.")
                    return
                messagebox.showinfo("Success", f"{target_filename} updated successfully! Dashboard changes will reflect upon restart.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to copy image: {e}")

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def _export_backup(self):
        password = simpledialog.askstring("Backup Password", "Enter a password to encrypt this backup:", show='*')
        if not password: return
        
        save_path = filedialog.asksaveasfilename(defaultextension=".qbak", filetypes=[("Quarium Backup", "*.qbak")], title="Save Encrypted Backup")
        if not save_path: return
        
        try:
            temp_zip = "temp_backup.zip"
            with zipfile.ZipFile(temp_zip, 'w') as zipf:
                for f in self.db_files:
                    if os.path.exists(f):
                        zipf.write(f)
            
            with open(temp_zip, 'rb') as f:
                zip_data = f.read()
            os.remove(temp_zip)
            
            salt = secrets.token_bytes(16)
            key = self._derive_key(password, salt)
            f_crypto = Fernet(key)
            encrypted_data = f_crypto.encrypt(zip_data)
            
            with open(save_path, 'wb') as f_out:
                f_out.write(salt)
                f_out.write(encrypted_data)
                
            messagebox.showinfo("Success", "Encrypted backup exported successfully.")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export backup: {e}")

    def _import_backup(self):
        # This replaces every database, locally and then in the cloud. Doing it
        # while someone else holds the edit lock would overwrite the work they
        # are in the middle of, with no way back.
        if not getattr(self, 'is_owner', True):
            messagebox.showwarning(
                "Read-Only",
                "Another user is editing, so a backup cannot be restored right now.\n\n"
                "Restoring replaces every database. Wait until you have edit access.")
            return
        file_path = filedialog.askopenfilename(filetypes=[("Quarium Backup", "*.qbak")], title="Select Encrypted Backup")
        if not file_path: return
        
        password = simpledialog.askstring("Backup Password", "Enter the password to decrypt this backup:", show='*')
        if not password: return
        
        try:
            with open(file_path, 'rb') as f:
                data = f.read()
                
            salt = data[:16]
            encrypted_data = data[16:]
            
            key = self._derive_key(password, salt)
            f_crypto = Fernet(key)
            
            try:
                decrypted_data = f_crypto.decrypt(encrypted_data)
            except InvalidToken:
                messagebox.showerror("Error", "Invalid password or corrupted backup file.")
                return
                
            temp_zip = "temp_restore.zip"
            with open(temp_zip, 'wb') as f_out:
                f_out.write(decrypted_data)
                
            for app in self.apps.values():
                try:
                    if hasattr(app, 'conn'): app.conn.close()
                except Exception: pass
                
            with zipfile.ZipFile(temp_zip, 'r') as zipf:
                zipf.extractall()
            os.remove(temp_zip)
            
            if self.drive_sync:
                self.drive_sync.sync_up(self.db_files)
                
            messagebox.showinfo("Success", "Backup restored successfully.\nThe application will now close to apply changes safely. Please reopen it.")
            self.root.destroy()
            
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to import backup: {e}")

def _unlock_workspace(workspace):
    """Asks for the passphrase when the folder was left encrypted.

    Runs before the managers import, so nothing tries to open a database that
    is still ciphertext. Returns the passphrase to re-lock with on exit, or
    None if encryption is off; False if the user gave up.
    """
    try:
        import QuariumVault
    except ImportError:
        return None
    if not QuariumVault.is_enabled(workspace):
        return None
    if QuariumVault.state(workspace) == QuariumVault.STATE_UNLOCKED:
        return None

    import tkinter as tk
    from tkinter import messagebox, simpledialog

    root = tk.Tk()
    root.withdraw()
    try:
        for _attempt in range(3):
            passphrase = simpledialog.askstring(
                "Encrypted Data",
                f"This data folder is encrypted:\n\n{workspace}\n\nEnter its passphrase:",
                show='*', parent=root)
            if passphrase is None:
                return False
            if not QuariumVault.verify(workspace, passphrase):
                messagebox.showerror("Wrong Passphrase",
                                     "That passphrase does not match this folder.", parent=root)
                continue
            try:
                QuariumVault.recover(workspace, passphrase)
            except Exception as e:
                messagebox.showerror("Could Not Decrypt",
                                     f"{e}\n\nNothing was deleted.", parent=root)
                return False
            return passphrase
        messagebox.showerror(
            "Encrypted Data",
            "The folder stays encrypted.\n\nThe cloud copy is not encrypted, so you can also "
            "switch to the default data folder in Settings and download a fresh copy.",
            parent=root)
        return False
    finally:
        root.destroy()


def _acquire_single_instance_lock():
    """Uses a Windows named mutex to detect an already-running instance.

    Checked before anything else (splash screen, network calls) so a second
    launch is rejected instantly even while the first instance is still
    silently loading over a slow connection -- the exact scenario that
    previously let clean_local_workspace() run twice and wipe local data.
    The OS releases the mutex automatically on process exit, even on a
    crash, so there's no stale-lock-file cleanup to worry about.
    """
    import ctypes
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, "Global\\QuariumDashboard_SingleInstance")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return mutex  # keep a reference alive for the process lifetime


if __name__ == "__main__":
    _instance_lock = _acquire_single_instance_lock()
    if _instance_lock is None:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            "Quarium Dashboard is already running.\n\n"
            "Check your taskbar for the existing window -- if your connection is slow, "
            "it may still be loading rather than stuck.",
            "Already Running",
            0x30,  # MB_ICONWARNING
        )
        sys.exit(0)

    # Seed the workspace from the program folder the first time, then work
    # from it. Anchoring the working directory here means every bare relative
    # path in the app and in the Drive sync resolves inside the workspace.
    import QuariumPaths
    migrated = QuariumPaths.migrate_if_needed()
    if migrated:
        print(f"Set up {_BASE_DIR} with {len(migrated)} item(s) from the program folder.")

    _vault_passphrase = _unlock_workspace(_BASE_DIR)
    if _vault_passphrase is False:
        sys.exit(0)

    os.chdir(_BASE_DIR)

    root = tk.Tk()
    app = QuariumDashboard(root)
    app.vault_passphrase = _vault_passphrase
    root.mainloop()

    # Re-encrypt once the window is gone and every database has been closed.
    if _vault_passphrase:
        try:
            import QuariumVault
            if QuariumVault.is_enabled(_BASE_DIR):
                QuariumVault.lock(_BASE_DIR, getattr(app, 'vault_passphrase', _vault_passphrase))
        except Exception as e:
            print("Could not re-encrypt the data folder:", e)