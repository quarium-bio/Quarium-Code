"""Payee attribution and the debts/credits ledger.

Costs themselves are already modelled: service_costs carries Labor,
Maintenance and Profit, and service_requirements x stock gives Reagents.
What this module adds is *attribution* -- who receives each slice -- plus a
signed ledger for advances and credits.

Payees live in their own synced database because they are referenced from
projects.db (splits, settlements), services.db (per-service defaults) and
stock.db (per-reagent defaults), and SQLite has no cross-database keys.
"""

import os
import sys
import json
import math
import sqlite3
from datetime import datetime

# Working data lives under %LOCALAPPDATA%, not beside the program: keeping
# live SQLite files inside the OneDrive-synced project folder meant two sync
# engines replicating the same open databases. Source runs get a separate
# workspace so testing cannot disturb live data.
from QuariumPaths import data_dir

_BASE_DIR = data_dir()

PAYEE_DB = 'payees.db'
PROJECT_DB = 'projects.db'
SERVICE_DB = 'services.db'
STOCK_DB = 'stock.db'

COST_LABOR = 'Labor'
COST_MAINTENANCE = 'Maintenance'
COST_REAGENTS = 'Reagents'
COST_PROFIT = 'Profit'

# Attribution defaults confirmed with the business: maintenance is Quarium's,
# reagents come through LNP, and profit is always Quarium's.
DEFAULT_MAINTENANCE_PAYEE = 'Quarium'
DEFAULT_REAGENT_PAYEE = 'LNP'
PROFIT_PAYEE = 'Quarium'

# The three buckets that can be owed to someone, and so the three a payout
# decision can be taken on. Profit is never chosen: it is the residual, what
# the money the client paid leaves over once these are settled.
ATTRIBUTABLE = (COST_REAGENTS, COST_LABOR, COST_MAINTENANCE)

# Which prices a bucket is paid at. The original is the cost basis recorded
# when the project was approved; the adjusted is what that same work costs on
# the day the payment is confirmed.
BASIS_ORIGINAL = 'original'
BASIS_ADJUSTED = 'adjusted'

# Finance progress segments. C and E reuse the pre-existing invoice columns so
# the Project Flow dialog and the finance view can never disagree; F is derived
# from whether every payee on the project has been settled.
SEGMENTS = [
    ('data_sent_to_client', 'A', 'Project Data Sent'),
    ('data_approved_by_client', 'B', 'Data Approved by Client'),
    ('invoice_sent', 'C', 'NF Sent to Client'),
    ('boleto_sent', 'D', 'Boleto Sent to Client'),
    ('invoice_paid', 'E', 'Payment Received'),
    (None, 'F', 'Debts Settled'),  # derived
]

NEW_PROJECT_COLUMNS = [
    'data_sent_to_client INTEGER DEFAULT 0',
    'data_approved_by_client INTEGER DEFAULT 0',
    'boleto_sent INTEGER DEFAULT 0',
]


def _path(name):
    return os.path.join(_BASE_DIR, name)


def _connect(name):
    return sqlite3.connect(_path(name))


# --------------------------------------------------------------------- schema

def init_payee_db():
    conn = _connect(PAYEE_DB)
    try:
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payees (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL DEFAULT 'person',
                active INTEGER NOT NULL DEFAULT 1,
                notes TEXT,
                created_at TEXT,
                created_by TEXT
            )
        ''')
        # One default per (what, which, cost type): a service's maintenance
        # payee, or a reagent's supplier, remembered for future projects.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payee_defaults (
                id INTEGER PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                cost_type TEXT NOT NULL,
                payee_id INTEGER NOT NULL,
                updated_at TEXT,
                updated_by TEXT,
                UNIQUE (target_type, target_id, cost_type)
            )
        ''')
        # Signed ledger: a credit increases what we owe the party, a debt
        # (an advance, say) reduces their next payout.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS ledger_entries (
                id INTEGER PRIMARY KEY,
                party_type TEXT NOT NULL,
                party_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                amount REAL NOT NULL,
                description TEXT,
                project_id INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                applied_project_id INTEGER,
                applied_at TEXT,
                created_at TEXT,
                created_by TEXT
            )
        ''')
        conn.commit()
    finally:
        conn.close()


def init_project_tables():
    conn = _connect(PROJECT_DB)
    try:
        cur = conn.cursor()
        for column in NEW_PROJECT_COLUMNS:
            try:
                cur.execute(f'ALTER TABLE projects ADD COLUMN {column}')
            except sqlite3.OperationalError:
                pass
        # A line is (project_service, cost type, optional stock item). A
        # service may list the same reagent on several requirement rows, but
        # they are always sourced together, so they are summed into one line.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS project_cost_splits (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                project_service_id INTEGER NOT NULL,
                cost_type TEXT NOT NULL,
                stock_item_id INTEGER,
                payee_id INTEGER NOT NULL,
                percentage REAL NOT NULL DEFAULT 100,
                updated_at TEXT,
                updated_by TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payee_settlements (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                payee_id INTEGER NOT NULL,
                paid INTEGER NOT NULL DEFAULT 0,
                paid_at TEXT,
                paid_by TEXT,
                UNIQUE (project_id, payee_id)
            )
        ''')
        # The cost basis as it stood when the project was approved. Written
        # once, at approval, and never recalculated: approval is when the
        # client agrees the price, and project_services.calculated_cost keeps
        # a per-service total only, so without this the split between
        # reagents, labour and maintenance is lost the moment a price moves.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS project_cost_basis (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                project_service_id INTEGER,
                service_id INTEGER,
                service_name TEXT,
                cost_type TEXT NOT NULL,
                stock_item_id INTEGER,
                label TEXT,
                raw_amount REAL NOT NULL,
                amount REAL NOT NULL
            )
        ''')
        # The settings those amounts were computed under, kept so a figure can
        # always be explained years later.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS project_basis_header (
                project_id INTEGER PRIMARY KEY,
                revenue REAL NOT NULL,
                profit_total REAL NOT NULL,
                profit_margin REAL,
                taxes_fees REAL,
                discount REAL,
                captured_at TEXT,
                captured_by TEXT
            )
        ''')
        # One decision per bucket, taken when the payment is confirmed.
        # No row means nobody has chosen yet.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS project_payout_basis (
                project_id INTEGER NOT NULL,
                cost_type TEXT NOT NULL,
                basis TEXT NOT NULL,
                decided_at TEXT,
                decided_by TEXT,
                PRIMARY KEY (project_id, cost_type)
            )
        ''')
        # What was actually handed over. Without it the debts ledger recomputes
        # a past payment from today's prices, so a payee settled in June is
        # restated every time a reagent moves afterwards.
        try:
            cur.execute('ALTER TABLE payee_settlements ADD COLUMN amount REAL')
        except sqlite3.OperationalError:
            pass
        cur.execute('CREATE INDEX IF NOT EXISTS idx_splits_project ON project_cost_splits (project_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_basis_project ON project_cost_basis (project_id)')
        conn.commit()
    finally:
        conn.close()


def seed_defaults(current_user="System"):
    """Creates Quarium, LNP and a payee per app user. Idempotent."""
    init_payee_db()
    existing = {p['name'] for p in load_payees(active_only=False)}
    for name, kind in [(PROFIT_PAYEE, 'company'), (DEFAULT_REAGENT_PAYEE, 'company')]:
        if name not in existing:
            add_payee(name, kind, current_user)
            existing.add(name)

    users_path = _path('users.json')
    if os.path.exists(users_path):
        try:
            with open(users_path, 'r', encoding='utf-8') as f:
                users = json.load(f)
        except Exception:
            users = {}
        for username, record in users.items():
            # Records are either {"full_name", "salt", "hash"} or a legacy
            # bare name string; never let credential material through.
            if isinstance(record, dict):
                name = record.get('full_name') or username
            elif isinstance(record, str):
                name = record
            else:
                continue
            if name and name not in existing:
                add_payee(name, 'person', current_user)
                existing.add(name)


def init_all(current_user="System"):
    init_payee_db()
    init_project_tables()
    seed_defaults(current_user)


# ---------------------------------------------------------------------- payees

def load_payees(active_only=True):
    init_payee_db()
    conn = _connect(PAYEE_DB)
    try:
        cur = conn.cursor()
        query = 'SELECT id, name, kind, active, COALESCE(notes, "") FROM payees'
        if active_only:
            query += ' WHERE active = 1'
        query += ' ORDER BY kind DESC, name'
        return [{'id': r[0], 'name': r[1], 'kind': r[2], 'active': r[3], 'notes': r[4]}
                for r in cur.execute(query).fetchall()]
    finally:
        conn.close()


def get_payee_by_name(name):
    if not name:
        return None
    conn = _connect(PAYEE_DB)
    try:
        row = conn.execute('SELECT id, name, kind FROM payees WHERE name = ?', (name,)).fetchone()
        return {'id': row[0], 'name': row[1], 'kind': row[2]} if row else None
    finally:
        conn.close()


def add_payee(name, kind='person', current_user="Unknown", notes=""):
    name = (name or '').strip()
    if not name:
        raise ValueError("O nome do beneficiario e obrigatorio.")
    init_payee_db()
    conn = _connect(PAYEE_DB)
    try:
        cur = conn.cursor()
        cur.execute('INSERT INTO payees (name, kind, active, notes, created_at, created_by) '
                    'VALUES (?, ?, 1, ?, ?, ?)',
                    (name, kind, notes, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), current_user))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def set_payee_active(payee_id, active):
    conn = _connect(PAYEE_DB)
    try:
        conn.execute('UPDATE payees SET active = ? WHERE id = ?', (1 if active else 0, payee_id))
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------------------------- defaults

def set_default_payee(target_type, target_id, cost_type, payee_id, current_user="Unknown"):
    """Remembers a payee for this service/reagent on future projects."""
    init_payee_db()
    conn = _connect(PAYEE_DB)
    try:
        conn.execute('''
            INSERT INTO payee_defaults (target_type, target_id, cost_type, payee_id, updated_at, updated_by)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (target_type, target_id, cost_type)
            DO UPDATE SET payee_id = excluded.payee_id,
                          updated_at = excluded.updated_at,
                          updated_by = excluded.updated_by
        ''', (target_type, target_id, cost_type, payee_id,
              datetime.now().strftime('%Y-%m-%d %H:%M:%S'), current_user))
        conn.commit()
    finally:
        conn.close()


def clear_default_payee(target_type, target_id, cost_type):
    conn = _connect(PAYEE_DB)
    try:
        conn.execute('DELETE FROM payee_defaults WHERE target_type = ? AND target_id = ? AND cost_type = ?',
                     (target_type, target_id, cost_type))
        conn.commit()
    finally:
        conn.close()


def load_defaults():
    """{(target_type, target_id, cost_type): payee_id}"""
    init_payee_db()
    conn = _connect(PAYEE_DB)
    try:
        return {(r[0], r[1], r[2]): r[3] for r in
                conn.execute('SELECT target_type, target_id, cost_type, payee_id FROM payee_defaults').fetchall()}
    finally:
        conn.close()


# ----------------------------------------------------------------- cost lines

def _unit_conversion(req_unit, stock_unit):
    mass = {"ng": 1e-9, "ug": 1e-6, "mg": 1e-3, "g": 1.0}
    vol = {"nL": 1e-9, "uL": 1e-6, "mL": 1e-3, "L": 1.0}
    if req_unit in mass and stock_unit in mass:
        return mass[req_unit] / mass[stock_unit]
    if req_unit in vol and stock_unit in vol:
        return vol[req_unit] / vol[stock_unit]
    return 1.0


def _load_settings():
    path = _path('settings.json')
    settings = {"profit_margin": 0.0, "taxes_and_fees": 0.0}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                settings.update(json.load(f))
        except Exception:
            pass
    return settings


def calculate_cost_lines(project_id):
    """Per-line costs for a project, mirroring calculate_cost_breakdown.

    Returns (lines, profit_total). Each line is one attributable slice:
    a service's Labor or Maintenance, or a single reagent requirement.
    Profit is returned separately because part of it is a project-level
    margin and it always goes to Quarium.
    """
    settings = _load_settings()
    profit_margin = float(settings.get("profit_margin", 0.0)) / 100.0
    taxes_fees = float(settings.get("taxes_and_fees", 0.0)) / 100.0

    conn = _connect(PROJECT_DB)
    try:
        cur = conn.cursor()
        row = cur.execute('SELECT total_samples, discount_percentage FROM projects WHERE id = ?',
                          (project_id,)).fetchone()
        if not row:
            return [], 0.0
        total_samples, discount_pct = row
        discount = (discount_pct / 100.0) if discount_pct else 0.0

        cur.execute("ATTACH DATABASE ? AS services_db", (_path(SERVICE_DB),))
        cur.execute("ATTACH DATABASE ? AS stock_db", (_path(STOCK_DB),))
        try:
            services = cur.execute(
                'SELECT id, service_id, samples_override FROM project_services WHERE project_id = ?',
                (project_id,)).fetchall()

            lines = []
            profit_raw = 0.0
            base_raw = 0.0

            for ps_id, service_id, override in services:
                samples = override if override is not None else total_samples
                service_name = cur.execute(
                    'SELECT name FROM services_db.services WHERE id = ?', (service_id,)).fetchone()
                service_name = service_name[0] if service_name else f"Servico {service_id}"

                reagents = {}
                for (stock_item_id, req_qty, req_unit, spb,
                     reagent_name, price, container_size, stock_unit) in cur.execute('''
                        SELECT sr.stock_item_id, sr.quantity, sr.unit, sr.samples_per_batch,
                               st.name, st.price, st.container_size, st.unit
                        FROM services_db.service_requirements sr
                        LEFT JOIN stock_db.stock st ON sr.stock_item_id = st.id
                        WHERE sr.service_id = ?
                        ORDER BY sr.id
                    ''', (service_id,)).fetchall():
                    if price is None or not container_size or container_size <= 0:
                        continue
                    unit_cost = price / container_size
                    conv = _unit_conversion(req_unit, stock_unit)
                    batches = math.ceil(samples / spb) if spb and spb > 0 else samples
                    amount = req_qty * conv * batches * unit_cost
                    base_raw += amount
                    # Several requirement rows can name the same reagent; they
                    # are sourced together, so they form one attributable line.
                    entry = reagents.setdefault(
                        stock_item_id,
                        {'amount': 0.0, 'label': reagent_name or f"Item {stock_item_id}"})
                    entry['amount'] += amount

                for stock_item_id, entry in reagents.items():
                    lines.append({
                        'project_service_id': ps_id,
                        'service_id': service_id,
                        'service_name': service_name,
                        'cost_type': COST_REAGENTS,
                        'stock_item_id': stock_item_id,
                        'label': entry['label'],
                        'raw_amount': entry['amount'],
                    })

                for cost_type, cost, spb in cur.execute(
                        'SELECT cost_type, cost, samples_per_batch FROM services_db.service_costs '
                        'WHERE service_id = ?', (service_id,)).fetchall():
                    batches = math.ceil(samples / spb) if spb and spb > 0 else samples
                    amount = cost * batches
                    base_raw += amount
                    if cost_type == COST_PROFIT:
                        profit_raw += amount
                        continue
                    if cost_type not in (COST_LABOR, COST_MAINTENANCE):
                        continue
                    lines.append({
                        'project_service_id': ps_id,
                        'service_id': service_id,
                        'service_name': service_name,
                        'cost_type': cost_type,
                        'stock_item_id': None,
                        'label': service_name,
                        'raw_amount': amount,
                    })
        finally:
            cur.execute("DETACH DATABASE services_db")
            cur.execute("DETACH DATABASE stock_db")
    finally:
        conn.close()

    # Same adjustment calculate_cost_breakdown applies, so the attributed
    # slices still add up to the project's final cost.
    multiplier = (1 + taxes_fees) * (1 - discount)
    for line in lines:
        line['amount'] = line['raw_amount'] * multiplier
    profit_total = (profit_raw + base_raw * profit_margin) * multiplier
    return lines, profit_total


# ----------------------------------------------------------------- cost basis

def project_revenue(project_id):
    """What the client was billed: the frozen quote, discount included."""
    conn = _connect(PROJECT_DB)
    try:
        row = conn.execute('SELECT final_cost FROM projects WHERE id = ?',
                           (project_id,)).fetchone()
        return float(row[0]) if row and row[0] else 0.0
    finally:
        conn.close()


def has_cost_basis(project_id):
    init_project_tables()
    conn = _connect(PROJECT_DB)
    try:
        return conn.execute('SELECT 1 FROM project_basis_header WHERE project_id = ?',
                            (project_id,)).fetchone() is not None
    finally:
        conn.close()


def capture_cost_basis(project_id, current_user="Unknown", force=False):
    """Records what the work costs, at the moment the project is approved.

    Approval is the only honest moment for this. It is when the client agrees
    the price, and from then on the amount due never moves, so this is the
    one record of what each bucket was worth under the agreed figure.

    Returns False if a basis was already recorded: capturing twice would
    quietly replace the agreed amounts with later ones.
    """
    init_project_tables()
    if has_cost_basis(project_id) and not force:
        return False

    lines, profit_total = calculate_cost_lines(project_id)
    settings = _load_settings()
    conn = _connect(PROJECT_DB)
    try:
        cur = conn.cursor()
        row = cur.execute(
            'SELECT discount_percentage, final_cost FROM projects WHERE id = ?',
            (project_id,)).fetchone()
        if not row:
            return False
        discount, revenue = float(row[0] or 0.0), float(row[1] or 0.0)

        cur.execute('DELETE FROM project_cost_basis WHERE project_id = ?', (project_id,))
        cur.executemany(
            'INSERT INTO project_cost_basis (project_id, project_service_id, service_id, '
            'service_name, cost_type, stock_item_id, label, raw_amount, amount) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [(project_id, l['project_service_id'], l['service_id'], l['service_name'],
              l['cost_type'], l['stock_item_id'], l['label'], l['raw_amount'], l['amount'])
             for l in lines])
        cur.execute(
            'INSERT INTO project_basis_header (project_id, revenue, profit_total, '
            'profit_margin, taxes_fees, discount, captured_at, captured_by) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT (project_id) DO UPDATE SET revenue = excluded.revenue, '
            'profit_total = excluded.profit_total, profit_margin = excluded.profit_margin, '
            'taxes_fees = excluded.taxes_fees, discount = excluded.discount, '
            'captured_at = excluded.captured_at, captured_by = excluded.captured_by',
            (project_id, revenue, profit_total,
             float(settings.get('profit_margin', 0.0)),
             float(settings.get('taxes_and_fees', 0.0)), discount,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'), current_user))
        conn.commit()
    finally:
        conn.close()
    return True


def load_cost_basis(project_id):
    """The recorded basis, its lines shaped as calculate_cost_lines returns
    them. None when the project was approved before bases were kept."""
    init_project_tables()
    conn = _connect(PROJECT_DB)
    try:
        keys = ('revenue', 'profit_total', 'profit_margin', 'taxes_fees', 'discount',
                'captured_at', 'captured_by')
        header = conn.execute(
            f'SELECT {", ".join(keys)} FROM project_basis_header WHERE project_id = ?',
            (project_id,)).fetchone()
        if not header:
            return None
        basis = dict(zip(keys, header))
        basis['lines'] = [
            {'project_service_id': r[0], 'service_id': r[1], 'service_name': r[2],
             'cost_type': r[3], 'stock_item_id': r[4], 'label': r[5],
             'raw_amount': r[6], 'amount': r[7]}
            for r in conn.execute(
                'SELECT project_service_id, service_id, service_name, cost_type, '
                'stock_item_id, label, raw_amount, amount FROM project_cost_basis '
                'WHERE project_id = ? ORDER BY id', (project_id,))]
        return basis
    finally:
        conn.close()


def get_payout_basis(project_id):
    """{cost_type: 'original' | 'adjusted'}, for the buckets already decided."""
    init_project_tables()
    conn = _connect(PROJECT_DB)
    try:
        return {r[0]: r[1] for r in conn.execute(
            'SELECT cost_type, basis FROM project_payout_basis WHERE project_id = ?',
            (project_id,))}
    finally:
        conn.close()


def set_payout_basis(project_id, cost_type, basis, current_user="Unknown"):
    """Records which prices one bucket is paid at."""
    if basis not in (BASIS_ORIGINAL, BASIS_ADJUSTED):
        raise ValueError(f"Unknown payout basis {basis!r}.")
    init_project_tables()
    conn = _connect(PROJECT_DB)
    try:
        conn.execute(
            'INSERT INTO project_payout_basis (project_id, cost_type, basis, decided_at, '
            'decided_by) VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT (project_id, cost_type) DO UPDATE SET basis = excluded.basis, '
            'decided_at = excluded.decided_at, decided_by = excluded.decided_by',
            (project_id, cost_type, basis,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'), current_user))
        conn.commit()
    finally:
        conn.close()


def payout_report(project_id):
    """What each bucket is worth on both bases, and what that leaves over.

    The money available to pay anyone is the money the client was billed, and
    that figure was frozen when the quote was written. So profit is not a
    bucket to be chosen but the residual: pay every bucket at the agreed
    price and the quoted profit survives intact; pay one at today's price
    instead and the difference comes out of profit.
    """
    init_project_tables()
    current_lines, current_profit = calculate_cost_lines(project_id)
    basis = load_cost_basis(project_id)
    chosen = get_payout_basis(project_id)
    revenue = project_revenue(project_id)

    def total_of(lines, cost_type):
        return sum(l['amount'] for l in lines if l['cost_type'] == cost_type)

    buckets = {}
    effective = []
    for cost_type in ATTRIBUTABLE:
        current_total = total_of(current_lines, cost_type)
        original_total = total_of(basis['lines'], cost_type) if basis else None
        # Default to the agreed price, so a payout nobody has looked at still
        # reconciles with the money that came in. Without a recorded basis
        # there is nothing to pay at but today's prices.
        pick = chosen.get(cost_type, BASIS_ORIGINAL)
        if basis is None:
            pick = BASIS_ADJUSTED
        source = basis['lines'] if pick == BASIS_ORIGINAL else current_lines
        effective += [l for l in source if l['cost_type'] == cost_type]
        buckets[cost_type] = {
            'original': original_total,
            'current': current_total,
            'basis': pick,
            'amount': current_total if pick == BASIS_ADJUSTED else original_total,
            'moved': (original_total is not None
                      and abs(current_total - original_total) >= 0.005),
            'decided': cost_type in chosen,
        }

    spend = sum(b['amount'] for b in buckets.values())
    # An unsaved or zero-value project has no revenue to divide; fall back to
    # the computed profit rather than reporting the whole spend as a loss.
    profit = (revenue - spend) if revenue > 0 else current_profit
    return {
        'project_id': project_id,
        'revenue': revenue,
        'recorded': basis is not None,
        'captured_at': basis['captured_at'] if basis else None,
        'buckets': buckets,
        'lines': effective,
        'spend': spend,
        'profit': profit,
        'original_profit': basis['profit_total'] if basis else current_profit,
        'at_a_loss': profit < -0.005,
        'moved': any(b['moved'] for b in buckets.values()),
    }


# --------------------------------------------------------------------- splits

def load_splits(project_id):
    """{(project_service_id, cost_type, stock_item_id): [(payee_id, percentage)]}"""
    init_project_tables()
    conn = _connect(PROJECT_DB)
    try:
        splits = {}
        for ps_id, cost_type, item_id, payee_id, pct in conn.execute(
                'SELECT project_service_id, cost_type, stock_item_id, payee_id, percentage '
                'FROM project_cost_splits WHERE project_id = ?', (project_id,)).fetchall():
            splits.setdefault((ps_id, cost_type, item_id), []).append((payee_id, pct))
        return splits
    finally:
        conn.close()


def set_split(project_id, project_service_id, cost_type, stock_item_id, allocations,
              current_user="Unknown"):
    """Replaces the attribution for one line. allocations = [(payee_id, percentage)]."""
    total = round(sum(pct for _, pct in allocations), 6)
    if allocations and total > 100.0000001:
        raise ValueError(f"A soma das porcentagens ({total:.2f}%) excede 100%.")
    conn = _connect(PROJECT_DB)
    try:
        cur = conn.cursor()
        cur.execute('DELETE FROM project_cost_splits WHERE project_id = ? AND project_service_id = ? '
                    'AND cost_type = ? AND stock_item_id IS ?',
                    (project_id, project_service_id, cost_type, stock_item_id))
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for payee_id, pct in allocations:
            cur.execute('INSERT INTO project_cost_splits (project_id, project_service_id, cost_type, '
                        'stock_item_id, payee_id, percentage, updated_at, updated_by) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        (project_id, project_service_id, cost_type, stock_item_id,
                         payee_id, pct, now, current_user))
        conn.commit()
    finally:
        conn.close()


def distribute(amount, allocations):
    """Splits an amount by percentage, in centavos, last payee absorbing the
    rounding so the parts always add back to the whole."""
    if not allocations:
        return []
    cents = int(round(amount * 100))
    out = []
    running = 0
    for payee_id, pct in allocations[:-1]:
        part = int(round(cents * (pct / 100.0)))
        running += part
        out.append((payee_id, part / 100.0))
    out.append((allocations[-1][0], (cents - running) / 100.0))
    return out


# --------------------------------------------------------------- attribution

def resolve_recipients(project_id, responsible_name=None):
    """Who receives what on this project.

    Falls back through explicit split -> remembered default -> category
    default (Labor to the project's responsible, Maintenance to Quarium,
    Reagents to LNP). Profit always goes to Quarium.

    The amounts are whatever the project's payout basis says each bucket is
    paid at, so switching a bucket to today's prices moves money out of
    Quarium's profit and into that bucket's payees, not into thin air.
    """
    init_all()
    report = payout_report(project_id)
    lines, profit_total = report['lines'], report['profit']
    splits = load_splits(project_id)
    defaults = load_defaults()

    by_name = {p['name']: p['id'] for p in load_payees(active_only=False)}
    id_to_name = {v: k for k, v in by_name.items()}

    quarium = by_name.get(PROFIT_PAYEE)
    lnp = by_name.get(DEFAULT_REAGENT_PAYEE)
    responsible_id = by_name.get(responsible_name) if responsible_name else None

    totals = {}
    unassigned = 0.0

    def credit(payee_id, value):
        if payee_id is None:
            return False
        totals[payee_id] = totals.get(payee_id, 0.0) + value
        return True

    for line in lines:
        key = (line['project_service_id'], line['cost_type'], line['stock_item_id'])
        allocations = splits.get(key)

        if not allocations:
            if line['cost_type'] == COST_REAGENTS:
                fallback = defaults.get(('stock_item', line['stock_item_id'], COST_REAGENTS)) or lnp
            elif line['cost_type'] == COST_MAINTENANCE:
                fallback = defaults.get(('service', line['service_id'], COST_MAINTENANCE)) or quarium
            else:
                fallback = (defaults.get(('service', line['service_id'], COST_LABOR))
                            or responsible_id)
            allocations = [(fallback, 100.0)] if fallback is not None else []

        if not allocations:
            unassigned += line['amount']
            continue
        for payee_id, value in distribute(line['amount'], allocations):
            if not credit(payee_id, value):
                unassigned += value

    if profit_total:
        if not credit(quarium, profit_total):
            unassigned += profit_total

    # Every line was rounded to whole centavos independently, so the parts no
    # longer re-sum to the unrounded project total. Payments happen in
    # centavos, so put the few-centavo residual somewhere explicit rather than
    # letting it vanish: Quarium absorbs it, profit being the residual bucket
    # anyway, else the largest recipient.
    target_cents = int(round((sum(l['amount'] for l in lines) + profit_total) * 100))
    current_cents = int(round((sum(totals.values()) + unassigned) * 100))
    residual_cents = target_cents - current_cents
    if residual_cents:
        absorber = quarium if quarium in totals else (max(totals, key=totals.get) if totals else None)
        if absorber is not None:
            totals[absorber] += residual_cents / 100.0
        else:
            unassigned += residual_cents / 100.0

    recipients = [{'payee_id': pid, 'name': id_to_name.get(pid, f'#{pid}'), 'amount': round(amount, 2)}
                  for pid, amount in totals.items()]
    recipients.sort(key=lambda r: -r['amount'])
    unassigned = round(unassigned, 2)
    return {'recipients': recipients, 'unassigned': unassigned,
            'total': round(sum(r['amount'] for r in recipients) + unassigned, 2)}


# ---------------------------------------------------------------- settlements

def get_settlements(project_id):
    init_project_tables()
    conn = _connect(PROJECT_DB)
    try:
        return {r[0]: {'paid': bool(r[1]), 'paid_at': r[2], 'paid_by': r[3], 'amount': r[4]}
                for r in conn.execute(
            'SELECT payee_id, paid, paid_at, paid_by, amount FROM payee_settlements '
            'WHERE project_id = ?', (project_id,)).fetchall()}
    finally:
        conn.close()


def set_settled(project_id, payee_id, paid, current_user="Unknown", amount=None):
    """Marks a payee paid, recording what was handed over.

    The amount is stored rather than recomputed later: a payment that has
    already happened must not be restated because a reagent price moved
    afterwards. Unmarking clears it, since nothing was paid after all.
    """
    init_project_tables()
    conn = _connect(PROJECT_DB)
    try:
        conn.execute('''
            INSERT INTO payee_settlements (project_id, payee_id, paid, paid_at, paid_by, amount)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (project_id, payee_id)
            DO UPDATE SET paid = excluded.paid, paid_at = excluded.paid_at,
                          paid_by = excluded.paid_by, amount = excluded.amount
        ''', (project_id, payee_id, 1 if paid else 0,
              datetime.now().strftime('%Y-%m-%d %H:%M:%S') if paid else None,
              current_user if paid else None,
              amount if paid else None))
        conn.commit()
    finally:
        conn.close()


def all_settled(project_id, responsible_name=None):
    """True once every recipient on the project has been marked paid.
    This is what drives segment F."""
    resolved = resolve_recipients(project_id, responsible_name)
    recipients = resolved['recipients']
    if not recipients:
        return False
    settlements = get_settlements(project_id)
    return all(settlements.get(r['payee_id'], {}).get('paid') for r in recipients)


# -------------------------------------------------------------------- ledger

STATUS_UNDERWAY = 'underway'
STATUS_NOT_SENT = 'not_sent'
STATUS_SENT = 'sent'
STATUS_RECEIVED = 'received'
STATUS_PAID = 'paid'

# Ordered from least to most advanced; the label is what the legend shows.
STATUS_ORDER = [
    (STATUS_UNDERWAY, "Project Underway", "#2563EB"),
    (STATUS_NOT_SENT, "NF + Boleto Not Sent", "#DC2626"),
    (STATUS_SENT, "NF + Boleto Sent", "#EA580C"),
    (STATUS_RECEIVED, "Payment Received", "#CA8A04"),
    (STATUS_PAID, "Payee Paid", "#16A34A"),
]
STATUS_LABELS = {k: label for k, label, _c in STATUS_ORDER}
STATUS_COLORS = {k: colour for k, _l, colour in STATUS_ORDER}


def obligation_status(flags, settled):
    """How far along the money is for one payee on one project."""
    if settled:
        return STATUS_PAID
    if flags.get('invoice_paid'):
        return STATUS_RECEIVED
    if flags.get('invoice_sent') and flags.get('boleto_sent'):
        return STATUS_SENT
    if flags.get('data_sent_to_client'):
        return STATUS_NOT_SENT
    return STATUS_UNDERWAY


def payee_obligations(payee_id=None, include_paid=True):
    """One row per (project, payee) that is owed money, newest project first."""
    init_all()
    conn = _connect(PROJECT_DB)
    try:
        cur = conn.cursor()
        attached = True
        try:
            cur.execute("ATTACH DATABASE ? AS clients_db", (_path('clients.db'),))
        except sqlite3.OperationalError:
            attached = False
        try:
            projects = cur.execute('''
                SELECT p.id, p.estimate_number, c.name, p.responsible_user,
                       COALESCE(p.data_sent_to_client, 0), COALESCE(p.data_approved_by_client, 0),
                       COALESCE(p.invoice_sent, 0), COALESCE(p.boleto_sent, 0),
                       COALESCE(p.invoice_paid, 0)
                FROM projects p
                LEFT JOIN clients_db.clients c ON p.client_id = c.id
                WHERE p.status > 0
                ORDER BY p.id DESC
            ''').fetchall()
        finally:
            if attached:
                try:
                    cur.execute("DETACH DATABASE clients_db")
                except sqlite3.OperationalError:
                    pass
    finally:
        conn.close()

    rows = []
    for (p_id, est_num, client, responsible, a_sent, b_appr, c_nf, d_bol, e_paid) in projects:
        flags = {'data_sent_to_client': a_sent, 'data_approved_by_client': b_appr,
                 'invoice_sent': c_nf, 'boleto_sent': d_bol, 'invoice_paid': e_paid}
        resolved = resolve_recipients(p_id, responsible)
        settlements = get_settlements(p_id)
        for recipient in resolved['recipients']:
            if payee_id is not None and recipient['payee_id'] != payee_id:
                continue
            settlement = settlements.get(recipient['payee_id'], {})
            settled = bool(settlement.get('paid'))
            if settled and not include_paid:
                continue
            # Once paid, report what was handed over. Recomputing it would
            # restate a closed payment at whatever prices apply today.
            amount = (settlement['amount'] if settled and settlement.get('amount') is not None
                      else recipient['amount'])
            rows.append({
                'project_id': p_id,
                'estimate_number': est_num,
                'client': client or "Unknown",
                'payee_id': recipient['payee_id'],
                'payee_name': recipient['name'],
                'amount': amount,
                'settled': settled,
                'status': obligation_status(flags, settled),
            })
    return rows


def payee_statement(payee_id):
    """Everything needed for one payee's report: obligations plus open ledger."""
    payee = next((p for p in load_payees(active_only=False) if p['id'] == payee_id), None)
    obligations = payee_obligations(payee_id=payee_id)
    ledger = load_ledger('payee', payee_id, status='open')
    owed = sum(o['amount'] for o in obligations if not o['settled'])
    paid = sum(o['amount'] for o in obligations if o['settled'])
    adjustment = ledger_balance('payee', payee_id)
    return {
        'payee': payee,
        'obligations': obligations,
        'ledger': ledger,
        'total_owed': owed,
        'total_paid': paid,
        'adjustment': adjustment,
        'net_due': owed + adjustment,
    }


def add_ledger_entry(party_type, party_id, kind, amount, description="",
                     project_id=None, current_user="Unknown"):
    """kind: 'credit' (we owe them more) or 'debt' (an advance, deduct later)."""
    if kind not in ('credit', 'debt'):
        raise ValueError("kind deve ser 'credit' ou 'debt'.")
    if amount is None or amount <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    init_payee_db()
    conn = _connect(PAYEE_DB)
    try:
        cur = conn.cursor()
        cur.execute('INSERT INTO ledger_entries (party_type, party_id, kind, amount, description, '
                    'project_id, status, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, "open", ?, ?)',
                    (party_type, party_id, kind, amount, description, project_id,
                     datetime.now().strftime('%Y-%m-%d %H:%M:%S'), current_user))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def load_ledger(party_type=None, party_id=None, status=None):
    init_payee_db()
    conn = _connect(PAYEE_DB)
    try:
        query = ('SELECT id, party_type, party_id, kind, amount, COALESCE(description, ""), '
                 'project_id, status, applied_project_id, applied_at, created_at, created_by '
                 'FROM ledger_entries WHERE 1=1')
        params = []
        if party_type:
            query += ' AND party_type = ?'
            params.append(party_type)
        if party_id is not None:
            query += ' AND party_id = ?'
            params.append(party_id)
        if status:
            query += ' AND status = ?'
            params.append(status)
        query += ' ORDER BY created_at DESC, id DESC'
        keys = ['id', 'party_type', 'party_id', 'kind', 'amount', 'description', 'project_id',
                'status', 'applied_project_id', 'applied_at', 'created_at', 'created_by']
        return [dict(zip(keys, r)) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def ledger_balance(party_type, party_id):
    """Net open adjustment: positive means we owe extra, negative means the
    party owes us (an outstanding advance)."""
    total = 0.0
    for entry in load_ledger(party_type, party_id, status='open'):
        total += entry['amount'] if entry['kind'] == 'credit' else -entry['amount']
    return total


def apply_ledger_entry(entry_id, project_id=None, current_user="Unknown"):
    conn = _connect(PAYEE_DB)
    try:
        conn.execute('UPDATE ledger_entries SET status = "applied", applied_project_id = ?, '
                     'applied_at = ? WHERE id = ?',
                     (project_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), entry_id))
        conn.commit()
    finally:
        conn.close()


def cancel_ledger_entry(entry_id):
    conn = _connect(PAYEE_DB)
    try:
        conn.execute('UPDATE ledger_entries SET status = "cancelled" WHERE id = ?', (entry_id,))
        conn.commit()
    finally:
        conn.close()
