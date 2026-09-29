"""Taking an approved project back out of the flow without deleting it.

Approving an estimate used to be one way: the only exit was deletion, which
loses the estimate, its services, its cost splits and any payment already
recorded against it. Two things are wanted instead.

  Return to estimate  -- the client wants changes before signing. The project
                         becomes an editable estimate again and can be
                         re-approved.
  Mark as cancelled   -- the work is not going ahead. The project leaves the
                         active flow but stays on record, and can be restored.

Cancelled projects carry status -1. Every other query in the application
selects `status > 0` for live work and `status = 0` for estimates, so a
negative status drops out of the flow, the finance tables, the payee
obligations and the contract list without any of them needing to change.

Nothing is ever erased here. Stage dates, completed services, cost splits and
settlements all survive both moves, so a project that comes back brings its
history with it. Callers are expected to show `describe_recorded_work` first
so the decision is made with that history in view.
"""

import os
import sqlite3
from datetime import datetime

from QuariumPaths import data_dir

_BASE_DIR = data_dir()

STATUS_CANCELLED = -1
STATUS_ESTIMATE = 0
STATUS_APPROVED = 1
STATUS_COMPLETED = 7

# Added on demand, in the additive style the rest of the schema uses.
CANCEL_COLUMNS = [
    'cancelled_at TEXT',
    'cancelled_by TEXT',
    'cancellation_reason TEXT',
    'cancelled_from_status INTEGER',
]

STAGE_NAMES = {
    -1: "Cancelled",
    0: "Estimate",
    1: "Orçamento Aprovado",
    2: "Contrato Enviado",
    3: "Contrato Assinado",
    4: "Amostras Recebidas",
    5: "Amostras Analisadas",
    6: "Dados Analisados e Liberados",
    7: "Completed",
}


class CancellationError(Exception):
    pass


def _projects_path():
    return os.path.join(_BASE_DIR, 'projects.db')


def ensure_schema(conn):
    """Adds the cancellation columns if this database predates them."""
    for column in CANCEL_COLUMNS:
        try:
            conn.execute(f'ALTER TABLE projects ADD COLUMN {column}')
        except sqlite3.OperationalError:
            pass  # already present
    conn.commit()


def _open():
    conn = sqlite3.connect(_projects_path())
    ensure_schema(conn)
    return conn


def _row(conn, project_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT id, estimate_number, status, contract_signed_at, contract_sent_at, '
        'samples_received_at, samples_analyzed_at, data_released_at, completed_at, '
        'invoice_sent, invoice_paid, lnp_emitted, lnp_paid, '
        'cancelled_at, cancelled_by, cancellation_reason, cancelled_from_status '
        'FROM projects WHERE id = ?', (project_id,)).fetchone()
    if row is None:
        raise CancellationError(f"No project with id {project_id}.")
    return row


def describe_recorded_work(project_id, conn=None):
    """Lists what has already happened on a project, for the confirmation.

    The point is that the user decides with the consequences in front of
    them. Nothing here blocks the move; it is all kept either way.
    """
    owned = conn is None
    conn = conn or _open()
    try:
        ensure_schema(conn)
        row = _row(conn, project_id)
        notes = []

        if row['contract_signed_at']:
            notes.append(f"The contract was signed on {row['contract_signed_at'].split()[0]}.")
        elif row['contract_sent_at']:
            notes.append(f"A contract was sent on {row['contract_sent_at'].split()[0]}.")
        if row['samples_received_at']:
            notes.append(f"Samples were received on {row['samples_received_at'].split()[0]}.")
        if row['samples_analyzed_at']:
            notes.append(f"Samples were analysed on {row['samples_analyzed_at'].split()[0]}.")
        if row['data_released_at']:
            notes.append(f"Data was released on {row['data_released_at'].split()[0]}.")

        done = conn.execute(
            'SELECT COUNT(*) FROM project_services WHERE project_id = ? AND completed = 1',
            (project_id,)).fetchone()[0]
        total = conn.execute(
            'SELECT COUNT(*) FROM project_services WHERE project_id = ?',
            (project_id,)).fetchone()[0]
        if done:
            notes.append(f"{done} of {total} services are marked complete.")

        if row['invoice_paid']:
            notes.append("The invoice is marked paid.")
        elif row['invoice_sent']:
            notes.append("An invoice has been sent.")
        if row['lnp_paid']:
            notes.append("LNP is marked paid.")
        elif row['lnp_emitted']:
            notes.append("An LNP note has been emitted.")

        notes.extend(_payment_notes(conn, project_id))

        contracts = _contract_count(conn, project_id, row['estimate_number'])
        if contracts:
            notes.append(f"{contracts} generated contract document(s) reference this project.")

        return {
            'project_id': project_id,
            'estimate_number': row['estimate_number'],
            'status': row['status'],
            'stage': STAGE_NAMES.get(row['status'], f"Stage {row['status']}"),
            'notes': notes,
            'has_history': bool(notes),
        }
    finally:
        if owned:
            conn.close()


def _payment_notes(conn, project_id):
    """Splits and settlements live in tables added by the payees feature, so
    an older database may not have them yet."""
    notes = []
    try:
        paid = conn.execute(
            'SELECT COUNT(*) FROM payee_settlements WHERE project_id = ? AND paid = 1',
            (project_id,)).fetchone()[0]
        if paid:
            notes.append(f"{paid} payee(s) have been marked paid on this project.")
    except sqlite3.OperationalError:
        pass
    try:
        splits = conn.execute(
            'SELECT COUNT(*) FROM project_cost_splits WHERE project_id = ?',
            (project_id,)).fetchone()[0]
        if splits:
            notes.append(f"{splits} custom cost split(s) are recorded.")
    except sqlite3.OperationalError:
        pass
    return notes


def _contract_count(conn, project_id, estimate_number):
    for sql, args in (
            ('SELECT COUNT(*) FROM contract_projects WHERE project_id = ?', (project_id,)),
            ('SELECT COUNT(*) FROM contracts WHERE estimate_number = ?', (estimate_number,))):
        try:
            count = conn.execute(sql, args).fetchone()[0]
            if count:
                return count
        except sqlite3.OperationalError:
            continue
    return 0


def _apply(conn, project_id, updates):
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f'UPDATE projects SET {sets} WHERE id = ?',
                 list(updates.values()) + [project_id])
    conn.commit()


def return_to_estimate(project_id, user=None, reason=None, conn=None):
    """Un-approves a project so it can be edited and approved again.

    The stage dates are left alone on purpose. Clearing them would quietly
    destroy the record of what actually happened, and they are harmless at
    status 0: nothing reads them until the project is approved again.
    """
    owned = conn is None
    conn = conn or _open()
    try:
        ensure_schema(conn)
        row = _row(conn, project_id)
        if row['status'] == STATUS_ESTIMATE:
            raise CancellationError("That project is already an estimate.")
        _apply(conn, project_id, {
            'status': STATUS_ESTIMATE,
            'cancelled_at': None,
            'cancelled_by': None,
            'cancelled_from_status': None,
            'cancellation_reason': _stamp(reason, user, f"returned to estimate from "
                                          f"{STAGE_NAMES.get(row['status'], row['status'])}"),
        })
        return {'project_id': project_id, 'estimate_number': row['estimate_number'],
                'from_status': row['status'], 'status': STATUS_ESTIMATE}
    finally:
        if owned:
            conn.close()


def cancel_project(project_id, user=None, reason=None, conn=None):
    """Takes a project out of the flow but keeps every trace of it."""
    owned = conn is None
    conn = conn or _open()
    try:
        ensure_schema(conn)
        row = _row(conn, project_id)
        if row['status'] == STATUS_CANCELLED:
            raise CancellationError("That project is already cancelled.")
        _apply(conn, project_id, {
            'status': STATUS_CANCELLED,
            'cancelled_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'cancelled_by': user or "",
            'cancelled_from_status': row['status'],
            'cancellation_reason': (reason or "").strip(),
        })
        return {'project_id': project_id, 'estimate_number': row['estimate_number'],
                'from_status': row['status'], 'status': STATUS_CANCELLED}
    finally:
        if owned:
            conn.close()


def restore_project(project_id, user=None, to_status=None, conn=None):
    """Puts a cancelled project back where it was, or at a stage you name."""
    owned = conn is None
    conn = conn or _open()
    try:
        ensure_schema(conn)
        row = _row(conn, project_id)
        if row['status'] != STATUS_CANCELLED:
            raise CancellationError("That project is not cancelled.")
        target = to_status if to_status is not None else row['cancelled_from_status']
        if target is None:
            target = STATUS_ESTIMATE
        if target == STATUS_CANCELLED:
            raise CancellationError("Cannot restore a project to the cancelled state.")
        _apply(conn, project_id, {
            'status': target,
            'cancelled_at': None,
            'cancelled_by': None,
            'cancelled_from_status': None,
            'cancellation_reason': _stamp(row['cancellation_reason'], user, "restored"),
        })
        return {'project_id': project_id, 'estimate_number': row['estimate_number'],
                'from_status': STATUS_CANCELLED, 'status': target}
    finally:
        if owned:
            conn.close()


def _stamp(reason, user, action):
    """Keeps a one-line trail on the project rather than silently dropping it."""
    when = datetime.now().strftime('%Y-%m-%d')
    who = f" by {user}" if user else ""
    note = f"[{when}{who}] {action}"
    reason = (reason or "").strip()
    if reason:
        note = f"{note}: {reason}"
    return note


def cancelled_projects(conn=None):
    """Every cancelled project, newest first, for the Cancelled list."""
    owned = conn is None
    conn = conn or _open()
    try:
        ensure_schema(conn)
        clients = os.path.join(os.path.dirname(_projects_path()), 'clients.db')
        attached = False
        if os.path.exists(clients):
            try:
                conn.execute("ATTACH DATABASE ? AS cancel_clients", (clients,))
                attached = True
            except sqlite3.OperationalError:
                pass
        name = ("(SELECT name FROM cancel_clients.clients WHERE id = p.client_id)"
                if attached else "NULL")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f'''
            SELECT p.id, p.estimate_number, {name} AS client_name, p.final_cost,
                   p.cancelled_at, p.cancelled_by, p.cancellation_reason,
                   p.cancelled_from_status
            FROM projects p
            WHERE p.status = ?
            ORDER BY p.cancelled_at DESC, p.id DESC
        ''', (STATUS_CANCELLED,)).fetchall()
        result = [{
            'id': r['id'],
            'estimate_number': r['estimate_number'],
            'client_name': r['client_name'] or "",
            'final_cost': r['final_cost'] or 0.0,
            'cancelled_at': r['cancelled_at'] or "",
            'cancelled_by': r['cancelled_by'] or "",
            'reason': r['cancellation_reason'] or "",
            'from_status': r['cancelled_from_status'],
            'from_stage': STAGE_NAMES.get(r['cancelled_from_status'], ""),
        } for r in rows]
        if attached:
            try:
                conn.execute("DETACH DATABASE cancel_clients")
            except sqlite3.OperationalError:
                pass
        return result
    finally:
        if owned:
            conn.close()
