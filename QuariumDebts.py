"""Debts and Credits: what is owed per project and payee, the credit/advance
ledger for payees and clients, and a per-payee statement."""

import os
import sys
import sqlite3
import webbrowser
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import QuariumPayees as QP
from QuariumUI import (C_BG, C_BORDER, C_DANGER, C_DONE, C_FAINT, C_MUTED, C_SURFACE,
                       C_TEXT, UI_FONT, ScrollableList, apply_modern_style,
                       format_br_currency, rounded_rect)

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# Working data lives under %LOCALAPPDATA%, not beside the program: keeping
# live SQLite files inside the OneDrive-synced project folder meant two sync
# engines replicating the same open databases. Source runs get a separate
# workspace so testing cannot disturb live data.
from QuariumPaths import data_dir

_BASE_DIR = data_dir()

ALL_PAYEES = "All payees"


def _clip(text, width):
    """ttk's width= counts characters, so trim to fit rather than overflow
    into the next column."""
    text = text or ""
    return text if len(text) <= width else text[:width - 1].rstrip() + "…"


class DebtsManager:
    def __init__(self, root, current_user="Unknown"):
        self.root = root
        self.current_user = current_user
        QP.init_all(current_user)
        apply_modern_style(root)
        self.create_ui()
        self.reload()

    # ------------------------------------------------------------------- UI

    def create_ui(self):
        main = ttk.Frame(self.root, padding=(20, 16))
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main)
        header.pack(fill="x")
        ttk.Label(header, text="Debts and Credits", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Refresh", command=self.reload,
                   style="Flat.TButton").pack(side="right")

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True, pady=(12, 0))

        self.owed_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.owed_tab, text="Amounts Owed")
        self._build_owed_tab()

        self.ledger_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.ledger_tab, text="Credits and Advances")
        self._build_ledger_tab()

    def _build_owed_tab(self):
        legend = tk.Canvas(self.owed_tab, height=24, highlightthickness=0, background=C_BG)
        legend.pack(fill="x")
        x = 2
        for key, label, colour in QP.STATUS_ORDER:
            rounded_rect(legend, x, 6, x + 13, 19, 3, fill=colour, outline="")
            legend.create_text(x + 19, 12, text=label, anchor="w",
                               font=(UI_FONT, 8), fill=C_MUTED)
            x += 9 * len(label) + 34

        filt = ttk.Frame(self.owed_tab)
        filt.pack(fill="x", pady=(10, 4))
        ttk.Label(filt, text="Payee:", style="Muted.TLabel").pack(side="left")
        self.filter_var = tk.StringVar(value=ALL_PAYEES)
        self.filter_combo = ttk.Combobox(filt, textvariable=self.filter_var, state="readonly",
                                         width=28)
        self.filter_combo.pack(side="left", padx=8)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda e: self.reload_owed())
        self.hide_paid = tk.BooleanVar(value=False)
        ttk.Checkbutton(filt, text="Hide settled", variable=self.hide_paid,
                        command=self.reload_owed).pack(side="left", padx=6)
        self.owed_summary = ttk.Label(filt, text="", style="Muted.TLabel")
        self.owed_summary.pack(side="right")

        head = ttk.Frame(self.owed_tab)
        head.pack(fill="x", pady=(6, 2))
        for text, width in (("PROJECT", 14), ("CLIENT", 24), ("PAYEE", 24),
                            ("TOTAL OWED", 16), ("STATUS", 22)):
            ttk.Label(head, text=text, style="Caps.TLabel", width=width).pack(side="left")
        ttk.Separator(self.owed_tab, orient="horizontal").pack(fill="x")

        self.owed_list = ScrollableList(self.owed_tab)
        self.owed_list.pack(fill="both", expand=True, pady=(4, 0))

        footer = ttk.Frame(self.owed_tab)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="Generate Payee Report", command=self.generate_report,
                   style="Primary.TButton").pack(side="left")
        ttk.Label(footer, text="Select a payee above, then generate their statement.",
                  style="Muted.TLabel").pack(side="left", padx=10)

    def _build_ledger_tab(self):
        ttk.Label(self.ledger_tab,
                  text="A credit increases what we owe. A debt, such as an advance paid "
                       "before the work, is deducted from the next payment.",
                  style="Muted.TLabel", wraplength=800).pack(anchor="w")

        controls = ttk.Frame(self.ledger_tab)
        controls.pack(fill="x", pady=(10, 6))
        ttk.Button(controls, text="+ Add entry", command=self.add_entry,
                   style="Primary.TButton").pack(side="left")
        ttk.Button(controls, text="Mark as applied", command=self.apply_entry,
                   style="Flat.TButton").pack(side="left", padx=6)
        ttk.Button(controls, text="Cancel entry", command=self.cancel_entry,
                   style="Flat.TButton").pack(side="left")
        self.show_closed = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Show applied and cancelled", variable=self.show_closed,
                        command=self.reload_ledger).pack(side="left", padx=12)

        columns = ("Party", "Type", "Amount", "Description", "Status", "Created")
        self.ledger_tree = ttk.Treeview(self.ledger_tab, columns=columns, show="headings", height=14)
        widths = {"Party": 190, "Type": 80, "Amount": 120, "Description": 260,
                  "Status": 90, "Created": 130}
        for c in columns:
            self.ledger_tree.heading(c, text=c)
            self.ledger_tree.column(c, width=widths[c],
                                    anchor="e" if c == "Amount" else "w")
        self.ledger_tree.pack(fill="both", expand=True)
        self.ledger_tree.tag_configure("debt", foreground=C_DANGER)
        self.ledger_tree.tag_configure("credit", foreground=C_DONE)
        self.ledger_tree.tag_configure("closed", foreground=C_FAINT)

        self.ledger_summary = ttk.Label(self.ledger_tab, text="", style="Muted.TLabel")
        self.ledger_summary.pack(anchor="w", pady=(8, 0))

    # ----------------------------------------------------------------- data

    def reload(self):
        self.payees = QP.load_payees(active_only=False)
        names = [p['name'] for p in self.payees]
        self.filter_combo['values'] = [ALL_PAYEES] + names
        if self.filter_var.get() not in [ALL_PAYEES] + names:
            self.filter_var.set(ALL_PAYEES)
        self.reload_owed()
        self.reload_ledger()

    def _selected_payee_id(self):
        name = self.filter_var.get()
        if name == ALL_PAYEES:
            return None
        return next((p['id'] for p in self.payees if p['name'] == name), None)

    def reload_owed(self):
        self.owed_list.clear()
        payee_id = self._selected_payee_id()
        try:
            rows = QP.payee_obligations(payee_id=payee_id,
                                        include_paid=not self.hide_paid.get())
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", str(e))
            return

        if not rows:
            ttk.Label(self.owed_list.inner, text="Nothing owed for this selection.",
                      style="Muted.TLabel").pack(anchor="w", pady=12)
            self.owed_summary.config(text="")
            return

        for row in rows:
            line = ttk.Frame(self.owed_list.inner)
            line.pack(fill="x", pady=2)
            ttk.Label(line, text=_clip(row['estimate_number'], 14), width=14,
                      font=(UI_FONT, 10)).pack(side="left")
            ttk.Label(line, text=_clip(row['client'], 23), width=24,
                      font=(UI_FONT, 10)).pack(side="left")
            ttk.Label(line, text=_clip(row['payee_name'], 23), width=24,
                      font=(UI_FONT, 10)).pack(side="left")
            ttk.Label(line, text=format_br_currency(row['amount']), width=16, anchor="e",
                      font=(UI_FONT, 10)).pack(side="left")

            swatch = tk.Canvas(line, width=16, height=16, highlightthickness=0, background=C_BG)
            swatch.pack(side="left", padx=(14, 6))
            rounded_rect(swatch, 1, 2, 15, 15, 3,
                         fill=QP.STATUS_COLORS[row['status']], outline="")
            ttk.Label(line, text=QP.STATUS_LABELS[row['status']],
                      font=(UI_FONT, 9), foreground=C_MUTED).pack(side="left")

        outstanding = sum(r['amount'] for r in rows if not r['settled'])
        self.owed_summary.config(
            text=f"{len(rows)} line(s)   ·   outstanding {format_br_currency(outstanding)}")

    def reload_ledger(self):
        for item in self.ledger_tree.get_children():
            self.ledger_tree.delete(item)
        by_id = {p['id']: p['name'] for p in self.payees}
        clients = self._client_names()

        entries = QP.load_ledger()
        shown = 0
        for entry in entries:
            if entry['status'] != 'open' and not self.show_closed.get():
                continue
            shown += 1
            party = (by_id.get(entry['party_id'], f"#{entry['party_id']}")
                     if entry['party_type'] == 'payee'
                     else clients.get(entry['party_id'], f"Client #{entry['party_id']}"))
            sign = "+" if entry['kind'] == 'credit' else "-"
            tag = "closed" if entry['status'] != 'open' else entry['kind']
            self.ledger_tree.insert(
                "", "end", iid=str(entry['id']),
                values=(f"{party}  ({entry['party_type']})",
                        entry['kind'].capitalize(),
                        f"{sign} {format_br_currency(entry['amount'])}",
                        entry['description'], entry['status'],
                        (entry['created_at'] or "").split('.')[0]),
                tags=(tag,))

        open_payee = sum((e['amount'] if e['kind'] == 'credit' else -e['amount'])
                         for e in entries if e['status'] == 'open' and e['party_type'] == 'payee')
        open_client = sum((e['amount'] if e['kind'] == 'credit' else -e['amount'])
                          for e in entries if e['status'] == 'open' and e['party_type'] == 'client')
        self.ledger_summary.config(
            text=f"{shown} entry(ies) shown   ·   open payee balance "
                 f"{format_br_currency(open_payee)}   ·   open client balance "
                 f"{format_br_currency(open_client)}")

    @staticmethod
    def _client_names():
        path = os.path.join(_BASE_DIR, 'clients.db')
        if not os.path.exists(path):
            return {}
        try:
            conn = sqlite3.connect(path)
            rows = conn.execute("SELECT id, name FROM clients").fetchall()
            conn.close()
            return {r[0]: r[1] for r in rows}
        except sqlite3.Error:
            return {}

    # -------------------------------------------------------------- actions

    def add_entry(self):
        dialog = LedgerEntryDialog(self.root, self.payees, self._client_names(),
                                   self.current_user)
        self.root.wait_window(dialog)
        if dialog.saved:
            self.reload()

    def _selected_entry(self):
        selection = self.ledger_tree.selection()
        if not selection:
            messagebox.showwarning("Select an entry", "Pick a ledger entry first.")
            return None
        return int(selection[0])

    def apply_entry(self):
        entry_id = self._selected_entry()
        if entry_id is None:
            return
        if messagebox.askyesno("Mark as applied",
                               "Mark this entry as applied?\n\nIt stops counting towards the "
                               "open balance but stays on the record."):
            QP.apply_ledger_entry(entry_id, current_user=self.current_user)
            self.reload()

    def cancel_entry(self):
        entry_id = self._selected_entry()
        if entry_id is None:
            return
        if messagebox.askyesno("Cancel entry", "Cancel this entry?"):
            QP.cancel_ledger_entry(entry_id)
            self.reload()

    def generate_report(self):
        payee_id = self._selected_payee_id()
        if payee_id is None:
            messagebox.showinfo("Pick a payee",
                                "Choose a specific payee above to generate their statement.")
            return
        statement = QP.payee_statement(payee_id)
        if not statement['obligations'] and not statement['ledger']:
            messagebox.showinfo("Nothing to report",
                                "This payee has no projects and no ledger entries.")
            return
        if not REPORTLAB_AVAILABLE:
            messagebox.showerror("Dependency Missing",
                                 "reportlab is required to generate the report.\n\n"
                                 "Install it with: pip install reportlab")
            return

        safe = "".join(ch for ch in statement['payee']['name'] if ch.isalnum() or ch in " -_").strip()
        path = filedialog.asksaveasfilename(
            title="Save payee statement", defaultextension=".pdf",
            initialfile=f"Statement_{safe}_{datetime.now():%Y%m%d}.pdf",
            filetypes=[("PDF Files", "*.pdf")])
        if not path:
            return
        try:
            build_payee_report(statement, path)
        except Exception as e:
            messagebox.showerror("Report Error", f"Could not generate the report: {e}")
            return
        if messagebox.askyesno("Report Ready", f"Saved to:\n{path}\n\nOpen it now?"):
            webbrowser.open(path)

    def on_closing(self):
        pass


def build_payee_report(statement, path):
    """Renders one payee's statement: every project they are owed on, its
    status, and any open advances or credits."""
    payee = statement['payee']
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle('T', parent=styles['Heading1'], fontSize=16, spaceAfter=4)
    sub = ParagraphStyle('S', parent=styles['Normal'], fontSize=9, textColor=rl_colors.grey)
    section = ParagraphStyle('H', parent=styles['Heading2'], fontSize=11, spaceBefore=14,
                             spaceAfter=6)

    story = [Paragraph(f"Statement  -  {payee['name'] if payee else 'Unknown'}", title),
             Paragraph(f"Generated {datetime.now():%d/%m/%Y %H:%M}", sub),
             Spacer(1, 10)]

    story.append(Paragraph("Projects", section))
    data = [["Project", "Client", "Amount", "Status"]]
    for row in statement['obligations']:
        data.append([row['estimate_number'], row['client'][:30],
                     format_br_currency(row['amount']),
                     QP.STATUS_LABELS[row['status']]])
    if len(data) == 1:
        data.append(["-", "-", "-", "-"])
    table = Table(data, colWidths=[3 * cm, 6 * cm, 3.2 * cm, 4.8 * cm])
    style = [('BACKGROUND', (0, 0), (-1, 0), rl_colors.HexColor("#F1F3F5")),
             ('TEXTCOLOR', (0, 0), (-1, 0), rl_colors.HexColor("#374151")),
             ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
             ('FONTSIZE', (0, 0), (-1, -1), 9),
             ('ALIGN', (2, 1), (2, -1), 'RIGHT'),
             ('GRID', (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#DDE1E6")),
             ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
             ('TOPPADDING', (0, 0), (-1, -1), 5),
             ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]
    # Colour the status cell to match the on-screen square.
    for i, row in enumerate(statement['obligations'], start=1):
        style.append(('TEXTCOLOR', (3, i), (3, i),
                      rl_colors.HexColor(QP.STATUS_COLORS[row['status']])))
    table.setStyle(TableStyle(style))
    story.append(table)

    if statement['ledger']:
        story.append(Paragraph("Open credits and advances", section))
        ldata = [["Type", "Amount", "Description", "Created"]]
        for entry in statement['ledger']:
            sign = "+" if entry['kind'] == 'credit' else "-"
            ldata.append([entry['kind'].capitalize(),
                          f"{sign} {format_br_currency(entry['amount'])}",
                          entry['description'][:48],
                          (entry['created_at'] or "").split(' ')[0]])
        ltable = Table(ldata, colWidths=[2.4 * cm, 3.2 * cm, 8.4 * cm, 3 * cm])
        ltable.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), rl_colors.HexColor("#F1F3F5")),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (1, 1), (1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#DDE1E6")),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
        story.append(ltable)

    story.append(Paragraph("Summary", section))
    summary = [["Outstanding on projects", format_br_currency(statement['total_owed'])],
               ["Already settled", format_br_currency(statement['total_paid'])],
               ["Credits less advances", format_br_currency(statement['adjustment'])],
               ["Net due", format_br_currency(statement['net_due'])]]
    stable = Table(summary, colWidths=[9 * cm, 4 * cm])
    stable.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, -1), (-1, -1), 0.8, rl_colors.HexColor("#374151")),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    story.append(stable)

    doc.build(story)
    return path


class LedgerEntryDialog(tk.Toplevel):
    """Records a credit or an advance against a payee or a client."""

    def __init__(self, parent, payees, clients, current_user):
        super().__init__(parent)
        self.payees = payees
        self.clients = clients
        self.current_user = current_user
        self.saved = False

        self.title("New Credit or Advance")
        self.geometry("520x400")
        self.configure(background=C_BG)
        self.transient(parent)
        self.grab_set()
        apply_modern_style(self)

        frm = ttk.Frame(self, padding=(22, 18))
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Applies to:").grid(row=0, column=0, sticky="w", pady=6)
        self.party_type = tk.StringVar(value="payee")
        types = ttk.Frame(frm)
        types.grid(row=0, column=1, sticky="w")
        for label, value in (("Payee", "payee"), ("Client", "client")):
            ttk.Radiobutton(types, text=label, value=value, variable=self.party_type,
                            command=self._refresh_parties).pack(side="left", padx=(0, 12))

        ttk.Label(frm, text="Who:").grid(row=1, column=0, sticky="w", pady=6)
        self.party_var = tk.StringVar()
        self.party_combo = ttk.Combobox(frm, textvariable=self.party_var, state="readonly")
        self.party_combo.grid(row=1, column=1, sticky="ew", pady=6)

        ttk.Label(frm, text="Kind:").grid(row=2, column=0, sticky="w", pady=6)
        self.kind = tk.StringVar(value="debt")
        kinds = ttk.Frame(frm)
        kinds.grid(row=2, column=1, sticky="w")
        ttk.Radiobutton(kinds, text="Advance / debt  (deduct later)", value="debt",
                        variable=self.kind).pack(anchor="w")
        ttk.Radiobutton(kinds, text="Credit  (we owe more)", value="credit",
                        variable=self.kind).pack(anchor="w")

        ttk.Label(frm, text="Amount (R$):").grid(row=3, column=0, sticky="w", pady=6)
        self.amount_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.amount_var, width=16).grid(row=3, column=1,
                                                                    sticky="w", pady=6)

        ttk.Label(frm, text="Description:").grid(row=4, column=0, sticky="nw", pady=6)
        self.description = tk.Text(frm, height=5, wrap="word")
        self.description.grid(row=4, column=1, sticky="ew", pady=6)

        footer = ttk.Frame(self, padding=(22, 0, 22, 18))
        footer.pack(fill="x")
        ttk.Button(footer, text="Save", command=self._save,
                   style="Primary.TButton").pack(side="right")
        ttk.Button(footer, text="Cancel", command=self.destroy,
                   style="Flat.TButton").pack(side="right", padx=8)

        self._refresh_parties()

    def _refresh_parties(self):
        if self.party_type.get() == "payee":
            self._options = {p['name']: p['id'] for p in self.payees}
        else:
            self._options = {name: cid for cid, name in self.clients.items()}
        names = sorted(self._options)
        self.party_combo['values'] = names
        self.party_var.set(names[0] if names else "")

    def _save(self):
        party_id = self._options.get(self.party_var.get())
        if party_id is None:
            messagebox.showwarning("Who?", "Choose who this applies to.", parent=self)
            return
        raw = self.amount_var.get().strip().replace('.', '').replace(',', '.')
        try:
            amount = float(raw)
        except ValueError:
            messagebox.showerror("Amount", "Enter a valid amount.", parent=self)
            return
        if amount <= 0:
            messagebox.showerror("Amount", "The amount must be greater than zero.", parent=self)
            return
        try:
            QP.add_ledger_entry(self.party_type.get(), party_id, self.kind.get(), amount,
                                self.description.get('1.0', 'end-1c').strip(),
                                None, self.current_user)
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)
            return
        self.saved = True
        self.destroy()
