import os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import sqlite3
from datetime import datetime

class ServiceManager:
    def __init__(self, root, current_user="Unknown"):
        self.root = root
        self.current_user = current_user
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.title("Quarium Service Manager")
            self.root.geometry("1000x700")

        # Path to stock database (shared with StockManager)
        self.stock_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock.db')

        # Service database
        self.service_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'services.db')

        self.init_db()
        self.create_ui()
        self.load_services()
        self.load_stock_items()

        self.last_selected_item = None
        self.clean_state = self.get_current_form_state()

        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def init_db(self):
        self.conn = sqlite3.connect(self.service_db_path)
        self.cursor = self.conn.cursor()
        
        # Enable foreign key constraints
        self.cursor.execute("PRAGMA foreign_keys = ON")

        self.cursor.execute("ATTACH DATABASE ? AS stock_db", (self.stock_db_path,))

        # Services table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                code TEXT UNIQUE,
                description TEXT,
                category_id INTEGER,
                created_at TEXT,
                updated_at TEXT,
                updated_by TEXT,
                FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE SET NULL
            )
        ''')

        # Add new columns if they don't exist (for database migration)
        # Check if code column exists
        self.cursor.execute("PRAGMA table_info(services)")
        columns = [column[1] for column in self.cursor.fetchall()]
        
        if 'code' not in columns:
            self.cursor.execute('ALTER TABLE services ADD COLUMN code TEXT')
        
        if 'category_id' not in columns:
            self.cursor.execute('ALTER TABLE services ADD COLUMN category_id INTEGER REFERENCES categories (id) ON DELETE SET NULL')

        if 'notes' not in columns:
            self.cursor.execute('ALTER TABLE services ADD COLUMN notes TEXT')
            
        if 'updated_by' not in columns:
            self.cursor.execute('ALTER TABLE services ADD COLUMN updated_by TEXT')

        # Categories table for organizing services
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TEXT
            )
        ''')
        # Add updated_by column to categories if it doesn't exist
        self.cursor.execute("PRAGMA table_info(categories)")
        cat_columns = [column[1] for column in self.cursor.fetchall()]
        if 'updated_by' not in cat_columns:
            self.cursor.execute('ALTER TABLE categories ADD COLUMN updated_by TEXT')

        # Service requirements table (links services to stock items)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS service_requirements (
                id INTEGER PRIMARY KEY,
                service_id INTEGER NOT NULL,
                stock_item_id INTEGER NOT NULL,
                quantity REAL NOT NULL,
                unit TEXT,
                samples_per_batch INTEGER DEFAULT 1,
                notes TEXT,
                FOREIGN KEY (service_id) REFERENCES services (id) ON DELETE CASCADE
            )
        ''')

        # Database migration: add samples_per_batch column if it doesn't exist
        self.cursor.execute("PRAGMA table_info(service_requirements)")
        req_columns = [column[1] for column in self.cursor.fetchall()]
        if 'samples_per_batch' not in req_columns:
            self.cursor.execute('ALTER TABLE service_requirements ADD COLUMN samples_per_batch INTEGER DEFAULT 1')

        # Non-Reagent Costs table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS service_costs (
                id INTEGER PRIMARY KEY,
                service_id INTEGER NOT NULL,
                cost_type TEXT NOT NULL,
                cost REAL NOT NULL,
                samples_per_batch INTEGER DEFAULT 1,
                notes TEXT,
                FOREIGN KEY (service_id) REFERENCES services (id) ON DELETE CASCADE
            )
        ''')

        self.conn.commit()

        # Database migration: remove invalid foreign key to 'stock' table if it exists
        self.cursor.execute("PRAGMA foreign_key_list(service_requirements)")
        if any(fk[2] == 'stock' for fk in self.cursor.fetchall()):
            self.cursor.execute("PRAGMA foreign_keys = OFF")
            self.cursor.execute('''
                CREATE TABLE service_requirements_new (
                    id INTEGER PRIMARY KEY,
                    service_id INTEGER NOT NULL,
                    stock_item_id INTEGER NOT NULL,
                    quantity REAL NOT NULL,
                    unit TEXT,
                    samples_per_batch INTEGER DEFAULT 1,
                    notes TEXT,
                    FOREIGN KEY (service_id) REFERENCES services (id) ON DELETE CASCADE
                )
            ''')
            self.cursor.execute("INSERT INTO service_requirements_new SELECT id, service_id, stock_item_id, quantity, unit, samples_per_batch, notes FROM service_requirements")
            self.cursor.execute("DROP TABLE service_requirements")
            self.cursor.execute("ALTER TABLE service_requirements_new RENAME TO service_requirements")
            self.conn.commit()
            self.cursor.execute("PRAGMA foreign_keys = ON")

        # Add default categories if they don't exist
        self.cursor.execute("SELECT COUNT(*) FROM categories")
        if self.cursor.fetchone()[0] == 0:
            default_categories = [
                ("Protein Analysis", "Services related to protein quantification and analysis"),
                ("DNA/RNA Services", "Nucleic acid extraction, quantification, and analysis"),
                ("Cell Culture", "Cell culture and maintenance services"),
                ("General Lab Services", "Miscellaneous laboratory services"),
            ]
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.cursor.executemany(
                "INSERT INTO categories (name, description, created_at, updated_by) VALUES (?, ?, ?, ?)",
                [(name, desc, now, "System") for name, desc in default_categories]
            )
            self.conn.commit()

    def create_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        # Left panel - Services list
        left_panel = ttk.LabelFrame(main_frame, text="Services", padding=10)
        left_panel.pack(side="left", fill="y", padx=(0, 10))

        # Services treeview (hierarchical with categories)
        self.services_tree = ttk.Treeview(left_panel, columns=("Code", "Description"), height=15)
        self.services_tree.heading("#0", text="Name", command=lambda: self.sort_services("Name"))
        self.services_tree.heading("Code", text="Code", command=lambda: self.sort_services("Code"))
        self.services_tree.heading("Description", text="Description")
        self.services_tree.column("#0", width=150)
        self.services_tree.column("Code", width=80)
        self.services_tree.column("Description", width=120)
        self.services_tree.pack(fill="y", expand=True)

        # Service buttons
        service_btn_frame = ttk.Frame(left_panel)
        service_btn_frame.pack(fill="x", pady=10)
        ttk.Button(service_btn_frame, text="Add Service", command=self.add_service).pack(fill="x", pady=2)
        ttk.Button(service_btn_frame, text="Add Category", command=self.add_category).pack(fill="x", pady=2)
        ttk.Button(service_btn_frame, text="Edit Selected", command=self.edit_selected).pack(fill="x", pady=2)
        ttk.Button(service_btn_frame, text="Duplicate Selected", command=self.duplicate_selected).pack(fill="x", pady=2)
        ttk.Button(service_btn_frame, text="Delete Selected", command=self.delete_selected).pack(fill="x", pady=2)

        # Right panel - Service details and requirements
        right_panel = ttk.LabelFrame(main_frame, text="Service Details", padding=10)
        right_panel.pack(side="right", fill="both", expand=True)

        # Service info frame
        info_frame = ttk.Frame(right_panel)
        info_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(info_frame, text="Service Name:").grid(row=0, column=0, sticky="w")
        self.service_name_var = tk.StringVar()
        ttk.Entry(info_frame, textvariable=self.service_name_var, width=40).grid(row=0, column=1, padx=5)

        ttk.Label(info_frame, text="Service Code:").grid(row=1, column=0, sticky="w")
        self.service_code_var = tk.StringVar()
        ttk.Entry(info_frame, textvariable=self.service_code_var, width=40).grid(row=1, column=1, padx=5)

        ttk.Label(info_frame, text="Description:").grid(row=2, column=0, sticky="nw", pady=5)
        self.service_desc_text = tk.Text(info_frame, width=40, height=4,
                                          background="#F0F0F0", relief="flat", borderwidth=1)
        self.service_desc_text.grid(row=2, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(info_frame, text="Notes:").grid(row=3, column=0, sticky="nw", pady=5)
        self.service_notes_text = tk.Text(info_frame, width=40, height=4,
                                          background="#F0F0F0", relief="flat", borderwidth=1)
        self.service_notes_text.grid(row=3, column=1, padx=5, pady=5, sticky="w")

        # Save button - dock to bottom first so it doesn't get cut off
        save_frame = ttk.Frame(right_panel)
        save_frame.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(save_frame, text="Save Service", command=self.save_service).pack(side="right", padx=5)

        # Tabs for Reagents and Costs
        self.notebook = ttk.Notebook(right_panel)
        self.notebook.pack(fill="both", expand=True, pady=(10, 0))

        # Requirements section
        req_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(req_frame, text="Required Reagents")

        # Available stock items
        stock_frame = ttk.Frame(req_frame)
        stock_frame.pack(fill="x", pady=(0, 10))

        search_frame = ttk.Frame(stock_frame)
        search_frame.pack(fill="x", pady=(0, 5))
        ttk.Label(search_frame, text="Search Reagent:").pack(side="left")
        self.reagent_search_var = tk.StringVar()
        self.reagent_search_entry = ttk.Entry(search_frame, textvariable=self.reagent_search_var)
        self.reagent_search_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.reagent_search_entry.bind("<KeyRelease>", self.filter_reagents)

        ttk.Label(stock_frame, text="Available Reagents:").pack(anchor="w")
        self.stock_listbox = tk.Listbox(stock_frame, height=4, width=60,
                                        background="#F0F0F0",
                                        selectbackground="#285D80",
                                        selectforeground="#FFFFFF",
                                        borderwidth=1, relief="flat",
                                        exportselection=False)
        self.stock_listbox.pack(fill="x", pady=5)
        self.stock_listbox.bind("<<ListboxSelect>>", self.on_stock_select)

        # Quantity input
        qty_frame = ttk.Frame(req_frame)
        qty_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(qty_frame, text="Quantity:").grid(row=0, column=0, sticky="w")
        self.quantity_var = tk.StringVar()
        ttk.Entry(qty_frame, textvariable=self.quantity_var, width=10).grid(row=0, column=1, padx=5)

        ttk.Label(qty_frame, text="Unit:").grid(row=0, column=2, sticky="w")
        self.unit_var = tk.StringVar()
        self.unit_combo = ttk.Combobox(qty_frame, textvariable=self.unit_var, width=10, state='disabled')
        self.unit_combo.grid(row=0, column=3, padx=5)

        self.base_unit = ""

        self.is_bulk_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(qty_frame, text="Bulk Reagent", variable=self.is_bulk_var, command=self.toggle_bulk).grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        
        ttk.Label(qty_frame, text="Per Samples:").grid(row=1, column=2, sticky="w")
        self.samples_var = tk.StringVar(value="1")
        self.samples_entry = ttk.Entry(qty_frame, textvariable=self.samples_var, width=10, state='disabled')
        self.samples_entry.grid(row=1, column=3, padx=5)

        ttk.Label(qty_frame, text="Notes:").grid(row=2, column=0, sticky="w")
        self.notes_var = tk.StringVar()
        ttk.Entry(qty_frame, textvariable=self.notes_var, width=40).grid(row=2, column=1, columnspan=3, padx=5, pady=5)

        # Requirement buttons
        req_btn_frame = ttk.Frame(req_frame)
        req_btn_frame.pack(fill="x", pady=5)
        ttk.Button(req_btn_frame, text="Add Requirement", command=self.add_requirement).pack(side="left", padx=5)
        ttk.Button(req_btn_frame, text="Edit Requirement", command=self.edit_requirement).pack(side="left", padx=5)
        ttk.Button(req_btn_frame, text="Remove Requirement", command=self.remove_requirement).pack(side="left", padx=5)

        # Current requirements list
        ttk.Label(req_frame, text="Current Requirements:").pack(anchor="w", pady=(10, 5))
        self.requirements_tree = ttk.Treeview(req_frame, columns=("Quantity", "Unit", "Per Samples", "Notes"), height=10)
        self.requirements_tree.heading("#0", text="Reagent")
        self.requirements_tree.heading("Quantity", text="Quantity")
        self.requirements_tree.heading("Unit", text="Unit")
        self.requirements_tree.heading("Per Samples", text="Per Samples")
        self.requirements_tree.heading("Notes", text="Notes")
        self.requirements_tree.column("#0", width=200)
        self.requirements_tree.column("Quantity", width=80)
        self.requirements_tree.column("Unit", width=60)
        self.requirements_tree.column("Per Samples", width=80)
        self.requirements_tree.column("Notes", width=150)
        self.requirements_tree.pack(fill="both", expand=True, pady=(0, 10))

        # Costs section
        costs_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(costs_frame, text="Non-Reagent Costs")
        
        cost_input_frame = ttk.Frame(costs_frame)
        cost_input_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Label(cost_input_frame, text="Cost Type:").grid(row=0, column=0, sticky="w")
        self.cost_type_var = tk.StringVar()
        cost_options = ["Labor", "Maintenance", "Profit"]
        ttk.Combobox(cost_input_frame, textvariable=self.cost_type_var, values=cost_options, width=15, state='readonly').grid(row=0, column=1, padx=5)
        
        ttk.Label(cost_input_frame, text="Amount (R$):").grid(row=0, column=2, sticky="w")
        self.cost_amount_var = tk.StringVar()
        ttk.Entry(cost_input_frame, textvariable=self.cost_amount_var, width=15).grid(row=0, column=3, padx=5)
        
        self.cost_is_bulk_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cost_input_frame, text="Per Group", variable=self.cost_is_bulk_var, command=self.toggle_cost_bulk).grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        
        ttk.Label(cost_input_frame, text="Group Size:").grid(row=1, column=2, sticky="w")
        self.cost_samples_var = tk.StringVar(value="1")
        self.cost_samples_entry = ttk.Entry(cost_input_frame, textvariable=self.cost_samples_var, width=10, state='disabled')
        self.cost_samples_entry.grid(row=1, column=3, padx=5)
        
        ttk.Label(cost_input_frame, text="Notes:").grid(row=2, column=0, sticky="w")
        self.cost_notes_var = tk.StringVar()
        ttk.Entry(cost_input_frame, textvariable=self.cost_notes_var, width=40).grid(row=2, column=1, columnspan=3, padx=5, pady=5)
        
        # Cost buttons
        cost_btn_frame = ttk.Frame(costs_frame)
        cost_btn_frame.pack(fill="x", pady=5)
        ttk.Button(cost_btn_frame, text="Add Cost", command=self.add_cost).pack(side="left", padx=5)
        ttk.Button(cost_btn_frame, text="Edit Cost", command=self.edit_cost).pack(side="left", padx=5)
        ttk.Button(cost_btn_frame, text="Remove Cost", command=self.remove_cost).pack(side="left", padx=5)
        
        # Costs list
        ttk.Label(costs_frame, text="Current Costs:").pack(anchor="w", pady=(10, 5))
        self.costs_tree = ttk.Treeview(costs_frame, columns=("Amount", "Per Samples", "Notes"), height=6)
        self.costs_tree.heading("#0", text="Type")
        self.costs_tree.heading("Amount", text="Amount")
        self.costs_tree.heading("Per Samples", text="Per Samples")
        self.costs_tree.heading("Notes", text="Notes")
        self.costs_tree.column("#0", width=150)
        self.costs_tree.column("Amount", width=80)
        self.costs_tree.column("Per Samples", width=80)
        self.costs_tree.column("Notes", width=200)
        self.costs_tree.pack(fill="x", pady=(0, 10))

        # Bind events
        self.services_tree.bind("<<TreeviewSelect>>", self.on_service_select)
        self.services_tree.bind("<ButtonPress-1>", self.on_drag_start)
        self.services_tree.bind("<ButtonRelease-1>", self.on_drag_end)
        self.services_tree.bind("<B1-Motion>", self.on_drag_motion)
        self.services_tree.bind("<Motion>", self.on_tree_motion)
        self.services_tree.bind("<Leave>", self.on_tree_leave)

    def get_current_form_state(self):
        return {
            'name': self.service_name_var.get().strip() if hasattr(self, 'service_name_var') else '',
            'code': self.service_code_var.get().strip() if hasattr(self, 'service_code_var') else '',
            'description': self.service_desc_text.get("1.0", tk.END).strip() if hasattr(self, 'service_desc_text') else '',
            'notes': self.service_notes_text.get("1.0", tk.END).strip() if hasattr(self, 'service_notes_text') else ''
        }

    def check_unsaved_changes(self):
        if not hasattr(self, 'clean_state'):
            return True
        if self.get_current_form_state() != self.clean_state:
            response = messagebox.askyesnocancel("Unsaved Changes", "You have unsaved changes to the current service. Do you want to save them before proceeding?")
            if response is True:
                return self.save_service()
            elif response is False:
                return True
            else:
                return False
        return True

    def sort_services(self, col):
        if not hasattr(self, 'service_sort_states'):
            self.service_sort_states = {"Name": 0, "Code": 0}
            
        current_state = self.service_sort_states.get(col, 0)
        next_state = 1 if current_state == 0 else 2 if current_state == 1 else 0
        
        for c in self.service_sort_states:
            self.service_sort_states[c] = 0
        self.service_sort_states[col] = next_state
        
        if next_state == 0:
            self.load_services() # Revert to alphabetical default
            return
            
        reverse = (next_state == 2)
        for cat_id in self.services_tree.get_children(''):
            item_type, _ = self.services_tree.item(cat_id, "tags")
            if item_type == "category":
                items = []
                for child in self.services_tree.get_children(cat_id):
                    val = self.services_tree.item(child, "text").lower() if col == "Name" else self.services_tree.set(child, col).lower()
                    items.append((val, child))
                items.sort(key=lambda x: x[0], reverse=reverse)
                for index, (_, child) in enumerate(items):
                    self.services_tree.move(child, cat_id, index)

    def toggle_bulk(self):
        """Enable/disable bulk reagent samples input"""
        if self.is_bulk_var.get():
            self.samples_entry.config(state='normal')
        else:
            self.samples_var.set("1")
            self.samples_entry.config(state='disabled')

    def toggle_cost_bulk(self):
        """Enable/disable bulk cost samples input"""
        if self.cost_is_bulk_var.get():
            self.cost_samples_entry.config(state='normal')
        else:
            self.cost_samples_var.set("1")
            self.cost_samples_entry.config(state='disabled')

    def update_unit_combo(self):
        mass_units = ["ng", "ug", "mg", "g"]
        vol_units = ["nL", "uL", "mL", "L"]
        
        if self.base_unit in mass_units:
            self.unit_combo.config(values=mass_units, state='readonly')
        elif self.base_unit in vol_units:
            self.unit_combo.config(values=vol_units, state='readonly')
        else:
            self.unit_combo.config(values=[self.base_unit] if self.base_unit else [], state='disabled')

    def on_stock_select(self, event):
        """Auto-populate unit when stock item is selected"""
        selection = self.stock_listbox.curselection()
        if selection:
            index = selection[0]
            if index < len(self.stock_items):
                _, _, _, _, unit = self.stock_items[index]
                self.base_unit = unit or ""
                self.unit_var.set(self.base_unit)
                self.update_unit_combo()

    def load_stock_items(self):
        """Load available stock items from the stock database"""
        try:
            try:
                self.cursor.execute('SELECT id, name, code, vendor, unit, synonyms FROM stock_db.stock ORDER BY name')
                self.all_stock_items = self.cursor.fetchall()
            except sqlite3.OperationalError:
                self.cursor.execute('SELECT id, name, code, vendor, unit FROM stock_db.stock ORDER BY name')
                self.all_stock_items = [row + (None,) for row in self.cursor.fetchall()]

            self.filter_reagents()

        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load stock items: {e}")
            self.all_stock_items = []
            self.stock_items = []

    def filter_reagents(self, event=None):
        query = self.reagent_search_var.get().lower() if hasattr(self, 'reagent_search_var') else ""
        self.stock_listbox.delete(0, tk.END)
        self.stock_items = []
        for item in self.all_stock_items:
            item_id, name, code, vendor, unit, synonyms = item
            searchable_text = f"{name} {code or ''} {vendor or ''} {synonyms or ''}".lower()
            if query in searchable_text:
                self.stock_items.append((item_id, name, code, vendor, unit))
                display_text = f"{name}"
                if code: display_text += f" ({code})"
                if vendor: display_text += f" - {vendor}"
                self.stock_listbox.insert(tk.END, display_text)

    def load_services(self):
        """Load all services and categories in hierarchical structure"""
        for item in self.services_tree.get_children():
            self.services_tree.delete(item)

        # Load categories
        self.cursor.execute('SELECT id, name, description FROM categories ORDER BY name')
        categories = self.cursor.fetchall()

        for cat_id, cat_name, cat_desc in categories:
            # Insert category as parent node
            cat_node = self.services_tree.insert("", "end", text=cat_name,
                                               values=("", cat_desc), tags=("category", cat_id))

            # Load services for this category
            self.cursor.execute('''
                SELECT id, name, code, description FROM services
                WHERE category_id = ? ORDER BY name
            ''', (cat_id,))

            for service_id, service_name, service_code, service_desc in self.cursor.fetchall():
                self.services_tree.insert(cat_node, "end", text=service_name,
                                        values=(service_code or "", service_desc or ""),
                                        tags=("service", service_id))

        # Load services without categories (uncategorized)
        uncat_node = self.services_tree.insert("", "end", text="Uncategorized",
                                             values=("", "Services without a category"), tags=("category", None))

        self.cursor.execute('''
            SELECT id, name, code, description FROM services
            WHERE category_id IS NULL ORDER BY name
        ''')

        for service_id, service_name, service_code, service_desc in self.cursor.fetchall():
            self.services_tree.insert(uncat_node, "end", text=service_name,
                                    values=(service_code or "", service_desc or ""),
                                    tags=("service", service_id))

    def add_service(self):
        """Add a new service"""
        if not self.check_unsaved_changes():
            return
        self.service_name_var.set("")
        self.service_code_var.set("")
        self.service_desc_text.delete("1.0", tk.END)
        self.service_notes_text.delete("1.0", tk.END)
        self.clear_requirements()
        self.clear_costs()
        self.current_service_id = None
        self.clean_state = self.get_current_form_state()
        self.last_selected_item = None
        self._ignore_select = True
        self.services_tree.selection_remove(self.services_tree.selection())
        self._ignore_select = False

    def add_category(self):
        """Add a new category"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Category")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("300x150")

        ttk.Label(dialog, text="Category Name:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        name_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=name_var, width=25).grid(row=0, column=1, padx=10, pady=5)

        ttk.Label(dialog, text="Description:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        desc_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=desc_var, width=25).grid(row=1, column=1, padx=10, pady=5)

        def save_category():
            category_name = name_var.get().strip()
            category_desc = desc_var.get().strip()

            if not category_name:
                messagebox.showerror("Error", "Category name is required", parent=dialog)
                return

            try:
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.cursor.execute(
                    "INSERT INTO categories (name, description, created_at, updated_by) VALUES (?, ?, ?, ?)",
                    (category_name, category_desc, now, self.current_user)
                )
                self.conn.commit()
                dialog.destroy()
                self.load_services()
            except sqlite3.IntegrityError:
                messagebox.showerror("Error", "A category with this name already exists", parent=dialog)
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not add category: {e}", parent=dialog)

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=15)
        ttk.Button(btn_frame, text="Save", command=save_category).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=5)

    def edit_selected(self):
        """Edit selected service or category"""
        selection = self.services_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select an item to edit")
            return

        item = selection[0]
        item_type, item_id = self.services_tree.item(item, "tags")

        if item_type == "category":
            self.edit_category(item_id)
        elif item_type == "service":
            self.edit_service(item_id)

    def delete_selected(self):
        """Delete selected service or category"""
        selection = self.services_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select an item to delete")
            return

        item = selection[0]
        item_type, item_id = self.services_tree.item(item, "tags")

        if item_type == "category":
            self.delete_category(item_id)
        elif item_type == "service":
            self.delete_service(item_id)

    def edit_category(self, category_id):
        """Edit a category"""
        if category_id is None or category_id == 'None':
            messagebox.showerror("Error", "Cannot edit the Uncategorized category")
            return

        self.cursor.execute("SELECT name, description FROM categories WHERE id = ?", (category_id,))
        result = self.cursor.fetchone()
        if not result:
            messagebox.showerror("Error", "Category not found")
            return

        current_name, current_desc = result

        # Create edit dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Category")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("300x150")

        ttk.Label(dialog, text="Category Name:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        name_var = tk.StringVar(value=current_name)
        ttk.Entry(dialog, textvariable=name_var, width=25).grid(row=0, column=1, padx=10, pady=5)

        ttk.Label(dialog, text="Description:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        desc_var = tk.StringVar(value=current_desc or "")
        ttk.Entry(dialog, textvariable=desc_var, width=25).grid(row=1, column=1, padx=10, pady=5)

        def save_category():
            new_name = name_var.get().strip()
            new_desc = desc_var.get().strip()

            if not new_name:
                messagebox.showerror("Error", "Category name is required")
                return

            try:
                self.cursor.execute(
                    "UPDATE categories SET name = ?, description = ?, updated_by = ? WHERE id = ?",
                    (new_name, new_desc, self.current_user, category_id)
                )
                self.conn.commit()
                dialog.destroy()
                self.load_services()
            except sqlite3.IntegrityError:
                messagebox.showerror("Error", "A category with this name already exists")
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not update category: {e}")

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=15)
        ttk.Button(btn_frame, text="Save", command=save_category).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=5)

    def delete_category(self, category_id):
        """Delete a category"""
        if category_id is None or category_id == 'None':
            messagebox.showerror("Error", "Cannot delete the Uncategorized category")
            return

        # Check if category has services
        self.cursor.execute("SELECT COUNT(*) FROM services WHERE category_id = ?", (category_id,))
        service_count = self.cursor.fetchone()[0]

        if service_count > 0:
            if not messagebox.askyesno("Confirm Delete",
                                     f"This category contains {service_count} service(s). "
                                     "Deleting it will move all services to 'Uncategorized'. Continue?"):
                return

        try:
            # Explicitly set category_id to NULL for services in this category
            # (in case foreign key constraint doesn't work)
            self.cursor.execute("UPDATE services SET category_id = NULL WHERE category_id = ?", (category_id,))
            self.cursor.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            self.conn.commit()
            self.load_services()
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not delete category: {e}")

    def edit_service(self, service_id):
        """Load service for editing"""
        self.cursor.execute('SELECT name, code, description, notes FROM services WHERE id = ?', (service_id,))
        result = self.cursor.fetchone()
        if result:
            self.service_name_var.set(result[0])
            self.service_code_var.set(result[1] or "")
            self.service_notes_text.delete("1.0", tk.END)
            self.service_desc_text.delete("1.0", tk.END)
            if result[2]: self.service_desc_text.insert("1.0", result[2])
            if result[3]: self.service_notes_text.insert("1.0", result[3])
            self.current_service_id = service_id
            self.load_requirements()
            self.load_costs()
            self.clean_state = self.get_current_form_state()

    def delete_service(self, service_id):
        """Delete selected service"""
        if messagebox.askyesno("Confirm Delete", "Are you sure you want to delete this service?"):
            try:
                self.cursor.execute('DELETE FROM services WHERE id = ?', (service_id,))
                self.conn.commit()
                self.load_services()
                self.clear_service_form()
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not delete service: {e}")

    def duplicate_selected(self):
        """Duplicate selected service and its requirements"""
        selection = self.services_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a service to duplicate")
            return

        item = selection[0]
        item_type, item_id = self.services_tree.item(item, "tags")

        if item_type != "service" or not item_id:
            messagebox.showwarning("Warning", "Only services can be duplicated")
            return

        try:
            # Get original service details
            self.cursor.execute('SELECT name, code, description, notes, category_id FROM services WHERE id = ?', (item_id,))
            service = self.cursor.fetchone()
            if not service:
                return
            
            orig_name, orig_code, description, notes, category_id = service
            
            # Generate unique name and code
            new_name = f"{orig_name} (Copy)"
            new_code = f"{orig_code}-COPY" if orig_code else None
            
            counter = 1
            while True:
                self.cursor.execute('SELECT COUNT(*) FROM services WHERE name = ? OR (code IS NOT NULL AND code = ?)', (new_name, new_code))
                if self.cursor.fetchone()[0] == 0:
                    break
                counter += 1
                new_name = f"{orig_name} (Copy {counter})"
                if orig_code:
                    new_code = f"{orig_code}-COPY{counter}"

            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Insert duplicated service
            self.cursor.execute('''
                INSERT INTO services (name, code, description, notes, category_id, created_at, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (new_name, new_code, description, notes, category_id, now, now, self.current_user))
            
            new_service_id = self.cursor.lastrowid
            
            # Duplicate requirements
            self.cursor.execute('''
                INSERT INTO service_requirements (service_id, stock_item_id, quantity, unit, samples_per_batch, notes)
                SELECT ?, stock_item_id, quantity, unit, samples_per_batch, notes FROM service_requirements WHERE service_id = ?
            ''', (new_service_id, item_id))
            
            # Duplicate costs
            self.cursor.execute('''
                INSERT INTO service_costs (service_id, cost_type, cost, samples_per_batch, notes)
                SELECT ?, cost_type, cost, samples_per_batch, notes FROM service_costs WHERE service_id = ?
            ''', (new_service_id, item_id))

            self.conn.commit()
            self.load_services()
            messagebox.showinfo("Success", f"Service duplicated as '{new_name}'")
            
        except sqlite3.Error as e:
            self.conn.rollback()
            messagebox.showerror("Database Error", f"Could not duplicate service: {e}")

    def save_service(self):
        """Save current service"""
        name = self.service_name_var.get().strip()
        code = self.service_code_var.get().strip()
        description = self.service_desc_text.get("1.0", tk.END).strip()
        notes = self.service_notes_text.get("1.0", tk.END).strip()

        if not name:
            messagebox.showerror("Error", "Service name is required")
            return False

        try:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            if self.current_service_id:
                # Update existing service
                self.cursor.execute('''
                    UPDATE services
                    SET name = ?, code = ?, description = ?, notes = ?, updated_at = ?, updated_by = ?
                    WHERE id = ?
                ''', (name, code if code else None, description, notes, now, self.current_user, self.current_service_id))
            else:
                # Create new service
                self.cursor.execute('''
                    INSERT INTO services (name, code, description, notes, created_at, updated_at, updated_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (name, code if code else None, description, notes, now, now, self.current_user))
                self.current_service_id = self.cursor.lastrowid

            self.conn.commit()
            self.load_services()
            self.clean_state = self.get_current_form_state()
            messagebox.showinfo("Success", "Service saved successfully")
            return True

        except sqlite3.IntegrityError as e:
            if "name" in str(e):
                messagebox.showerror("Error", "A service with this name already exists")
            elif "code" in str(e):
                messagebox.showerror("Error", "A service with this code already exists")
            else:
                messagebox.showerror("Error", str(e))
            return False
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not save service: {e}")
            return False

    def add_requirement(self):
        """Add a requirement to current service"""
        if not self.current_service_id:
            messagebox.showerror("Error", "Please save the service first")
            return

        selection = self.stock_listbox.curselection()
        if not selection:
            messagebox.showerror("Error", "Please select a reagent from the list")
            return

        try:
            qty_str = self.quantity_var.get().strip()
            if ',' in qty_str:
                proposed = qty_str.replace(',', '.')
                if messagebox.askyesno("Convert Decimal", f"You entered '{qty_str}'. Convert to '{proposed}'?"):
                    qty_str = proposed
                    self.quantity_var.set(proposed)
                else:
                    return
            
            quantity = float(qty_str)
            unit = self.unit_var.get()
            try:
                samples_per_batch = int(self.samples_var.get())
                if samples_per_batch < 1:
                    samples_per_batch = 1
            except ValueError:
                samples_per_batch = 1
            notes = self.notes_var.get().strip()

            stock_index = selection[0]
            stock_item_id = self.stock_items[stock_index][0]

            self.cursor.execute('''
                INSERT INTO service_requirements (service_id, stock_item_id, quantity, unit, samples_per_batch, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (self.current_service_id, stock_item_id, quantity, unit, samples_per_batch, notes))

            self.conn.commit()
            self.load_requirements()
            self.quantity_var.set("")
            self.unit_var.set("")
            self.is_bulk_var.set(False)
            self.samples_var.set("1")
            self.samples_entry.config(state='disabled')
            self.notes_var.set("")

        except ValueError:
            messagebox.showerror("Error", "Quantity must be a number")
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not add requirement: {e}")

    def edit_requirement(self):
        """Edit selected requirement"""
        selection = self.requirements_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a requirement to edit")
            return

        item = selection[0]
        req_id = self.requirements_tree.item(item, "tags")[0]

        # Fetch current data
        self.cursor.execute('''
            SELECT stock_item_id, quantity, unit, samples_per_batch, notes
            FROM service_requirements WHERE id = ?
        ''', (req_id,))
        req_data = self.cursor.fetchone()
        if not req_data:
            return
            
        stock_item_id, quantity, unit, samples_per_batch, notes = req_data

        # Fetch name from stock db
        display_name = "Unknown Reagent"
        base_unit = ""
        try:
            self.cursor.execute('SELECT name, code, vendor, unit FROM stock_db.stock WHERE id = ?', (stock_item_id,))
            stock_item = self.cursor.fetchone()
            
            if stock_item:
                name, code, vendor, base_unit = stock_item
                display_name = f"{name}"
                if code: display_name += f" ({code})"
                if vendor: display_name += f" - {vendor}"
        except sqlite3.Error:
            pass

        # Create edit dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Requirement")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text=f"Reagent: {display_name}").grid(row=0, column=0, columnspan=2, padx=10, pady=10, sticky="w")

        ttk.Label(dialog, text="Quantity:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        qty_var = tk.StringVar(value=str(quantity))
        ttk.Entry(dialog, textvariable=qty_var, width=15).grid(row=1, column=1, padx=10, pady=5, sticky="w")

        ttk.Label(dialog, text="Unit:").grid(row=2, column=0, sticky="w", padx=10, pady=5)
        unit_var = tk.StringVar(value=unit or "")

        mass_units = ["ng", "ug", "mg", "g"]
        vol_units = ["nL", "uL", "mL", "L"]
        
        if base_unit in mass_units:
            unit_combo = ttk.Combobox(dialog, textvariable=unit_var, values=mass_units, width=12, state='readonly')
        elif base_unit in vol_units:
            unit_combo = ttk.Combobox(dialog, textvariable=unit_var, values=vol_units, width=12, state='readonly')
        else:
            unit_combo = ttk.Combobox(dialog, textvariable=unit_var, values=[base_unit] if base_unit else [], width=12, state='disabled')
            
        unit_combo.grid(row=2, column=1, padx=10, pady=5, sticky="w")

        is_bulk_var = tk.BooleanVar(value=(samples_per_batch > 1))
        samples_var = tk.StringVar(value=str(samples_per_batch))

        def toggle_bulk_edit():
            if is_bulk_var.get():
                samples_entry.config(state='normal')
            else:
                samples_var.set("1")
                samples_entry.config(state='disabled')

        ttk.Checkbutton(dialog, text="Bulk Reagent", variable=is_bulk_var, command=toggle_bulk_edit).grid(row=3, column=0, sticky="w", padx=10, pady=5)
        samples_entry = ttk.Entry(dialog, textvariable=samples_var, width=15, state='normal' if is_bulk_var.get() else 'disabled')
        samples_entry.grid(row=3, column=1, padx=10, pady=5, sticky="w")

        ttk.Label(dialog, text="Notes:").grid(row=4, column=0, sticky="w", padx=10, pady=5)
        notes_var = tk.StringVar(value=notes or "")
        ttk.Entry(dialog, textvariable=notes_var, width=30).grid(row=4, column=1, padx=10, pady=5, sticky="w")

        def save_edit():
            try:
                qty_str = qty_var.get().strip()
                if ',' in qty_str:
                    proposed = qty_str.replace(',', '.')
                    if messagebox.askyesno("Convert Decimal", f"You entered '{qty_str}'. Convert to '{proposed}'?", parent=dialog):
                        qty_str = proposed
                        qty_var.set(proposed)
                    else:
                        return
                        
                new_qty = float(qty_str)
                new_unit = unit_var.get()
                try:
                    new_samples = int(samples_var.get())
                    if new_samples < 1:
                        new_samples = 1
                except ValueError:
                    new_samples = 1
                new_notes = notes_var.get().strip()

                self.cursor.execute('''
                    UPDATE service_requirements
                    SET quantity = ?, unit = ?, samples_per_batch = ?, notes = ?
                    WHERE id = ?
                ''', (new_qty, new_unit, new_samples, new_notes, req_id))
                self.conn.commit()
                self.load_requirements()
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Error", "Quantity must be a number", parent=dialog)
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not update requirement: {e}", parent=dialog)

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=15)
        ttk.Button(btn_frame, text="Save", command=save_edit).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=5)

    def remove_requirement(self):
        """Remove selected requirement"""
        selection = self.requirements_tree.selection()
        if not selection:
            messagebox.showerror("Error", "Please select a requirement to remove")
            return

        if messagebox.askyesno("Confirm Remove", "Remove this requirement?"):
            item = selection[0]
            req_id = self.requirements_tree.item(item, "tags")[0]

            try:
                self.cursor.execute('DELETE FROM service_requirements WHERE id = ?', (req_id,))
                self.conn.commit()
                self.load_requirements()
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not remove requirement: {e}")

    def load_requirements(self):
        """Load requirements for current service"""
        for item in self.requirements_tree.get_children():
            self.requirements_tree.delete(item)

        if not self.current_service_id:
            return

        # First get requirements from service database
        self.cursor.execute('''
            SELECT sr.id, sr.stock_item_id, sr.quantity, sr.unit, sr.samples_per_batch, sr.notes
            FROM service_requirements sr
            WHERE sr.service_id = ?
            ORDER BY sr.id
        ''', (self.current_service_id,))

        requirements = self.cursor.fetchall()

        # For each requirement, get stock item details from stock database
        try:
            for req_id, stock_item_id, quantity, unit, samples_per_batch, notes in requirements:
                self.cursor.execute('SELECT name, code, vendor FROM stock_db.stock WHERE id = ?', (stock_item_id,))
                stock_item = self.cursor.fetchone()
                
                if stock_item:
                    name, code, vendor = stock_item
                    display_name = f"{name}"
                    if code:
                        display_name += f" ({code})"
                    if vendor:
                        display_name += f" - {vendor}"

                    self.requirements_tree.insert("", "end", text=display_name,
                                                values=(quantity, unit or "", samples_per_batch or 1, notes or ""),
                                                tags=(req_id,))
            
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load requirements: {e}")

    def on_service_select(self, event):
        """Handle service selection"""
        if getattr(self, '_ignore_select', False):
            return
            
        selection = self.services_tree.selection()
        if not selection:
            return
            
        if not self.check_unsaved_changes():
            self._ignore_select = True
            if hasattr(self, 'last_selected_item') and self.last_selected_item:
                self.services_tree.selection_set(self.last_selected_item)
            else:
                self.services_tree.selection_remove(selection)
            self._ignore_select = False
            return

        item = selection[0]
        self.last_selected_item = item
        item_type, item_id = self.services_tree.item(item, "tags")

        if item_type == "service" and item_id:
            # Load service details
            self.cursor.execute('SELECT name, code, description, notes FROM services WHERE id = ?', (item_id,))
            result = self.cursor.fetchone()
            if result:
                self.service_name_var.set(result[0])
                self.service_code_var.set(result[1] or "")
                self.service_desc_text.delete("1.0", tk.END)
                if result[2]: self.service_desc_text.insert("1.0", result[2])
                self.service_notes_text.delete("1.0", tk.END)
                if result[3]: self.service_notes_text.insert("1.0", result[3])
                self.current_service_id = item_id
                self.load_requirements()
                self.load_costs()
                self.clean_state = self.get_current_form_state()
        else:
            # Category selected or no valid service
            self.clear_service_form()

    def clear_service_form(self):
        """Clear the service form"""
        self.service_name_var.set("")
        self.service_code_var.set("")
        self.service_desc_text.delete("1.0", tk.END)
        self.service_notes_text.delete("1.0", tk.END)
        self.current_service_id = None
        self.clear_requirements()
        self.clear_costs()
        self.clean_state = self.get_current_form_state()

    def clear_requirements(self):
        """Clear requirements display"""
        for item in self.requirements_tree.get_children():
            self.requirements_tree.delete(item)
        self.quantity_var.set("")
        self.unit_var.set("")
        if hasattr(self, 'unit_combo'):
            self.unit_combo.config(values=[], state='disabled')
        self.base_unit = ""
        if hasattr(self, 'is_bulk_var'):
            self.is_bulk_var.set(False)
            self.samples_var.set("1")
            self.samples_entry.config(state='disabled')
        self.notes_var.set("")

    def add_cost(self):
        """Add a non-reagent cost to current service"""
        if not self.current_service_id:
            messagebox.showerror("Error", "Please save the service first")
            return

        cost_type = self.cost_type_var.get().strip()
        if not cost_type:
            messagebox.showerror("Error", "Please select a cost type")
            return

        try:
            amt_str = self.cost_amount_var.get().strip()
            if ',' in amt_str:
                proposed = amt_str.replace(',', '.')
                if messagebox.askyesno("Convert Decimal", f"You entered '{amt_str}'. Convert to '{proposed}'?"):
                    amt_str = proposed
                    self.cost_amount_var.set(proposed)
                else:
                    return
                    
            amount = float(amt_str)
            try:
                samples_per_batch = int(self.cost_samples_var.get())
                if samples_per_batch < 1:
                    samples_per_batch = 1
            except ValueError:
                samples_per_batch = 1
            notes = self.cost_notes_var.get().strip()

            self.cursor.execute('''
                INSERT INTO service_costs (service_id, cost_type, cost, samples_per_batch, notes)
                VALUES (?, ?, ?, ?, ?)
            ''', (self.current_service_id, cost_type, amount, samples_per_batch, notes))

            self.conn.commit()
            self.load_costs()
            self.cost_type_var.set("")
            self.cost_amount_var.set("")
            self.cost_is_bulk_var.set(False)
            self.cost_samples_var.set("1")
            self.cost_samples_entry.config(state='disabled')
            self.cost_notes_var.set("")

        except ValueError:
            messagebox.showerror("Error", "Amount must be a number")
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not add cost: {e}")

    def edit_cost(self):
        """Edit selected cost"""
        selection = self.costs_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a cost to edit")
            return

        item = selection[0]
        cost_id = self.costs_tree.item(item, "tags")[0]

        self.cursor.execute('SELECT cost_type, cost, samples_per_batch, notes FROM service_costs WHERE id = ?', (cost_id,))
        cost_data = self.cursor.fetchone()
        if not cost_data:
            return

        cost_type, amount, samples_per_batch, notes = cost_data

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Cost")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Cost Type:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        type_var = tk.StringVar(value=cost_type)
        cost_options = ["Labor", "Maintenance", "Profit"]
        ttk.Combobox(dialog, textvariable=type_var, values=cost_options, width=15, state='readonly').grid(row=0, column=1, padx=10, pady=5, sticky="w")

        ttk.Label(dialog, text="Amount (R$):").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        amount_var = tk.StringVar(value=str(amount))
        ttk.Entry(dialog, textvariable=amount_var, width=15).grid(row=1, column=1, padx=10, pady=5, sticky="w")

        is_bulk_var = tk.BooleanVar(value=(samples_per_batch > 1))
        samples_var = tk.StringVar(value=str(samples_per_batch))

        def toggle_bulk_edit():
            if is_bulk_var.get():
                samples_entry.config(state='normal')
            else:
                samples_var.set("1")
                samples_entry.config(state='disabled')

        ttk.Checkbutton(dialog, text="Per Group", variable=is_bulk_var, command=toggle_bulk_edit).grid(row=2, column=0, sticky="w", padx=10, pady=5)
        samples_entry = ttk.Entry(dialog, textvariable=samples_var, width=15, state='normal' if is_bulk_var.get() else 'disabled')
        samples_entry.grid(row=2, column=1, padx=10, pady=5, sticky="w")

        ttk.Label(dialog, text="Notes:").grid(row=3, column=0, sticky="w", padx=10, pady=5)
        notes_var = tk.StringVar(value=notes or "")
        ttk.Entry(dialog, textvariable=notes_var, width=30).grid(row=3, column=1, padx=10, pady=5, sticky="w")

        def save_edit():
            try:
                new_type = type_var.get().strip()
                amt_str = amount_var.get().strip()
                if ',' in amt_str:
                    proposed = amt_str.replace(',', '.')
                    if messagebox.askyesno("Convert Decimal", f"You entered '{amt_str}'. Convert to '{proposed}'?", parent=dialog):
                        amt_str = proposed
                        amount_var.set(proposed)
                    else:
                        return
                        
                new_amount = float(amt_str)
                try:
                    new_samples = int(samples_var.get())
                    if new_samples < 1:
                        new_samples = 1
                except ValueError:
                    new_samples = 1
                new_notes = notes_var.get().strip()

                self.cursor.execute('''
                    UPDATE service_costs
                    SET cost_type = ?, cost = ?, samples_per_batch = ?, notes = ?
                    WHERE id = ?
                ''', (new_type, new_amount, new_samples, new_notes, cost_id))
                self.conn.commit()
                self.load_costs()
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Error", "Amount must be a number", parent=dialog)
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not update cost: {e}", parent=dialog)

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=15)
        ttk.Button(btn_frame, text="Save", command=save_edit).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=5)

    def remove_cost(self):
        """Remove selected cost"""
        selection = self.costs_tree.selection()
        if not selection:
            messagebox.showerror("Error", "Please select a cost to remove")
            return

        if messagebox.askyesno("Confirm Remove", "Remove this cost?"):
            item = selection[0]
            cost_id = self.costs_tree.item(item, "tags")[0]

            try:
                self.cursor.execute('DELETE FROM service_costs WHERE id = ?', (cost_id,))
                self.conn.commit()
                self.load_costs()
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not remove cost: {e}")

    def load_costs(self):
        """Load non-reagent costs for current service"""
        for item in self.costs_tree.get_children():
            self.costs_tree.delete(item)

        if not self.current_service_id:
            return

        try:
            self.cursor.execute('''
                SELECT id, cost_type, cost, samples_per_batch, notes
                FROM service_costs
                WHERE service_id = ?
                ORDER BY id
            ''', (self.current_service_id,))

            for cost_id, cost_type, cost, samples_per_batch, notes in self.cursor.fetchall():
                self.costs_tree.insert("", "end", text=cost_type,
                                       values=(f"R${cost:.2f}", samples_per_batch or 1, notes or ""),
                                       tags=(cost_id,))
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load costs: {e}")

    def clear_costs(self):
        """Clear costs display"""
        for item in self.costs_tree.get_children():
            self.costs_tree.delete(item)
        if hasattr(self, 'cost_type_var'):
            self.cost_type_var.set("")
            self.cost_amount_var.set("")
            self.cost_is_bulk_var.set(False)
            self.cost_samples_var.set("1")
            self.cost_samples_entry.config(state='disabled')
            self.cost_notes_var.set("")

    def on_closing(self):
        """Handle window closing"""
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.destroy()

    # Drag and drop functionality
    def on_drag_start(self, event):
        """Start drag operation"""
        item = self.services_tree.identify_row(event.y)
        if item:
            item_type, item_id = self.services_tree.item(item, "tags")
            if item_type == "service":  # Only allow dragging services
                self.drag_item = item
                self.drag_occurred = False
                self.drag_item_type = item_type
                self.drag_item_id = item_id
                # Store original selection to restore later
                self.original_selection = self.services_tree.selection()

    def on_drag_motion(self, event):
        """Handle drag motion with visual feedback and auto-expansion"""
        if not hasattr(self, 'drag_item') or not self.drag_item:
            return

        self.drag_occurred = True
        # Find item under cursor
        current_item = self.services_tree.identify_row(event.y)
        
        # Clear previous visual feedback
        if hasattr(self, 'drag_highlight') and self.drag_highlight:
            self.services_tree.selection_remove(self.drag_highlight)
        
        if current_item:
            item_type, item_id = self.services_tree.item(current_item, "tags")
            
            # Auto-expand categories when hovering over them
            if item_type == "category":
                self.services_tree.item(current_item, open=True)
                # Highlight the category to show it's a valid drop target
                self.services_tree.selection_add(current_item)
                self.drag_highlight = current_item
            elif item_type == "service" and current_item != self.drag_item:
                # Show that services are not valid drop targets by briefly highlighting
                # (this gives feedback that the drop won't work here)
                self.services_tree.selection_add(current_item)
                self.drag_highlight = current_item
                # Could add a timer to remove this highlight after a brief moment
            else:
                self.drag_highlight = None
        else:
            self.drag_highlight = None

    def on_drag_end(self, event):
        """Complete drag operation"""
        if not hasattr(self, 'drag_item') or not self.drag_item:
            return
            
        if not getattr(self, 'drag_occurred', False):
            self.drag_item = None
            self.drag_item_type = None
            self.drag_item_id = None
            if hasattr(self, 'drag_highlight'):
                self.drag_highlight = None
            if hasattr(self, 'original_selection'):
                delattr(self, 'original_selection')
            return

        # Clear visual feedback
        if hasattr(self, 'drag_highlight') and self.drag_highlight:
            self.services_tree.selection_remove(self.drag_highlight)
        
        # Restore original selection if it was a service
        if hasattr(self, 'original_selection'):
            try:
                item_type, _ = self.services_tree.item(self.original_selection[0], "tags") if self.original_selection else (None, None)
                if item_type == "service":
                    self.services_tree.selection_set(self.original_selection)
            except:
                pass

        # Find the target item
        target_item = self.services_tree.identify_row(event.y)
        if target_item and target_item != self.drag_item:
            target_type, target_id = self.services_tree.item(target_item, "tags")

            if target_type == "category" and self.drag_item_type == "service":
                # Moving service to a category
                try:
                    if target_id is None or target_id == 'None':
                        # Moving to "Uncategorized" - set category_id to NULL
                        self.cursor.execute(
                            "UPDATE services SET category_id = NULL WHERE id = ?",
                            (self.drag_item_id,)
                        )
                    else:
                        # Moving to a regular category
                        self.cursor.execute(
                            "UPDATE services SET category_id = ? WHERE id = ?",
                            (target_id, self.drag_item_id)
                        )
                    self.conn.commit()
                    self.load_services()
                except sqlite3.Error as e:
                    messagebox.showerror("Database Error", f"Could not move service: {e}")
                    # Debug: print the actual error
                    print(f"Debug: target_id={target_id}, drag_item_id={self.drag_item_id}, error: {e}")

        # Clear drag state
        self.drag_item = None
        self.drag_item_type = None
        self.drag_item_id = None
        if hasattr(self, 'drag_highlight'):
            self.drag_highlight = None
        if hasattr(self, 'original_selection'):
            delattr(self, 'original_selection')

    def on_tree_motion(self, event):
        item = self.services_tree.identify_row(event.y)
        column = self.services_tree.identify_column(event.x)
        
        if item and column == "#2":
            values = self.services_tree.item(item, "values")
            if len(values) > 1 and values[1]:
                desc_text = values[1]
                
                if getattr(self, 'tooltip_item', None) == item:
                    return
                
                self.hide_tooltip()
                self.tooltip_item = item
                self.show_tooltip(event.x_root, event.y_root, desc_text)
                return

        if getattr(self, 'tooltip_item', None) is not None:
            self.hide_tooltip()
            self.tooltip_item = None

    def on_tree_leave(self, event):
        self.hide_tooltip()
        self.tooltip_item = None

    def show_tooltip(self, x, y, text):
        self.tooltip = tk.Toplevel(self.root)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x+15}+{y+15}")
        
        # Use a themed label for consistency, with a border for the tooltip effect
        label = ttk.Label(self.tooltip, text=text, justify='left',
                         wraplength=300, padding=5, relief="solid", borderwidth=1)
        label.pack()

    def hide_tooltip(self):
        if getattr(self, 'tooltip', None):
            self.tooltip.destroy()
            self.tooltip = None

if __name__ == "__main__":
    root = tk.Tk()
    app = ServiceManager(root)
    root.mainloop()
    def edit_cost(self):
        """Edit selected cost"""
        selection = self.costs_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a cost to edit")
            return

        item = selection[0]
        cost_id = self.costs_tree.item(item, "tags")[0]

        self.cursor.execute('SELECT cost_type, cost, samples_per_batch, notes FROM service_costs WHERE id = ?', (cost_id,))
        cost_data = self.cursor.fetchone()
        if not cost_data:
            return

        cost_type, amount, samples_per_batch, notes = cost_data

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Cost")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Cost Type:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        type_var = tk.StringVar(value=cost_type)
        cost_options = ["Labor", "Maintenance", "Profit"]
        ttk.Combobox(dialog, textvariable=type_var, values=cost_options, width=15, state='readonly').grid(row=0, column=1, padx=10, pady=5, sticky="w")

        ttk.Label(dialog, text="Amount (R$):").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        amount_var = tk.StringVar(value=str(amount))
        ttk.Entry(dialog, textvariable=amount_var, width=15).grid(row=1, column=1, padx=10, pady=5, sticky="w")

        is_bulk_var = tk.BooleanVar(value=(samples_per_batch > 1))
        samples_var = tk.StringVar(value=str(samples_per_batch))

        def toggle_bulk_edit():
            if is_bulk_var.get():
                samples_entry.config(state='normal')
            else:
                samples_var.set("1")
                samples_entry.config(state='disabled')

        ttk.Checkbutton(dialog, text="Per Group", variable=is_bulk_var, command=toggle_bulk_edit).grid(row=2, column=0, sticky="w", padx=10, pady=5)
        samples_entry = ttk.Entry(dialog, textvariable=samples_var, width=15, state='normal' if is_bulk_var.get() else 'disabled')
        samples_entry.grid(row=2, column=1, padx=10, pady=5, sticky="w")

        ttk.Label(dialog, text="Notes:").grid(row=3, column=0, sticky="w", padx=10, pady=5)
        notes_var = tk.StringVar(value=notes or "")
        ttk.Entry(dialog, textvariable=notes_var, width=30).grid(row=3, column=1, padx=10, pady=5, sticky="w")

        def save_edit():
            try:
                new_type = type_var.get().strip()
                new_amount = float(amount_var.get())
                try:
                    new_samples = int(samples_var.get())
                    if new_samples < 1:
                        new_samples = 1
                except ValueError:
                    new_samples = 1
                new_notes = notes_var.get().strip()

                self.cursor.execute('''
                    UPDATE service_costs
                    SET cost_type = ?, cost = ?, samples_per_batch = ?, notes = ?
                    WHERE id = ?
                ''', (new_type, new_amount, new_samples, new_notes, cost_id))
                self.conn.commit()
                self.load_costs()
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Error", "Amount must be a number", parent=dialog)
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not update cost: {e}", parent=dialog)

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=15)
        ttk.Button(btn_frame, text="Save", command=save_edit).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=5)

    def remove_cost(self):
        """Remove selected cost"""
        selection = self.costs_tree.selection()
        if not selection:
            messagebox.showerror("Error", "Please select a cost to remove")
            return

        if messagebox.askyesno("Confirm Remove", "Remove this cost?"):
            item = selection[0]
            cost_id = self.costs_tree.item(item, "tags")[0]

            try:
                self.cursor.execute('DELETE FROM service_costs WHERE id = ?', (cost_id,))
                self.conn.commit()
                self.load_costs()
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not remove cost: {e}")

    def load_costs(self):
        """Load non-reagent costs for current service"""
        for item in self.costs_tree.get_children():
            self.costs_tree.delete(item)

        if not self.current_service_id:
            return

        try:
            self.cursor.execute('''
                SELECT id, cost_type, cost, samples_per_batch, notes
                FROM service_costs
                WHERE service_id = ?
                ORDER BY id
            ''', (self.current_service_id,))

            for cost_id, cost_type, cost, samples_per_batch, notes in self.cursor.fetchall():
                self.costs_tree.insert("", "end", text=cost_type,
                                       values=(f"R${cost:.2f}", samples_per_batch or 1, notes or ""),
                                       tags=(cost_id,))
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load costs: {e}")

    def clear_costs(self):
        """Clear costs display"""
        for item in self.costs_tree.get_children():
            self.costs_tree.delete(item)
        if hasattr(self, 'cost_type_var'):
            self.cost_type_var.set("")
            self.cost_amount_var.set("")
            self.cost_is_bulk_var.set(False)
            self.cost_samples_var.set("1")
            self.cost_samples_entry.config(state='disabled')
            self.cost_notes_var.set("")

    def on_closing(self):
        """Handle window closing"""
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.destroy()

    # Drag and drop functionality
    def on_drag_start(self, event):
        """Start drag operation"""
        item = self.services_tree.identify_row(event.y)
        if item:
            item_type, item_id = self.services_tree.item(item, "tags")
            if item_type == "service":  # Only allow dragging services
                self.drag_item = item
                self.drag_item_type = item_type
                self.drag_item_id = item_id
                # Store original selection to restore later
                self.original_selection = self.services_tree.selection()

    def on_drag_motion(self, event):
        """Handle drag motion with visual feedback and auto-expansion"""
        if not hasattr(self, 'drag_item') or not self.drag_item:
            return

        # Find item under cursor
        current_item = self.services_tree.identify_row(event.y)
        
        # Clear previous visual feedback
        if hasattr(self, 'drag_highlight') and self.drag_highlight:
            self.services_tree.selection_remove(self.drag_highlight)
        
        if current_item:
            item_type, item_id = self.services_tree.item(current_item, "tags")
            
            # Auto-expand categories when hovering over them
            if item_type == "category":
                self.services_tree.item(current_item, open=True)
                # Highlight the category to show it's a valid drop target
                self.services_tree.selection_add(current_item)
                self.drag_highlight = current_item
            elif item_type == "service" and current_item != self.drag_item:
                # Show that services are not valid drop targets by briefly highlighting
                # (this gives feedback that the drop won't work here)
                self.services_tree.selection_add(current_item)
                self.drag_highlight = current_item
                # Could add a timer to remove this highlight after a brief moment
            else:
                self.drag_highlight = None
        else:
            self.drag_highlight = None

    def on_drag_end(self, event):
        """Complete drag operation"""
        if not hasattr(self, 'drag_item') or not self.drag_item:
            return

        # Clear visual feedback
        if hasattr(self, 'drag_highlight') and self.drag_highlight:
            self.services_tree.selection_remove(self.drag_highlight)
        
        # Restore original selection if it was a service
        if hasattr(self, 'original_selection'):
            try:
                item_type, _ = self.services_tree.item(self.original_selection[0], "tags") if self.original_selection else (None, None)
                if item_type == "service":
                    self.services_tree.selection_set(self.original_selection)
            except:
                pass

        # Find the target item
        target_item = self.services_tree.identify_row(event.y)
        if target_item and target_item != self.drag_item:
            target_type, target_id = self.services_tree.item(target_item, "tags")

            if target_type == "category" and self.drag_item_type == "service":
                # Moving service to a category
                try:
                    if target_id is None or target_id == 'None':
                        # Moving to "Uncategorized" - set category_id to NULL
                        self.cursor.execute(
                            "UPDATE services SET category_id = NULL WHERE id = ?",
                            (self.drag_item_id,)
                        )
                    else:
                        # Moving to a regular category
                        self.cursor.execute(
                            "UPDATE services SET category_id = ? WHERE id = ?",
                            (target_id, self.drag_item_id)
                        )
                    self.conn.commit()
                    self.load_services()
                except sqlite3.Error as e:
                    messagebox.showerror("Database Error", f"Could not move service: {e}")
                    # Debug: print the actual error
                    print(f"Debug: target_id={target_id}, drag_item_id={self.drag_item_id}, error: {e}")

        # Clear drag state
        self.drag_item = None
        self.drag_item_type = None
        self.drag_item_id = None
        if hasattr(self, 'drag_highlight'):
            self.drag_highlight = None
        if hasattr(self, 'original_selection'):
            delattr(self, 'original_selection')

    def on_tree_motion(self, event):
        item = self.services_tree.identify_row(event.y)
        column = self.services_tree.identify_column(event.x)
        
        if item and column == "#2":
            values = self.services_tree.item(item, "values")
            if len(values) > 1 and values[1]:
                desc_text = values[1]
                
                if getattr(self, 'tooltip_item', None) == item:
                    return
                
                self.hide_tooltip()
                self.tooltip_item = item
                self.show_tooltip(event.x_root, event.y_root, desc_text)
                return

        if getattr(self, 'tooltip_item', None) is not None:
            self.hide_tooltip()
            self.tooltip_item = None

    def on_tree_leave(self, event):
        self.hide_tooltip()
        self.tooltip_item = None

    def show_tooltip(self, x, y, text):
        self.tooltip = tk.Toplevel(self.root)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x+15}+{y+15}")
        
        label = ttk.Label(self.tooltip, text=text, justify='left',
                         relief='solid', borderwidth=1,
                         wraplength=300, padding=5)
        label.pack()

    def hide_tooltip(self):
        if getattr(self, 'tooltip', None):
            self.tooltip.destroy()
            self.tooltip = None

if __name__ == "__main__":
    root = tk.Tk()
    app = ServiceManager(root)
    root.mainloop()