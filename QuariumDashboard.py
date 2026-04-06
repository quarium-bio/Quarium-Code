import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import os
import json
import base64
import secrets
import zipfile
import shutil
import threading
import time
from tkinter import filedialog
import sqlite3
import urllib.request
import urllib.error

# Import the application classes
from QuariumClientManager import ClientManager
from QuariumServiceManager import ServiceManager
from QuariumProjectManager import ProjectManager # New import
from CompositeStockManager import CompositeStockManager
from QuariumSM import StockManager
from QuariumProjectFlow import ProjectFlowManager

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

CURRENT_VERSION = "1.0.0"
UPDATE_URL = "https://raw.githubusercontent.com/quarium-bio/bio-dashboard/main/version.json" # Change to your actual raw URL

class QuariumDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Quarium Dashboard")
        self.root.withdraw() # Hide until authenticated
        
        self.apps = {}
        self.frames = {}
        self.current_user = None
        self.drive_sync = None # Initialize to None
        self.db_files = ['stock.db', 'services.db', 'clients.db', 'projects.db', 'users.json', 'settings.json', 'QLogo.png', 'EstimateLogo.png']
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.startup_check()
        
    def check_updates(self):
        try:
            req = urllib.request.Request(UPDATE_URL, headers={'User-Agent': 'QuariumApp/1.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
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
        self.clean_local_workspace()
        prof = config["companies"].get(comp_name)
        if not prof: return
        with open("credentials.json", "w") as f:
            f.write(prof.get("credentials", ""))
        if prof.get("token"):
            with open("token.json", "w") as f:
                f.write(prof["token"])
        else:
            if os.path.exists("token.json"):
                os.remove("token.json")

    def startup_check(self):
        if not self.check_updates():
            return
            
        config = self.load_local_config()
        
        # Legacy migration for existing users
        if os.path.exists("credentials.json") and not config["companies"]:
            comp_name = simpledialog.askstring("Setup", "Existing connection detected.\nPlease enter your Company Name (e.g., Quarium):")
            if not comp_name: comp_name = "Default Company"
            
            with open("credentials.json", "r") as f: creds = f.read()
            token_data = ""
            if os.path.exists("token.json"):
                with open("token.json", "r") as f: token_data = f.read()
                
            config["companies"][comp_name] = {"credentials": creds, "token": token_data}
            config["active_company"] = comp_name
            self.save_local_config(config)
            
        active = config.get("active_company")
        if active and active in config["companies"] and not getattr(self, 'force_disconnect', False):
            self.apply_profile(active, config)
            self.authenticate_and_sync()
        else:
            self.show_connection_manager()

    def show_connection_manager(self):
        self.root.withdraw()
        conn_win = tk.Toplevel(self.root)
        conn_win.title("Connection Manager")
        conn_win.geometry("450x420")
        conn_win.grab_set()
        
        config = self.load_local_config()
        
        ttk.Label(conn_win, text="Welcome to Quarium", font=("Helvetica", 14, "bold")).pack(pady=10)
        ttk.Label(conn_win, text="Select an existing company profile or load a new credentials.json file to connect.", wraplength=400, justify="center").pack(pady=10)
        
        if config["companies"]:
            ttk.Label(conn_win, text="Saved Companies:").pack(pady=(10,0))
            comp_var = tk.StringVar()
            cb = ttk.Combobox(conn_win, textvariable=comp_var, values=list(config["companies"].keys()), state="readonly", width=30)
            cb.pack(pady=5)
            if config.get("active_company") in config["companies"]:
                cb.set(config["active_company"])
            
            def connect_existing():
                sel = comp_var.get()
                if sel:
                    config["active_company"] = sel
                    self.save_local_config(config)
                    self.apply_profile(sel, config)
                    conn_win.destroy()
                    self.authenticate_and_sync()
                    
            ttk.Button(conn_win, text="Connect", command=connect_existing, style="Accent.TButton").pack(pady=5)
        
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
                conn_win.destroy()
                self.authenticate_and_sync()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load credentials: {e}", parent=conn_win)
            
        ttk.Button(conn_win, text="Load New credentials.json", command=load_new).pack(pady=5)
        ttk.Button(conn_win, text="How to create credentials.json (Tutorial)", command=self.show_tutorial).pack(pady=5)
        
        self.root.wait_window(conn_win)
        if not getattr(self, 'current_user', None):
            self.root.destroy()

    def authenticate_and_sync(self):
        if DriveSyncManager:
            try:
                self.drive_sync = DriveSyncManager()
            except ImportError as e:
                messagebox.showwarning("Sync Dependencies Missing", f"{e}\n\nOperating in local mode.")
            except Exception as e:
                messagebox.showerror("Sync Error", f"An unexpected error occurred during Google Drive sync: {e}\n\nOperating in local mode.")
        else:
            messagebox.showinfo("Local Mode", "QuariumDriveSync not found or Google API libraries missing. Operating locally.")
            
        self.show_login_dialog()
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

    def show_login_dialog(self):
        login_win = tk.Toplevel(self.root)
        login_win.title("User Login")
        login_win.geometry("300x200")
        login_win.grab_set()
        login_win.focus_force() # Make sure it appears on top

        users = {}
        if os.path.exists('users.json'):
            with open('users.json', 'r') as f:
                users = json.load(f)

        ttk.Label(login_win, text="Select User:").pack(pady=(10, 5))
        
        user_var = tk.StringVar()
        user_combo = ttk.Combobox(login_win, textvariable=user_var, values=list(users.keys()), state='readonly')
        user_combo.pack(pady=5)
        
        def login():
            selected = user_var.get()
            if selected:
                self.current_user = selected
                login_win.destroy()
                self.check_and_acquire_lock()
            else:
                messagebox.showwarning("Warning", "Please select a user")

        def new_user():
            name = simpledialog.askstring("New User", "Enter Full Name:", parent=login_win)
            if name:
                username = simpledialog.askstring("New User", "Enter Username:", parent=login_win)
                if username:
                    if username in users:
                        messagebox.showwarning("Warning", "Username already exists")
                    else:
                        users[username] = name
                        with open('users.json', 'w') as f:
                            json.dump(users, f)
                        user_combo['values'] = list(users.keys())
                        user_var.set(username)
                        if self.drive_sync:
                            try:
                                self.drive_sync.sync_up(['users.json'])
                            except Exception as e:
                                print("Error syncing users file:", e)
        
        ttk.Button(login_win, text="Login", command=login).pack(pady=(10, 5))
        ttk.Button(login_win, text="Create New User", command=new_user).pack(pady=5)

        self.root.wait_window(login_win)
        
        if not self.current_user:
            self.root.destroy()
            
    def check_and_acquire_lock(self):
        if not self.drive_sync:
            self.finish_init()
            return
            
        self._show_progress_dialog("Checking Status", "Checking online database status...")
        lock_data = self.drive_sync.read_lock()  # type: ignore
        self._hide_progress_dialog()
        
        now = time.time()
        if lock_data and lock_data.get('owner') and (now - lock_data.get('last_active', 0) < 45):
            owner = lock_data['owner']
            if owner == self.current_user:
                self.do_sync_down_and_finish(read_only=False)
                return
                
            res = messagebox.askyesnocancel("Database in Use", 
                f"User '{owner}' is currently editing the database.\n\n"
                "Do you want to request editing permissions? (They will have 15 seconds to respond).\n\n"
                "Select 'No' to immediately open a Read-Only copy.")
            
            if res is True:
                self.request_lock(owner)
            elif res is False:
                self.do_sync_down_and_finish(read_only=True)
            else:
                self.root.destroy()
        else:
            self.do_sync_down_and_finish(read_only=False)
            

    def do_sync_down_and_finish(self, read_only=False):
        ui_exists = bool(self.frames)
        if ui_exists:
            for app in self.apps.values():
                try:
                    if hasattr(app, 'conn'): app.conn.close()
                except Exception: pass
                
        self._show_progress_dialog("Syncing", "Downloading latest databases...")
        if self.drive_sync:
            try: self.drive_sync.sync_down(self.db_files)  # type: ignore
            except Exception as e: print("Sync down error:", e)
        self._hide_progress_dialog()
        
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
            if not self.frames: self.finish_init()
            self.enforce_read_only_mode()
            if not ui_exists:
                messagebox.showinfo("Read-Only", "You are now in Read-Only mode. Edits cannot be saved.")

    def _notify_lock_lost(self):
        self.enforce_read_only_mode()
        messagebox.showwarning("Session Expired", "You lost your connection to the server and another user took over editing permissions.\n\nYou have been placed in Read-Only mode to prevent data conflicts. Any work done while offline will be safely backed up as a conflict file when you close the app.")

    def acquire_lock(self):
        if self.drive_sync:
            ld = {"owner": self.current_user, "last_active": time.time(), "request_by": None, "response": None}
            self.drive_sync.write_lock(ld)  # type: ignore
        self.is_owner = True
        if not self.frames: self.finish_init()
        else: self.enable_read_write_mode()
        self.start_lock_poller()

    def request_lock(self, owner):
        if not self.drive_sync: return
        self._show_progress_dialog("Requesting Access", f"Waiting for {owner} to respond (15s timeout)...")
        ld = self.drive_sync.read_lock() or {}  # type: ignore
        ld['request_by'] = self.current_user
        ld['response'] = None
        self.drive_sync.write_lock(ld)  # type: ignore
        
        def poll_response():
            start = time.time()
            while time.time() - start < 15:
                time.sleep(2)
                data = self.drive_sync.read_lock()  # type: ignore
                if data and data.get('response') == 'allowed':
                    self.root.after(0, self._on_request_allowed)
                    return
                elif data and data.get('response') == 'denied':
                    self.root.after(0, self._on_request_denied, owner)
                    return
            self.root.after(0, self._on_request_timeout, owner)
            
        threading.Thread(target=poll_response, daemon=True).start()

    def _on_request_allowed(self):
        self._hide_progress_dialog()
        messagebox.showinfo("Access Granted", "Editing permissions transferred to you! Downloading latest data...")
        self.do_sync_down_and_finish(read_only=False)

    def _on_request_denied(self, owner):
        self._hide_progress_dialog()
        messagebox.showwarning("Access Denied", f"{owner} declined your request. Opening in Read-Only mode.")
        self.do_sync_down_and_finish(read_only=True)

    def _on_request_timeout(self, owner):
        self._hide_progress_dialog()
        messagebox.showwarning("Timeout", f"{owner} did not respond. Opening in Read-Only mode.")
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
                    if ld.get('request_by') and not ld.get('response'):
                        self.root.after(0, self.handle_lock_request, ld['request_by'])
                        continue
                    ld['last_active'] = time.time()
                    self.drive_sync.write_lock(ld)  # type: ignore
                except Exception as e: print("Poller error:", e)
        threading.Thread(target=poll, daemon=True).start()
        
    def handle_lock_request(self, requester):
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Request")
        dialog.geometry("350x160")
        dialog.transient(self.root)
        dialog.grab_set()
        
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 175
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 80
        dialog.geometry(f"+{x}+{y}")
        
        ttk.Label(dialog, text=f"User '{requester}' is requesting edit access.\nIf you yield, your work will be saved\nand you will enter Read-Only mode.", justify="center").pack(pady=10)
        
        time_left = tk.IntVar(value=10)
        ttk.Label(dialog, textvariable=time_left, font=('Helvetica', 12, 'bold')).pack()
        
        result = [False]
        
        def yield_access(): result[0] = True; dialog.destroy()
        def deny_access(): result[0] = False; dialog.destroy()
            
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Yield Access", command=yield_access, style="Accent.TButton").pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Keep Access", command=deny_access).pack(side="left", padx=5)
        
        def update_timer():
            if not dialog.winfo_exists(): return
            val = time_left.get()
            if val > 0: time_left.set(val - 1); dialog.after(1000, update_timer)
            else: deny_access()
                
        dialog.after(1000, update_timer)
        self.root.wait_window(dialog)
        
        if result[0]:
            self._show_progress_dialog("Yielding", "Saving databases to cloud...")
            if self.drive_sync:
                try:
                    self.drive_sync.sync_up(self.db_files, self.current_user)  # type: ignore
                    ld = self.drive_sync.read_lock() or {}  # type: ignore
                    ld['owner'] = requester; ld['response'] = 'allowed'; ld['request_by'] = None
                    self.drive_sync.write_lock(ld)  # type: ignore
                except Exception as e: print("Error yielding:", e)
            self.is_owner = False
            self._hide_progress_dialog()
            self.enforce_read_only_mode()
            messagebox.showinfo("Read-Only", "You are now in Read-Only mode.")
        else:
            if self.drive_sync:
                try:
                    ld = self.drive_sync.read_lock() or {}  # type: ignore
                    ld['response'] = 'denied'; ld['last_active'] = time.time()
                    self.drive_sync.write_lock(ld)  # type: ignore
                except Exception as e: print("Error denying:", e)

    def enforce_read_only_mode(self):
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
        self.root.deiconify() # Show main window
        
        window_width = 1200
        window_height = 800
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        center_x = int((screen_width / 2) - (window_width / 2))
        center_y = int((screen_height / 2) - (window_height / 2))
        self.root.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        
        try:
            self.root.state('zoomed') # Maximize the window on Windows
        except tk.TclError:
            pass
        
        self.create_ui()
        
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'QLogo.png')
        if os.path.exists(logo_path):
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
        style.configure("Toolbutton",
                        background=COLOR_WHITE,
                        foreground=COLOR_PRIMARY,
                        font=('Helvetica', 11),
                        padding=10,
                        borderwidth=0,
                        anchor="w")
        style.map("Toolbutton",
                  background=[('selected', COLOR_PRIMARY), ('active', COLOR_LIGHT_GRAY)],
                  foreground=[('selected', COLOR_WHITE)])

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
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'QLogo.png')
        if os.path.exists(logo_path):
            try:
                from PIL import Image, ImageTk
                img = Image.open(logo_path)
                img.thumbnail((120, 120))  # Resize nicely while preserving aspect ratio
                self.logo_img = ImageTk.PhotoImage(img)
            except ImportError:
                messagebox.showwarning("Optional Dependency Missing", "The 'Pillow' library is not installed. Logo image quality may be reduced.\n\nInstall it with: pip install Pillow")
                # Fallback to standard Tkinter PhotoImage if Pillow is not installed
                self.logo_img = tk.PhotoImage(file=logo_path)
                if self.logo_img.width() > 150:
                    factor = max(1, self.logo_img.width() // 120)
                    self.logo_img = self.logo_img.subsample(factor, factor)
            ttk.Label(sidebar, image=self.logo_img).pack(pady=(10, 5))
        
        # Application title in sidebar
        ttk.Label(sidebar, text="Quarium\nDashboard", font=('Helvetica', 16, 'bold'), justify="center").pack(pady=(0, 10))
        
        self.status_label = tk.Label(sidebar, text="● EDITING", font=('Helvetica', 11, 'bold'), bg="#FFFFFF", fg="#2E7D32")
        self.status_label.pack(pady=(0, 10))
        
        self.request_edit_btn = ttk.Button(sidebar, text="Request Edit Access", command=self.manual_request_edit, style="Accent.TButton")
        
        # Main content area
        self.content_area = ttk.Frame(self.root)
        self.content_area.grid(row=0, column=1, sticky="nsew")
        self.content_area.rowconfigure(0, weight=1)
        self.content_area.columnconfigure(0, weight=1)
        
        # Define apps to load
        app_definitions = [
            ("Projects", "Project Manager", ProjectManager, {'current_user': self.current_user}),
            ("Flow", "Project Flow", ProjectFlowManager, {'current_user': self.current_user}),
            ("Clients", "Client Manager", ClientManager, {'current_user': self.current_user}),
            ("Services", "Service Manager", ServiceManager, {'current_user': self.current_user}),
            ("Stock", "Stock Manager", StockManager, {'on_edit_composite': self.open_composite_editor, 'current_user': self.current_user}),
            ("Composites", "Composite Creator", CompositeStockManager, {'is_embedded': True, 'current_user': self.current_user}),
        ]
        
        self.current_view = tk.StringVar(value="Projects")
        
        # Create navigation buttons and app frames
        for app_id, title, app_class, kwargs in app_definitions:
            # Navigation button (acting like a tab using the Toolbutton style)
            btn = ttk.Radiobutton(
                sidebar, 
                text=title, 
                variable=self.current_view, 
                value=app_id,
                style="Toolbutton",
                command=self.switch_view
            )
            btn.pack(fill="x", pady=5, ipady=5)
            
            # App frame
            frame = ttk.Frame(self.content_area)
            frame.grid(row=0, column=0, sticky="nsew")
            self.frames[app_id] = frame
            
            # Initialize the app inside its frame
            if kwargs:
                self.apps[app_id] = app_class(frame, **kwargs)
            else:
                self.apps[app_id] = app_class(frame)
            
        # Bottom buttons
        ttk.Button(sidebar, text="Quit", command=self.on_closing).pack(side="bottom", fill="x", pady=(0, 5))
        ttk.Button(sidebar, text="Log Out", command=self.logout).pack(side="bottom", fill="x", pady=(5, 5))
        ttk.Button(sidebar, text="Settings", command=self.open_settings).pack(side="bottom", fill="x", pady=(5, 5))

        # Show initial view
        self.switch_view()
        
    def open_composite_editor(self, composite_name):
        self.current_view.set("Composites")
        self.switch_view()
        comp_app = self.apps["Composites"]
        if hasattr(comp_app, 'load_composite'):
            comp_app.load_composite(composite_name)
            
    def _show_progress_dialog(self, title, message):
        self.progress_dialog = tk.Toplevel(self.root)
        self.progress_dialog.title(title)
        self.progress_dialog.transient(self.root)
        self.progress_dialog.grab_set()
        self.progress_dialog.resizable(False, False)
        self.progress_dialog.update_idletasks() # Ensure dialog is ready for geometry calculation
        
        # Center the dialog
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (self.progress_dialog.winfo_width() // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (self.progress_dialog.winfo_height() // 2)
        self.progress_dialog.geometry(f"+{x}+{y}")

        ttk.Label(self.progress_dialog, text=message, padding=10).pack()
        self.progress_bar = ttk.Progressbar(self.progress_dialog, mode='indeterminate', length=200)
        self.progress_bar.pack(pady=10, padx=10)
        self.progress_bar.start()
        self.progress_dialog.update_idletasks() # Ensure widgets are drawn inside dialog
        self.root.update_idletasks() # Ensure main window updates and dialog is visible

    def _hide_progress_dialog(self):
        if hasattr(self, 'progress_dialog') and self.progress_dialog.winfo_exists():
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
        elif view_id == "Stock":
            app.refresh_tree()
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
                ld = self.drive_sync.read_lock() or {}  # type: ignore
                if ld.get('owner') == self.current_user:
                    ld['owner'] = None
                    self.drive_sync.write_lock(ld)  # type: ignore
            except Exception: pass

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
        self.root.destroy()

    def on_closing(self):
        self._show_progress_dialog("Closing Application", "Saving data and closing connections...")
        self.root.after(100, self._perform_closing)

    def open_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("650x500")
        dialog.transient(self.root)
        dialog.grab_set()

        notebook = ttk.Notebook(dialog)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        settings_path = 'settings.json'
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    settings = json.load(f)
            except Exception: pass

        gen_frame = ttk.Frame(notebook, padding=10)
        notebook.add(gen_frame, text="General")

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

        users_frame = ttk.Frame(notebook, padding=10)
        notebook.add(users_frame, text="Users")

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
                for uname, fname in users_dict.items():
                    user_tree.insert("", "end", text=uname, values=(fname,))
            except Exception: pass

        def _save_users(u_dict):
            with open('users.json', 'w') as f:
                json.dump(u_dict, f)
            if self.drive_sync:
                try: self.drive_sync.sync_up(['users.json'])
                except Exception: pass
            for item in user_tree.get_children():
                user_tree.delete(item)
            for uname, fname in u_dict.items():
                user_tree.insert("", "end", text=uname, values=(fname,))
            messagebox.showinfo("Saved", "Users updated.", parent=dialog)

        def edit_user():
            sel = user_tree.selection()
            if not sel: return
            old_uname = user_tree.item(sel[0], "text")
            old_fname = user_tree.item(sel[0], "values")[0]
            
            new_fname = simpledialog.askstring("Edit User", "Full Name:", initialvalue=old_fname, parent=dialog)
            if new_fname is None: return
            new_uname = simpledialog.askstring("Edit User", "Username:", initialvalue=old_uname, parent=dialog)
            if new_uname is None: return
            
            if new_uname != old_uname and new_uname in users_dict:
                messagebox.showerror("Error", "Username already exists!", parent=dialog)
                return
                
            del users_dict[old_uname]
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
            users_dict[new_uname] = new_fname
            _save_users(users_dict)

        u_btn_frame = ttk.Frame(users_frame)
        u_btn_frame.pack(fill="x", pady=5)
        ttk.Button(u_btn_frame, text="Add User", command=add_user).pack(side="left", padx=5)
        ttk.Button(u_btn_frame, text="Edit Selected", command=edit_user).pack(side="left", padx=5)

        taxes_frame = ttk.Frame(notebook, padding=10)
        notebook.add(taxes_frame, text="Taxes")

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

        obs_frame = ttk.Frame(notebook, padding=10)
        notebook.add(obs_frame, text="Default Observations")

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

        conn_frame = ttk.Frame(notebook, padding=10)
        notebook.add(conn_frame, text="Connection")
        
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
            self.save_local_config(config)
            
            self.force_disconnect = True
            dialog.destroy()
            
            if os.path.exists("credentials.json"): os.remove("credentials.json")
            if os.path.exists("token.json"): os.remove("token.json")
            self.clean_local_workspace()
            
            self.logout_no_sync()
            
        ttk.Button(conn_frame, text="Disconnect from Company", command=do_disconnect, style="Accent.TButton").pack(pady=10)

        conflicts_frame = ttk.Frame(notebook, padding=10)
        notebook.add(conflicts_frame, text="Sync Conflicts")
        
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

        backup_frame = ttk.Frame(notebook, padding=10)
        notebook.add(backup_frame, text="Data Backup")
        
        ttk.Label(backup_frame, text="Create or restore an encrypted backup of all system databases and images.", wraplength=500).pack(pady=10)
        
        if CRYPTO_AVAILABLE:
            ttk.Button(backup_frame, text="Export Encrypted Backup", command=self._export_backup).pack(pady=10)
            ttk.Button(backup_frame, text="Import Encrypted Backup", command=self._import_backup).pack(pady=10)
        else:
            ttk.Label(backup_frame, text="Backup feature requires the 'cryptography' library.\nPlease install it via terminal: pip install cryptography", foreground="red").pack(pady=10)

    def _select_image(self, target_filename):
        file_path = filedialog.askopenfilename(filetypes=[("Image Files", "*.png *.jpg *.jpeg")])
        if file_path:
            try:
                shutil.copy(file_path, target_filename)
                if self.drive_sync:
                    self.drive_sync.sync_up([target_filename])
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

if __name__ == "__main__":
    root = tk.Tk()
    app = QuariumDashboard(root)
    root.mainloop()