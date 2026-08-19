import os
import re
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime

# When frozen by PyInstaller, __file__ resolves inside the temporary
# extraction folder rather than the exe's real folder, so paths built from
# it point at a throwaway location. Use the exe's directory instead.
_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

class ClientManager:
    def __init__(self, root, current_user="Unknown"):
        self.root = root
        self.current_user = current_user
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.title("Quarium Client Manager")
            self.root.geometry("1000x700")

        # Client database
        self.db_path = os.path.join(_BASE_DIR, 'clients.db')

        self.init_db()
        self.create_ui()
        self.load_clients()

        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def init_db(self):
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        
        # Enable foreign key constraints
        self.cursor.execute("PRAGMA foreign_keys = ON")

        # Companies table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                code INTEGER NOT NULL UNIQUE,
                created_at TEXT,
                updated_by TEXT,
                cnpj TEXT,
                address TEXT,
                is_legal_entity INTEGER DEFAULT 0
            )
        ''')

        # Clients table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                address TEXT,
                funding_code TEXT,
                is_academic INTEGER DEFAULT 0,
                company_id INTEGER,
                created_at TEXT,
                updated_at TEXT,
                cpf TEXT,
                rg TEXT,
                rg_issuer TEXT,
                nationality TEXT,
                marital_status TEXT,
                profession TEXT,
                updated_by TEXT,
                title TEXT,
                FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE SET NULL
            )
        ''')

        self.cursor.execute("PRAGMA table_info(clients)")
        client_columns = [column[1] for column in self.cursor.fetchall()]
        if 'phone' not in client_columns:
            self.cursor.execute('ALTER TABLE clients ADD COLUMN phone TEXT')
        if 'updated_by' not in client_columns:
            self.cursor.execute('ALTER TABLE clients ADD COLUMN updated_by TEXT')
            try: self.cursor.execute('ALTER TABLE companies ADD COLUMN updated_by TEXT')
            except: pass
        
        # Add new fields for contract generation
        new_client_cols = ['cpf', 'rg', 'rg_issuer', 'nationality', 'marital_status', 'profession']
        for col in new_client_cols:
            if col not in client_columns:
                self.cursor.execute(f'ALTER TABLE clients ADD COLUMN {col} TEXT')
        if 'title' not in client_columns:
            self.cursor.execute('ALTER TABLE clients ADD COLUMN title TEXT')

        self.cursor.execute("PRAGMA table_info(companies)")
        company_columns = [column[1] for column in self.cursor.fetchall()]
        new_company_cols = ['cnpj', 'address', 'is_legal_entity']
        for col in new_company_cols:
            if col not in company_columns:
                self.cursor.execute(f'ALTER TABLE companies ADD COLUMN {col} TEXT' if col != 'is_legal_entity' else 'ALTER TABLE companies ADD COLUMN is_legal_entity INTEGER DEFAULT 0')


        self.conn.commit()

    def create_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        # Left panel - Client & Company Tree
        left_panel = ttk.LabelFrame(main_frame, text="Clients by Company", padding=10)
        left_panel.pack(side="left", fill="y", padx=(0, 10))

        # Treeview (hierarchical with companies)
        self.tree = ttk.Treeview(left_panel, columns=("Email", "Academic"), height=20)
        self.tree.heading("#0", text="Name")
        self.tree.heading("Email", text="Email")
        self.tree.heading("Academic", text="Academic")
        self.tree.column("#0", width=180)
        self.tree.column("Email", width=150)
        self.tree.column("Academic", width=80)
        self.tree.pack(fill="y", expand=True)

        # Tree buttons
        btn_frame = ttk.Frame(left_panel)
        btn_frame.pack(fill="x", pady=10)
        ttk.Button(btn_frame, text="Add Client", command=self.add_client).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="Add Company", command=self.add_company).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="Edit Selected", command=self.edit_selected).pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="Delete Selected", command=self.delete_selected).pack(fill="x", pady=2)

        # Right panel - Client Details form
        right_panel = ttk.LabelFrame(main_frame, text="Client Details", padding=10)
        right_panel.pack(side="right", fill="both", expand=True)

        info_frame = ttk.Frame(right_panel)
        info_frame.pack(fill="x", pady=(0, 10))

        # --- Client Type Radio Buttons ---
        type_frame = ttk.Frame(info_frame)
        type_frame.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        self.client_type_var = tk.StringVar(value="PF")
        pf_radio = ttk.Radiobutton(type_frame, text="Pessoa Física (Individual)", variable=self.client_type_var, value="PF", command=self.toggle_client_type_fields)
        pf_radio.pack(side="left", padx=5)
        pj_radio = ttk.Radiobutton(type_frame, text="Pessoa Jurídica (Company)", variable=self.client_type_var, value="PJ", command=self.toggle_client_type_fields)
        pj_radio.pack(side="left", padx=5)

        ttk.Label(info_frame, text="Title (e.g., Dr., Ms.):").grid(row=1, column=0, sticky="w", pady=5)
        self.title_var = tk.StringVar()
        ttk.Entry(info_frame, textvariable=self.title_var, width=10).grid(row=1, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(info_frame, text="Client Name * :").grid(row=2, column=0, sticky="w", pady=5)
        self.name_var = tk.StringVar()
        ttk.Entry(info_frame, textvariable=self.name_var, width=40).grid(row=2, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(info_frame, text="Client Email * :").grid(row=3, column=0, sticky="w", pady=5)
        self.email_var = tk.StringVar()
        ttk.Entry(info_frame, textvariable=self.email_var, width=40).grid(row=3, column=1, padx=5, pady=5, sticky="w")

        # --- Pessoa Física Fields ---
        self.pf_widgets = []
        self.pf_labels = []

        self.pf_labels.append(ttk.Label(info_frame, text="CPF:"))
        self.pf_labels[-1].grid(row=4, column=0, sticky="w", pady=5)
        self.cpf_var = tk.StringVar()
        self.pf_widgets.append(ttk.Entry(info_frame, textvariable=self.cpf_var, width=40))
        self.pf_widgets[-1].grid(row=4, column=1, padx=5, pady=5, sticky="w")

        self.pf_labels.append(ttk.Label(info_frame, text="RG:"))
        self.pf_labels[-1].grid(row=5, column=0, sticky="w", pady=5)
        self.rg_var = tk.StringVar()
        self.pf_widgets.append(ttk.Entry(info_frame, textvariable=self.rg_var, width=20))
        self.pf_widgets[-1].grid(row=5, column=1, padx=5, pady=5, sticky="w")

        self.pf_labels.append(ttk.Label(info_frame, text="RG Issuer:"))
        self.pf_labels[-1].grid(row=5, column=2, sticky="w", pady=5)
        self.rg_issuer_var = tk.StringVar()
        self.pf_widgets.append(ttk.Entry(info_frame, textvariable=self.rg_issuer_var, width=10))
        self.pf_widgets[-1].grid(row=5, column=3, padx=5, pady=5, sticky="w")

        self.pf_labels.append(ttk.Label(info_frame, text="Nationality:"))
        self.pf_labels[-1].grid(row=6, column=0, sticky="w", pady=5)
        self.nationality_var = tk.StringVar()
        self.pf_widgets.append(ttk.Entry(info_frame, textvariable=self.nationality_var, width=40))
        self.pf_widgets[-1].grid(row=6, column=1, padx=5, pady=5, sticky="w")

        self.pf_labels.append(ttk.Label(info_frame, text="Marital Status:"))
        self.pf_labels[-1].grid(row=7, column=0, sticky="w", pady=5)
        self.marital_status_var = tk.StringVar()
        marital_options = ["Solteiro(a)", "Casado(a)", "Divorciado(a)", "Viúvo(a)", "União Estável"]
        self.marital_status_combo = ttk.Combobox(info_frame, textvariable=self.marital_status_var, values=marital_options, width=37) # This is also a widget
        self.pf_widgets.append(self.marital_status_combo)
        self.pf_widgets[-1].grid(row=7, column=1, padx=5, pady=5, sticky="w")

        self.pf_labels.append(ttk.Label(info_frame, text="Profession:"))
        self.pf_labels[-1].grid(row=8, column=0, sticky="w", pady=5)
        self.profession_var = tk.StringVar()
        self.pf_widgets.append(ttk.Entry(info_frame, textvariable=self.profession_var, width=40))
        self.pf_widgets[-1].grid(row=8, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(info_frame, text="Phone Number:").grid(row=9, column=0, sticky="w", pady=5)
        self.phone_var = tk.StringVar()
        ttk.Entry(info_frame, textvariable=self.phone_var, width=40).grid(row=9, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(info_frame, text="Client Address:").grid(row=10, column=0, sticky="nw", pady=5) # This is for the company address
        self.address_text = tk.Text(info_frame, width=30, height=4,
                                    background="#F0F0F0", relief="flat", borderwidth=1)
        self.address_text.grid(row=10, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(info_frame, text="Funding Code:").grid(row=11, column=0, sticky="w", pady=5)
        self.funding_code_var = tk.StringVar()
        ttk.Entry(info_frame, textvariable=self.funding_code_var, width=40).grid(row=11, column=1, padx=5, pady=5, sticky="w")

        self.is_academic_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(info_frame, text="Academic Use", variable=self.is_academic_var).grid(row=12, column=0, columnspan=2, sticky="w", pady=10)

        # Save button docked to bottom
        save_frame = ttk.Frame(right_panel)
        save_frame.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(save_frame, text="Save Client", command=self.save_client).pack(side="right", padx=5)

        # Make the form expandable
        info_frame.columnconfigure(1, weight=1)
        info_frame.columnconfigure(3, weight=1)

        self.current_client_id = None

        # Bind events
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<ButtonPress-1>", self.on_drag_start)
        self.tree.bind("<ButtonRelease-1>", self.on_drag_end)
        self.tree.bind("<B1-Motion>", self.on_drag_motion)

        self.toggle_client_type_fields() # Set initial visibility

    def toggle_client_type_fields(self):
        """Shows or hides fields based on the client type radio button."""
        if self.client_type_var.get() == "PF": # Pessoa Física
            for widget in self.pf_widgets + self.pf_labels:
                widget.grid()
        else: # Pessoa Jurídica
            for widget in self.pf_widgets + self.pf_labels:
                widget.grid_remove()


    def load_clients(self):
        """Load all clients categorized by their companies"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Load companies
        self.cursor.execute("SELECT id, name, code FROM companies ORDER BY name")
        for comp_id, name, code in self.cursor.fetchall():
            sanitized_name = name.replace('\n', ' ')
            node = self.tree.insert("", "end", text=f"{sanitized_name} (Code: {code})", tags=("company", str(comp_id)), open=True)  # type: ignore
            
            # Load clients for this company
            self.cursor.execute("SELECT id, name, email, is_academic FROM clients WHERE company_id = ? ORDER BY name", (comp_id,))
            for client_id, c_name, c_email, is_acad in self.cursor.fetchall():
                acad_text = "Yes" if is_acad else "No"
                sanitized_c_name = c_name.replace('\n', ' ')
                self.tree.insert(node, "end", text=sanitized_c_name, values=(c_email, acad_text), tags=("client", str(client_id)))  # type: ignore

        # Load uncategorized clients
        uncat_node = self.tree.insert("", "end", text="Uncategorized", tags=("company", ""), open=True)  # type: ignore
        self.cursor.execute("SELECT id, name, email, is_academic FROM clients WHERE company_id IS NULL ORDER BY name")
        for client_id, c_name, c_email, is_acad in self.cursor.fetchall():
            acad_text = "Yes" if is_acad else "No"
            sanitized_c_name = c_name.replace('\n', ' ')
            self.tree.insert(uncat_node, "end", text=sanitized_c_name, values=(c_email, acad_text), tags=("client", str(client_id)))  # type: ignore

    def add_client(self):
        """Prepare form for new client entry"""
        self.clear_form()

    def add_company(self):
        """Add a new company with a number code"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Company")
        dialog.transient(self.root)
        dialog.grab_set()
        
        ttk.Label(dialog, text="Company Name:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        name_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=name_var, width=25).grid(row=0, column=1, padx=10, pady=5)
        
        # Calculate next unused integer code
        self.cursor.execute("SELECT MAX(code) FROM companies")
        max_code = self.cursor.fetchone()[0]
        next_code = (max_code or 0) + 1
        
        ttk.Label(dialog, text="Company Code:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        code_var = tk.StringVar(value=str(next_code))
        ttk.Entry(dialog, textvariable=code_var, width=25).grid(row=1, column=1, padx=10, pady=5)
        
        def save():
            name = name_var.get().strip()
            try:
                code = int(code_var.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Code must be an integer", parent=dialog)
                return
                
            if not name:
                messagebox.showerror("Error", "Company name is required", parent=dialog)
                return
                
            try:
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.cursor.execute("INSERT INTO companies (name, code, created_at, updated_by) VALUES (?, ?, ?, ?)", (name, code, now, self.current_user))
                self.conn.commit()
                self.load_clients()
                dialog.destroy()
            except sqlite3.IntegrityError as e:
                if "code" in str(e).lower():
                    messagebox.showerror("Error", f"Company code '{code}' is already used by another company.", parent=dialog)
                else:
                    messagebox.showerror("Error", "A company with this name already exists", parent=dialog)
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not add company: {e}", parent=dialog)

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=15)
        ttk.Button(btn_frame, text="Save", command=save).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=5)

    def edit_selected(self):
        """Edit either a client or a company"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select an item to edit")
            return
        item_type, item_id = self.tree.item(selection[0], "tags")
        if item_type == "company":
            self.edit_company(item_id)
        elif item_type == "client":
            self.load_client_to_form(item_id)

    def edit_company(self, company_id):
        """Edit company details"""
        if not company_id or company_id == 'None':
            messagebox.showerror("Error", "Cannot edit the Uncategorized folder")
            return

        self.cursor.execute("SELECT name, code FROM companies WHERE id = ?", (company_id,))
        result = self.cursor.fetchone()
        if not result:
            return
            
        current_name, current_code = result

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Company")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Company Name:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        name_var = tk.StringVar(value=current_name)
        ttk.Entry(dialog, textvariable=name_var, width=25).grid(row=0, column=1, padx=10, pady=5)

        ttk.Label(dialog, text="Company Code:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        code_var = tk.StringVar(value=str(current_code))
        ttk.Entry(dialog, textvariable=code_var, width=25).grid(row=1, column=1, padx=10, pady=5)

        # Add new fields for companies
        self.cursor.execute("SELECT cnpj, address, is_legal_entity FROM companies WHERE id = ?", (company_id,))
        comp_extra = self.cursor.fetchone()
        cnpj, address, is_legal = comp_extra if comp_extra else ("", "", 0)

        is_legal_var = tk.BooleanVar(value=bool(is_legal))
        ttk.Checkbutton(dialog, text="Is a Legal Entity (for contracts)", variable=is_legal_var).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=5)

        ttk.Label(dialog, text="CNPJ:").grid(row=3, column=0, sticky="w", padx=10, pady=5)
        cnpj_var = tk.StringVar(value=cnpj or "")
        ttk.Entry(dialog, textvariable=cnpj_var, width=25).grid(row=3, column=1, padx=10, pady=5)

        ttk.Label(dialog, text="Address:").grid(row=4, column=0, sticky="nw", padx=10, pady=5)
        address_text = tk.Text(dialog, width=30, height=3)
        address_text.grid(row=4, column=1, padx=10, pady=5)
        if address:
            address_text.insert("1.0", address)


        def save():
            new_name = name_var.get().strip()
            try:
                new_code = int(code_var.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Code must be an integer", parent=dialog)
                return

            if not new_name:
                messagebox.showerror("Error", "Company name is required", parent=dialog)
                return

            new_cnpj = cnpj_var.get().strip()
            if new_cnpj and not self._is_valid_cnpj(new_cnpj):
                messagebox.showerror("Invalid CNPJ", "The CNPJ entered is not valid. Please check the number.", parent=dialog)
                return


            new_cnpj = cnpj_var.get().strip()
            new_address = address_text.get("1.0", tk.END).strip()
            new_is_legal = 1 if is_legal_var.get() else 0

            try:
                self.cursor.execute("UPDATE companies SET name = ?, code = ?, cnpj = ?, address = ?, is_legal_entity = ?, updated_by = ? WHERE id = ?", 
                                    (new_name, new_code, new_cnpj, new_address, new_is_legal, self.current_user, company_id))
                self.conn.commit()
                self.load_clients()
                dialog.destroy()
            except sqlite3.IntegrityError as e:
                if "code" in str(e).lower():
                    messagebox.showerror("Error", f"Company code '{new_code}' is already used by another company.", parent=dialog)
                else:
                    messagebox.showerror("Error", "A company with this name already exists", parent=dialog)
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not update company: {e}", parent=dialog)

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=15)
        ttk.Button(btn_frame, text="Save", command=save).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=5)

    def delete_selected(self):
        """Delete either a client or a company"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select an item to delete")
            return
        item_type, item_id = self.tree.item(selection[0], "tags")
        
        if item_type == "company":
            if not item_id or item_id == 'None':
                messagebox.showerror("Error", "Cannot delete the Uncategorized folder")
                return
            self.cursor.execute("SELECT COUNT(*) FROM clients WHERE company_id = ?", (item_id,))
            count = self.cursor.fetchone()[0]
            if count > 0:
                if not messagebox.askyesno("Confirm Delete", f"This company contains {count} client(s). Deleting it will move them to 'Uncategorized'. Continue?"):
                    return
            try:
                self.cursor.execute("UPDATE clients SET company_id = NULL WHERE company_id = ?", (item_id,))
                self.cursor.execute("DELETE FROM companies WHERE id = ?", (item_id,))
                self.conn.commit()
                self.load_clients()
            except sqlite3.Error as e:
                messagebox.showerror("Database Error", f"Could not delete company: {e}")
                
        elif item_type == "client":
            if messagebox.askyesno("Confirm Delete", "Are you sure you want to delete this client?"):
                try:
                    self.cursor.execute("DELETE FROM clients WHERE id = ?", (item_id,))
                    self.conn.commit()
                    self.load_clients()
                    self.clear_form()
                except sqlite3.Error as e:
                    messagebox.showerror("Database Error", f"Could not delete client: {e}")

    def load_client_to_form(self, client_id):
        """Load existing client details onto the UI inputs"""
        self.cursor.execute("SELECT name, email, phone, address, funding_code, is_academic, cpf, rg, rg_issuer, nationality, marital_status, profession, title FROM clients WHERE id = ?", (client_id,))
        result = self.cursor.fetchone()
        if result:
            (name, email, phone, address, funding_code, is_academic, 
             cpf, rg, rg_issuer, nationality, marital_status, profession, title) = result # type: ignore
            self.name_var.set(name)
            self.email_var.set(email)

            # Determine client type based on whether CPF exists
            if cpf:
                self.client_type_var.set("PF")
            else:
                self.client_type_var.set("PJ")
            self.toggle_client_type_fields()

            self.cpf_var.set(cpf or "")
            self.rg_var.set(rg or "")
            self.rg_issuer_var.set(rg_issuer or "")
            self.nationality_var.set(nationality or "")
            self.marital_status_var.set(marital_status or "")
            self.profession_var.set(profession or "")
            self.phone_var.set(phone or "")
            self.title_var.set(title or "")
            self.address_text.delete("1.0", tk.END)
            if address:
                self.address_text.insert(tk.END, address)
            self.funding_code_var.set(funding_code or "")
            self.is_academic_var.set(bool(is_academic))
            self.current_client_id = client_id

    def save_client(self):
        """Persist UI Client form to DB"""
        name = self.name_var.get().strip()
        email = self.email_var.get().strip()
        title = self.title_var.get().strip()

        if self.client_type_var.get() == "PF":
            cpf = self.cpf_var.get().strip()
            rg = self.rg_var.get().strip()
            rg_issuer = self.rg_issuer_var.get().strip()
            nationality = self.nationality_var.get().strip()
            marital_status = self.marital_status_var.get().strip()
            profession = self.profession_var.get().strip()
        else: # Clear PF fields if saving as PJ
            cpf, rg, rg_issuer, nationality, marital_status, profession = "", "", "", "", "", ""

        phone = self.phone_var.get().strip()
        address = self.address_text.get("1.0", tk.END).strip()
        funding_code = self.funding_code_var.get().strip()
        is_academic = 1 if self.is_academic_var.get() else 0

        

        if not name:
            messagebox.showerror("Error", "Client Name is required.")
            return

        # CPF validation
        if cpf and not self._is_valid_cpf(cpf):
            messagebox.showerror("Invalid CPF", "The CPF entered is not valid. Please check the number.")
            return

        # Email validation regex
        if email and not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
            messagebox.showerror("Error", "Please enter a valid email address format")
            return

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            if self.current_client_id:
                self.cursor.execute('''
                    UPDATE clients SET name = ?, email = ?, phone = ?, address = ?, funding_code = ?, is_academic = ?, title = ?,
                    cpf = ?, rg = ?, rg_issuer = ?, nationality = ?, marital_status = ?, profession = ?, updated_at = ?, updated_by = ?
                    WHERE id = ?
                ''', (name, email, phone, address, funding_code, is_academic, title, cpf, rg, rg_issuer, nationality, marital_status, profession, now, self.current_user, self.current_client_id))
            else:
                self.cursor.execute('''
                    INSERT INTO clients (name, email, phone, address, funding_code, is_academic, title, cpf, rg, 
                    rg_issuer, nationality, marital_status, profession, created_at, updated_at, updated_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (name, email, phone, address, funding_code, is_academic, title, cpf, rg, rg_issuer, nationality, marital_status, profession, now, now, self.current_user))
                self.current_client_id = self.cursor.lastrowid
            
            self.conn.commit()
            self.load_clients()
            
            # Auto-highlight and focus the newly saved client
            if self.current_client_id:
                for comp_node in self.tree.get_children():
                    for client_node in self.tree.get_children(comp_node):
                        tags = self.tree.item(client_node, "tags")
                        if tags and tags[0] == "client" and str(tags[1]) == str(self.current_client_id):
                            self.tree.selection_set(client_node)
                            self.tree.see(client_node)
                            break
                            
            messagebox.showinfo("Success", "Client saved successfully")
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not save client: {e}")

    def on_select(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        item_type, item_id = self.tree.item(selection[0], "tags")
        if item_type == "client" and item_id:
            self.load_client_to_form(item_id)
        else:
            self.clear_form()

    def clear_form(self):
        self.name_var.set("")
        self.email_var.set("")
        self.title_var.set("")
        self.cpf_var.set("")
        self.rg_var.set("")
        self.rg_issuer_var.set("")
        self.nationality_var.set("")
        self.marital_status_var.set("")
        self.profession_var.set("")
        self.client_type_var.set("PF")
        self.phone_var.set("")
        self.address_text.delete("1.0", tk.END)
        self.funding_code_var.set("")
        self.is_academic_var.set(False)
        self.current_client_id = None

    # Drag and drop functionality
    def on_drag_start(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            item_type, item_id = self.tree.item(item, "tags")
            if item_type == "client":
                self.drag_item = item
                self.drag_item_type = item_type
                self.drag_item_id = item_id
                self.original_selection = self.tree.selection()

    def on_drag_motion(self, event):
        if not hasattr(self, 'drag_item') or not self.drag_item:
            return
        current_item = self.tree.identify_row(event.y)
        self.root.config(cursor="") # Reset cursor
        
        if hasattr(self, 'drag_highlight') and self.drag_highlight:
            self.tree.selection_remove(self.drag_highlight)
        
        if current_item:
            item_type, _ = self.tree.item(current_item, "tags")
            if item_type == "company":
                self.tree.item(current_item, open=True)
                self.tree.selection_add(current_item)
                self.root.config(cursor="plus")
                self.drag_highlight = current_item
            elif item_type == "client" and current_item != self.drag_item:
                self.tree.selection_add(current_item)
                self.root.config(cursor="no")
                self.drag_highlight = current_item
            else:
                self.drag_highlight = None
        else:
            self.drag_highlight = None
            self.root.config(cursor="no")

    def on_drag_end(self, event):
        if not hasattr(self, 'drag_item') or not self.drag_item:
            return

        if hasattr(self, 'drag_highlight') and self.drag_highlight:
            self.tree.selection_remove(self.drag_highlight)
        
        self.root.config(cursor="") # Reset cursor
        if hasattr(self, 'original_selection'):
            try:
                item_type, _ = self.tree.item(self.original_selection[0], "tags") if self.original_selection else (None, None)
                if item_type == "client":
                    self.tree.selection_set(self.original_selection)
            except:
                pass

        target_item = self.tree.identify_row(event.y)
        if target_item and target_item != self.drag_item:
            target_type, target_id = self.tree.item(target_item, "tags")
            if target_type == "company" and self.drag_item_type == "client":
                try:
                    if not target_id or target_id == 'None':
                        self.cursor.execute("UPDATE clients SET company_id = NULL WHERE id = ?", (self.drag_item_id,))
                    else:
                        self.cursor.execute("UPDATE clients SET company_id = ? WHERE id = ?", (target_id, self.drag_item_id))
                    self.conn.commit()
                    self.load_clients()
                except sqlite3.Error as e:
                    messagebox.showerror("Database Error", f"Could not move client: {e}")

        self.drag_item = None
        self.drag_item_type = None
        self.drag_item_id = None
        if hasattr(self, 'drag_highlight'):
            self.drag_highlight = None
        if hasattr(self, 'original_selection'):
            delattr(self, 'original_selection')

    def on_closing(self):
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass
            
    def _is_valid_cpf(self, cpf: str) -> bool:
        """Validates a Brazilian CPF number."""
        cpf = ''.join(re.findall(r'\d', cpf))

        if not cpf or len(cpf) != 11 or len(set(cpf)) == 1:
            return False

        # Calculate first check digit
        s = sum(int(cpf[i]) * (10 - i) for i in range(9))
        d1 = (s * 10) % 11
        if d1 == 10: d1 = 0
        if d1 != int(cpf[9]):
            return False

        # Calculate second check digit
        s = sum(int(cpf[i]) * (11 - i) for i in range(10))
        d2 = (s * 10) % 11
        if d2 == 10: d2 = 0
        if d2 != int(cpf[10]):
            return False

        return True

    def _is_valid_cnpj(self, cnpj: str) -> bool:
        """Validates a Brazilian CNPJ number."""
        cnpj = ''.join(re.findall(r'\d', cnpj))

        if not cnpj or len(cnpj) != 14 or len(set(cnpj)) == 1:
            return False

        # Calculate first check digit
        weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        s = sum(int(cnpj[i]) * weights1[i] for i in range(12))
        d1 = 11 - (s % 11)
        if d1 >= 10: d1 = 0
        if d1 != int(cnpj[12]): return False

        # Calculate second check digit
        weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        s = sum(int(cnpj[i]) * weights2[i] for i in range(13))
        d2 = 11 - (s % 11)
        if d2 >= 10: d2 = 0
        return d2 == int(cnpj[13])

if __name__ == "__main__":
    root = tk.Tk()
    app = ClientManager(root)
    root.mainloop()