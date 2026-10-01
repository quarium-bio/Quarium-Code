"""The people and companies Quarium pays, and what is needed to pay them.

Payees began as names to attribute a cost to, created in passing from the
attribution editor. They are also who money is actually sent to, which needs
a document number, an email and somewhere to send it. This is where that is
kept and corrected.

A payee is never deleted. Projects, settlements and ledger entries reference
them by id, so removing one would leave attributions pointing at nothing;
retiring instead keeps every past project readable while taking the name out
of the pickers.
"""

import tkinter as tk
from tkinter import messagebox, ttk

import QuariumPayees as QP
from QuariumUI import (C_BG, C_DANGER, C_FAINT, C_MUTED, C_TEXT, UI_FONT,
                       apply_modern_style, format_cpf_cnpj, is_valid_cnpj,
                       is_valid_cpf)

KIND_LABEL = {'person': 'PF', 'company': 'PJ'}
KIND_FULL = {'person': 'Pessoa Física', 'company': 'Pessoa Jurídica'}

# label, field, row, column, span. The short ones are paired across two
# columns; anything that can run long gets the full width. Stacking all seven
# pushed the buttons off the bottom of the content area.
FIELDS = [
    ("Name", 'name', 0, 0, 2),
    ("CPF / CNPJ", 'cpf_cnpj', 1, 0, 1),
    ("Phone", 'phone', 1, 1, 1),
    ("Email", 'email', 2, 0, 1),
    ("PIX key", 'pix_key', 2, 1, 1),
    ("Bank details", 'bank_details', 3, 0, 2),
    ("Address", 'address', 4, 0, 2),
]
FIELD_NAMES = [field for _label, field, _r, _c, _s in FIELDS]
COLUMN_W = 186


class PayeeManager:
    def __init__(self, root, current_user="Unknown"):
        self.root = root
        self.current_user = current_user
        self.payees = []
        self.selected_id = None
        self.vars = {}

        QP.init_payee_db()
        apply_modern_style(self.root)
        self.create_ui()
        self.load_payees()

    # ------------------------------------------------------------------- UI

    def create_ui(self):
        outer = ttk.Frame(self.root, padding=(16, 14))
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Payee Manager", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Who gets paid for labour, reagents and maintenance, "
                              "and what is needed to pay them.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 12))

        columns = ttk.Frame(outer)
        columns.pack(fill="both", expand=True)

        left = ttk.Frame(columns)
        left.pack(side="left", fill="both", expand=True, padx=(0, 18))

        filters = ttk.Frame(left)
        filters.pack(fill="x", pady=(0, 6))
        self.show_retired = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text="Show retired", variable=self.show_retired,
                        command=self.load_payees).pack(side="left")
        self.count_label = ttk.Label(filters, text="", style="Muted.TLabel")
        self.count_label.pack(side="right")

        self.tree = ttk.Treeview(left, columns=("Type", "Document", "Email"),
                                 show="tree headings", height=18)
        self.tree.heading("#0", text="Name")
        self.tree.heading("Type", text="Type")
        self.tree.heading("Document", text="CPF / CNPJ")
        self.tree.heading("Email", text="Email")
        self.tree.column("#0", width=200)
        self.tree.column("Type", width=52, anchor="center")
        self.tree.column("Document", width=140)
        self.tree.column("Email", width=180)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.tag_configure('retired', foreground=C_MUTED)

        right = ttk.Frame(columns)
        right.pack(side="left", fill="y")

        ttk.Label(right, text="DETAILS", font=(UI_FONT, 8, "bold"),
                  foreground=C_FAINT).pack(anchor="w")
        self.subtitle = ttk.Label(right, text="Select a payee on the left.",
                                  style="Muted.TLabel")
        self.subtitle.pack(anchor="w", pady=(0, 10))

        form = ttk.Frame(right)
        form.pack(fill="x")
        form.columnconfigure(0, minsize=COLUMN_W, weight=1)
        form.columnconfigure(1, minsize=COLUMN_W, weight=1)
        for label, field, row, column, span in FIELDS:
            ttk.Label(form, text=label, font=(UI_FONT, 9)).grid(
                row=row * 2, column=column, columnspan=span, sticky="w", pady=(5, 0))
            variable = tk.StringVar()
            self.vars[field] = variable
            ttk.Entry(form, textvariable=variable, font=(UI_FONT, 10)).grid(
                row=row * 2 + 1, column=column, columnspan=span, sticky="we",
                padx=(0, 8 if span == 1 and column == 0 else 0))

        kind_row = ttk.Frame(right)
        kind_row.pack(fill="x", pady=(12, 0))
        ttk.Label(kind_row, text="Type", font=(UI_FONT, 9)).pack(side="left", padx=(0, 10))
        self.vars['kind'] = tk.StringVar(value='person')
        for kind in ('person', 'company'):
            ttk.Radiobutton(kind_row, text=f"{KIND_LABEL[kind]}  ·  {KIND_FULL[kind]}",
                            value=kind, variable=self.vars['kind'],
                            style="Basis.TRadiobutton").pack(side="left", padx=(0, 12))

        ttk.Label(right, text="Notes", font=(UI_FONT, 9)).pack(anchor="w", pady=(10, 0))
        self.notes = tk.Text(right, height=2, width=44, font=(UI_FONT, 10),
                             wrap="word", relief="solid", borderwidth=1)
        self.notes.pack(fill="x")

        self.message = ttk.Label(right, text="", style="Muted.TLabel", wraplength=380,
                                 justify="left")
        self.message.pack(anchor="w", pady=(10, 0))

        self.usage_label = ttk.Label(right, text="", style="Muted.TLabel", wraplength=380,
                                     justify="left")
        self.usage_label.pack(anchor="w", pady=(8, 0))

        # Packed last so that if anything has to be squeezed, it is the text
        # above and never the controls.
        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(12, 0))
        self.save_btn = ttk.Button(buttons, text="Save Changes", style="Primary.TButton",
                                   command=self.save, state="disabled")
        self.save_btn.pack(side="left")
        self.retire_btn = ttk.Button(buttons, text="Retire", command=self.toggle_retired,
                                     state="disabled")
        self.retire_btn.pack(side="left", padx=6)
        ttk.Button(buttons, text="New Payee", command=self.new_payee).pack(side="right")

    # ---------------------------------------------------------------- data

    def load_payees(self):
        self.payees = QP.load_payees(active_only=not self.show_retired.get())
        keep = self.selected_id
        self.tree.delete(*self.tree.get_children())
        for payee in self.payees:
            tags = () if payee['active'] else ('retired',)
            label = payee['name'] + ("" if payee['active'] else "   (retired)")
            self.tree.insert("", "end", iid=str(payee['id']), text=label,
                             values=(KIND_LABEL.get(payee['kind'], payee['kind']),
                                     format_cpf_cnpj(payee['cpf_cnpj']),
                                     payee['email']),
                             tags=tags)
        people = sum(1 for p in self.payees if p['kind'] == 'person')
        self.count_label.config(
            text=f"{len(self.payees)} payee(s)  ·  {people} PF, {len(self.payees) - people} PJ")

        if keep is not None and self.tree.exists(str(keep)):
            self.tree.selection_set(str(keep))
        else:
            self.clear_form()

    def on_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        self.selected_id = int(selection[0])
        payee = next((p for p in self.payees if p['id'] == self.selected_id), None)
        if not payee:
            return
        for field in FIELD_NAMES:
            self.vars[field].set(payee.get(field, "") or "")
        self.vars['kind'].set(payee['kind'] if payee['kind'] in KIND_LABEL else 'person')
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', payee.get('notes', '') or "")

        self.subtitle.config(text=f"{KIND_FULL.get(payee['kind'], payee['kind'])}"
                                  + (" · retired" if not payee['active'] else ""))
        self.message.config(text="", style="Muted.TLabel")
        self.save_btn.config(state="normal")
        self.retire_btn.config(state="normal",
                               text="Bring Back" if not payee['active'] else "Retire")
        self.show_usage(payee)

    def show_usage(self, payee):
        usage = QP.payee_usage(payee['id'])
        parts = []
        if usage['projects']:
            parts.append(f"{usage['projects']} project(s)")
        if usage['settlements']:
            parts.append(f"{usage['settlements']} payment(s) recorded")
        if usage['defaults']:
            parts.append(f"{usage['defaults']} default(s)")
        if usage['ledger_entries']:
            parts.append(f"{usage['ledger_entries']} ledger entr(ies)")
        self.usage_label.config(
            text=("Referenced by " + ", ".join(parts) + "." if parts
                  else "Not referenced by any project yet."))

    def clear_form(self):
        self.selected_id = None
        for field in FIELD_NAMES:
            self.vars[field].set("")
        self.vars['kind'].set('person')
        self.notes.delete('1.0', 'end')
        self.subtitle.config(text="Select a payee on the left.")
        self.message.config(text="", style="Muted.TLabel")
        self.usage_label.config(text="")
        self.save_btn.config(state="disabled")
        self.retire_btn.config(state="disabled", text="Retire")

    # -------------------------------------------------------------- actions

    def _document_problem(self, document, kind):
        """A document number is optional, but a wrong one is worth catching
        before it ends up on a transfer."""
        if not document.strip():
            return None
        if kind == 'person':
            return None if is_valid_cpf(document) else (
                "That is not a valid CPF. A pessoa física needs 11 digits with "
                "matching check digits.")
        return None if is_valid_cnpj(document) else (
            "That is not a valid CNPJ. A pessoa jurídica needs 14 digits with "
            "matching check digits.")

    def save(self):
        if self.selected_id is None:
            return
        values = {field: self.vars[field].get().strip() for field in FIELD_NAMES}
        values['kind'] = self.vars['kind'].get()
        values['notes'] = self.notes.get('1.0', 'end-1c').strip()

        if not values['name']:
            self.message.config(text="A payee needs a name.", style="Danger.TLabel")
            return

        problem = self._document_problem(values['cpf_cnpj'], values['kind'])
        if problem and not messagebox.askyesno(
                "Check the document number", f"{problem}\n\nSave it anyway?",
                parent=self.root):
            return
        if values['cpf_cnpj']:
            values['cpf_cnpj'] = format_cpf_cnpj(values['cpf_cnpj'])

        clash = next((p for p in QP.load_payees(active_only=False)
                      if p['name'].lower() == values['name'].lower()
                      and p['id'] != self.selected_id), None)
        if clash:
            self.message.config(text=f"Another payee is already called '{clash['name']}'.",
                                style="Danger.TLabel")
            return

        try:
            QP.update_payee(self.selected_id, self.current_user, **values)
        except (ValueError, Exception) as e:
            self.message.config(text=f"Could not save: {e}", style="Danger.TLabel")
            return
        self.load_payees()
        self.message.config(text="Saved.", style="Muted.TLabel")

    def new_payee(self):
        import QuariumAttribution
        dialog = QuariumAttribution.NewPayeeDialog(self.root)
        self.root.wait_window(dialog)
        if not dialog.result:
            return
        name, kind = dialog.result
        try:
            payee_id = QP.add_payee(name, kind, self.current_user)
        except Exception as e:
            messagebox.showerror("New Payee", f"Could not add payee: {e}", parent=self.root)
            return
        self.selected_id = payee_id
        self.load_payees()
        if self.tree.exists(str(payee_id)):
            self.tree.selection_set(str(payee_id))
            self.tree.see(str(payee_id))
        self.message.config(text="Added. Fill in the details and save.", style="Muted.TLabel")

    def toggle_retired(self):
        if self.selected_id is None:
            return
        payee = next((p for p in self.payees if p['id'] == self.selected_id), None)
        if not payee:
            return
        if payee['active']:
            usage = QP.payee_usage(self.selected_id)
            referenced = sum(usage.values())
            if not messagebox.askyesno(
                    "Retire Payee",
                    f"Retire '{payee['name']}'?\n\nThey will stop appearing when "
                    "attributing costs on new projects."
                    + (f"\n\nEvery past project keeps them: {usage['projects']} project(s) "
                       f"and {usage['settlements']} recorded payment(s) still reference "
                       "this payee, and are not touched." if referenced else ""),
                    parent=self.root):
                return
        QP.set_payee_active(self.selected_id, not payee['active'])
        if not payee['active']:
            self.show_retired.set(True)
        self.load_payees()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Payee Manager")
    root.geometry("1150x640")
    app = PayeeManager(root)
    root.mainloop()
