import os
import sys
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
from QuariumProjectFlow import ProjectFlowManager

# When frozen by PyInstaller, __file__ resolves inside the temporary
# extraction folder rather than the exe's real folder, so paths built from
# it point at a throwaway location. Use the exe's directory instead.
_BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

class FinanceManager:
    def __init__(self, root, current_user="Unknown"):
        self.root = root
        self.current_user = current_user
        self.db_path = os.path.join(_BASE_DIR, 'projects.db')
        
        # We need the ProjectFlowManager's logic for cost breakdown
        self.flow_manager = ProjectFlowManager(root, current_user)

        self.create_ui()
        self.load_financial_data()

    def create_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        # --- Top Frame for Project List ---
        projects_frame = ttk.LabelFrame(main_frame, text="Projects Financial Status", padding=10)
        projects_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.projects_tree = ttk.Treeview(projects_frame, columns=("Client", "Status", "Total Cost", "Profit"), height=15)
        self.projects_tree.heading("#0", text="Estimate #")
        self.projects_tree.heading("Client", text="Client")
        self.projects_tree.heading("Status", text="Financial Status")
        self.projects_tree.heading("Total Cost", text="Total Cost")
        self.projects_tree.heading("Profit", text="Est. Profit")

        self.projects_tree.column("#0", width=150)
        self.projects_tree.column("Client", width=200)
        self.projects_tree.column("Status", width=150, anchor="center")
        self.projects_tree.column("Total Cost", width=120, anchor="e")
        self.projects_tree.column("Profit", width=120, anchor="e")
        
        self.projects_tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(projects_frame, orient="vertical", command=self.projects_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.projects_tree.configure(yscrollcommand=scrollbar.set)

        # --- Bottom Frame for Totals ---
        totals_frame = ttk.LabelFrame(main_frame, text="Overall Financial Summary", padding=10)
        totals_frame.pack(fill="x")

        self.totals_tree = ttk.Treeview(totals_frame, columns=("Amount",), height=4)
        self.totals_tree.heading("#0", text="Category")
        self.totals_tree.heading("Amount", text="Total Amount")
        self.totals_tree.column("#0", width=200)
        self.totals_tree.column("Amount", width=150, anchor="e")
        self.totals_tree.pack(fill="x")

        # --- Controls ---
        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(fill="x", pady=(10,0))
        ttk.Button(controls_frame, text="Refresh Data", command=self.load_financial_data).pack(side="left")

    def get_financial_status(self, project_data):
        status, inv_sent, inv_paid, lnp_emit, lnp_paid = project_data
        if status < 6:
            return "In Progress"
        if lnp_paid:
            return "LNP Paid"
        if lnp_emit:
            return "LNP Emitted"
        if inv_paid:
            return "Invoice Paid"
        if inv_sent:
            return "Invoice Sent"
        return "Awaiting Invoice"

    def load_financial_data(self):
        # Clear existing data
        for item in self.projects_tree.get_children():
            self.projects_tree.delete(item)
        for item in self.totals_tree.get_children():
            self.totals_tree.delete(item)

        total_reagents = 0
        total_labor = 0
        total_maintenance = 0
        total_profit = 0

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("ATTACH DATABASE ? AS clients_db", (os.path.join(os.path.dirname(self.db_path), 'clients.db'),))
            
            # Simplified query to fetch only necessary project identifiers and status
            cursor.execute('''
                SELECT 
                    p.id, p.estimate_number, c.name as client_name, p.final_cost, p.status, 
                    p.invoice_sent, p.invoice_paid, p.lnp_emitted, p.lnp_paid
                FROM projects p
                LEFT JOIN clients_db.clients c ON p.client_id = c.id
                WHERE p.status > 0
                ORDER BY p.status, p.id DESC;
            ''')
            projects = cursor.fetchall()
            conn.close()

            for row in projects:
                p_id, est_num, client_name, final_cost, status, inv_sent, inv_paid, lnp_emit, lnp_paid = row
                
                # Use the centralized calculation logic from ProjectFlowManager
                breakdown = self.flow_manager.calculate_cost_breakdown(p_id)

                # Get financial status text
                fin_status = self.get_financial_status((status, inv_sent, inv_paid, lnp_emit, lnp_paid))

                # Add to project tree
                self.projects_tree.insert("", "end", text=est_num, values=(
                    client_name or "Unknown",
                    fin_status,
                    self.flow_manager.format_br_currency(final_cost),
                    self.flow_manager.format_br_currency(breakdown.get("Profit", 0))
                ))

                # Accumulate totals using the consistent breakdown data
                total_reagents += breakdown.get("Reagents", 0)
                total_labor += breakdown.get("Labor", 0)
                total_maintenance += breakdown.get("Maintenance", 0)
                total_profit += breakdown.get("Profit", 0)

        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Could not load financial data: {e}")
            return

    def on_closing(self):
        if hasattr(self, 'flow_manager') and hasattr(self.flow_manager, 'conn'):
            try:
                self.flow_manager.conn.close()
            except:
                pass