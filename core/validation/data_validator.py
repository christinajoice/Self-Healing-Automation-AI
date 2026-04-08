"""
Data Validator
==============
Compares UI table data against DB query results in a generic, app-agnostic way.

Handles:
  - Different column names between UI headers and DB columns  (config-driven map)
  - Numeric formatting differences  (1,234  vs  1234  vs  1.234k)
  - Percentage values  (54.2%  vs  0.542  vs  54.2)
  - Case and whitespace differences
  - Configurable numeric tolerance  (default ±0)
  - UI showing a paginated subset of DB rows  (only visible rows are validated)
  - Key column auto-detection for row matching

Public API:
  compare(ui_rows, db_rows, config)  → list[str]  (empty = all matched)
  extract_ui_table(page, col_header) → list[dict]  (reads full table from page)
"""

from __future__ import annotations

import re
import logging
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Value normalisation
# ---------------------------------------------------------------------------

def normalize(raw: Any) -> str:
    """
    Normalise a value to a canonical string for comparison.
    Strips formatting artefacts so that  "1,234"  ==  "1234"  ==  "1.234k".
    """
    if raw is None:
        return ""

    s = str(raw).strip()
    s = re.sub(r"\s+", " ", s)          # collapse whitespace
    s = s.replace(",", "")               # remove thousands separators
    s = s.rstrip("%")                    # strip trailing percent sign

    # Expand common shorthand suffixes  (3.5k → 3500, 1.2m → 1200000)
    lower = s.lower()
    if re.match(r"^-?\d+(\.\d+)?k$", lower):
        s = str(int(float(lower[:-1]) * 1_000))
    elif re.match(r"^-?\d+(\.\d+)?m$", lower):
        s = str(int(float(lower[:-1]) * 1_000_000))

    return s.lower()


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def values_match(ui_val: Any, db_val: Any, tolerance: float = 0.0) -> bool:
    """
    Return True if ui_val and db_val represent the same value.

    Comparison order:
      1. Normalised string equality  (handles formatting differences)
      2. Numeric comparison with optional tolerance
      3. Percentage ↔ decimal  (54.2% ≈ 0.542  when tolerance allows)
    """
    ui_norm = normalize(ui_val)
    db_norm = normalize(db_val)

    if ui_norm == db_norm:
        return True

    ui_num = _to_float(ui_norm)
    db_num = _to_float(db_norm)

    if ui_num is not None and db_num is not None:
        # Direct numeric comparison
        tol = tolerance if tolerance > 0 else 0.0
        if abs(ui_num - db_num) <= tol:
            return True

        # Percentage ↔ decimal  (UI: 54.2  DB: 0.542)
        if abs(ui_num - db_num * 100) <= max(tol, 0.01):
            return True
        if abs(ui_num / 100 - db_num) <= max(tol, 0.0001):
            return True

    return False


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------

def build_column_map(
    config: Dict,
    ui_columns: List[str],
    db_columns: List[str],
) -> Dict[str, str]:
    """
    Build  {db_column → ui_column}  mapping.

    Resolution order:
      1. Explicit  column_map  in query YAML
      2. Exact case-insensitive match
      3. Substring match  (one name contains the other)
    """
    explicit: Dict[str, str] = config.get("column_map", {})
    result: Dict[str, str] = {}

    for db_col in db_columns:
        # 1. Explicit mapping
        if db_col in explicit:
            result[db_col] = explicit[db_col]
            continue

        # 2. Exact match (case-insensitive)
        match = next(
            (u for u in ui_columns if u.lower() == db_col.lower()),
            None,
        )
        if match:
            result[db_col] = match
            continue

        # 3. Substring match
        match = next(
            (
                u for u in ui_columns
                if db_col.lower() in u.lower() or u.lower() in db_col.lower()
            ),
            None,
        )
        if match:
            result[db_col] = match

    return result


# ---------------------------------------------------------------------------
# Key column detection
# ---------------------------------------------------------------------------

def detect_key_column(config: Dict, db_columns: List[str]) -> Optional[str]:
    """
    Identify the column that uniquely identifies each row so that UI rows
    can be matched against DB rows by value rather than by position.

    Resolution order:
      1. Explicit  key_column  in query YAML
      2. Column whose name contains a common identity hint
      3. First column as last resort
    """
    explicit = config.get("key_column")
    if explicit and explicit in db_columns:
        return explicit

    KEY_HINTS = ["name", "state", "county", "region", "id", "code", "label", "title"]
    for hint in KEY_HINTS:
        match = next(
            (c for c in db_columns if hint in c.lower()),
            None,
        )
        if match:
            return match

    return db_columns[0] if db_columns else None


# ---------------------------------------------------------------------------
# UI table extraction  (runs in browser via Playwright page.evaluate)
# ---------------------------------------------------------------------------

async def extract_ui_table(page, target_columns: Optional[List[str]] = None) -> List[Dict]:
    """
    Extract all visible rows from the first data table on the page.

    Returns a list of dicts keyed by the UI column header text.
    If  target_columns  is provided, only those columns are returned.

    Works with:
      - MUI DataGrid  (role=row / role=columnheader / role=cell)
      - Standard HTML <table><th><td>
      - Any ARIA-grid pattern
    """
    rows: List[Dict] = await page.evaluate(
        """
        (targetCols) => {
            // ── Find column headers ─────────────────────────────────────
            let headers = Array.from(document.querySelectorAll(
                '[role="columnheader"]'
            )).map(h => h.textContent.trim());

            if (!headers.length) {
                headers = Array.from(document.querySelectorAll('th'))
                    .map(h => h.textContent.trim());
            }
            if (!headers.length) return [];

            // Filter to requested columns (or keep all)
            const wantedIdx = targetCols && targetCols.length
                ? headers.reduce((acc, h, i) => {
                    if (targetCols.some(t => h.toLowerCase().includes(t.toLowerCase())))
                        acc.push(i);
                    return acc;
                }, [])
                : headers.map((_, i) => i);

            // ── Read data rows ──────────────────────────────────────────
            const dataRows = Array.from(document.querySelectorAll('[role="row"]'))
                .filter(r => !r.querySelector('[role="columnheader"]'));

            if (!dataRows.length) {
                // Fall back to HTML table rows
                const trs = Array.from(document.querySelectorAll('tbody tr'));
                return trs.map(tr => {
                    const cells = Array.from(tr.querySelectorAll('td'));
                    const obj = {};
                    wantedIdx.forEach(i => {
                        if (headers[i] && cells[i])
                            obj[headers[i]] = cells[i].textContent.trim();
                    });
                    return obj;
                }).filter(o => Object.keys(o).length > 0);
            }

            return dataRows.map(row => {
                const cells = Array.from(row.querySelectorAll(
                    '[role="cell"], [role="gridcell"], .MuiDataGrid-cell'
                ));
                const obj = {};
                wantedIdx.forEach(i => {
                    if (headers[i] && cells[i])
                        obj[headers[i]] = cells[i].textContent.trim();
                });
                return obj;
            }).filter(o => Object.keys(o).length > 0);
        }
        """,
        target_columns,
    )
    return rows or []


# ---------------------------------------------------------------------------
# Main comparison
# ---------------------------------------------------------------------------

def compare(
    ui_rows: List[Dict],
    db_rows: List[Dict],
    config: Dict,
) -> List[str]:
    """
    Compare UI table rows against DB query results.
    Returns a list of human-readable mismatch descriptions.
    An empty list means everything matched.

    Parameters
    ----------
    ui_rows  : extracted from the page via extract_ui_table()
    db_rows  : returned by connector.execute()
    config   : loaded from queries/<key>.yaml  (column_map, tolerance, key_column)
    """
    if not ui_rows:
        return ["No rows visible in the UI table"]
    if not db_rows:
        return ["DB query returned no rows — check your SQL and parameters"]

    ui_columns = list(ui_rows[0].keys())
    db_columns = list(db_rows[0].keys())
    tolerance  = float(config.get("tolerance", 0))

    col_map  = build_column_map(config, ui_columns, db_columns)
    key_col  = detect_key_column(config, db_columns)
    ui_key   = col_map.get(key_col) if key_col else None

    logger.info(
        "[VALIDATOR] Column map: %s | Key: %s→%s | Tolerance: %s",
        col_map, key_col, ui_key, tolerance,
    )

    if not col_map:
        return [
            f"Cannot map any DB columns to UI columns. "
            f"DB columns: {db_columns}. "
            f"UI columns: {ui_columns}. "
            f"Add a 'column_map' section to your query YAML."
        ]

    # Build DB lookup keyed by normalised key-column value
    db_lookup: Dict[str, Dict] = {}
    for row in db_rows:
        k = normalize(row.get(key_col, "")) if key_col else str(id(row))
        db_lookup[k] = row

    mismatches: List[str] = []
    not_in_db:  List[str] = []
    matched = 0

    for idx, ui_row in enumerate(ui_rows):
        # ── Find matching DB row ──────────────────────────────────────
        if ui_key and ui_key in ui_row:
            lookup_key = normalize(ui_row[ui_key])
            db_row = db_lookup.get(lookup_key)
        else:
            # Positional fallback when no key column can be identified
            db_row = db_rows[idx] if idx < len(db_rows) else None

        row_label = ui_row.get(ui_key, f"row #{idx + 1}") if ui_key else f"row #{idx + 1}"

        if db_row is None:
            not_in_db.append(str(row_label))
            continue

        # ── Compare each mapped column ────────────────────────────────
        row_ok = True
        for db_col, ui_col in col_map.items():
            if db_col == key_col:
                continue  # key column already used for matching
            if ui_col not in ui_row:
                continue

            ui_val = ui_row[ui_col]
            db_val = db_row.get(db_col)

            if not values_match(ui_val, db_val, tolerance):
                mismatches.append(
                    f"{row_label} | {ui_col}: "
                    f"UI='{ui_val}'  DB='{db_val}'"
                )
                row_ok = False

        if row_ok:
            matched += 1

    if not_in_db:
        mismatches.append(
            f"{len(not_in_db)} UI row(s) not found in DB results "
            f"(check SQL WHERE clause matches current filters): "
            f"{not_in_db[:5]}"
        )

    logger.info(
        "[VALIDATOR] %d matched, %d mismatched, %d not in DB",
        matched, len(mismatches), len(not_in_db),
    )
    return mismatches
