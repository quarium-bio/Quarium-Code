"""Editors for deciding who gets paid for each service, and for splitting a
single line between several payees."""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import QuariumPayees as QP
from QuariumUI import (C_BG, C_BORDER, C_DANGER, C_DONE, C_FAINT, C_MUTED, C_SURFACE,
                       C_TEXT, UI_FONT, ScrollableList, apply_modern_style,
                       format_br_currency)

CUSTOM_OPTION = "Custom..."
ADD_OPTION = "+ Add new payee..."

TITLES = {
    QP.COST_LABOR: ("Labor", "Who performed each service"),
    QP.COST_MAINTENANCE: ("Maintenance", "Which company receives the maintenance fee"),
    QP.COST_REAGENTS: ("Reagents", "Who supplies each reagent"),
}

# Only these remember a default for future projects; labour follows whoever is
# responsible for the project, which changes project to project.
DEFAULTABLE = {QP.COST_MAINTENANCE: 'service', QP.COST_REAGENTS: 'stock_item'}


class AttributionEditor(tk.Toplevel):
    def __init__(self, parent, project_id, cost_type, responsible,
                 current_user="Unknown", on_change=None):
        super().__init__(parent)
        self.project_id = project_id
        self.cost_type = cost_type
        self.responsible = responsible
        self.current_user = current_user
        self.on_change = on_change
        self.row_widgets = []

        title, subtitle = TITLES[cost_type]
        self.title(f"{title}  -  Attribution")
        self.geometry("880x540")
        self.configure(background=C_BG)
        self.transient(parent)
        self.grab_set()
        apply_modern_style(self)

        self._build_ui(title, subtitle)
        self.reload()

    # ------------------------------------------------------------------- UI

    def _build_ui(self, title, subtitle):
        outer = ttk.Frame(self, padding=(22, 18))
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text=subtitle, style="Muted.TLabel").pack(anchor="w", pady=(0, 14))

        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="ITEM", style="Caps.TLabel", width=44).pack(side="left")
        ttk.Label(head, text="AMOUNT", style="Caps.TLabel", width=14, anchor="e").pack(side="left")
        ttk.Label(head, text="PAYEE", style="Caps.TLabel").pack(side="left", padx=(14, 0))
        ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=(6, 4))

        self.list = ScrollableList(outer)
        self.list.pack(fill="both", expand=True)

        self.summary = ttk.Label(outer, text="", style="Muted.TLabel")
        self.summary.pack(anchor="w", pady=(10, 0))

        footer = ttk.Frame(self, padding=(22, 0, 22, 18))
        footer.pack(fill="x")
        ttk.Button(footer, text="Done", command=self._close, style="Primary.TButton").pack(side="right")

    def _close(self):
        if self.on_change:
            self.on_change()
        self.destroy()

    # ----------------------------------------------------------------- data

    def _payee_names(self):
        self.payees = QP.load_payees(active_only=True)
        self.by_name = {p['name']: p['id'] for p in self.payees}
        self.by_id = {p['id']: p['name'] for p in self.payees}
        return [p['name'] for p in self.payees]

    def _current_choice(self, line, splits, defaults):
        """What the dropdown should show for this line."""
        key = (line['project_service_id'], line['cost_type'], line['requirement_id'])
        allocations = splits.get(key)
        if allocations:
            if len(allocations) == 1:
                return self.by_id.get(allocations[0][0], CUSTOM_OPTION), allocations
            return CUSTOM_OPTION, allocations

        if self.cost_type == QP.COST_REAGENTS:
            fallback = defaults.get(('stock_item', line['stock_item_id'], QP.COST_REAGENTS))
            fallback = fallback or self.by_name.get(QP.DEFAULT_REAGENT_PAYEE)
        elif self.cost_type == QP.COST_MAINTENANCE:
            fallback = defaults.get(('service', line['service_id'], QP.COST_MAINTENANCE))
            fallback = fallback or self.by_name.get(QP.PROFIT_PAYEE)
        else:
            fallback = (defaults.get(('service', line['service_id'], QP.COST_LABOR))
                        or self.by_name.get(self.responsible))
        return (self.by_id.get(fallback, ""), None)

    def reload(self):
        names = self._payee_names()
        lines, _profit = QP.calculate_cost_lines(self.project_id)
        self.lines = [l for l in lines if l['cost_type'] == self.cost_type]
        splits = QP.load_splits(self.project_id)
        defaults = QP.load_defaults()

        self.list.clear()
        self.row_widgets = []

        if not self.lines:
            ttk.Label(self.list.inner, text="This project has no costs in this category.",
                      style="Muted.TLabel").pack(anchor="w", pady=10)
            self.summary.config(text="")
            return

        current_service = None
        for line in self.lines:
            # Reagents are grouped under the service that consumes them.
            if self.cost_type == QP.COST_REAGENTS and line['service_name'] != current_service:
                current_service = line['service_name']
                ttk.Label(self.list.inner, text=current_service, style="Section.TLabel").pack(
                    anchor="w", pady=(12, 2))

            choice, allocations = self._current_choice(line, splits, defaults)
            self._build_row(line, names, choice, allocations)

        total = sum(l['amount'] for l in self.lines)
        self.summary.config(text=f"{len(self.lines)} item(s)   ·   "
                                 f"category total {format_br_currency(total)}")

    def _build_row(self, line, names, choice, allocations):
        row = ttk.Frame(self.list.inner)
        row.pack(fill="x", pady=2)

        indent = 16 if self.cost_type == QP.COST_REAGENTS else 0
        label = line['label'] if self.cost_type == QP.COST_REAGENTS else line['service_name']
        ttk.Label(row, text=label, width=44 - (2 if indent else 0),
                  font=(UI_FONT, 10)).pack(side="left", padx=(indent, 0))
        ttk.Label(row, text=format_br_currency(line['amount']), width=14, anchor="e",
                  font=(UI_FONT, 10)).pack(side="left")

        var = tk.StringVar(value=choice)
        combo = ttk.Combobox(row, textvariable=var, state="readonly", width=26,
                             values=names + [CUSTOM_OPTION, ADD_OPTION])
        combo.pack(side="left", padx=(14, 6))
        combo.bind("<<ComboboxSelected>>",
                   lambda e, ln=line, v=var, c=combo: self._on_choice(ln, v, c))

        info = ttk.Label(row, text="", style="Muted.TLabel", width=14)
        info.pack(side="left")
        if allocations and len(allocations) > 1:
            info.config(text=f"split {len(allocations)} ways")

        if self.cost_type in DEFAULTABLE:
            ttk.Button(row, text="Set default", style="Tiny.TButton",
                       command=lambda ln=line, v=var: self._set_default(ln, v)).pack(side="left")

        self.row_widgets.append({'line': line, 'var': var, 'combo': combo, 'info': info})

    # -------------------------------------------------------------- actions

    def _on_choice(self, line, var, combo):
        choice = var.get()
        if choice == ADD_OPTION:
            self._add_payee(var, combo)
            return
        if choice == CUSTOM_OPTION:
            self._open_split(line, var)
            return
        payee_id = self.by_name.get(choice)
        if payee_id is None:
            return
        QP.set_split(self.project_id, line['project_service_id'], line['cost_type'],
                     line['requirement_id'], [(payee_id, 100.0)], self.current_user)
        self.reload()

    def _add_payee(self, var, combo):
        name = simpledialog.askstring("New Payee", "Name of the person or company:", parent=self)
        if not name or not name.strip():
            var.set("")
            return
        kind = "company" if messagebox.askyesno(
            "Type", f"Is '{name.strip()}' a company?\n\nYes = company, No = person",
            parent=self) else "person"
        try:
            QP.add_payee(name.strip(), kind, self.current_user)
        except Exception as e:
            messagebox.showerror("Error", f"Could not add payee: {e}", parent=self)
            return
        self.reload()

    def _set_default(self, line, var):
        choice = var.get()
        payee_id = self.by_name.get(choice)
        if payee_id is None:
            messagebox.showinfo(
                "Pick a payee first",
                "Choose a single payee for this item before making it the default.\n\n"
                "A custom split cannot be stored as a default.", parent=self)
            return
        target_type = DEFAULTABLE[self.cost_type]
        target_id = line['stock_item_id'] if target_type == 'stock_item' else line['service_id']
        if target_id is None:
            return
        label = line['label'] if target_type == 'stock_item' else line['service_name']
        if not messagebox.askyesno(
                "Set Default",
                f"Make '{choice}' the default payee for '{label}' on future projects?\n\n"
                "Existing projects are not affected.", parent=self):
            return
        QP.set_default_payee(target_type, target_id, self.cost_type, payee_id, self.current_user)
        messagebox.showinfo("Default Saved", f"'{choice}' is now the default for '{label}'.",
                            parent=self)

    def _open_split(self, line, var):
        existing = None
        splits = QP.load_splits(self.project_id)
        key = (line['project_service_id'], line['cost_type'], line['requirement_id'])
        if splits.get(key):
            existing = splits[key]
        label = line['label'] if self.cost_type == QP.COST_REAGENTS else line['service_name']
        dialog = SplitDialog(self, label, line['amount'], self.payees, existing)
        self.wait_window(dialog)
        if dialog.result is None:
            self.reload()
            return
        QP.set_split(self.project_id, line['project_service_id'], line['cost_type'],
                     line['requirement_id'], dialog.result, self.current_user)
        self.reload()


class SplitDialog(tk.Toplevel):
    """Splits one line between several payees with percentage sliders.

    Each slider is capped at whatever is still unallocated, so the total can
    never pass 100%, and the money is only accepted once it reconciles exactly
    to the line total.
    """

    def __init__(self, parent, label, amount, payees, existing=None):
        super().__init__(parent)
        self.amount = amount
        self.payees = payees
        self.by_name = {p['name']: p['id'] for p in payees}
        self.by_id = {p['id']: p['name'] for p in payees}
        self.rows = []
        self.result = None
        self._updating = False

        self.title("Custom Split")
        self.geometry("640x480")
        self.configure(background=C_BG)
        self.transient(parent)
        self.grab_set()
        apply_modern_style(self)

        outer = ttk.Frame(self, padding=(22, 18))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=label, style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text=f"Total to split: {format_br_currency(amount)}",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 14))

        self.list = ScrollableList(outer)
        self.list.pack(fill="both", expand=True)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(10, 0))
        ttk.Button(controls, text="+ Add payee", command=self._add_row,
                   style="Flat.TButton").pack(side="left")
        ttk.Button(controls, text="Split evenly", command=self._even_split,
                   style="Flat.TButton").pack(side="left", padx=6)
        self.status = ttk.Label(controls, text="", style="Muted.TLabel")
        self.status.pack(side="right")

        footer = ttk.Frame(self, padding=(22, 0, 22, 18))
        footer.pack(fill="x")
        self.ok_btn = ttk.Button(footer, text="Apply", command=self._apply, style="Primary.TButton")
        self.ok_btn.pack(side="right")
        ttk.Button(footer, text="Cancel", command=self.destroy,
                   style="Flat.TButton").pack(side="right", padx=8)

        if existing:
            for payee_id, pct in existing:
                self._add_row(self.by_id.get(payee_id), pct, refresh=False)
        else:
            names = [p['name'] for p in payees][:2]
            for name in names:
                self._add_row(name, 0, refresh=False)
            self._even_split()
        self._recalculate()

    # ------------------------------------------------------------------ rows

    def _add_row(self, name=None, pct=None, refresh=True):
        names = [p['name'] for p in self.payees]
        if not names:
            messagebox.showwarning("No payees", "Register a payee first.", parent=self)
            return
        row = ttk.Frame(self.list.inner)
        row.pack(fill="x", pady=4)

        if name is None:
            # Offer someone not already in the split, rather than a duplicate
            # that would only be rejected on Apply.
            taken = {r['var'].get() for r in self.rows}
            name = next((n for n in names if n not in taken), names[0])
        var = tk.StringVar(value=name)
        ttk.Combobox(row, textvariable=var, state="readonly", width=20,
                     values=names).pack(side="left")

        scale_var = tk.DoubleVar(value=float(pct or 0))
        scale = ttk.Scale(row, from_=0, to=100, orient="horizontal", variable=scale_var,
                          length=210, style="Split.Horizontal.TScale")
        scale.pack(side="left", padx=10)

        pct_lbl = ttk.Label(row, text="0%", width=7, anchor="e", font=(UI_FONT, 10))
        pct_lbl.pack(side="left")
        amt_lbl = ttk.Label(row, text="", width=14, anchor="e", font=(UI_FONT, 10))
        amt_lbl.pack(side="left")

        entry = {'frame': row, 'var': var, 'scale_var': scale_var, 'scale': scale,
                 'pct': pct_lbl, 'amt': amt_lbl}
        ttk.Button(row, text="✕", style="Tiny.TButton",
                   command=lambda e=entry: self._remove_row(e)).pack(side="left", padx=(6, 0))

        scale_var.trace_add("write", lambda *a, e=entry: self._on_slide(e))
        self.rows.append(entry)

        if refresh:
            self._even_split()

    def _remove_row(self, entry):
        if len(self.rows) <= 1:
            messagebox.showinfo("Split", "A split needs at least one payee.", parent=self)
            return
        entry['frame'].destroy()
        self.rows.remove(entry)
        self._even_split()

    def _even_split(self):
        if not self.rows:
            return
        self._updating = True
        share = 100.0 / len(self.rows)
        for entry in self.rows:
            entry['scale_var'].set(share)
        self._updating = False
        self._recalculate()

    def _on_slide(self, entry):
        if self._updating:
            return
        # Clamp to whatever is left, so the total can never exceed 100%.
        others = sum(e['scale_var'].get() for e in self.rows if e is not entry)
        ceiling = max(0.0, 100.0 - others)
        if entry['scale_var'].get() > ceiling:
            self._updating = True
            entry['scale_var'].set(ceiling)
            self._updating = False
        self._recalculate()

    def _recalculate(self):
        total_pct = sum(e['scale_var'].get() for e in self.rows)
        allocations = [(self.by_name.get(e['var'].get()), e['scale_var'].get()) for e in self.rows]
        amounts = QP.distribute(self.amount, allocations) if allocations else []
        for entry, (_pid, value) in zip(self.rows, amounts):
            entry['pct'].config(text=f"{entry['scale_var'].get():.1f}%")
            entry['amt'].config(text=format_br_currency(value))

        remaining = 100.0 - total_pct
        allocated = sum(a for _p, a in amounts)
        if abs(remaining) < 0.05:
            self.status.config(text=f"Allocated {format_br_currency(allocated)}  ·  balanced",
                               foreground=C_DONE)
            self.ok_btn.state(["!disabled"])
        else:
            self.status.config(text=f"{remaining:.1f}% unallocated", foreground=C_DANGER)
            self.ok_btn.state(["disabled"])

    def _apply(self):
        allocations = []
        seen = set()
        for entry in self.rows:
            payee_id = self.by_name.get(entry['var'].get())
            pct = entry['scale_var'].get()
            if payee_id is None or pct <= 0:
                continue
            if payee_id in seen:
                messagebox.showerror("Duplicate payee",
                                     f"'{entry['var'].get()}' appears more than once.", parent=self)
                return
            seen.add(payee_id)
            allocations.append((payee_id, pct))
        if not allocations:
            messagebox.showerror("Split", "Give at least one payee a share above zero.", parent=self)
            return
        total = sum(p for _i, p in allocations)
        if abs(total - 100.0) > 0.05:
            messagebox.showerror("Split", f"The shares total {total:.1f}%, not 100%.", parent=self)
            return
        self.result = allocations
        self.destroy()
