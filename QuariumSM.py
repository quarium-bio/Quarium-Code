import os
import re
import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime
import webbrowser

class StockManager:
    def __init__(self, root, current_user="Unknown", on_edit_composite=None):
        self.root = root
        self.current_user = current_user
        self.on_edit_composite = on_edit_composite
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.title("Quarium Stock Manager")
            self.root.geometry("900x600")
        self.last_removed = None
        self.last_price_update = None
        self.init_db()
        self.create_ui()
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    def init_db(self):
        root_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(root_dir, 'stock.db')
        self.backup_dir = os.path.join(root_dir, 'stock_backups')
        os.makedirs(self.backup_dir, exist_ok=True)

        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                container_size REAL NOT NULL,
                unit TEXT NOT NULL,
                code TEXT,
                last_updated TEXT,
                vendor TEXT
            )
        ''')
        # Add new columns if they don't exist
        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN code TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN last_updated TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN vendor TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN is_composite INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN synonyms TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN composite_id INTEGER')
        except sqlite3.OperationalError:
            pass
        try:
            self.cursor.execute('ALTER TABLE stock ADD COLUMN updated_by TEXT')
        except sqlite3.OperationalError:
            pass
        self.conn.commit()
        self.create_backup()
    
    def create_ui(self):
        # Input Frame
        input_frame = ttk.LabelFrame(self.root, text="Add Stock Item", padding=10)
        input_frame.pack(fill="x", padx=10, pady=10)
        
        ttk.Label(input_frame, text="Item Name:").grid(row=0, column=0, sticky="w")
        self.name_entry = ttk.Entry(input_frame, width=25)
        self.name_entry.grid(row=0, column=1, padx=5)
        self.name_entry.bind("<KeyRelease>", self.on_name_typing)
 
        self.suggestions = tk.Listbox(input_frame, height=3, width=25,
                                      background="#F0F0F0",
                                      selectbackground="#285D80",
                                      selectforeground="#FFFFFF",
                                      borderwidth=1, relief="flat")
        self.suggestions.grid(row=1, column=1, padx=5, pady=(2, 5), sticky='w')
        self.suggestions.bind("<<ListboxSelect>>", self.on_suggestion_select)

        ttk.Label(input_frame, text="Synonyms (; sep):").grid(row=0, column=2, sticky="w")
        self.synonyms_entry = ttk.Entry(input_frame, width=25)
        self.synonyms_entry.grid(row=0, column=3, padx=5)
 
        ttk.Label(input_frame, text="Vendor:").grid(row=2, column=0, sticky="w")
        self.vendor_entry = ttk.Entry(input_frame, width=25)
        self.vendor_entry.grid(row=2, column=1, padx=5)
        self.vendor_entry.bind("<KeyRelease>", self.on_vendor_typing)
 
        self.vendor_suggestions = tk.Listbox(input_frame, height=3, width=25,
                                             background="#F0F0F0",
                                             selectbackground="#285D80",
                                             selectforeground="#FFFFFF",
                                             borderwidth=1, relief="flat")
        self.vendor_suggestions.grid(row=3, column=1, padx=5, pady=(2, 5), sticky='w')
        self.vendor_suggestions.bind("<<ListboxSelect>>", self.on_vendor_suggestion_select)

        ttk.Label(input_frame, text="Code:").grid(row=2, column=2, sticky="w")
        self.code_entry = ttk.Entry(input_frame, width=25)
        self.code_entry.grid(row=2, column=3, padx=5)

        ttk.Label(input_frame, text="Price (R$):").grid(row=4, column=0, sticky="w")
        self.price_entry = ttk.Entry(input_frame, width=25)
        self.price_entry.grid(row=4, column=1, padx=5)
        
        ttk.Label(input_frame, text="Container Size:").grid(row=5, column=0, sticky="w")
        self.size_entry = ttk.Entry(input_frame, width=15)
        self.size_entry.grid(row=5, column=1, sticky="w", padx=5)
        
        ttk.Label(input_frame, text="Unit:").grid(row=5, column=2, sticky="w")
        unit_options = ["vial", "tube", "mL", "L", "gal", "mg", "tablet", "plate", "reaction", "hour", "uL", "g"]
        unit_options.sort()
        self.unit_var = tk.StringVar(value="g")
        unit_combo = ttk.Combobox(input_frame, textvariable=self.unit_var,
                                   values=unit_options, width=10, state='readonly')
        unit_combo.grid(row=5, column=3, padx=5)
        
        btn_frame = ttk.Frame(input_frame)
        btn_frame.grid(row=6, column=0, columnspan=4, pady=10, sticky="w")
        ttk.Button(btn_frame, text="Add Item", command=self.add_item).pack(side="left", padx=(0, 5))
        ttk.Button(btn_frame, text="Clear Fields", command=self.clear_fields).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Bulk Add Items", command=self.show_bulk_add_dialog).pack(side="left", padx=5)
        self.undo_button = ttk.Button(btn_frame, text="Undo Remove", command=self.undo_delete, state="disabled")
        self.undo_button.pack(side="left", padx=5)
        self.undo_update_button = ttk.Button(btn_frame, text="Undo Price Change", command=self.undo_price_change, state="disabled")
        self.undo_update_button.pack(side="left", padx=5)
        
        # Display Frame
        display_frame = ttk.LabelFrame(self.root, text="Stock Items", padding=10)
        display_frame.pack(fill="both", expand=True, padx=10, pady=10)
        display_frame.columnconfigure(0, weight=1)  # Allow horizontal expansion
        display_frame.rowconfigure(0, weight=1)     # Allow vertical expansion for treeview
        
        # Treeview that expands vertically
        self.tree = ttk.Treeview(display_frame, columns=("Name", "Code", "Vendor", "Price", "Size", "Unit", "Per-Unit Cost", "Last Updated", "Remove"))
        self.tree.heading("#0", text="ID")
        self.tree.column("#0", width=30)
        self.sort_states = {}
        for col in ("Name", "Code", "Vendor", "Price", "Size", "Unit", "Per-Unit Cost", "Last Updated", "Remove"):
            if col != "Remove":
                self.tree.heading(col, text=col, command=lambda c=col: self.sort_column(c))
            else:
                self.tree.heading(col, text=col)
            self.tree.column(col, width=80)
        self.tree.column("Remove", width=40, anchor='center')
        self.tree.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        
        # Button frame at bottom (fixed size)
        button_frame = ttk.Frame(display_frame)
        button_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        ttk.Button(button_frame, text="Edit Selected Item", command=self.edit_item).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Search on Google", command=self.search_google).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Update Composite Prices", command=self.update_composite_prices).pack(side="left", padx=5)
        
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.refresh_tree()
    
    def on_tree_click(self, event):
        item = self.tree.identify('item', event.x, event.y)
        column = self.tree.identify_column(event.x)
        if not item or column == '#0':
            return
        col_num = int(column[1:]) - 1  # #1 -> 0, #2 -> 1, etc.
        values = self.tree.item(item)["values"]
        if col_num < len(values) and values[col_num] == "❌":
            item_id = self.tree.item(item)["text"]
            self.delete_item(item_id)
    
    def sort_column(self, col):
        current_state = self.sort_states.get(col, 0)
        next_state = (current_state + 1) % 3
        self.sort_states[col] = next_state
        
        for c in self.sort_states:
            if c != col:
                self.sort_states[c] = 0
                
        if next_state == 0:
            self.refresh_tree()
            return
            
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children('')]
        
        def convert(val):
            if not val:
                return ''
            if col in ("Price", "Size", "Per-Unit Cost"):
                m = re.search(r"[-+]?\d*\.\d+|\d+", str(val).replace(',', '.'))
                return float(m.group()) if m else 0.0
            return str(val).lower()
            
        items.sort(key=lambda t: convert(t[0]), reverse=(next_state == 2))
        
        for index, (val, k) in enumerate(items):
            self.tree.move(k, '', index)

    def on_closing(self):
        try:
            self.conn.commit()
            self.create_backup()
            self.conn.close()
        except Exception:
            pass
        if isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.destroy()

    def update_composite_prices(self):
        try:
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
            self.create_backup()
            self.refresh_tree()
            messagebox.showinfo('Updated', 'All composite costs recalculated based on current stock prices.')
        except sqlite3.OperationalError:
            messagebox.showinfo('Info', 'No composite items found or table does not exist.')

    def add_item(self):
        try:
            name = self.name_entry.get().strip()
            code = self.code_entry.get().strip()
            vendor = self.vendor_entry.get().strip()
            synonyms = self.synonyms_entry.get().strip()
            
            price_str = self.price_entry.get().strip()
            size_str = self.size_entry.get().strip()
            for label, val_str, entry_widget in [("Price", price_str, self.price_entry), ("Container Size", size_str, self.size_entry)]:
                if ',' in val_str:
                    proposed = val_str.replace(',', '.')
                    if messagebox.askyesno("Convert Decimal", f"You entered '{val_str}' for {label}. Convert to '{proposed}'?"):
                        entry_widget.delete(0, tk.END)
                        entry_widget.insert(0, proposed)
                        if label == "Price": price_str = proposed
                        else: size_str = proposed
                    else:
                        return
                        
            price = float(price_str)
            size = float(size_str)
            unit = self.unit_var.get()
            last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            if not name or price < 0 or size < 0:
                messagebox.showerror("Error", "Invalid input values")
                return

            self.cursor.execute('SELECT id, price, container_size, unit, code, last_updated, vendor, synonyms FROM stock WHERE LOWER(name) = LOWER(?)', (name,))
            existing = self.cursor.fetchone()

            if existing:
                action = self.ask_duplicate_action(name)
                if action == 'cancel':
                    return
                if action == 'update':
                    item_id, old_price, old_size, old_unit, old_code, old_last_updated, old_vendor, old_synonyms = existing
                    self.last_price_update = (item_id, old_price, old_size, old_unit, old_code, old_last_updated, old_vendor, old_synonyms)
                    self.cursor.execute('UPDATE stock SET price = ?, container_size = ?, unit = ?, code = ?, last_updated = ?, vendor = ?, synonyms = ?, updated_by = ? WHERE id = ?',
                                        (price, size, unit, code, last_updated, vendor, synonyms, self.current_user, item_id))
                    self.conn.commit()
                    self.create_backup()
                    self.undo_update_button.config(state='normal')
                    self.refresh_tree()
                    return
                # else 'new' -> continue to insert

            self.cursor.execute('INSERT INTO stock (name, price, container_size, unit, code, last_updated, vendor, synonyms, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                              (name, price, size, unit, code, last_updated, vendor, synonyms, self.current_user))
            self.conn.commit()
            self.create_backup()
            
            self.name_entry.delete(0, tk.END)
            self.synonyms_entry.delete(0, tk.END)
            self.code_entry.delete(0, tk.END)
            self.vendor_entry.delete(0, tk.END)
            self.price_entry.delete(0, tk.END)
            self.size_entry.delete(0, tk.END)
            self.refresh_tree()
        except ValueError:
            messagebox.showerror("Error", "Price and size must be numbers")
            
    def show_bulk_add_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Bulk Add/Update Stock Items")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("950x600")

        instruction_text = (
            "Paste data from a spreadsheet (e.g., Excel, Google Sheets) directly into the table below.\n"
            "The columns must be in the correct order: Name, Code, Vendor, Price, Size, Unit, Synonyms.\n"
            "Existing items with a matching 'Name' will be updated. New items will be added."
        )
        ttk.Label(dialog, text=instruction_text, justify="left").pack(padx=10, pady=(10, 5), fill="x")

        # --- Create a scrollable table of Entry widgets ---
        table_frame = ttk.Frame(dialog)
        table_frame.pack(fill="both", expand=True, padx=10, pady=5)

        canvas = tk.Canvas(table_frame)
        scrollbar_y = ttk.Scrollbar(table_frame, orient="vertical", command=canvas.yview)
        scrollbar_x = ttk.Scrollbar(dialog, orient="horizontal", command=canvas.xview)
        
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")
            )
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar_y.pack(side="right", fill="y")
        scrollbar_x.pack(side="bottom", fill="x")

        # --- Table Headers ---
        headers = ["Name *", "Code", "Vendor", "Price (R$) *", "Container Size *", "Unit", "Synonyms (; sep)"]
        self.entry_grid = []

        for col, header_text in enumerate(headers):
            header_label = ttk.Label(scrollable_frame, text=header_text, font=('Helvetica', 10, 'bold'), relief="groove", padding=5, anchor="center")
            header_label.grid(row=0, column=col, sticky="nsew")

        # --- Table Entry Grid ---
        num_rows = 50
        num_cols = len(headers)

        for row in range(1, num_rows + 1):
            row_entries = []
            for col in range(num_cols):
                entry = ttk.Entry(scrollable_frame, width=20 if col > 2 else 30)
                entry.grid(row=row, column=col, sticky="nsew")
                # The lambda captures the row and col at definition time
                entry.bind("<Control-v>", lambda e, r=row-1, c=col: self.handle_paste(e, r, c))
                row_entries.append(entry)
            self.entry_grid.append(row_entries)

        # --- Process Button ---
        def process_grid_data():
            success_count = 0
            errors = []
            last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            for i, row_entries in enumerate(self.entry_grid):
                row_num = i + 1
                values = [entry.get().strip() for entry in row_entries]

                if not any(values):
                    continue

                name, code, vendor, price_str, size_str, unit, synonyms = values
                unit = unit if unit else "g"

                if not name or not price_str or not size_str:
                    if name or price_str or size_str:
                        errors.append(f"Row {row_num}: Missing required fields (Name, Price, Size).")
                    continue

                def parse_number(num_str):
                    clean = num_str.replace('R$', '').replace('r$', '').strip()
                    if ',' in clean and '.' in clean:
                        if clean.rfind(',') > clean.rfind('.'):
                            clean = clean.replace('.', '').replace(',', '.')
                        else:
                            clean = clean.replace(',', '')
                    else:
                        clean = clean.replace(',', '.')
                    return float(clean)

                try:
                    price = parse_number(price_str)
                except (ValueError, IndexError):
                    errors.append(f"Row {row_num}: Invalid price '{price_str}'.")
                    continue

                try:
                    size = parse_number(size_str)
                except (ValueError, IndexError):
                    errors.append(f"Row {row_num}: Invalid container size '{size_str}'.")
                    continue
                
                self.cursor.execute('SELECT id FROM stock WHERE LOWER(name) = LOWER(?)', (name,))
                existing = self.cursor.fetchone()

                try:
                    if existing:
                        item_id = existing[0]
                        self.cursor.execute('''
                            UPDATE stock SET price = ?, container_size = ?, unit = ?, code = ?, 
                            last_updated = ?, vendor = ?, synonyms = ?, updated_by = ? WHERE id = ?
                        ''', (price, size, unit, code, last_updated, vendor, synonyms, self.current_user, item_id))
                    else:
                        self.cursor.execute('''
                            INSERT INTO stock (name, price, container_size, unit, code, last_updated, vendor, synonyms, updated_by) 
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (name, price, size, unit, code, last_updated, vendor, synonyms, self.current_user))
                    success_count += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: Database error - {str(e)}")

            if success_count > 0:
                self.conn.commit()
                self.create_backup()
                self.refresh_tree()
                
            if errors:
                error_msg = f"Successfully processed {success_count} items.\n\nErrors encountered:\n" + "\n".join(errors[:10])
                if len(errors) > 10:
                    error_msg += f"\n... and {len(errors) - 10} more errors."
                messagebox.showwarning("Bulk Add Results", error_msg, parent=dialog)
            else:
                messagebox.showinfo("Success", f"Successfully processed {success_count} items.", parent=dialog)
                dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10, fill='x')
        ttk.Button(btn_frame, text="Process Data", command=process_grid_data).pack(side="right", padx=10)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=5)

    def handle_paste(self, event, start_row, start_col):
        """Handle pasting data from clipboard into the entry grid."""
        try:
            clipboard_data = self.root.clipboard_get()
        except tk.TclError:
            return "break" # Prevents default paste on empty clipboard

        rows = clipboard_data.strip().split('\n')
        data = [row.split('\t') for row in rows]

        for r_offset, row_data in enumerate(data):
            current_row = start_row + r_offset
            if current_row < len(self.entry_grid):
                for c_offset, cell_data in enumerate(row_data):
                    current_col = start_col + c_offset
                    if current_col < len(self.entry_grid[current_row]):
                        entry = self.entry_grid[current_row][current_col]
                        entry.delete(0, tk.END)
                        entry.insert(0, cell_data.strip())
        
        return "break" # Prevent the default paste behavior
    
    def on_name_typing(self, event):
        needle = self.name_entry.get().strip()
        self.set_suggestions(needle)

    def set_suggestions(self, needle):
        self.suggestions.delete(0, tk.END)
        if not needle:
            return
        pattern = '%' + needle + '%'
        self.cursor.execute('SELECT name FROM stock WHERE name LIKE ? OR synonyms LIKE ? ORDER BY name LIMIT 8', (pattern, pattern))
        matches = self.cursor.fetchall()
        
        seen = set()
        for r in matches:
            name = r[0]
            if name not in seen:
                self.suggestions.insert(tk.END, name)
                seen.add(name)

    def on_suggestion_select(self, event):
        selection = self.suggestions.curselection()
        if not selection:
            return
        value = self.suggestions.get(selection[0])
        self.name_entry.delete(0, tk.END)
        self.name_entry.insert(0, value)
        self.suggestions.delete(0, tk.END)

    def on_vendor_typing(self, event):
        needle = self.vendor_entry.get().strip()
        self.set_vendor_suggestions(needle)

    def set_vendor_suggestions(self, needle):
        self.vendor_suggestions.delete(0, tk.END)
        if not needle:
            return
        pattern = needle + '%'
        self.cursor.execute('SELECT DISTINCT vendor FROM stock WHERE vendor LIKE ? ORDER BY vendor LIMIT 8', (pattern,))
        matches = [r[0] for r in self.cursor.fetchall() if r[0]]
        for m in matches:
            self.vendor_suggestions.insert(tk.END, m)

    def on_vendor_suggestion_select(self, event):
        selection = self.vendor_suggestions.curselection()
        if not selection:
            return
        value = self.vendor_suggestions.get(selection[0])
        self.vendor_entry.delete(0, tk.END)
        self.vendor_entry.insert(0, value)
        self.vendor_suggestions.delete(0, tk.END)

    def undo_delete(self):
        if not self.last_removed:
            return
        name, price, size, unit, code, last_updated, vendor, synonyms = self.last_removed
        self.cursor.execute('INSERT INTO stock (name, price, container_size, unit, code, last_updated, vendor, synonyms, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (name, price, size, unit, code, last_updated, vendor, synonyms, self.current_user))
        self.conn.commit()
        self.create_backup()
        self.last_removed = None
        self.undo_button.config(state='disabled')
        self.refresh_tree()

    def clear_fields(self):
        self.name_entry.delete(0, tk.END)
        self.synonyms_entry.delete(0, tk.END)
        self.code_entry.delete(0, tk.END)
        self.vendor_entry.delete(0, tk.END)
        self.price_entry.delete(0, tk.END)
        self.size_entry.delete(0, tk.END)
        self.unit_var.set('g')
        self.suggestions.delete(0, tk.END)
        self.vendor_suggestions.delete(0, tk.END)

    def ask_duplicate_action(self, name):
        dialog = tk.Toplevel(self.root)
        dialog.title("Duplicate Item")
        dialog.transient(self.root)
        dialog.grab_set()
        tk.Label(dialog, text=f"Item '{name}' already exists. What do you want to do?").pack(padx=15, pady=10)

        result = {'choice': 'cancel'}

        def choose(option):
            result['choice'] = option
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(padx=10, pady=10)
        ttk.Button(btn_frame, text="Add as New Item", command=lambda: choose('new')).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="Update Existing", command=lambda: choose('update')).grid(row=0, column=1, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=lambda: choose('cancel')).grid(row=0, column=2, padx=5)

        self.root.wait_window(dialog)
        return result['choice']

    def undo_price_change(self):
        if not self.last_price_update:
            return
        item_id, old_price, old_size, old_unit, old_code, old_last_updated, old_vendor, old_synonyms = self.last_price_update
        self.cursor.execute('UPDATE stock SET price = ?, container_size = ?, unit = ?, code = ?, last_updated = ?, vendor = ?, synonyms = ?, updated_by = ? WHERE id = ?',
                            (old_price, old_size, old_unit, old_code, old_last_updated, old_vendor, old_synonyms, self.current_user, item_id))
        self.conn.commit()
        self.create_backup()
        self.last_price_update = None
        self.undo_update_button.config(state='disabled')
        self.refresh_tree()

    def delete_item(self, item_id):
        try:
            self.cursor.execute('''
                SELECT c.name FROM composite_items c 
                JOIN composite_components cc ON c.id = cc.composite_id 
                WHERE cc.stock_item_id = ?
            ''', (item_id,))
            uses = [r[0] for r in self.cursor.fetchall()]
            if uses:
                messagebox.showerror("Cannot Delete", f"This reagent is used in the following composite reagent(s):\n\n" + "\n".join(uses) + "\n\nPlease remove it from them before deleting.")
                return
        except sqlite3.OperationalError:
            pass

        self.cursor.execute('SELECT name, price, container_size, unit, code, last_updated, vendor, synonyms FROM stock WHERE id = ?', (item_id,))
        result = self.cursor.fetchone()
        if result:
            self.last_removed = result
        self.cursor.execute('DELETE FROM stock WHERE id = ?', (item_id,))
        self.conn.commit()
        self.create_backup()
        self.undo_button.config(state='normal')
        self.refresh_tree()
    
    def edit_item(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Error", "Please select an item to edit")
            return
        
        item = selection[0]
        item_id = self.tree.item(item)["text"]
        
        self.cursor.execute('SELECT name, price, container_size, unit, code, vendor, is_composite, synonyms FROM stock WHERE id = ?', (item_id,))
        result = self.cursor.fetchone()
        if not result:
            messagebox.showerror("Error", "Item not found")
            return
        
        name, price, size, unit, code, vendor, is_composite, synonyms = result
        if is_composite:
            if self.on_edit_composite:
                self.on_edit_composite(name)
            else:
                messagebox.showinfo("Composite Item", "This is a composite reagent. Please edit it in the Composite Creator tab.")
            return
            
        self.show_edit_dialog(item_id, (name, price, size, unit, code, vendor, synonyms))
    
    def show_edit_dialog(self, item_id, item_data):
        name, price, size, unit, code, vendor, synonyms = item_data
        
        used_in_composites = False
        try:
            self.cursor.execute('SELECT COUNT(*) FROM composite_components WHERE stock_item_id = ?', (item_id,))
            if self.cursor.fetchone()[0] > 0:
                used_in_composites = True
        except sqlite3.OperationalError:
            pass

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Edit Item: {name}")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("420x360")
        
        # Create form fields
        ttk.Label(dialog, text="Item Name:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        name_entry = ttk.Entry(dialog, width=30)
        name_entry.grid(row=0, column=1, padx=10, pady=5)
        name_entry.insert(0, name)
        
        ttk.Label(dialog, text="Synonyms (; sep):").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        synonyms_entry = ttk.Entry(dialog, width=30)
        synonyms_entry.grid(row=1, column=1, padx=10, pady=5)
        synonyms_entry.insert(0, synonyms or '')
        
        ttk.Label(dialog, text="Vendor:").grid(row=2, column=0, sticky="w", padx=10, pady=5)
        vendor_entry = ttk.Entry(dialog, width=30)
        vendor_entry.grid(row=2, column=1, padx=10, pady=5)
        vendor_entry.insert(0, vendor or '')
        
        ttk.Label(dialog, text="Code:").grid(row=3, column=0, sticky="w", padx=10, pady=5)
        code_entry = ttk.Entry(dialog, width=30)
        code_entry.grid(row=3, column=1, padx=10, pady=5)
        code_entry.insert(0, code or '')
        
        ttk.Label(dialog, text="Price (R$):").grid(row=4, column=0, sticky="w", padx=10, pady=5)
        price_entry = ttk.Entry(dialog, width=30)
        price_entry.grid(row=4, column=1, padx=10, pady=5)
        price_entry.insert(0, str(price))
        
        ttk.Label(dialog, text="Container Size:").grid(row=5, column=0, sticky="w", padx=10, pady=5)
        size_entry = ttk.Entry(dialog, width=30)
        size_entry.grid(row=5, column=1, padx=10, pady=5)
        size_entry.insert(0, str(size))
        
        ttk.Label(dialog, text="Unit:").grid(row=6, column=0, sticky="w", padx=10, pady=5)
        unit_options = ["vial", "tube", "mL", "L", "gal", "mg", "tablet", "plate", "reaction", "hour", "uL", "g"]
        unit_options.sort()
        unit_var = tk.StringVar(value=unit)
        unit_combo = ttk.Combobox(dialog, textvariable=unit_var, values=unit_options, width=27, state='disabled' if used_in_composites else 'readonly')
        unit_combo.grid(row=6, column=1, padx=10, pady=5)

        style = ttk.Style()
        style.configure("Muted.TLabel", foreground="gray")

        if used_in_composites:
            muted_label = ttk.Label(dialog, text="(Unit locked: Used in a composite reagent)", style="Muted.TLabel")
            muted_label.grid(row=7, column=0, columnspan=2, padx=10)

        result = {'action': 'cancel'}
        
        def save_changes():
            try:
                new_name = name_entry.get().strip()
                new_synonyms = synonyms_entry.get().strip()
                new_vendor = vendor_entry.get().strip()
                new_code = code_entry.get().strip()
                
                price_str = price_entry.get().strip()
                size_str = size_entry.get().strip()
                for label, val_str, entry_widget in [("Price", price_str, price_entry), ("Container Size", size_str, size_entry)]:
                    if ',' in val_str:
                        proposed = val_str.replace(',', '.')
                        if messagebox.askyesno("Convert Decimal", f"You entered '{val_str}' for {label}. Convert to '{proposed}'?", parent=dialog):
                            entry_widget.delete(0, tk.END)
                            entry_widget.insert(0, proposed)
                            if label == "Price": price_str = proposed
                            else: size_str = proposed
                        else:
                            return
                            
                new_price = float(price_str)
                new_size = float(size_str)
                new_unit = unit_var.get()
                
                if not new_name or new_price < 0 or new_size < 0:
                    messagebox.showerror("Error", "Invalid input values")
                    return
                
                last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.cursor.execute('UPDATE stock SET name = ?, price = ?, container_size = ?, unit = ?, code = ?, vendor = ?, synonyms = ?, last_updated = ?, updated_by = ? WHERE id = ?',
                                    (new_name, new_price, new_size, new_unit, new_code, new_vendor, new_synonyms, last_updated, self.current_user, item_id))
                self.conn.commit()
                self.create_backup()
                result['action'] = 'save'
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Error", "Price and size must be numbers")
        
        def cancel_edit():
            result['action'] = 'cancel'
            dialog.destroy()
        
        # Button frame
        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=9, column=0, columnspan=2, pady=15)
        ttk.Button(btn_frame, text="Save", command=save_changes).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=cancel_edit).pack(side="left", padx=5)
        
        self.root.wait_window(dialog)
        if result['action'] == 'save':
            self.refresh_tree()
    
    def search_google(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Error", "Please select an item to search")
            return
        
        item = selection[0]
        values = self.tree.item(item)["values"]
        
        # Extract name, code, and vendor from the tree values
        # Tree columns: Name(0), Code(1), Vendor(2), Price(3), Size(4), Unit(5), Per-Unit Cost(6), Last Updated(7), Remove(8)
        name = values[0] if values[0] else ""
        code = values[1] if values[1] else ""
        vendor = values[2] if values[2] else ""
        
        # Build search query
        search_terms = []
        if name:
            search_terms.append(name)
        if vendor:
            search_terms.append(vendor)
        if code:
            search_terms.append(code)
        
        if not search_terms:
            messagebox.showwarning("Error", "No search terms available for this item")
            return
        
        # Create Google search URL
        query = "+".join(search_terms)
        search_url = f"https://www.google.com/search?q={query}"
        
        # Open in default web browser
        webbrowser.open(search_url)
    
    def create_backup(self):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = os.path.join(self.backup_dir, f'stock_backup_{timestamp}.db')
        with sqlite3.connect(backup_path) as backup_conn:
            self.conn.backup(backup_conn)

    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        self.cursor.execute('SELECT id, name, price, container_size, unit, code, last_updated, vendor, is_composite FROM stock')
        for row in self.cursor.fetchall():
            item_id, name, price, size, unit, code, last_updated, vendor, is_composite = row
            per_unit_cost = price / size if size > 0 else 0
            self.tree.insert("", "end", text=item_id, values=(name, code or '', vendor or '', f"R${price:.2f}", size, unit, f"R${per_unit_cost:.3f}/{unit}", last_updated or '', "❌"))

if __name__ == "__main__":
    root = tk.Tk()
    app = StockManager(root)
    root.mainloop()