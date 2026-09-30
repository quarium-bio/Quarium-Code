"""What an estimate costs, when that stops being true, and what to do then.

Two different numbers have always lived side by side here, and only one of
them was frozen.

  The quote is frozen. project_services.calculated_cost and
  projects.final_cost are written once when an estimate is saved and never
  recalculated. The PDF, the contract and the invoice all use them, so a
  client quoted in March pays the March price.

  The cost basis is not. Project Finances, the payee attribution and the
  debts ledger all recompute from whatever services.db and stock.db say
  today. Raise a reagent price and every past project silently reports a
  different total.

That gap is useful -- it is what tells you a job has stopped being
profitable -- but only if someone is told about it. This module measures
it, and offers the three ways out: keep the old prices, reprice at
today's, or reprice by an inflation rate.
"""

import datetime
import json
import math
import os
import sqlite3
import urllib.error
import urllib.request

import QuariumPayees as QP
from QuariumPaths import data_dir

_BASE_DIR = data_dir()

# Banco Central time series 433 is monthly IPCA variation, in percent.
BCB_SERIES_IPCA = 433
BCB_URL = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados"
           "?formato=json&dataInicial={start}&dataFinal={end}")
BCB_TIMEOUT = 8

MODE_KEEP = 'keep'
MODE_CURRENT = 'current'
MODE_INFLATION = 'inflation'


class PricingError(Exception):
    pass


def _path(name):
    return os.path.join(_BASE_DIR, name)


def _settings():
    try:
        with open(_path('settings.json'), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _open(name='projects.db'):
    return sqlite3.connect(_path(name))


# ------------------------------------------------------------------ validity

def validity(project_id, conn=None):
    """When the quote was written, and whether it has run out."""
    owned = conn is None
    conn = conn or _open()
    try:
        row = conn.execute(
            'SELECT created_at, validity_days, final_cost, status FROM projects WHERE id = ?',
            (project_id,)).fetchone()
        if not row:
            raise PricingError(f"No project with id {project_id}.")
        created_at, days, final_cost, status = row
        written = _as_date(created_at)
        days = days if days and days > 0 else 30
        expires = written + datetime.timedelta(days=days) if written else None
        today = datetime.date.today()
        return {
            'project_id': project_id,
            'written_on': written,
            'validity_days': days,
            'expires_on': expires,
            'days_left': (expires - today).days if expires else None,
            'expired': bool(expires and today > expires),
            'final_cost': final_cost or 0.0,
            'status': status,
        }
    finally:
        if owned:
            conn.close()


def _as_date(value):
    if not value:
        return None
    text = str(value).split()[0]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------- the numbers

def service_sell_cost(service_id, samples, settings=None, cur=None):
    """One service priced at today's rates, before any client discount.

    This is the number stored in project_services.calculated_cost, and it
    follows the same formula the estimate editor uses: reagents plus the
    service's own cost rows, marked up by the profit margin and then by
    taxes and fees.
    """
    settings = settings if settings is not None else _settings()
    profit_margin = float(settings.get("profit_margin", 0.0)) / 100.0
    taxes_fees = float(settings.get("taxes_and_fees", 0.0)) / 100.0

    owned = cur is None
    conn = None
    if owned:
        conn = _open()
        cur = conn.cursor()
        cur.execute("ATTACH DATABASE ? AS services_db", (_path('services.db'),))
        cur.execute("ATTACH DATABASE ? AS stock_db", (_path('stock.db'),))
    try:
        base = 0.0
        for (req_qty, req_unit, spb, price, container, stock_unit) in cur.execute('''
                SELECT sr.quantity, sr.unit, sr.samples_per_batch,
                       st.price, st.container_size, st.unit
                FROM services_db.service_requirements sr
                LEFT JOIN stock_db.stock st ON sr.stock_item_id = st.id
                WHERE sr.service_id = ?
            ''', (service_id,)).fetchall():
            if price is None or not container or container <= 0:
                continue
            batches = math.ceil(samples / spb) if spb and spb > 0 else samples
            base += (req_qty * QP._unit_conversion(req_unit, stock_unit)
                     * batches * (price / container))

        for cost, spb in cur.execute(
                'SELECT cost, samples_per_batch FROM services_db.service_costs '
                'WHERE service_id = ?', (service_id,)).fetchall():
            if cost is None:
                continue
            batches = math.ceil(samples / spb) if spb and spb > 0 else samples
            base += cost * batches

        return base * (1 + profit_margin) * (1 + taxes_fees)
    finally:
        if owned:
            try:
                cur.execute("DETACH DATABASE services_db")
                cur.execute("DETACH DATABASE stock_db")
            except sqlite3.OperationalError:
                pass
            conn.close()


def quote(project_id, conn=None):
    """The frozen quote: what the client was actually told."""
    owned = conn is None
    conn = conn or _open()
    try:
        row = conn.execute(
            'SELECT total_samples, discount_percentage, final_cost FROM projects WHERE id = ?',
            (project_id,)).fetchone()
        if not row:
            raise PricingError(f"No project with id {project_id}.")
        total_samples, discount_pct, final_cost = row
        lines = [{'project_service_id': ps_id, 'service_id': sid,
                  'samples_override': override, 'cost': cost or 0.0}
                 for ps_id, sid, override, cost in conn.execute(
                     'SELECT id, service_id, samples_override, calculated_cost '
                     'FROM project_services WHERE project_id = ? ORDER BY id', (project_id,))]
        return {
            'project_id': project_id,
            'total_samples': total_samples or 1,
            'discount': (discount_pct or 0.0) / 100.0,
            'discount_pct': discount_pct or 0.0,
            'final_cost': final_cost or 0.0,
            'lines': lines,
            'line_total': sum(l['cost'] for l in lines),
        }
    finally:
        if owned:
            conn.close()


def current_prices(project_id, conn=None):
    """The same services priced at today's rates."""
    owned = conn is None
    conn = conn or _open()
    try:
        frozen = quote(project_id, conn=conn)
        settings = _settings()
        cur = conn.cursor()
        cur.execute("ATTACH DATABASE ? AS services_db", (_path('services.db'),))
        cur.execute("ATTACH DATABASE ? AS stock_db", (_path('stock.db'),))
        try:
            lines = []
            for line in frozen['lines']:
                samples = (line['samples_override'] if line['samples_override'] is not None
                           else frozen['total_samples'])
                lines.append(dict(line, cost=service_sell_cost(
                    line['service_id'], samples, settings=settings, cur=cur)))
        finally:
            try:
                cur.execute("DETACH DATABASE services_db")
                cur.execute("DETACH DATABASE stock_db")
            except sqlite3.OperationalError:
                pass
        total = sum(l['cost'] for l in lines)
        return {'lines': lines, 'line_total': total,
                'final_cost': total * (1 - frozen['discount'])}
    finally:
        if owned:
            conn.close()


def outgoing_cost(project_id):
    """Real money out at today's prices: labour, reagents and maintenance.

    Deliberately the raw amounts. The discount reduces what the client pays,
    not what a reagent costs, and the tax markup is part of the price rather
    than of the work, so neither belongs on this side of the comparison.
    """
    lines, _profit = QP.calculate_cost_lines(project_id)
    buckets = {QP.COST_LABOR: 0.0, QP.COST_MAINTENANCE: 0.0, QP.COST_REAGENTS: 0.0}
    for line in lines:
        if line['cost_type'] in buckets:
            buckets[line['cost_type']] += line['raw_amount']
    return buckets


def margin(project_id):
    """Compares what the client pays against what the work now costs."""
    frozen = quote(project_id)
    buckets = outgoing_cost(project_id)
    spend = sum(buckets.values())
    revenue = frozen['final_cost']
    current = current_prices(project_id)
    return {
        'project_id': project_id,
        'revenue': revenue,
        'labor': buckets[QP.COST_LABOR],
        'maintenance': buckets[QP.COST_MAINTENANCE],
        'reagents': buckets[QP.COST_REAGENTS],
        'spend': spend,
        'margin': revenue - spend,
        'margin_pct': ((revenue - spend) / revenue * 100.0) if revenue else 0.0,
        'at_a_loss': spend > revenue,
        'discount_pct': frozen['discount_pct'],
        'quoted_line_total': frozen['line_total'],
        'current_line_total': current['line_total'],
        'current_final_cost': current['final_cost'],
        'drift': current['final_cost'] - revenue,
    }


# ------------------------------------------------------------------ IPCA

def ipca_accumulated(start, end=None, timeout=BCB_TIMEOUT):
    """Accumulated IPCA between two dates, as a fraction, from the Banco Central.

    Returns (rate, note). The rate is None when the series cannot be
    reached, and the note always says where the number came from, so a
    figure that goes to a client is never silently invented.
    """
    end = end or datetime.date.today()
    if not start:
        return None, "No estimate date to measure inflation from."
    if start >= end:
        return 0.0, "The estimate is not old enough to have accrued inflation."
    url = BCB_URL.format(series=BCB_SERIES_IPCA,
                         start=start.strftime('%d/%m/%Y'), end=end.strftime('%d/%m/%Y'))
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'QuariumApp/1.0'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            series = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return None, f"Could not reach the Banco Central series ({e}). Enter a rate by hand."
    if not series:
        return None, "The Banco Central returned no IPCA readings for that period."
    factor = 1.0
    for entry in series:
        try:
            factor *= 1 + float(str(entry['valor']).replace(',', '.')) / 100.0
        except (KeyError, ValueError):
            continue
    months = len(series)
    return factor - 1.0, (f"IPCA accumulated over {months} month(s) to "
                          f"{series[-1].get('data', '')}, from the Banco Central.")


# ------------------------------------------------------------------ repricing

def reprice_preview(project_id, mode, rate=0.0):
    """What each option would do to the total, before anything is written."""
    frozen = quote(project_id)
    old = frozen['final_cost']
    if mode == MODE_KEEP:
        new = old
    elif mode == MODE_CURRENT:
        new = current_prices(project_id)['final_cost']
    elif mode == MODE_INFLATION:
        new = old * (1 + rate)
    else:
        raise PricingError(f"Unknown repricing mode {mode!r}.")
    return {'mode': mode, 'old_total': old, 'new_total': new,
            'difference': new - old,
            'difference_pct': ((new - old) / old * 100.0) if old else 0.0}


def apply_reprice(project_id, mode, rate=0.0, user=None):
    """Writes the chosen prices onto the estimate. Returns the preview."""
    preview = reprice_preview(project_id, mode, rate)
    if mode == MODE_KEEP:
        return preview

    conn = _open()
    try:
        frozen = quote(project_id, conn=conn)
        if mode == MODE_CURRENT:
            new_lines = {l['project_service_id']: l['cost']
                         for l in current_prices(project_id, conn=conn)['lines']}
        else:
            new_lines = {l['project_service_id']: l['cost'] * (1 + rate)
                         for l in frozen['lines']}

        for ps_id, cost in new_lines.items():
            conn.execute('UPDATE project_services SET calculated_cost = ? WHERE id = ?',
                         (cost, ps_id))
        total = sum(new_lines.values())
        conn.execute(
            'UPDATE projects SET final_cost = ?, updated_at = ?, updated_by = ? WHERE id = ?',
            (total * (1 - frozen['discount']),
             datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             user or "", project_id))
        conn.commit()
        return preview
    finally:
        conn.close()
