import os
import json
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime, timedelta
import math

class ProjectFlowManager:
    def __init__(self, root, current_user="Unknown"):
        self.root = root
        self.current_user = current_user
        self.db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'projects.db')
        self.service_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'services.db')
        self.stock_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock.db')
        
        self.stages = [
            (1, "Orçamento Aprovado"),
            (2, "Contrato Enviado"),
            (3, "Contrato Assinado"),
            (4, "Amostras Recebidas"),
            (5, "Amostras Analisadas"),
            (6, "Dados Analisados e Liberados")
        ]
        
        self.trees = {}
        self.init_db()
        self.create_ui()
        self.load_data()
        
    def init_db(self):
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        
        flow_columns = ['status INTEGER DEFAULT 0', 'approved_at TEXT', 'agreed_business_days INTEGER', 
                        'contract_sent_at TEXT', 'contract_signed_at TEXT', 'samples_received_at TEXT', 
                        'sample_storage_location TEXT', 'samples_analyzed_at TEXT', 'data_released_at TEXT', 
                        'data_link TEXT', 'deletion_threshold_months INTEGER DEFAULT 3', 'completed_at TEXT',
                        'invoice_sent INTEGER DEFAULT 0', 'invoice_paid INTEGER DEFAULT 0', 'invoice_paid_date TEXT',
                        'lnp_emitted INTEGER DEFAULT 0', 'lnp_paid INTEGER DEFAULT 0', 'lnp_paid_date TEXT']
        for col in flow_columns:
            try: self.cursor.execute(f'ALTER TABLE projects ADD COLUMN {col}')
            except sqlite3.OperationalError: pass
            
        try: self.cursor.execute('ALTER TABLE project_services ADD COLUMN completed INTEGER DEFAULT 0')
        except sqlite3.OperationalError: pass
        try: self.cursor.execute('ALTER TABLE project_services ADD COLUMN executor TEXT')
        except sqlite3.OperationalError: pass
            
        self.conn.commit()

    def load_settings(self):
        settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')
        settings = {"profit_margin": 0.0, "taxes_and_fees": 0.0}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    settings.update(json.load(f))
            except Exception: pass
        return settings

    def create_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill="both", expand=True)

        # Tab 1: Active Flow
        self.tab_active = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_active, text="Active Projects Flow")

        grid_frame = ttk.Frame(self.tab_active)
        grid_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        for i in range(2): grid_frame.rowconfigure(i, weight=1)
        for j in range(3): grid_frame.columnconfigure(j, weight=1)

        for idx, (status_val, title) in enumerate(self.stages):
            row, col = divmod(idx, 3)
            frame = ttk.LabelFrame(grid_frame, text=title, padding=5)
            frame.grid(row=row, column=col, sticky="nsew", padx=5, pady=5)
            
            tree = ttk.Treeview(frame, columns=("Client", "User", "Days"), height=8)
            tree.heading("#0", text="Estimate #")
            tree.heading("Client", text="Client")
            tree.heading("User", text="Responsible")
            tree.heading("Days", text="Days")
            
            tree.column("#0", width=80)
            tree.column("Client", width=120)
            tree.column("User", width=100)
            tree.column("Days", width=80, anchor="center")
            
            tree.pack(fill="both", expand=True)
            tree.tag_configure("orange", background="#FFD580") # Soft orange
            
            tree.bind("<Double-1>", self.on_double_click)
            tree.bind("<Button-1>", self.on_tree_click)
            tree.bind("<Motion>", self.on_hover)
            tree.bind("<Leave>", self.on_leave)
            
            self.trees[status_val] = tree

        controls = ttk.Frame(self.tab_active, padding=5)
        controls.pack(fill="x")
        ttk.Button(controls, text="Move Selected to Next Stage ➔", command=self.move_next, style="Accent.TButton").pack(side="right", padx=10)
        ttk.Button(controls, text="✎ Edit Stage Info", command=self.edit_info).pack(side="right", padx=10)
        ttk.Button(controls, text="Refresh Data", command=self.load_data).pack(side="left", padx=10)

        # Tab 2: Completed Projects
        self.tab_completed = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.tab_completed, text="Completed Projects")
        
        self.tree_completed = ttk.Treeview(self.tab_completed, columns=("Approved", "Samples", "Data", "Cost"), height=15)
        self.tree_completed.heading("#0", text="Estimate #")
        self.tree_completed.heading("Approved", text="Date Approved")
        self.tree_completed.heading("Samples", text="Samples Arrived")
        self.tree_completed.heading("Data", text="Data Available")
        self.tree_completed.heading("Cost", text="Total Cost")
        
        for col in ("#0", "Approved", "Samples", "Data", "Cost"):
            self.tree_completed.column(col, width=150, anchor="center")
            
        self.tree_completed.pack(fill="both", expand=True)
        self.tree_completed.bind("<Double-1>", self.on_double_click)
        
        self.tooltip = None

    def get_business_days(self, start_str, end_date=None):
        if not start_str: return 0
        start_date = datetime.strptime(start_str.split()[0], '%Y-%m-%d')
        if not end_date: end_date = datetime.now()
        
        days = 0
        current = start_date
        while current.date() < end_date.date():
            if current.weekday() < 5: # Monday = 0, Friday = 4
                days += 1
            current += timedelta(days=1)
        return days

    def get_calendar_days(self, start_str):
        if not start_str: return 0
        start_date = datetime.strptime(start_str.split()[0], '%Y-%m-%d')
        return (datetime.now().date() - start_date.date()).days

    def load_data(self):
        for tree in self.trees.values():
            for item in tree.get_children(): tree.delete(item)
        for item in self.tree_completed.get_children():
            self.tree_completed.delete(item)
            
        self.cursor.execute("ATTACH DATABASE ? AS clients_db", (os.path.join(os.path.dirname(self.db_path), 'clients.db'),))
        
        self.cursor.execute('''
            SELECT p.id, p.estimate_number, c.name, p.responsible_user, p.status, p.approved_at,
                   p.contract_sent_at, p.contract_signed_at, p.samples_received_at, p.samples_analyzed_at,
                   p.data_released_at, p.agreed_business_days, p.deletion_threshold_months, p.final_cost
            FROM projects p
            LEFT JOIN clients_db.clients c ON p.client_id = c.id
            WHERE p.status > 0
        ''')
        projects = self.cursor.fetchall()
        self.cursor.execute("DETACH DATABASE clients_db")
        
        for p in projects:
            (p_id, est_num, client, user, status, approved_at, sent_at, signed_at, 
             recv_at, anal_at, data_at, agreed_days, thresh_months, final_cost) = p
             
            if status == 7: # Completed
                d_app = approved_at.split()[0] if approved_at else ""
                d_rec = recv_at.split()[0] if recv_at else ""
                d_dat = data_at.split()[0] if data_at else ""
                self.tree_completed.insert("", "end", text=est_num, values=(d_app, d_rec, d_dat, f"R$ {final_cost:.2f}"), tags=(p_id,))
                continue
                
            tree = self.trees.get(status)
            if not tree: continue
            
            days_str = ""
            tags = (p_id, status)
            
            if status == 1: days_str = f"{self.get_calendar_days(approved_at)} days"
            elif status == 2: days_str = f"{self.get_calendar_days(sent_at)} days"
            elif status == 3: days_str = f"{self.get_calendar_days(signed_at)} days"
            elif status in (4, 5):
                elapsed = self.get_business_days(recv_at)
                remaining = agreed_days - elapsed if agreed_days else 0
                days_str = f"{remaining} left"
            elif status == 6:
                days = self.get_calendar_days(data_at)
                days_str = f"{days} days"
                if thresh_months and days >= (thresh_months * 30):
                    tags = (p_id, status, "orange")
                    
            tree.insert("", "end", text=est_num, values=(client or "Unknown", user, days_str), tags=tags)

    def get_selected(self):
        for status, tree in self.trees.items():
            sel = tree.selection()
            if sel: return tree, sel[0], status
        return None, None, None

    def on_tree_click(self, event):
        clicked_tree = event.widget
        for tree in self.trees.values():
            if tree != clicked_tree:
                for item in tree.selection():
                    tree.selection_remove(item)

    def edit_info(self):
        tree, item, status = self.get_selected()
        if not tree or not item:
            messagebox.showwarning("Warning", "Select a project to edit.")
            return
            
        p_id = tree.item(item, "tags")[0]
        
        if status in (4, 5):
            dialog = tk.Toplevel(self.root)
            dialog.title("Edit Stage Info")
            dialog.geometry("650x450")
            dialog.transient(self.root)
            dialog.grab_set()
            
            self.cursor.execute("SELECT sample_storage_location FROM projects WHERE id = ?", (p_id,))
            row = self.cursor.fetchone()
            current_loc = row[0] if row and row[0] else ""
            
            top_frame = ttk.Frame(dialog, padding=10)
            top_frame.pack(fill="x")
            ttk.Label(top_frame, text="Sample Storage Location:").pack(anchor="w", pady=(0,2))
            loc_var = tk.StringVar(value=current_loc)
            ttk.Entry(top_frame, textvariable=loc_var, width=50).pack(anchor="w")
            
            mid_frame = ttk.LabelFrame(dialog, text="Services Execution", padding=10)
            mid_frame.pack(fill="both", expand=True, padx=10, pady=5)
            
            sv_tree = ttk.Treeview(mid_frame, columns=("Status", "Executor"), height=6)
            sv_tree.heading("#0", text="Service")
            sv_tree.heading("Status", text="Completed")
            sv_tree.heading("Executor", text="Responsible")
            sv_tree.column("#0", width=250)
            sv_tree.column("Status", width=80, anchor="center")
            sv_tree.column("Executor", width=150)
            sv_tree.pack(side="left", fill="both", expand=True)
            
            scroll = ttk.Scrollbar(mid_frame, orient="vertical", command=sv_tree.yview)
            scroll.pack(side="right", fill="y")
            sv_tree.configure(yscrollcommand=scroll.set)
            
            def load_services():
                for item in sv_tree.get_children(): sv_tree.delete(item)
                self.cursor.execute("ATTACH DATABASE ? AS services_db", (self.service_db_path,))
                self.cursor.execute('''
                    SELECT ps.id, s.name, ps.completed, ps.executor 
                    FROM project_services ps
                    JOIN services_db.services s ON ps.service_id = s.id
                    WHERE ps.project_id = ?
                ''', (p_id,))
                for ps_id, s_name, comp, exec_name in self.cursor.fetchall():
                    status_text = "Yes" if comp else "No"
                    sv_tree.insert("", "end", text=s_name, values=(status_text, exec_name or ""), tags=(ps_id, comp))
                self.cursor.execute("DETACH DATABASE services_db")
                
            load_services()
            
            btn_frame = ttk.Frame(dialog, padding=10)
            btn_frame.pack(fill="x")
            
            def toggle_service():
                sel = sv_tree.selection()
                if not sel: return
                item = sel[0]
                ps_id, is_comp = sv_tree.item(item, "tags")
                is_comp = int(is_comp)
                
                if is_comp:
                    self.cursor.execute("UPDATE project_services SET completed = 0, executor = NULL WHERE id = ?", (ps_id,))
                else:
                    def_name = self.current_user
                    try:
                        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json'), 'r') as f:
                            u_dict = json.load(f)
                            if self.current_user in u_dict:
                                def_name = u_dict[self.current_user]
                    except: pass
                    
                    exec_name = simpledialog.askstring("Executor", "Enter the name of the responsible for this service:", parent=dialog, initialvalue=def_name)
                    if exec_name is None: return
                    self.cursor.execute("UPDATE project_services SET completed = 1, executor = ? WHERE id = ?", (exec_name, ps_id))
                    
                self.conn.commit()
                load_services()
                
            ttk.Button(btn_frame, text="Toggle Selected Completed", command=toggle_service).pack(side="left")
            
            def save_all():
                self.cursor.execute("UPDATE projects SET sample_storage_location = ? WHERE id = ?", (loc_var.get(), p_id))
                self.conn.commit()
                dialog.destroy()
                
            ttk.Button(btn_frame, text="Save Location & Close", command=save_all).pack(side="right")
                
        elif status == 6:
            self.cursor.execute("SELECT data_link, deletion_threshold_months FROM projects WHERE id = ?", (p_id,))
            row = self.cursor.fetchone()
            current_link, current_thresh = row if row else ("", 3)
            
            dialog = tk.Toplevel(self.root)
            dialog.title("Edit Data Info")
            dialog.geometry("400x150")
            dialog.transient(self.root)
            dialog.grab_set()
            
            ttk.Label(dialog, text="Online Data Link:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
            link_var = tk.StringVar(value=current_link or "")
            ttk.Entry(dialog, textvariable=link_var, width=30).grid(row=0, column=1, padx=10, pady=10)
            
            ttk.Label(dialog, text="Deletion Threshold (months):").grid(row=1, column=0, padx=10, pady=5, sticky="w")
            thresh_var = tk.IntVar(value=current_thresh or 3)
            ttk.Entry(dialog, textvariable=thresh_var, width=10).grid(row=1, column=1, padx=10, pady=5, sticky="w")
            
            def save():
                self.cursor.execute("UPDATE projects SET data_link = ?, deletion_threshold_months = ? WHERE id = ?", 
                                    (link_var.get(), thresh_var.get(), p_id))
                self.conn.commit()
                self.load_data()
                dialog.destroy()
                
            ttk.Button(dialog, text="Save", command=save).grid(row=2, column=0, columnspan=2, pady=15)
        else:
            messagebox.showinfo("Info", "No specific information to edit for this stage.")

    def move_next(self):
        tree, item, status = self.get_selected()
        if not tree or not item or status is None:
            messagebox.showwarning("Warning", "Select a project to move.")
            return
            
        p_id = tree.item(item, "tags")[0]
        est_num = tree.item(item, "text")
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        next_stage_title = dict(self.stages).get(status + 1, "Completed Projects")
        if not messagebox.askyesno("Confirm Move", f"Move project {est_num} to '{next_stage_title}'?"):
            return
            
        updates = {"status": status + 1}
        
        if status == 1: updates["contract_sent_at"] = now
        elif status == 2: updates["contract_signed_at"] = now
        elif status == 3:
            loc = simpledialog.askstring("Samples Location", "Where were the samples stored?", parent=self.root)
            updates["sample_storage_location"] = loc or "Unknown"
            updates["samples_received_at"] = now
        elif status == 4: updates["samples_analyzed_at"] = now
        elif status == 5:
            link = simpledialog.askstring("Data Link", "Enter the online location/link of the data:", parent=self.root)
            updates["data_link"] = link or ""
            updates["data_released_at"] = now
        elif status == 6: updates["completed_at"] = now
        
        query = "UPDATE projects SET " + ", ".join([f"{k} = ?" for k in updates.keys()]) + " WHERE id = ?"
        values = list(updates.values()) + [p_id]
        
        try:
            self.cursor.execute(query, values)
            self.conn.commit()
            self.load_data()
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", str(e))

    def on_hover(self, event):
        tree = event.widget
        item = tree.identify_row(event.y)
        if item:
            tags = tree.item(item, "tags")
            if "orange" in tags:
                if self.tooltip and self.tooltip.winfo_exists(): return
                self.tooltip = tk.Toplevel(self.root)
                self.tooltip.wm_overrideredirect(True)
                self.tooltip.wm_geometry(f"+{event.x_root+15}+{event.y_root+15}")
                ttk.Label(self.tooltip, text="Ready for data deletion", relief="solid", borderwidth=1, padding=5, background="#FFD580").pack()
                return
        self.on_leave(event)

    def on_leave(self, event):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None

    def format_br_currency(self, value):
        formatted = f"{value:,.2f}"
        formatted = formatted.replace(',', 'X').replace('.', ',').replace('X', '.')
        return f"R$ {formatted}"

    def calculate_cost_breakdown(self, project_id):
        settings = self.load_settings()
        pm = float(settings.get("profit_margin", 0.0)) / 100.0
        tf = float(settings.get("taxes_and_fees", 0.0)) / 100.0
        
        self.cursor.execute("SELECT total_samples, discount_percentage FROM projects WHERE id = ?", (project_id,))
        total_samples, discount_pct = self.cursor.fetchone()
        discount = discount_pct / 100.0 if discount_pct else 0.0
        
        reagents_raw = 0.0
        labor_raw = 0.0
        maintenance_raw = 0.0
        profit_raw = 0.0
        
        self.cursor.execute("ATTACH DATABASE ? AS services_db", (self.service_db_path,))
        self.cursor.execute("ATTACH DATABASE ? AS stock_db", (self.stock_db_path,))
        
        self.cursor.execute("SELECT service_id, samples_override FROM project_services WHERE project_id = ?", (project_id,))
        for s_id, override in self.cursor.fetchall():
            samples = override if override is not None else total_samples
            
            self.cursor.execute('''
                SELECT sr.quantity, sr.unit, sr.samples_per_batch, st.price, st.container_size, st.unit
                FROM services_db.service_requirements sr
                LEFT JOIN stock_db.stock st ON sr.stock_item_id = st.id
                WHERE sr.service_id = ?
            ''', (s_id,))
            
            for req_qty, req_unit, spb, price, container_size, stock_unit in self.cursor.fetchall():
                if price is not None and container_size and container_size > 0:
                    unit_cost = price / container_size
                    conv = 1.0
                    mass = {"ng": 1e-9, "ug": 1e-6, "mg": 1e-3, "g": 1.0}
                    vol = {"nL": 1e-9, "uL": 1e-6, "mL": 1e-3, "L": 1.0}
                    if req_unit in mass and stock_unit in mass: conv = mass[req_unit] / mass[stock_unit]
                    elif req_unit in vol and stock_unit in vol: conv = vol[req_unit] / vol[stock_unit]
                    
                    batches = math.ceil(samples / spb) if spb > 0 else samples
                    reagents_raw += (req_qty * conv * batches * unit_cost)
                    
            self.cursor.execute("SELECT cost_type, cost, samples_per_batch FROM services_db.service_costs WHERE service_id = ?", (s_id,))
            for c_type, cost, spb in self.cursor.fetchall():
                batches = math.ceil(samples / spb) if spb > 0 else samples
                val = cost * batches
                if c_type == "Labor": labor_raw += val
                elif c_type == "Maintenance": maintenance_raw += val
                elif c_type == "Profit": profit_raw += val
                
        self.cursor.execute("DETACH DATABASE services_db")
        self.cursor.execute("DETACH DATABASE stock_db")
        
        base_cost = reagents_raw + labor_raw + maintenance_raw + profit_raw
        
        # Mathematical alignment: 
        # The user specifically requested Reagents, Labor, and Maintenance adjusted by taxes/fees, NO profit margin.
        # And Profit bucket to include the calculated profit margin + any "Profit" costs, adjusted by taxes/fees.
        # Total matches the standard Final Cost equation mathematically!
        tax_and_discount_multiplier = (1 + tf) * (1 - discount)
        
        breakdown = {
            "Reagents": reagents_raw * tax_and_discount_multiplier,
            "Labor": labor_raw * tax_and_discount_multiplier,
            "Maintenance": maintenance_raw * tax_and_discount_multiplier,
            "Profit": (profit_raw + base_cost * pm) * tax_and_discount_multiplier
        }
        return breakdown

    def on_double_click(self, event):
        tree = event.widget
        item = tree.identify_row(event.y)
        if not item: return
        
        p_id = tree.item(item, "tags")[0]
        est_num = tree.item(item, "text")
        
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Project Breakdown & Finances: {est_num}")
        dialog.geometry("650x550")
        dialog.transient(self.root)
        
        notebook = ttk.Notebook(dialog)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        
        tab_overview = ttk.Frame(notebook, padding=10)
        notebook.add(tab_overview, text="Services & Breakdown")
        
        ttk.Label(tab_overview, text="Services Hired", font=("Helvetica", 12, "bold")).pack(pady=(0,5))
        
        sv_tree = ttk.Treeview(tab_overview, columns=("Samples", "Cost"), height=6)
        sv_tree.heading("#0", text="Service")
        sv_tree.heading("Samples", text="Samples")
        sv_tree.heading("Cost", text="Total Cost")
        sv_tree.column("#0", width=300)
        sv_tree.column("Samples", width=80, anchor="center")
        sv_tree.column("Cost", width=120, anchor="e")
        sv_tree.pack(fill="both", expand=True, padx=10)
        
        self.cursor.execute("ATTACH DATABASE ? AS services_db", (self.service_db_path,))
        self.cursor.execute('''
            SELECT s.name, ps.samples_override, ps.calculated_cost 
            FROM project_services ps
            JOIN services_db.services s ON ps.service_id = s.id
            WHERE ps.project_id = ?
        ''', (p_id,))
        
        total_val = 0.0
        for s_name, override, cost in self.cursor.fetchall():
            sv_tree.insert("", "end", text=s_name, values=(override or "Default", self.format_br_currency(cost)))
            total_val += cost
        self.cursor.execute("DETACH DATABASE services_db")
        
        ttk.Label(tab_overview, text="Cost Breakdown", font=("Helvetica", 12, "bold")).pack(pady=(20,5))
        
        bd = self.calculate_cost_breakdown(p_id)
        bd_tree = ttk.Treeview(tab_overview, columns=("Amount",), height=4)
        bd_tree.heading("#0", text="Category")
        bd_tree.heading("Amount", text="Adjusted Total")
        bd_tree.column("#0", width=300)
        bd_tree.column("Amount", width=200, anchor="e")
        bd_tree.pack(fill="x", padx=10, pady=(0, 10))
        
        bd_tree.insert("", "end", text="Reagents", values=(self.format_br_currency(bd["Reagents"]),))
        bd_tree.insert("", "end", text="Labor", values=(self.format_br_currency(bd["Labor"]),))
        bd_tree.insert("", "end", text="Maintenance", values=(self.format_br_currency(bd["Maintenance"]),))
        bd_tree.insert("", "end", text="Profit (Margin + Raw)", values=(self.format_br_currency(bd["Profit"]),))

        # --- Tab 2: Finances ---
        tab_finances = ttk.Frame(notebook, padding=20)
        notebook.add(tab_finances, text="Finances")
        
        self.cursor.execute('''
            SELECT status, invoice_sent, invoice_paid, invoice_paid_date, 
                   lnp_emitted, lnp_paid, lnp_paid_date 
            FROM projects WHERE id = ?
        ''', (p_id,))
        fin_data = self.cursor.fetchone()
        if not fin_data:
            status_val, inv_sent, inv_paid, inv_paid_date, lnp_emit, lnp_paid, lnp_paid_date = 0, 0, 0, "", 0, 0, ""
        else:
            status_val, inv_sent, inv_paid, inv_paid_date, lnp_emit, lnp_paid, lnp_paid_date = fin_data
            
        cb_proj_comp_var = tk.BooleanVar(value=(status_val >= 6))
        cb_proj_comp = ttk.Checkbutton(tab_finances, text="Project Complete", variable=cb_proj_comp_var, state="disabled")
        cb_proj_comp.pack(anchor="w", pady=5)
        
        inv_sent_var = tk.BooleanVar(value=bool(inv_sent))
        ttk.Checkbutton(tab_finances, text="Invoice Sent", variable=inv_sent_var).pack(anchor="w", pady=5)
        
        inv_paid_frame = ttk.Frame(tab_finances)
        inv_paid_frame.pack(fill="x", pady=5)
        inv_paid_var = tk.BooleanVar(value=bool(inv_paid))
        ttk.Checkbutton(inv_paid_frame, text="Invoice Paid", variable=inv_paid_var).pack(side="left")
        ttk.Label(inv_paid_frame, text="Date (DD/MM/YYYY):").pack(side="left", padx=(10,2))
        inv_paid_date_var = tk.StringVar(value=inv_paid_date or "")
        ttk.Entry(inv_paid_frame, textvariable=inv_paid_date_var, width=12).pack(side="left")
        
        lnp_frame = ttk.LabelFrame(tab_finances, text="LNP Details", padding=10)
        lnp_sum = bd["Reagents"] + bd["Maintenance"]
        ttk.Label(lnp_frame, text=f"Reagents + Maintenance: {self.format_br_currency(lnp_sum)}", font=("Helvetica", 10, "bold")).pack(anchor="w", pady=(0,10))
        
        lnp_emit_var = tk.BooleanVar(value=bool(lnp_emit))
        ttk.Checkbutton(lnp_frame, text="LNP Invoice Emitted", variable=lnp_emit_var).pack(anchor="w", pady=5)
        
        lnp_paid_frame = ttk.Frame(lnp_frame)
        lnp_paid_frame.pack(fill="x", pady=5)
        lnp_paid_var = tk.BooleanVar(value=bool(lnp_paid))
        ttk.Checkbutton(lnp_paid_frame, text="LNP Invoice Paid", variable=lnp_paid_var).pack(side="left")
        ttk.Label(lnp_paid_frame, text="Date (DD/MM/YYYY):").pack(side="left", padx=(10,2))
        lnp_paid_date_var = tk.StringVar(value=lnp_paid_date or "")
        ttk.Entry(lnp_paid_frame, textvariable=lnp_paid_date_var, width=12).pack(side="left")
        
        def toggle_lnp_frame(*args):
            if inv_paid_var.get(): lnp_frame.pack(fill="x", pady=15)
            else: lnp_frame.pack_forget()
                
        inv_paid_var.trace_add("write", toggle_lnp_frame)
        toggle_lnp_frame()
        
        def save_finances():
            self.cursor.execute('''
                UPDATE projects 
                SET invoice_sent = ?, invoice_paid = ?, invoice_paid_date = ?,
                    lnp_emitted = ?, lnp_paid = ?, lnp_paid_date = ?
                WHERE id = ?
            ''', (int(inv_sent_var.get()), int(inv_paid_var.get()), inv_paid_date_var.get(),
                  int(lnp_emit_var.get()), int(lnp_paid_var.get()), lnp_paid_date_var.get(), p_id))
            self.conn.commit()
            messagebox.showinfo("Saved", "Finances saved successfully.", parent=dialog)
            
        ttk.Button(tab_finances, text="Save Finances", command=save_finances, style="Accent.TButton").pack(anchor="sw", pady=20)