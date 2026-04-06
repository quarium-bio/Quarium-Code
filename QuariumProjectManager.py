import os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import sqlite3
from datetime import datetime
import json
import math
import re
from tkinter import filedialog

try:
    from reportlab.lib.pagesizes import A4  # type: ignore
    from reportlab.lib import colors  # type: ignore
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage  # type: ignore
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore
    from reportlab.lib.units import cm  # type: ignore
    from reportlab.pdfgen import canvas  # type: ignore
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    A4 = (595.27, 841.89)  # type: ignore
    cm = 28.3465  # type: ignore

    class _DummyColor:
        lightgrey = None
        grey = None
    colors = _DummyColor()  # type: ignore

    class SimpleDocTemplate:  # type: ignore
        def __init__(self, *args, **kwargs): pass
        def build(self, *args, **kwargs): pass

    class Paragraph:  # type: ignore
        def __init__(self, *args, **kwargs): pass

    class Spacer:  # type: ignore
        def __init__(self, *args, **kwargs): pass

    class Table:  # type: ignore
        def __init__(self, *args, **kwargs): pass
        def setStyle(self, *args, **kwargs): pass

    class TableStyle:  # type: ignore
        def __init__(self, *args, **kwargs): pass

    class RLImage:  # type: ignore
        def __init__(self, *args, **kwargs): pass

    def getSampleStyleSheet(*args, **kwargs):  # type: ignore
        return {'Heading1': None, 'Normal': None}

    class ParagraphStyle:  # type: ignore
        def __init__(self, *args, **kwargs): pass

    class canvas:  # type: ignore
        class Canvas:
            def __init__(self, *args, **kwargs): pass
            def setFont(self, *args, **kwargs): pass
            def setFillColor(self, *args, **kwargs): pass
            def drawCentredString(self, *args, **kwargs): pass
            def drawRightString(self, *args, **kwargs): pass

class ProjectManager:
    def __init__(self, root, current_user="Unknown"):
        self.root = root
        self.current_user = current_user
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.title("Quarium Project Manager")
            self.root.geometry("1200x800")

        self.project_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'projects.db')
        self.client_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'clients.db')
        self.service_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'services.db')
        self.stock_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock.db')
        self.users_json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json')

        self.init_db()
        self.create_ui()
        self.load_all_data() # Load clients, services, users, projects
        self.clear_form() # Start with a clean form for a new project

        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def init_db(self):
        self.conn = sqlite3.connect(self.project_db_path)
        self.cursor = self.conn.cursor()
        
        # Attach databases to allow cross-database JOINS
        self.cursor.execute("ATTACH DATABASE ? AS clients_db", (self.client_db_path,))
        self.cursor.execute("ATTACH DATABASE ? AS services_db", (self.service_db_path,))
        self.cursor.execute("ATTACH DATABASE ? AS stock_db", (self.stock_db_path,))

        # Handle legacy schema with problematic foreign keys
        self.cursor.execute("PRAGMA foreign_key_list(projects)")
        fks = self.cursor.fetchall()
        if any(fk[2] == 'clients' for fk in fks):
            self.cursor.execute("PRAGMA foreign_keys = OFF")
            self.cursor.execute("CREATE TABLE projects_new (id INTEGER PRIMARY KEY, estimate_number TEXT NOT NULL UNIQUE, client_id INTEGER, validity_days INTEGER, responsible_user TEXT, total_samples INTEGER, discount_percentage REAL DEFAULT 0.0, final_cost REAL, created_at TEXT, updated_at TEXT, updated_by TEXT)")
            self.cursor.execute("INSERT INTO projects_new SELECT id, estimate_number, client_id, validity_days, responsible_user, total_samples, discount_percentage, final_cost, created_at, updated_at, updated_by FROM projects")
            self.cursor.execute("DROP TABLE projects")
            self.cursor.execute("ALTER TABLE projects_new RENAME TO projects")
            self.cursor.execute("PRAGMA foreign_keys = ON")
            self.conn.commit()

        self.cursor.execute("PRAGMA foreign_key_list(project_services)")
        fks = self.cursor.fetchall()
        if any(fk[2] == 'services' for fk in fks):
            self.cursor.execute("PRAGMA foreign_keys = OFF")
            self.cursor.execute("CREATE TABLE project_services_new (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, service_id INTEGER NOT NULL, samples_override INTEGER, calculated_cost REAL, notes TEXT, FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE)")
            self.cursor.execute("INSERT INTO project_services_new SELECT id, project_id, service_id, samples_override, calculated_cost, notes FROM project_services")
            self.cursor.execute("DROP TABLE project_services")
            self.cursor.execute("ALTER TABLE project_services_new RENAME TO project_services")
            self.cursor.execute("PRAGMA foreign_keys = ON")
            self.conn.commit()

        self.cursor.execute("PRAGMA foreign_keys = ON")

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                estimate_number TEXT NOT NULL UNIQUE,
                client_id INTEGER,
                validity_days INTEGER,
                responsible_user TEXT,
                total_samples INTEGER,
                discount_percentage REAL DEFAULT 0.0,
                final_cost REAL,
                created_at TEXT,
                updated_at TEXT,
                updated_by TEXT,
                description TEXT,
                notes TEXT
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS project_services (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                service_id INTEGER NOT NULL,
                samples_override INTEGER,
                calculated_cost REAL,
                notes TEXT,
                FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
            )
        ''')
        self.conn.commit()
        
        # Handle legacy schema upgrades
        try:
            self.cursor.execute('ALTER TABLE projects ADD COLUMN description TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute('ALTER TABLE projects ADD COLUMN notes TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute('ALTER TABLE project_services ADD COLUMN custom_description TEXT')
        except sqlite3.OperationalError:
            pass
            
        flow_columns = ['status INTEGER DEFAULT 0', 'approved_at TEXT', 'agreed_business_days INTEGER', 
                        'contract_sent_at TEXT', 'contract_signed_at TEXT', 'samples_received_at TEXT', 
                        'sample_storage_location TEXT', 'samples_analyzed_at TEXT', 'data_released_at TEXT', 
                        'data_link TEXT', 'deletion_threshold_months INTEGER DEFAULT 3', 'completed_at TEXT']
        for col in flow_columns:
            try: self.cursor.execute(f'ALTER TABLE projects ADD COLUMN {col}')
            except sqlite3.OperationalError: pass

    def create_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill="both", expand=True)

        # --- Tab 1: Editor ---
        self.tab_editor = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_editor, text="Estimate Editor")

        # Left Panel - Project Details
        left_panel = ttk.LabelFrame(self.tab_editor, text="Project Details", padding=10)
        left_panel.pack(side="left", fill="y", padx=(0, 10))

        # Estimate Number
        ttk.Label(left_panel, text="Estimate Number:").grid(row=0, column=0, sticky="w", pady=2)
        self.estimate_number_var = tk.StringVar()
        self.estimate_number_entry = ttk.Entry(left_panel, textvariable=self.estimate_number_var, width=30)
        self.estimate_number_entry.grid(row=0, column=1, padx=5, pady=2)
        ttk.Button(left_panel, text="Generate New", command=self.generate_new_estimate_number).grid(row=0, column=2, padx=5, pady=2)

        # Client Selection
        ttk.Label(left_panel, text="Client:").grid(row=1, column=0, sticky="w", pady=2)
        self.client_var = tk.StringVar()
        self.client_combobox = ttk.Combobox(left_panel, textvariable=self.client_var, state="readonly", width=30)
        self.client_combobox.grid(row=1, column=1, columnspan=2, padx=5, pady=2)
        self.client_combobox.bind("<<ComboboxSelected>>", self.on_client_selected)

        # Validity
        ttk.Label(left_panel, text="Validity (days):").grid(row=2, column=0, sticky="w", pady=2)
        self.validity_var = tk.IntVar(value=30)
        ttk.Entry(left_panel, textvariable=self.validity_var, width=10).grid(row=2, column=1, sticky="w", padx=5, pady=2)

        # Responsible User
        ttk.Label(left_panel, text="Responsible User:").grid(row=3, column=0, sticky="w", pady=2)
        self.user_var = tk.StringVar()
        self.user_combobox = ttk.Combobox(left_panel, textvariable=self.user_var, state="readonly", width=30)
        self.user_combobox.grid(row=3, column=1, columnspan=2, padx=5, pady=2)

        # Total Samples for Project
        ttk.Label(left_panel, text="Total Samples:").grid(row=4, column=0, sticky="w", pady=2)
        self.total_samples_var = tk.IntVar(value=1)
        self.total_samples_entry = ttk.Entry(left_panel, textvariable=self.total_samples_var, width=10)
        self.total_samples_entry.grid(row=4, column=1, sticky="w", padx=5, pady=2)
        self.total_samples_entry.bind("<KeyRelease>", self.update_total_cost)

        # Discount
        self.apply_discount_var = tk.BooleanVar(value=False)
        self.discount_checkbox = ttk.Checkbutton(left_panel, text="Apply Discount", variable=self.apply_discount_var, command=self.update_total_cost)
        self.discount_checkbox.grid(row=5, column=0, sticky="w", pady=2)

        ttk.Label(left_panel, text="Discount (%):").grid(row=5, column=1, sticky="w", pady=2)
        self.discount_percentage_var = tk.DoubleVar(value=0.0)
        self.discount_entry = ttk.Entry(left_panel, textvariable=self.discount_percentage_var, width=10)
        self.discount_entry.grid(row=5, column=2, sticky="w", padx=5, pady=2)
        self.discount_entry.bind("<KeyRelease>", self.update_total_cost)

        # Total Cost Display
        ttk.Label(left_panel, text="Estimated Total Cost:").grid(row=6, column=0, sticky="w", pady=10)
        self.estimated_total_cost_var = tk.StringVar(value="R$ 0.00")
        ttk.Label(left_panel, textvariable=self.estimated_total_cost_var, font=('Helvetica', 12, 'bold')).grid(row=6, column=1, columnspan=2, sticky="w", pady=10)

        # Project Action Buttons
        project_btn_frame = ttk.Frame(left_panel)
        project_btn_frame.grid(row=7, column=0, columnspan=3, pady=10)
        ttk.Button(project_btn_frame, text="Save Project", command=self.save_project).pack(side="left", padx=5)
        ttk.Button(project_btn_frame, text="Clear Form", command=self.clear_form).pack(side="left", padx=5)
        ttk.Button(project_btn_frame, text="Delete Project", command=self.delete_project).pack(side="left", padx=5)
        ttk.Button(project_btn_frame, text="Export PDF", command=lambda: self.export_pdf_dialog(self.estimate_number_var.get())).pack(side="left", padx=5)

        # Approval Action Button
        self.approve_btn_frame = ttk.Frame(left_panel)
        self.approve_btn_frame.grid(row=8, column=0, columnspan=3, pady=(15, 10))
        
        self.approve_btn = ttk.Button(self.approve_btn_frame, text="Approve Estimate", command=self.approve_estimate, style="Accent.TButton")
        self.approve_btn.pack(fill="x", padx=20, pady=5, ipady=10)
        self.approve_btn.pack_forget() # Hide initially

        # Right Panel - Services
        right_panel = ttk.LabelFrame(self.tab_editor, text="Project Services", padding=10)
        right_panel.pack(side="right", fill="both", expand=True)

        # Available Services
        available_services_frame = ttk.LabelFrame(right_panel, text="Available Services", padding=5)
        available_services_frame.pack(fill="x", pady=(0, 10))

        self.available_services_tree = ttk.Treeview(available_services_frame, columns=("Code", "Description"), height=8)
        self.available_services_tree.heading("#0", text="Service Name")
        self.available_services_tree.heading("Code", text="Code")
        self.available_services_tree.heading("Description", text="Description")
        self.available_services_tree.column("#0", width=150)
        self.available_services_tree.column("Code", width=80)
        self.available_services_tree.column("Description", width=200)
        self.available_services_tree.pack(fill="x", expand=True)
        self.available_services_tree.bind("<<TreeviewSelect>>", self.on_available_service_select)

        # Add Service to Project Controls
        add_service_frame = ttk.Frame(available_services_frame)
        add_service_frame.pack(fill="x", pady=5)
        ttk.Label(add_service_frame, text="Samples Override:").pack(side="left", padx=5)
        self.service_samples_override_var = tk.StringVar(value="")
        self.service_samples_override_entry = ttk.Entry(add_service_frame, textvariable=self.service_samples_override_var, width=10)
        self.service_samples_override_entry.pack(side="left", padx=5)
        ttk.Button(add_service_frame, text="Add Service to Project", command=self.add_service_to_project).pack(side="left", padx=10)

        # Current Project Services
        current_services_frame = ttk.LabelFrame(right_panel, text="Current Project Services", padding=5)
        current_services_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.project_services_tree = ttk.Treeview(current_services_frame, columns=("Service ID", "Samples", "Cost"), height=10)
        self.project_services_tree.heading("#0", text="Service Name")
        self.project_services_tree.heading("Service ID", text="ID")
        self.project_services_tree.heading("Samples", text="Samples")
        self.project_services_tree.heading("Cost", text="Cost")
        self.project_services_tree.column("#0", width=200)
        self.project_services_tree.column("Service ID", width=50)
        self.project_services_tree.column("Samples", width=80)
        self.project_services_tree.column("Cost", width=100)
        self.project_services_tree.pack(fill="both", expand=True)

        ps_btn_frame = ttk.Frame(current_services_frame)
        ps_btn_frame.pack(pady=5)
        
        ttk.Button(ps_btn_frame, text="▲", width=3, command=self.move_service_up).pack(side="left", padx=2)
        ttk.Button(ps_btn_frame, text="▼", width=3, command=self.move_service_down).pack(side="left", padx=2)
        ttk.Button(ps_btn_frame, text="Remove Selected Service", command=self.remove_service_from_project).pack(side="left", padx=10)

        self.current_project_id = None
        self.current_client_company_id = None # To help with estimate number generation

        # --- Tab 2: Saved Estimates ---
        self.tab_saved = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_saved, text="Saved Estimates")
        
        self.saved_tree = ttk.Treeview(self.tab_saved, columns=("Client", "Cost", "Date", "User", "Versions"), height=15)
        self.saved_tree.heading("#0", text="Company / Estimate #")
        self.saved_tree.heading("Client", text="Client")
        self.saved_tree.heading("Cost", text="Total Cost")
        self.saved_tree.heading("Date", text="Date Created")
        self.saved_tree.heading("User", text="User")
        self.saved_tree.heading("Versions", text="Versions")
        
        self.saved_tree.column("#0", width=250)
        self.saved_tree.column("Client", width=150)
        self.saved_tree.column("Cost", width=100)
        self.saved_tree.column("Date", width=150)
        self.saved_tree.column("User", width=100)
        self.saved_tree.column("Versions", width=80, anchor="center")
        self.saved_tree.pack(fill="both", expand=True, pady=(0, 10))

        self.saved_tree.bind("<Double-1>", self.on_saved_estimate_double_click)
        self.saved_tree.bind("<Button-1>", self.on_saved_tree_click)

        btn_frame = ttk.Frame(self.tab_saved)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Open Selected Estimate", command=self.open_selected_estimate).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Delete Selected Estimate", command=self.delete_selected_estimate).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Export PDF", command=self.export_selected_pdf).pack(side="left", padx=5)

    def load_all_data(self):
        self.load_settings()
        self.load_clients()
        self.load_services()
        self.load_users()
        self.load_saved_estimates()
        self.update_total_cost()
        
    def load_settings(self):
        settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')
        self.settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    self.settings = json.load(f)
            except Exception:
                pass

    def load_clients(self):
        self.clients_data = {} # {client_name: (client_id, company_id, is_academic, company_code)}
        self.client_names = []
        try:
            client_conn = sqlite3.connect(self.client_db_path)
            client_cursor = client_conn.cursor()
            client_cursor.execute('''
                SELECT c.id, c.name, c.company_id, c.is_academic, comp.code
                FROM clients c
                LEFT JOIN companies comp ON c.company_id = comp.id
                ORDER BY c.name
            ''')
            for client_id, name, company_id, is_academic, company_code in client_cursor.fetchall():
                self.clients_data[name] = (client_id, company_id, is_academic, company_code)
                self.client_names.append(name)
            client_conn.close()
            self.client_combobox['values'] = self.client_names
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load clients: {e}")

    def load_services(self):
        self.services_data = {} # {service_name: service_id}
        for item in self.available_services_tree.get_children():
            self.available_services_tree.delete(item)

        try:
            service_conn = sqlite3.connect(self.service_db_path)
            service_cursor = service_conn.cursor()

            service_cursor.execute('SELECT id, name, description FROM categories ORDER BY name')
            categories = service_cursor.fetchall()

            for cat_id, cat_name, cat_desc in categories:
                cat_node = self.available_services_tree.insert("", "end", text=cat_name,
                                                               values=("", cat_desc), tags=("category", str(cat_id)))  # type: ignore
                service_cursor.execute('''
                    SELECT id, name, code, description FROM services
                    WHERE category_id = ? ORDER BY name
                ''', (cat_id,))
                for service_id, service_name, service_code, service_desc in service_cursor.fetchall():
                    self.services_data[service_name] = service_id
                    self.available_services_tree.insert(cat_node, "end", text=service_name,
                                                        values=(service_code or "", service_desc or ""),
                                                        tags=("service", str(service_id)))  # type: ignore

            uncat_node = self.available_services_tree.insert("", "end", text="Uncategorized",
                                                             values=("", "Services without a category"), tags=("category", ""))  # type: ignore
            service_cursor.execute('''
                SELECT id, name, code, description FROM services
                WHERE category_id IS NULL ORDER BY name
            ''')
            for service_id, service_name, service_code, service_desc in service_cursor.fetchall():
                self.services_data[service_name] = service_id
                self.available_services_tree.insert(uncat_node, "end", text=service_name,
                                                    values=(service_code or "", service_desc or ""),
                                                    tags=("service", str(service_id)))  # type: ignore
            service_conn.close()
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load services: {e}")

    def load_users(self):
        self.users_data = []
        self.username_to_full = {}
        if os.path.exists(self.users_json_path):
            try:
                with open(self.users_json_path, 'r') as f:
                    users_dict = json.load(f)
                    self.username_to_full = users_dict
                    self.users_data = list(users_dict.values())
            except json.JSONDecodeError as e:
                messagebox.showerror("Error", f"Could not load users.json: {e}")
        self.user_combobox['values'] = self.users_data

    def on_client_selected(self, event=None):
        selected_client_name = self.client_var.get()
        if selected_client_name in self.clients_data:
            _, _, is_academic, company_code = self.clients_data[selected_client_name]
            self.current_client_company_id = company_code # Store company code for estimate number generation
            if is_academic:
                self.apply_discount_var.set(True)
                self.discount_percentage_var.set(20.0)
            else:
                self.apply_discount_var.set(False)
                self.discount_percentage_var.set(0.0)
            self.update_total_cost()
        else:
            self.current_client_company_id = None

    def on_available_service_select(self, event=None):
        selection = self.available_services_tree.selection()
        if selection:
            item = selection[0]
            item_type, _ = self.available_services_tree.item(item, "tags")
            if item_type == "service":
                # Optionally pre-fill samples override with project total samples
                try:
                    self.service_samples_override_var.set(str(self.total_samples_var.get()))
                except tk.TclError: # Handle case where total_samples_var is not an int
                    self.service_samples_override_var.set("")
            else:
                self.service_samples_override_var.set("")

    def add_service_to_project(self):
        selection = self.available_services_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a service to add.")
            return

        item = selection[0]
        item_type, service_id = self.available_services_tree.item(item, "tags")
        if item_type != "service" or not service_id:
            messagebox.showwarning("Warning", "Please select an actual service, not a category.")
            return

        service_name = self.available_services_tree.item(item, "text")
        
        samples_override_str = self.service_samples_override_var.get().strip()
        samples_override = None
        if samples_override_str:
            try:
                samples_override = int(samples_override_str)
                if samples_override <= 0:
                    raise ValueError("Samples override must be positive.")
            except ValueError:
                messagebox.showerror("Error", "Samples override must be a positive integer.")
                return

        project_total_samples = self.total_samples_var.get()
        if project_total_samples <= 0:
            messagebox.showerror("Error", "Project total samples must be a positive integer.")
            return

        # Calculate cost for this service
        num_samples_for_cost = samples_override if samples_override is not None else project_total_samples
        calculated_cost = self.calculate_service_cost(service_id, num_samples_for_cost)

        # Add to treeview
        self.project_services_tree.insert("", "end", text=service_name,
                                          values=(str(service_id), str(samples_override) if samples_override is not None else "Project Default", f"R$ {calculated_cost:.2f}"),  # type: ignore
                                          tags=(str(service_id), str(samples_override) if samples_override is not None else "", str(calculated_cost)))  # type: ignore
        self.update_total_cost()
        self.service_samples_override_var.set("") # Clear override field

    def move_service_up(self):
        selection = self.project_services_tree.selection()
        if not selection:
            return
        for item in selection:
            index = self.project_services_tree.index(item)
            if index > 0:
                self.project_services_tree.move(item, "", index - 1)

    def move_service_down(self):
        selection = self.project_services_tree.selection()
        if not selection:
            return
        items = self.project_services_tree.get_children()
        for item in reversed(selection):
            index = self.project_services_tree.index(item)
            if index < len(items) - 1:
                self.project_services_tree.move(item, "", index + 1)

    def remove_service_from_project(self):
        selection = self.project_services_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a service to remove from the project.")
            return
        
        for item in selection:
            self.project_services_tree.delete(item)
        self.update_total_cost()

    def calculate_service_cost(self, service_id, num_samples):
        total_service_cost = 0.0
        
        try:
            # 1. Calculate Reagent Costs
            self.cursor.execute('''
                SELECT sr.stock_item_id, sr.quantity, sr.unit, sr.samples_per_batch,
                       st.price, st.container_size, st.unit
                FROM services_db.service_requirements sr
                LEFT JOIN stock_db.stock st ON sr.stock_item_id = st.id
                WHERE sr.service_id = ?
            ''', (service_id,))
            requirements = self.cursor.fetchall()

            for stock_item_id, req_quantity, req_unit, samples_per_batch, stock_price, stock_container_size, stock_unit in requirements:
                if stock_price is not None and stock_container_size and stock_container_size > 0:
                        unit_cost_per_stock_unit = stock_price / stock_container_size
                        
                        # Convert req_quantity to stock_unit if necessary
                        conversion_factor = 1.0
                        mass_units = {"ng": 1e-9, "ug": 1e-6, "mg": 1e-3, "g": 1.0}
                        vol_units = {"nL": 1e-9, "uL": 1e-6, "mL": 1e-3, "L": 1.0}
                        
                        if req_unit in mass_units and stock_unit in mass_units:
                            conversion_factor = mass_units[req_unit] / mass_units[stock_unit]
                        elif req_unit in vol_units and stock_unit in vol_units:
                            conversion_factor = vol_units[req_unit] / vol_units[stock_unit]
                            
                        converted_req_quantity = req_quantity * conversion_factor
                        
                        # Calculate how many batches are needed for the given num_samples
                        batches_needed = math.ceil(num_samples / samples_per_batch) if samples_per_batch > 0 else num_samples
                        
                        # Total quantity of stock item needed for all samples/batches
                        total_req_quantity = converted_req_quantity * batches_needed
                        
                        total_reagent_cost = total_req_quantity * unit_cost_per_stock_unit
                        total_service_cost += total_reagent_cost
                else:
                    print(f"Warning: Stock item {stock_item_id} has invalid price/size data.")

            # 2. Calculate Non-Reagent Costs
            self.cursor.execute('''
                SELECT sc.cost, sc.samples_per_batch
                FROM services_db.service_costs sc
                WHERE sc.service_id = ?
            ''', (service_id,))
            non_reagent_costs = self.cursor.fetchall()

            for cost_amount, samples_per_batch in non_reagent_costs:
                if cost_amount is not None:
                    batches_needed = math.ceil(num_samples / samples_per_batch) if samples_per_batch > 0 else num_samples
                    total_service_cost += cost_amount * batches_needed

        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Error calculating service cost: {e}")
            total_service_cost = 0.0 # Default to 0 on error
            
        profit_margin = float(self.settings.get("profit_margin", 0.0))
        taxes_fees = float(self.settings.get("taxes_and_fees", 0.0))
        
        cost_with_profit = total_service_cost * (1 + profit_margin / 100.0)
        final_service_cost = cost_with_profit * (1 + taxes_fees / 100.0)
            
        return final_service_cost

    def update_total_cost(self, event=None):
        current_total_cost = 0.0
        try:
            project_total_samples = self.total_samples_var.get()
            if project_total_samples <= 0:
                project_total_samples = 1
        except tk.TclError:
            project_total_samples = 1

        for item_id in self.project_services_tree.get_children():
            tags = self.project_services_tree.item(item_id, "tags")
            if not tags: continue
            
            service_id, samples_override, _ = tags
            
            num_samples_for_cost = project_total_samples
            if samples_override and str(samples_override).strip():
                try:
                    num_samples_for_cost = int(samples_override)
                except ValueError:
                    pass

            recalculated_cost = self.calculate_service_cost(service_id, num_samples_for_cost)
            
            # Update the tree display and tags with the new cost
            values = list(self.project_services_tree.item(item_id, "values"))
            values[2] = f"R$ {recalculated_cost:.2f}"
            
            new_tags = list(tags)
            new_tags[2] = str(recalculated_cost)
            self.project_services_tree.item(item_id, values=tuple(values), tags=tuple(new_tags))  # type: ignore
            
            current_total_cost += recalculated_cost

        # Apply discount
        if self.apply_discount_var.get():
            discount_percent = self.discount_percentage_var.get()
            if 0 <= discount_percent <= 100:
                current_total_cost *= (1 - discount_percent / 100)
            else:
                messagebox.showwarning("Warning", "Discount percentage must be between 0 and 100.")
                self.discount_percentage_var.set(0.0) # Reset to 0
                self.apply_discount_var.set(False) # Uncheck discount
                self.root.after_idle(self.update_total_cost) # Recalculate without discount
                return

        self.estimated_total_cost_var.set(f"R$ {current_total_cost:.2f}")

    def generate_new_estimate_number(self):
        if not self.current_client_company_id:
            messagebox.showwarning("Warning", "Please select a client first to generate an estimate number based on company ID.")
            return

        company_code = self.current_client_company_id
        
        # Find the highest existing estimate number for this company code
        self.cursor.execute("SELECT estimate_number FROM projects WHERE estimate_number LIKE ? || '-%' ORDER BY estimate_number DESC", (str(company_code),))
        existing_estimates = self.cursor.fetchall()

        next_integer = 1
        if existing_estimates:
            # Example: 3-04v2 -> 04
            # Example: 3-10v1 -> 10
            # Need to find the highest integer part
            max_int_part = 0
            for est_num_tuple in existing_estimates:
                est_num = est_num_tuple[0]
                parts = est_num.split('-')
                if len(parts) > 1:
                    num_version_part = parts[1] # e.g., "04v2"
                    num_part = num_version_part.split('v')[0] # e.g., "04"
                    try:
                        max_int_part = max(max_int_part, int(num_part))
                    except ValueError:
                        pass # Ignore malformed estimate numbers
            next_integer = max_int_part + 1

        new_estimate_num = f"{company_code}-{next_integer:02d}v1" # Default to version 1
        self.estimate_number_var.set(new_estimate_num)

    def save_project(self):
        estimate_number = self.estimate_number_var.get().strip()
        client_name = self.client_var.get().strip()
        validity_days = self.validity_var.get()
        responsible_user = self.user_var.get().strip()
        total_samples = self.total_samples_var.get()
        discount_percentage = self.discount_percentage_var.get() if self.apply_discount_var.get() else 0.0
        final_cost_str = self.estimated_total_cost_var.get().replace("R$", "").strip()
        
        if not estimate_number or not client_name or not responsible_user or total_samples <= 0:
            messagebox.showerror("Error", "Estimate Number, Client, Responsible User, and Total Samples are required.")
            return
        
        if client_name not in self.clients_data:
            messagebox.showerror("Error", "Selected client not found in database.")
            return
        client_id, _, _, _ = self.clients_data[client_name]

        if not self.project_services_tree.get_children():
            messagebox.showerror("Error", "Please add at least one service to the project.")
            return

        try:
            final_cost = float(final_cost_str)
        except ValueError:
            messagebox.showerror("Error", "Could not parse final cost.")
            return

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            self.cursor.execute("SELECT id FROM projects WHERE estimate_number = ?", (estimate_number,))
            existing_project = self.cursor.fetchone()

            if existing_project:
                match = re.match(r'(.*v)(\d+)$', estimate_number)
                if match:
                    base = match.group(1)
                else:
                    base = f"{estimate_number}v"
                
                self.cursor.execute("SELECT estimate_number FROM projects WHERE estimate_number LIKE ?", (f"{base}%",))
                existing_versions = self.cursor.fetchall()
                
                max_v = 0
                for (en,) in existing_versions:
                    m = re.match(r'.*v(\d+)$', en)
                    if m:
                        max_v = max(max_v, int(m.group(1)))
                
                new_version = max_v + 1 if max_v > 0 else 2
                new_estimate_number = f"{base}{new_version}"

                messagebox.showinfo("New Version", f"Estimates are read-only and cannot be overwritten.\nSaving as new version: {new_estimate_number}")
                estimate_number = str(new_estimate_number)
                self.estimate_number_var.set(str(estimate_number))
                
            self.cursor.execute('''
                INSERT INTO projects (estimate_number, client_id, validity_days, responsible_user,
                total_samples, discount_percentage, final_cost, created_at, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (estimate_number, client_id, validity_days, responsible_user,
                  total_samples, discount_percentage, final_cost, now, now, self.current_user))
            project_id = self.cursor.lastrowid
            
            # Insert project services
            for item_id in self.project_services_tree.get_children():
                service_name = self.project_services_tree.item(item_id, "text")
                service_id, samples_override, calculated_cost_str = self.project_services_tree.item(item_id, "tags")
                
                calculated_cost = float(calculated_cost_str) if calculated_cost_str else 0.0
                samples_override_val = int(samples_override) if samples_override != "Project Default" else None

                self.cursor.execute('''
                    INSERT INTO project_services (project_id, service_id, samples_override, calculated_cost)
                    VALUES (?, ?, ?, ?)
                ''', (project_id, service_id, samples_override_val, calculated_cost))

            self.conn.commit()
            self.current_project_id = project_id
            self.load_saved_estimates()
            messagebox.showinfo("Success", f"Project '{estimate_number}' saved successfully.")
            
        except sqlite3.IntegrityError as e:
            messagebox.showerror("Database Error", f"Integrity Error: {e}\nEstimate number might already exist.")
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not save project: {e}")

    def load_saved_estimates(self):
        for item in self.saved_tree.get_children():
            self.saved_tree.delete(item)

        try:
            self.cursor.execute('''
                SELECT * FROM (
                    SELECT
                        p.estimate_number,
                        c.name as client_name,
                        p.final_cost,
                        p.created_at,
                        p.responsible_user,
                        comp.id as company_id,
                        comp.name as company_name,
                        SUBSTR(p.estimate_number, 1, INSTR(p.estimate_number, 'v') - 1) as base_estimate,
                        COUNT(*) OVER(PARTITION BY SUBSTR(p.estimate_number, 1, INSTR(p.estimate_number, 'v') - 1)) as version_count,
                        ROW_NUMBER() OVER(PARTITION BY SUBSTR(p.estimate_number, 1, INSTR(p.estimate_number, 'v') - 1) ORDER BY CAST(SUBSTR(p.estimate_number, INSTR(p.estimate_number, 'v') + 1) AS INTEGER) DESC) as rn
                    FROM projects p
                    LEFT JOIN clients_db.clients c ON p.client_id = c.id
                    LEFT JOIN clients_db.companies comp ON c.company_id = comp.id
                )
                WHERE rn = 1
                ORDER BY company_name, estimate_number DESC
            ''')
            
            projects = self.cursor.fetchall()
            
            companies = {}
            for p in projects:
                estimate_number, client_name, final_cost, created_at, responsible_user, comp_id, comp_name, base_estimate, version_count = p[:9]
                comp_name = comp_name or "Uncategorized"
                if comp_name not in companies:
                    node = self.saved_tree.insert("", "end", text=comp_name, open=True)
                    companies[comp_name] = node
                versions_text = "[...]" if version_count > 1 else ""
                self.saved_tree.insert(companies[comp_name], "end", text=estimate_number, values=(client_name or "", f"R$ {final_cost:.2f}", created_at or "", responsible_user or "", versions_text), tags=("project", estimate_number, base_estimate))  # type: ignore
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load saved estimates: {e}")

    def on_saved_estimate_double_click(self, event):
        item = self.saved_tree.identify_row(event.y)
        if item:
            tags = self.saved_tree.item(item, "tags")
            if tags and tags[0] == "project":
                estimate_number = self.saved_tree.item(item, "text")
                self.load_project(estimate_number)
                self.notebook.select(self.tab_editor)

    def on_saved_tree_click(self, event):
        """Handle single clicks on the saved estimates tree to check for 'Versions' button click."""
        item = self.saved_tree.identify_row(event.y)
        column = self.saved_tree.identify_column(event.x)
        
        if not item or not column:
            return

        # Check if the "Versions" column was clicked (it's the 5th column, so #5)
        if column == '#5':
            values = self.saved_tree.item(item, "values")
            if values and values[4] == "[...]": # Check the text in the versions column
                tags = self.saved_tree.item(item, "tags")
                if len(tags) > 2:
                    base_estimate = tags[2]
                    self.show_versions_dialog(base_estimate)
                
    def open_selected_estimate(self):
        selection = self.saved_tree.selection()
        if not selection:
            return
        item = selection[0]
        tags = self.saved_tree.item(item, "tags")
        if tags and tags[0] == "project":
            estimate_number = self.saved_tree.item(item, "text")
            self.load_project(estimate_number)
            self.notebook.select(self.tab_editor)

    def show_versions_dialog(self, base_estimate):
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Other Versions for {base_estimate}")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("500x300")

        tree_frame = ttk.Frame(dialog, padding=10)
        tree_frame.pack(fill="both", expand=True)

        versions_tree = ttk.Treeview(tree_frame, columns=("Date", "Cost"))
        versions_tree.heading("#0", text="Estimate #")
        versions_tree.heading("Date", text="Date Created")
        versions_tree.heading("Cost", text="Total Cost")
        versions_tree.column("#0", width=150)
        versions_tree.column("Date", width=150)
        versions_tree.column("Cost", width=100, anchor="e")
        versions_tree.pack(fill="both", expand=True)

        try:
            self.cursor.execute(
                "SELECT estimate_number, created_at, final_cost FROM projects WHERE estimate_number LIKE ? ORDER BY CAST(SUBSTR(estimate_number, INSTR(estimate_number, 'v') + 1) AS INTEGER) DESC",
                (f"{base_estimate}v%",)
            )
            for est_num, created_at, cost in self.cursor.fetchall():
                versions_tree.insert("", "end", text=est_num, values=(created_at, f"R$ {cost:.2f}"))  # type: ignore
            
            # Pre-select the first (most recent) item if available
            if versions_tree.get_children():
                versions_tree.selection_set(versions_tree.get_children()[0])
                versions_tree.focus(versions_tree.get_children()[0]) # Also focus it
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load versions: {e}", parent=dialog)
            dialog.destroy()
            return

        def load_selected_version(event=None):
            selection = versions_tree.selection()
            if not selection:
                return
            estimate_number = versions_tree.item(selection[0], "text")
            self.load_project(estimate_number)
            self.notebook.select(self.tab_editor)
            dialog.destroy()

        versions_tree.bind("<Double-1>", load_selected_version)

        btn_frame = ttk.Frame(dialog, padding=(10, 0, 10, 10))
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Load Version", command=load_selected_version).pack(side="right")
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=5)

    def load_project(self, estimate_number):
        self.clear_form() # Clear current form before loading new data

        self.cursor.execute('''
            SELECT p.id, p.client_id, p.validity_days, p.responsible_user, p.total_samples,
                   p.discount_percentage, p.final_cost, c.name, c.is_academic, comp.code, p.status
            FROM projects p
            LEFT JOIN clients_db.clients c ON p.client_id = c.id
            LEFT JOIN clients_db.companies comp ON c.company_id = comp.id
            WHERE p.estimate_number = ?
        ''', (estimate_number,))
        project_data = self.cursor.fetchone()

        if not project_data:
            messagebox.showerror("Error", f"Project '{estimate_number}' not found.")
            return

        (project_id, client_id, validity_days, responsible_user, total_samples,
         discount_percentage, final_cost, client_name, is_academic, company_code, status) = project_data

        if hasattr(self, 'username_to_full') and responsible_user in self.username_to_full:
            responsible_user = self.username_to_full[responsible_user]

        self.current_project_id = project_id
        self.estimate_number_var.set(estimate_number)
        self.client_var.set(client_name if client_name else "")
        self.current_client_company_id = company_code
        self.validity_var.set(validity_days)
        self.user_var.set(responsible_user)
        self.total_samples_var.set(total_samples)
        self.discount_percentage_var.set(discount_percentage)
        self.apply_discount_var.set(discount_percentage > 0)
        self.estimated_total_cost_var.set(f"R$ {final_cost:.2f}")

        if status == 0 or status is None:
            self.approve_btn.pack(fill="x", padx=20, pady=5, ipady=10)
        else:
            self.approve_btn.pack_forget()

        # Load project services
        self.cursor.execute('''
            SELECT ps.service_id, ps.samples_override, ps.calculated_cost, s.name
            FROM project_services ps
            JOIN services_db.services s ON ps.service_id = s.id
            WHERE ps.project_id = ? ORDER BY ps.id
        ''', (project_id,))
        project_services = self.cursor.fetchall()

        for service_id, samples_override, calculated_cost, service_name in project_services:
            self.project_services_tree.insert("", "end", text=service_name,
                                              values=(str(service_id), str(samples_override) if samples_override is not None else "Project Default", f"R$ {calculated_cost:.2f}"),  # type: ignore
                                              tags=(str(service_id), str(samples_override) if samples_override is not None else "", str(calculated_cost)))  # type: ignore
        self.update_total_cost() # Recalculate to ensure consistency

    def _ask_delete_type(self, estimate_number, base_estimate):
        dialog = tk.Toplevel(self.root)
        dialog.title("Confirm Deletion")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("400x180")

        msg = f"Multiple versions exist for estimate '{base_estimate}'.\nWhat would you like to delete?"
        ttk.Label(dialog, text=msg, justify='left').pack(padx=20, pady=10)

        result = tk.StringVar(value='cancel')

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)

        ttk.Button(btn_frame, text=f"Delete ONLY Version '{estimate_number}'", command=lambda: [result.set('this'), dialog.destroy()]).pack(fill='x', padx=20, pady=5)
        ttk.Button(btn_frame, text=f"Delete ALL Versions for '{base_estimate}'", command=lambda: [result.set('all'), dialog.destroy()]).pack(fill='x', padx=20, pady=5)
        ttk.Button(btn_frame, text="Cancel", command=lambda: [result.set('cancel'), dialog.destroy()]).pack(fill='x', padx=20, pady=5)

        # Center dialog
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (dialog.winfo_width() // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        self.root.wait_window(dialog)
        return result.get()

    def _execute_deletion_logic(self, estimate_number):
        base_estimate = re.sub(r'v\d+$', '', estimate_number)

        self.cursor.execute("SELECT COUNT(*) FROM projects WHERE estimate_number LIKE ?", (f"{base_estimate}v%",))
        version_count = self.cursor.fetchone()[0]

        if version_count > 1:
            choice = self._ask_delete_type(estimate_number, base_estimate)
            if choice == 'this':
                self.cursor.execute("DELETE FROM projects WHERE estimate_number = ?", (estimate_number,))
                self.conn.commit()
                messagebox.showinfo("Success", f"Project '{estimate_number}' deleted successfully.")
            elif choice == 'all':
                self.cursor.execute("DELETE FROM projects WHERE estimate_number LIKE ?", (f"{base_estimate}v%",))
                self.conn.commit()
                messagebox.showinfo("Success", f"All versions for '{base_estimate}' deleted successfully.")
            else: # cancel
                return
        else: # only one version
            if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete project '{estimate_number}'? This action cannot be undone."):
                self.cursor.execute("DELETE FROM projects WHERE estimate_number = ?", (estimate_number,))
                self.conn.commit()
                messagebox.showinfo("Success", f"Project '{estimate_number}' deleted successfully.")
            else:
                return

        # Refresh UI
        self.load_saved_estimates()
        if self.estimate_number_var.get().startswith(base_estimate):
            self.clear_form()

    def delete_project(self):
        estimate_number = self.estimate_number_var.get().strip()
        if not estimate_number:
            messagebox.showwarning("Warning", "No project selected to delete.")
            return
        
        try:
            self._execute_deletion_logic(estimate_number)
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not delete project: {e}")

    def delete_selected_estimate(self):
        selection = self.saved_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select an estimate to delete.")
            return

        item = selection[0]
        tags = self.saved_tree.item(item, "tags")
        if not tags or tags[0] != "project":
            messagebox.showwarning("Warning", "Please select an actual estimate, not a company.")
            return

        estimate_number = self.saved_tree.item(item, "text")
        try:
            self._execute_deletion_logic(estimate_number)
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not delete project: {e}")

    def clear_form(self):
        self.current_project_id = None
        self.current_client_company_id = None
        self.estimate_number_var.set("")
        self.client_var.set("")
        self.validity_var.set(30)
        
        current_user_display = self.current_user
        if hasattr(self, 'username_to_full') and self.current_user in self.username_to_full:
            current_user_display = self.username_to_full[self.current_user]
        self.user_var.set(current_user_display)
        self.total_samples_var.set(1)
        self.apply_discount_var.set(False)
        self.discount_percentage_var.set(0.0)
        self.estimated_total_cost_var.set("R$ 0.00")
        self.service_samples_override_var.set("")
        self.approve_btn.pack_forget()

        for item in self.project_services_tree.get_children():
            self.project_services_tree.delete(item)

    def on_closing(self):
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.destroy()

    def format_br_currency(self, value):
        formatted = f"{value:,.2f}"
        formatted = formatted.replace(',', 'X').replace('.', ',').replace('X', '.')
        return f"R$ {formatted}"

    def export_selected_pdf(self):
        selection = self.saved_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select an estimate to export.")
            return
        item = selection[0]
        tags = self.saved_tree.item(item, "tags")
        if tags and tags[0] == "project":
            estimate_number = self.saved_tree.item(item, "text")
            self.export_pdf_dialog(estimate_number)

    def export_pdf_dialog(self, estimate_number=None):
        if not REPORTLAB_AVAILABLE:
            messagebox.showerror("Dependency Missing", "Please install reportlab to generate PDFs.\nCommand: pip install reportlab")
            return
        
        estimate_number = estimate_number.strip() if estimate_number else ""
        if not estimate_number:
            messagebox.showwarning("Warning", "Please save or select a project first.")
            return
            
        self.cursor.execute("SELECT id, description, notes, final_cost FROM projects WHERE estimate_number = ?", (estimate_number,))
        row = self.cursor.fetchone()
        if not row:
            messagebox.showwarning("Warning", f"Project '{estimate_number}' not found in database. Make sure it is saved.")
            return
            
        project_id, description, notes, db_final_cost = row
        
        # Verify if the UI totals match DB totals (unsaved tax edits/recalculations)
        if estimate_number == self.estimate_number_var.get().strip():
            ui_cost_str = self.estimated_total_cost_var.get().replace("R$", "").strip()
            try:
                ui_cost = float(ui_cost_str)
                if abs(ui_cost - db_final_cost) > 0.01:
                    resp = messagebox.askyesnocancel(
                        "Unsaved Changes", 
                        "The project costs have changed (e.g. due to updated taxes or modified services).\n\n"
                        "Do you want to save these changes as a new version before exporting to PDF?\n"
                        "If you select 'No', the export will be cancelled."
                    )
                    if resp is True:
                        self.save_project()
                        new_est = self.estimate_number_var.get().strip()
                        self.export_pdf_dialog(new_est)
                        return
                    else:
                        return
            except ValueError:
                pass
        
        if not description:
            base = re.sub(r'v\d+$', '', estimate_number)
            self.cursor.execute("SELECT description FROM projects WHERE estimate_number LIKE ? AND estimate_number != ? AND description IS NOT NULL AND description != '' ORDER BY id DESC", (f"{base}v%", estimate_number))
            old_desc = self.cursor.fetchone()
            if old_desc:
                description = old_desc[0]
                
        # Fetch project services to allow editing their descriptions
        self.cursor.execute('''
            SELECT ps.id, s.name, COALESCE(ps.custom_description, s.description)
            FROM project_services ps
            JOIN services_db.services s ON ps.service_id = s.id
            WHERE ps.project_id = ? ORDER BY ps.id
        ''', (project_id,))
        project_services = self.cursor.fetchall()

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Export PDF: {estimate_number}")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("750x650")

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(side="bottom", fill="x", pady=10)

        main_frame = ttk.Frame(dialog)
        main_frame.pack(side="top", fill="both", expand=True, padx=10, pady=10)

        canvas = tk.Canvas(main_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)

        canvas.bind("<Configure>", on_canvas_configure)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        self.cursor.execute("SELECT discount_percentage FROM projects WHERE id = ?", (project_id,))
        discount_pct_result = self.cursor.fetchone()
        discount_pct = discount_pct_result[0] if discount_pct_result else 0.0

        discount_name_var = tk.StringVar(value="Desconto")
        if discount_pct > 0:
            ttk.Label(scrollable_frame, text="Discount Name (Appears on PDF):", font=('', 10, 'bold')).pack(anchor="w", pady=(10, 2))
            ttk.Entry(scrollable_frame, textvariable=discount_name_var, width=40).pack(anchor="w", padx=5)

        ttk.Label(scrollable_frame, text="Project Description (will appear on PDF):", font=('', 10, 'bold')).pack(anchor="w", pady=(10, 2))
        desc_text = tk.Text(scrollable_frame, height=4, width=80)
        desc_text.pack(fill="x", padx=5)
        if description: desc_text.insert("1.0", description)

        service_texts = {}
        if project_services:
            ttk.Label(scrollable_frame, text="Service Descriptions (editable for this project):", font=('', 10, 'bold')).pack(anchor="w", pady=(15, 5))
            for ps_id, s_name, s_desc in project_services:
                ttk.Label(scrollable_frame, text=f"• {s_name}:").pack(anchor="w", pady=(5, 2))
                stext = tk.Text(scrollable_frame, height=3, width=80)
                stext.pack(fill="x", padx=(15, 5))
                if s_desc: stext.insert("1.0", s_desc)
                service_texts[ps_id] = stext

        ttk.Label(scrollable_frame, text="Additional Notes (will appear at the end):", font=('', 10, 'bold')).pack(anchor="w", pady=(15, 2))
        notes_text = tk.Text(scrollable_frame, height=4, width=80)
        notes_text.pack(fill="x", padx=5)
        if notes: notes_text.insert("1.0", notes)

        settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    settings = json.load(f)
            except Exception: pass
            
        obs_list = settings.get("default_observations", [])
        obs_vars = []
        if obs_list:
            obs_header_frame = ttk.Frame(scrollable_frame)
            obs_header_frame.pack(fill="x", anchor="w", pady=(15, 5))
            ttk.Label(obs_header_frame, text="Default Observations (Select to append to notes):", font=('', 10, 'bold')).pack(side="left")
            
            def select_all_obs():
                for var, _ in obs_vars:
                    var.set(True)
                    
            ttk.Button(obs_header_frame, text="Select All", command=select_all_obs).pack(side="right", padx=15)

            for obs in obs_list:
                var = tk.BooleanVar(value=False)
                obs_vars.append((var, obs))
                obs_row = ttk.Frame(scrollable_frame)
                obs_row.pack(fill="x", pady=2, padx=(15, 5))
                ttk.Checkbutton(obs_row, variable=var).pack(side="left", anchor="n")
                ttk.Label(obs_row, text=obs, wraplength=650, font=("Helvetica", 9)).pack(side="left", anchor="n", padx=(5, 0))

        def save_and_get_data():
            new_desc = desc_text.get("1.0", tk.END).strip()
            new_notes = notes_text.get("1.0", tk.END).strip()
            self.cursor.execute("UPDATE projects SET description = ?, notes = ? WHERE id = ?", (new_desc, new_notes, project_id))
            
            for ps_id, stext in service_texts.items():
                s_new_desc = stext.get("1.0", tk.END).strip()
                self.cursor.execute("UPDATE project_services SET custom_description = ? WHERE id = ?", (s_new_desc, ps_id))
                
            self.conn.commit()
            
            appended_notes = new_notes
            for var, obs in obs_vars:
                if var.get():
                    appended_notes = f"{appended_notes}\n\n{obs}" if appended_notes else obs
            return new_desc, appended_notes, discount_name_var.get().strip()

        def confirm():
            new_desc, appended_notes, discount_name = save_and_get_data()
            dialog.destroy()
            self.generate_pdf(project_id, estimate_number, new_desc, appended_notes, is_preview=False, discount_name=discount_name)
            
        def preview():
            new_desc, appended_notes, discount_name = save_and_get_data()
            self.generate_pdf(project_id, estimate_number, new_desc, appended_notes, is_preview=True, discount_name=discount_name)
            
        ttk.Button(btn_frame, text="Generate PDF", command=confirm).pack(side="right", padx=15)
        ttk.Button(btn_frame, text="Preview PDF", command=preview).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=5)

    def generate_pdf(self, project_id, estimate_number, description, notes, is_preview=False, discount_name="Desconto"):
        import tempfile
        import webbrowser
        
        self.cursor.execute('''
            SELECT p.created_at, p.validity_days, p.responsible_user, p.total_samples, p.discount_percentage, p.final_cost,
                   c.name, c.email, c.phone
            FROM projects p
            LEFT JOIN clients_db.clients c ON p.client_id = c.id
            WHERE p.id = ?
        ''', (project_id,))
        p_data = self.cursor.fetchone()
        created_at, validity_days, user, total_samples, discount_pct, final_cost, c_name, c_email, c_phone = p_data
        
        client_parts = [c_name if c_name else "Unknown"]
        if c_email: client_parts.append(c_email)
        if c_phone: client_parts.append(c_phone)
        client_info = " - ".join(client_parts)
        
        date_str = created_at.split()[0] if created_at else datetime.now().strftime('%Y-%m-%d')
        try: date_str = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d/%m/%Y')
        except: pass

        if is_preview:
            pdf_path = os.path.join(tempfile.gettempdir(), f"Preview_Orcamento_{estimate_number}.pdf")
        else:
            pdf_path = filedialog.asksaveasfilename(defaultextension=".pdf", initialfile=f"Orcamento_{estimate_number}.pdf", title="Save Estimate PDF", filetypes=[("PDF files", "*.pdf")])
            if not pdf_path: return

        doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2.5*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=18, spaceAfter=12, alignment=1)
        normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=10, spaceAfter=2)
        
        settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    settings = json.load(f)
            except Exception: pass
        NumberedCanvas.footer_text = settings.get("footer_text", "Quarium Consultoria em Biologia Analítica, Ltda. | Campinas, SP | Email: quarium.bio@gmail.com")
        
        est_logo_file = settings.get("estimate_logo", "QLogo.png")
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), est_logo_file)
        if not os.path.exists(logo_path):
            logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'QLogo.png')
        if os.path.exists(logo_path): logo = RLImage(logo_path, width=3*cm, height=3*cm, kind='proportional')
        else: logo = Paragraph("<b>[Logo Missing]</b>", normal_style)
            
        header_table = Table([[logo, Paragraph("Orçamento Serviços - Quarium", title_style)]], colWidths=[4*cm, A4[0] - 8*cm])
        header_table.setStyle(TableStyle([('ALIGN', (0,0), (0,0), 'LEFT'), ('ALIGN', (1,0), (1,0), 'CENTER'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
        elements.append(header_table)
        elements.append(Spacer(1, 0.5*cm))
        
        info_data = [
            [Paragraph("<b>N° Orçamento:</b>", normal_style), Paragraph(estimate_number, normal_style), Paragraph("<b>Data:</b>", normal_style), Paragraph(date_str, normal_style)],
            [Paragraph("<b>Validade:</b>", normal_style), Paragraph(f"{validity_days} dias", normal_style), "", ""],
            [Paragraph("<b>Cliente:</b>", normal_style), Paragraph(client_info, normal_style), "", ""],
            [Paragraph("<b>Responsável:</b>", normal_style), Paragraph(user, normal_style), "", ""],
        ]
        if description: info_data.append([Paragraph("<b>Descrição:</b>", normal_style), Paragraph(description.replace('\n', '<br/>'), normal_style), "", ""])
            
        info_table = Table(info_data, colWidths=[3*cm, 7*cm, 1.5*cm, 5.5*cm])
        info_table.setStyle(TableStyle([('SPAN', (1,1), (3,1)), ('SPAN', (1,2), (3,2)), ('SPAN', (1,3), (3,3)), ('SPAN', (1,4), (3,4)) if description else ('SPAN', (0,0), (0,0)), ('VALIGN', (0,0), (-1,-1), 'TOP')]))
        elements.append(info_table)
        elements.append(Spacer(1, 1*cm))
        
        services_data = [[Paragraph("<b>Qtd</b>", normal_style), Paragraph("<b>Descrição</b>", normal_style), Paragraph("<b>Preço Unidade</b>", normal_style), Paragraph("<b>Total Parcial</b>", normal_style)]]
        self.cursor.execute('''
            SELECT ps.samples_override, ps.calculated_cost, s.name, COALESCE(ps.custom_description, s.description)
            FROM project_services ps
            JOIN services_db.services s ON ps.service_id = s.id
            WHERE ps.project_id = ? ORDER BY ps.id
        ''', (project_id,))
        
        raw_total = 0.0
        for s_override, s_cost, s_name, s_desc in self.cursor.fetchall():
            qty = s_override if s_override is not None else total_samples
            unit_cost = s_cost / qty if qty > 0 else 0
            raw_total += s_cost
            
            desc_text = f"<b>{s_name}</b>"
            if s_desc: 
                s_desc_html = str(s_desc).replace('\n', '<br/>&nbsp;&nbsp;')
                desc_text += f"<br/><font color='dimgrey'>&nbsp;&nbsp;{s_desc_html}</font>"
            
            services_data.append([
                Paragraph(str(qty), normal_style), Paragraph(desc_text, normal_style),
                Paragraph(self.format_br_currency(unit_cost), normal_style), Paragraph(self.format_br_currency(s_cost), normal_style)
            ])
            
        sv_table = Table(services_data, colWidths=[1.5*cm, A4[0] - 12.5*cm, 3.5*cm, 3.5*cm], repeatRows=1)
        sv_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
            ('ALIGN', (0,0), (0,-1), 'CENTER'), ('ALIGN', (2,0), (3,-1), 'RIGHT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('INNERGRID', (0,0), (-1,-1), 0.25, colors.grey), ('BOX', (0,0), (-1,-1), 0.25, colors.grey),
            ('RIGHTPADDING', (3,0), (3,-1), 6)
        ]))
        elements.append(sv_table)
        elements.append(Spacer(1, 0.5*cm))
        
        totals_data = []
        if discount_pct > 0:
            totals_data.append([Paragraph("<b>Subtotal:</b>", normal_style), Paragraph(self.format_br_currency(raw_total), normal_style)])
            d_name = discount_name if discount_name else "Desconto"
            totals_data.append([Paragraph(f"<b>{d_name} ({discount_pct}%):</b>", normal_style), Paragraph("-" + self.format_br_currency(raw_total * (discount_pct / 100.0)), normal_style)])
            totals_data.append([Paragraph("<b>Total com Desconto:</b>", normal_style), Paragraph(f"<b>{self.format_br_currency(final_cost)}</b>", normal_style)])
        else:
            totals_data.append([Paragraph("<b>Total:</b>", normal_style), Paragraph(f"<b>{self.format_br_currency(final_cost)}</b>", normal_style)])
            
        t_table = Table(totals_data, colWidths=[A4[0] - 7.5*cm, 3.5*cm])
        t_table.setStyle(TableStyle([('ALIGN', (0,0), (0,-1), 'RIGHT'), ('ALIGN', (1,0), (1,-1), 'RIGHT'), ('RIGHTPADDING', (1,0), (1,-1), 6)]))
        elements.append(t_table)
        
        if notes:
            elements.append(Spacer(1, 1*cm))
            elements.append(Paragraph("<b>Notas:</b>", normal_style))
            elements.append(Paragraph(notes.replace('\n', '<br/>'), normal_style))
            
        try:
            doc.build(elements, canvasmaker=NumberedCanvas)
            if is_preview:
                webbrowser.open(pdf_path)
            else:
                messagebox.showinfo("Success", f"PDF exported successfully to:\n{pdf_path}")
        except PermissionError:
            messagebox.showerror("Export Error", "Cannot generate file. The previous preview file is likely currently open in your PDF viewer. Please close it and try again.")
        except Exception as e:
            messagebox.showerror("Export Error", f"Could not generate PDF: {e}")

    def approve_estimate(self):
        if not self.current_project_id:
            messagebox.showwarning("Warning", "Please save or load a project first.")
            return
            
        days = simpledialog.askinteger("Approve Estimate", "Enter the number of agreed upon business days for completion:", parent=self.root, minvalue=1)
        if days is None:
            return
            
        try:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.cursor.execute('''
                UPDATE projects 
                SET status = 1, approved_at = ?, agreed_business_days = ?
                WHERE id = ?
            ''', (now, days, self.current_project_id))
            self.conn.commit()
            
            self.approve_btn.pack_forget()
            messagebox.showinfo("Success", f"Project approved and moved to Project Flow tab!\nAgreed completion time: {days} business days.")
            self.load_saved_estimates()
            
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not approve project: {e}")

if REPORTLAB_AVAILABLE:
    class NumberedCanvas(canvas.Canvas):
        footer_text = "Quarium Consultoria em Biologia Analítica, Ltda. | Campinas, SP | Email: quarium.bio@gmail.com"
        def __init__(self, *args, **kwargs):
            canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []
        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()  # type: ignore
        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.draw_page_number(num_pages)  # type: ignore
                canvas.Canvas.showPage(self)  # type: ignore
            canvas.Canvas.save(self)  # type: ignore
        def draw_page_number(self, page_count):
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.grey)
            self.drawCentredString(A4[0] / 2.0, 1.0 * cm, self.footer_text)
            self.drawRightString(A4[0] - 1.5 * cm, 1.0 * cm, f"Página {self._pageNumber} de {page_count}")  # type: ignore

if __name__ == "__main__":
    root = tk.Tk()
    app = ProjectManager(root)
    root.mainloop()