import os
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

try:
    from QuariumSM import StockManager
except ImportError:
    StockManager = None

DB_NAME = 'stock.db'

class CompositeStockManager:
    def __init__(self, root, is_embedded=False, current_user="Unknown"):
        self.root = root
        self.is_embedded = is_embedded
        self.current_user = current_user
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.title('Composite Item Creator')
            self.root.geometry('1000x700')

        self.db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_NAME)
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self.setup_db()

        self.items = []
        self.selected_item = None
        self.components = []

        self.create_ui()
        self.load_items()
        self.update_summary()

    def setup_db(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS composite_items (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                output_size REAL NOT NULL,
                output_unit TEXT NOT NULL,
                created_at TEXT NOT NULL,
                valid INTEGER NOT NULL DEFAULT 1,
                warning TEXT,
                updated_by TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS composite_components (
                id INTEGER PRIMARY KEY,
                composite_id INTEGER NOT NULL,
                stock_item_id INTEGER NOT NULL,
                quantity REAL NOT NULL,
                unit TEXT NOT NULL,
                base_unit_cost REAL NOT NULL,
                FOREIGN KEY(composite_id) REFERENCES composite_items(id),
                FOREIGN KEY(stock_item_id) REFERENCES stock(id)
            )
        ''')

        # add composite markers in stock table
        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN is_composite INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN composite_id INTEGER')
        except sqlite3.OperationalError:
            pass

        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN synonyms TEXT')
        except sqlite3.OperationalError:
            pass

        try:
            self.cursor.execute('ALTER TABLE composite_items ADD COLUMN updated_by TEXT')
        except sqlite3.OperationalError:
            pass

        self.conn.commit()

    def load_items(self):
        self.cursor.execute('SELECT id, name, code, price, container_size, unit, is_composite, composite_id, synonyms FROM stock')
        self.items = [dict(zip(('id', 'name', 'code', 'price', 'container_size', 'unit', 'is_composite', 'composite_id', 'synonyms'), row)) for row in self.cursor.fetchall()]

        self.name_var.set('')
        self.code_var.set('')
        self.unit_var.set('')

    def create_ui(self):
        top_frame = ttk.LabelFrame(self.root, text='Select Base Item', padding=10)
        top_frame.pack(fill='x', padx=10, pady=5)

        ttk.Label(top_frame, text='Name:').grid(row=0, column=0, sticky='w')
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(top_frame, textvariable=self.name_var, width=30)
        self.name_entry.grid(row=0, column=1, padx=5)
        self.name_entry.bind('<KeyRelease>', self.update_name_suggestions)

        ttk.Label(top_frame, text='Code:').grid(row=0, column=2, sticky='w')
        self.code_var = tk.StringVar()
        self.code_entry = ttk.Entry(top_frame, textvariable=self.code_var, width=25)
        self.code_entry.grid(row=0, column=3, padx=5)
        self.code_entry.bind('<KeyRelease>', self.update_code_suggestions)
 
        self.suggestion_box = tk.Listbox(top_frame, height=6, width=80,
                                         background="#F0F0F0",
                                         selectbackground="#285D80",
                                         selectforeground="#FFFFFF",
                                         borderwidth=1, relief="flat")
        self.suggestion_box.grid(row=1, column=0, columnspan=6, pady=5, sticky='w')
        self.suggestion_box.bind('<<ListboxSelect>>', self.on_suggestion_selected)

        ttk.Label(top_frame, text='Quantity:').grid(row=2, column=0, sticky='w')
        self.quantity_var = tk.StringVar(value="0.0")
        self.quantity_entry = ttk.Entry(top_frame, textvariable=self.quantity_var, width=12)
        self.quantity_entry.grid(row=2, column=1, padx=5)

        ttk.Label(top_frame, text='Unit:').grid(row=2, column=2, sticky='w')
        self.unit_var = tk.StringVar()
        self.unit_label = ttk.Label(top_frame, textvariable=self.unit_var, width=15)
        self.unit_label.grid(row=2, column=3, padx=5, sticky='w')

        self.add_component_btn = ttk.Button(top_frame, text='Add Component', command=self.add_component)
        self.add_component_btn.grid(row=2, column=4, padx=5)

        component_frame = ttk.LabelFrame(self.root, text='Component List', padding=10)
        component_frame.pack(fill='both', expand=True, padx=10, pady=5)

        self.component_tree = ttk.Treeview(component_frame, columns=('name', 'code', 'unit', 'quantity', 'unit_cost', 'cost'), show='headings', height=8)
        for col, t in [('name', 'Name'), ('code', 'Code'), ('unit', 'Unit'), ('quantity', 'Qty'), ('unit_cost', 'Unit Cost'), ('cost', 'Cost')]:
            self.component_tree.heading(col, text=t)
            self.component_tree.column(col, width=100, anchor='center')
        self.component_tree.pack(fill='both', expand=True)

        self.remove_component_btn = ttk.Button(component_frame, text='Remove Selected Component', command=self.remove_component)
        self.remove_component_btn.pack(pady=5)

        summary_frame = ttk.LabelFrame(self.root, text='Composite Summary', padding=10)
        summary_frame.pack(fill='x', padx=10, pady=5)

        ttk.Label(summary_frame, text='Output Name:').grid(row=0, column=0, sticky='w')
        self.output_name_var = tk.StringVar()
        ttk.Entry(summary_frame, textvariable=self.output_name_var, width=30).grid(row=0, column=1, padx=5)

        ttk.Label(summary_frame, text='Output Amount:').grid(row=0, column=2, sticky='w')
        self.output_size_var = tk.StringVar(value="1.0")
        ttk.Entry(summary_frame, textvariable=self.output_size_var, width=12).grid(row=0, column=3, padx=5)

        ttk.Label(summary_frame, text='Output Unit:').grid(row=0, column=4, sticky='w')
        self.output_unit_var = tk.StringVar()
        ttk.Entry(summary_frame, textvariable=self.output_unit_var, width=10).grid(row=0, column=5, padx=5)

        self.total_cost_var = tk.DoubleVar(value=0.0)
        ttk.Label(summary_frame, text='Total Cost (R$):').grid(row=1, column=0, sticky='w')
        ttk.Label(summary_frame, textvariable=self.total_cost_var).grid(row=1, column=1, sticky='w')

        self.new_unit_cost_var = tk.DoubleVar(value=0.0)
        ttk.Label(summary_frame, text='New Unit Cost (R$):').grid(row=1, column=2, sticky='w')
        ttk.Label(summary_frame, textvariable=self.new_unit_cost_var).grid(row=1, column=3, sticky='w')

        action_frame = ttk.Frame(summary_frame)
        action_frame.grid(row=2, column=0, columnspan=6, pady=10)

        ttk.Button(action_frame, text='Save Composite Item', command=self.save_composite).pack(side='left', padx=5)
        ttk.Button(action_frame, text='Recalculate Composite Costs', command=self.recalculate_all_composites).pack(side='left', padx=5)
        ttk.Button(action_frame, text='Clear Fields', command=self.clear_fields).pack(side='left', padx=5)
        ttk.Button(action_frame, text='Refresh Stock Items', command=self.load_items).pack(side='left', padx=5)

        if StockManager and not self.is_embedded:
            ttk.Button(action_frame, text='Open Stock Manager', command=self.open_stock_manager).pack(side='left', padx=5)

        style = ttk.Style()
        style.configure("Warning.TLabel", foreground="#FF6A7E")

        warning_frame = ttk.LabelFrame(self.root, text='Protection / Delete Helper', padding=10)
        warning_frame.pack(fill='x', padx=10, pady=5)

        ttk.Button(warning_frame, text='Delete Stock Item', command=self.delete_stock_item, style="Accent.TButton").pack(side='left', padx=5)
        self.warning_var = tk.StringVar(value='')
        warning_label = ttk.Label(warning_frame, textvariable=self.warning_var, style="Warning.TLabel")
        warning_label.pack(side='left', padx=10)

    def update_name_suggestions(self, _event):
        query = self.name_var.get().strip().lower()
        self.suggestion_box.delete(0, tk.END)
        for item in self.items:
            name_match = query in item['name'].lower()
            syn_match = item.get('synonyms') and query in item['synonyms'].lower()
            if query and (name_match or syn_match):
                self.suggestion_box.insert(tk.END, f"{item['name']} | {item['code']}")

    def update_code_suggestions(self, _event):
        query = self.code_var.get().strip().lower()
        self.suggestion_box.delete(0, tk.END)
        for item in self.items:
            code_match = query in (item['code'] or '').lower()
            syn_match = item.get('synonyms') and query in item['synonyms'].lower()
            if query and (code_match or syn_match):
                self.suggestion_box.insert(tk.END, f"{item['name']} | {item['code']}")

    def on_suggestion_selected(self, event):
        selection = self.suggestion_box.curselection()
        if not selection:
            return
        text = self.suggestion_box.get(selection[0])
        name_part, code_part = [p.strip() for p in text.split('|', 1)]
        item = next((i for i in self.items if i['name'] == name_part and i['code'] == code_part), None)
        if not item:
            return
        self.select_item(item)

    def select_item(self, item):
        self.selected_item = item
        self.name_var.set(item['name'])
        self.code_var.set(item['code'] or '')
        self.unit_var.set(item['unit'])
        if not self.output_unit_var.get():
            self.output_unit_var.set(item['unit'])

    def add_component(self):
        if not self.selected_item:
            messagebox.showerror('Error', 'Select a base item first')
            return
            
        qty_str = self.quantity_var.get().strip()
        if ',' in qty_str:
            proposed = qty_str.replace(',', '.')
            if messagebox.askyesno("Convert Decimal", f"You entered '{qty_str}'. Convert to '{proposed}'?"):
                qty_str = proposed
                self.quantity_var.set(proposed)
            else:
                return
                
        try:
            q = float(qty_str)
        except ValueError:
            messagebox.showerror('Error', 'Quantity must be a valid number')
            return
            
        if q <= 0:
            messagebox.showerror('Error', 'Quantity must be positive')
            return

        item = self.selected_item
        if self.output_unit_var.get() and self.output_unit_var.get() != item['unit']:
            # allow cross-unit if user wants, but warn
            pass

        unit_cost = item['price'] / item['container_size'] if item['container_size'] > 0 else 0
        cost = unit_cost * q

        entry = {
            'stock_item_id': item['id'],
            'name': item['name'],
            'code': item['code'],
            'unit': item['unit'],
            'quantity': q,
            'unit_cost': unit_cost,
            'cost': cost
        }
        self.components.append(entry)
        self.refresh_component_tree()
        self.update_summary()

    def refresh_component_tree(self):
        for r in self.component_tree.get_children():
            self.component_tree.delete(r)
        for c in self.components:
            self.component_tree.insert('', 'end', values=(c['name'], c['code'], c['unit'], f"{c['quantity']:.4f}", f"R${c['unit_cost']:.6f}", f"R${c['cost']:.4f}"))

    def remove_component(self):
        selected = self.component_tree.selection()
        if not selected:
            return
        i = self.component_tree.index(selected[0])
        self.components.pop(i)
        self.refresh_component_tree()
        self.update_summary()

    def clear_fields(self):
        self.output_name_var.set('')
        self.output_size_var.set("1.0")
        self.output_unit_var.set('')
        self.components.clear()
        self.refresh_component_tree()
        self.update_summary()
        self.select_item({'name': '', 'code': '', 'unit': ''})
        self.selected_item = None

    def update_summary(self):
        total_cost = sum(c['cost'] for c in self.components)
        self.total_cost_var.set(round(total_cost, 6))

        try:
            output_size = float(self.output_size_var.get())
        except ValueError:
            output_size = 0.0
            
        if output_size > 0:
            new_unit_cost = total_cost / output_size
        else:
            new_unit_cost = 0
        self.new_unit_cost_var.set(round(new_unit_cost, 6))

    def load_composite(self, comp_name):
        self.cursor.execute('SELECT id, name, output_size, output_unit FROM composite_items WHERE name = ?', (comp_name,))
        comp = self.cursor.fetchone()
        if not comp:
            return
        comp_id, name, out_size, out_unit = comp
        self.output_name_var.set(name)
        self.output_size_var.set(str(out_size))
        self.output_unit_var.set(out_unit)

        self.cursor.execute('''
            SELECT cc.stock_item_id, s.name, s.code, cc.unit, cc.quantity, cc.base_unit_cost
            FROM composite_components cc
            JOIN stock s ON cc.stock_item_id = s.id
            WHERE cc.composite_id = ?
        ''', (comp_id,))
        
        self.components = []
        for row in self.cursor.fetchall():
            sid, sname, scode, sunit, qty, unit_cost = row
            self.components.append({
                'stock_item_id': sid,
                'name': sname,
                'code': scode,
                'unit': sunit,
                'quantity': qty,
                'unit_cost': unit_cost,
                'cost': unit_cost * qty
            })
        self.refresh_component_tree()
        self.update_summary()

    def save_composite(self):
        name = self.output_name_var.get().strip()
        if not name:
            messagebox.showerror('Error', 'Output name required')
            return

        if not self.components:
            messagebox.showerror('Error', 'Add components first')
            return

        out_str = self.output_size_var.get().strip()
        if ',' in out_str:
            proposed = out_str.replace(',', '.')
            if messagebox.askyesno("Convert Decimal", f"You entered '{out_str}'. Convert to '{proposed}'?"):
                out_str = proposed
                self.output_size_var.set(proposed)
            else:
                return
                
        try:
            output_size = float(out_str)
        except ValueError:
            messagebox.showerror('Error', 'Output amount must be a valid number')
            return

        if output_size <= 0:
            messagebox.showerror('Error', 'Output amount must be greater than zero')
            return

        output_unit = self.output_unit_var.get().strip() or self.components[0]['unit']
        total_cost = self.total_cost_var.get()

        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        self.cursor.execute('SELECT id FROM composite_items WHERE name = ?', (name,))
        existing = self.cursor.fetchone()
        
        if existing:
            composite_id = existing[0]
            if not messagebox.askyesno('Update', f"Composite item '{name}' already exists. Update its components and size?"):
                return
            self.cursor.execute('UPDATE composite_items SET output_size = ?, output_unit = ?, valid = 1, warning = ?, updated_by = ? WHERE id = ?',
                                (output_size, output_unit, '', self.current_user, composite_id))
        else:
            self.cursor.execute('INSERT INTO composite_items (name, output_size, output_unit, created_at, valid, warning, updated_by) VALUES (?, ?, ?, ?, 1, ?, ?)',
                                (name, output_size, output_unit, created_at, '', self.current_user))
            composite_id = self.cursor.lastrowid

        self.cursor.execute('DELETE FROM composite_components WHERE composite_id = ?', (composite_id,))

        for c in self.components:
            self.cursor.execute('INSERT INTO composite_components (composite_id, stock_item_id, quantity, unit, base_unit_cost) VALUES (?, ?, ?, ?, ?)',
                                (composite_id, c['stock_item_id'], c['quantity'], c['unit'], c['unit_cost']))

        item_price = total_cost
        # Save to stock as non-editable composite item
        self.cursor.execute(
            'INSERT OR REPLACE INTO stock (id, name, code, price, container_size, unit, is_composite, composite_id, last_updated, updated_by) '
            'VALUES (COALESCE((SELECT id FROM stock WHERE name = ?), NULL), ?, ?, ?, ?, ?, 1, ?, ?, ?)',
            (name, name, '', item_price, output_size, output_unit, composite_id, created_at, self.current_user)
        )

        self.conn.commit()
        self.load_items()
        self.update_summary()
        messagebox.showinfo('Saved', f"Composite item '{name}' saved successfully.")

    def recalculate_all_composites(self):
        self.load_items()
        self.cursor.execute('SELECT id, name, output_size, output_unit FROM composite_items')
        composites = self.cursor.fetchall()
        for comp_id, comp_name, output_size, output_unit in composites:
            self.cursor.execute('SELECT cc.stock_item_id, cc.quantity, s.price, s.container_size, s.unit FROM composite_components cc JOIN stock s ON cc.stock_item_id = s.id WHERE cc.composite_id = ?', (comp_id,))
            rows = self.cursor.fetchall()
            total_cost = 0.0
            missing = []
            for stock_item_id, quantity, price, container_size, unit in rows:
                if price is None or container_size is None or container_size == 0:
                    missing.append(stock_item_id)
                    continue
                unit_cost = price / container_size
                total_cost += unit_cost * quantity
            if missing:
                warning = f'Missing price data for base id(s): {missing}'
                valid = 0
            else:
                warning = ''
                valid = 1
            self.cursor.execute('UPDATE composite_items SET valid = ?, warning = ? WHERE id = ?', (valid, warning, comp_id))
            self.cursor.execute('UPDATE stock SET price = ?, container_size = ?, unit = ? WHERE composite_id = ?', (total_cost, output_size, output_unit, comp_id))
        self.conn.commit()
        self.load_items()
        self.update_summary()
        messagebox.showinfo('Recalculated', 'All composite costs recalculated.')

    def delete_stock_item(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showerror('Error', 'Select item by name first')
            return
        item = next((i for i in self.items if i['name'] == name), None)
        if not item:
            messagebox.showerror('Error', 'Item not found')
            return

        # warn if used in composite
        self.cursor.execute('SELECT c.name FROM composite_items c JOIN composite_components cc ON c.id = cc.composite_id WHERE cc.stock_item_id = ?', (item['id'],))
        uses = [r[0] for r in self.cursor.fetchall()]
        if uses:
            messagebox.showerror('Cannot Delete', f"This reagent is used in the following composite reagent(s):\n\n" + "\n".join(uses) + "\n\nPlease remove it from them before deleting.")
            return

        self.cursor.execute('DELETE FROM stock WHERE id = ?', (item['id'],))
        self.conn.commit()
        self.load_items()
        self.update_summary()
        self.warning_var.set('Item deleted successfully.')

    def open_stock_manager(self):
        if not StockManager:
            messagebox.showerror('Error', 'StockManager not available to import from QuariumSM')
            return
        # Ensure all changes are committed before opening new instance
        self.conn.commit()
        stock_window = tk.Toplevel(self.root)
        manager = StockManager(stock_window, current_user=self.current_user)
        # Force refresh to load current database state
        manager.refresh_tree()

    def close(self):
        self.conn.close()
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    app = CompositeStockManager(root)
    root.protocol('WM_DELETE_WINDOW', app.close)
    root.mainloop()
