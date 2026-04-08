"""
Query Runner
============
Loads SQL templates from the queries/ directory, resolves parameter values
(from the current UI page or inline in the CSV data field), and executes
them against the provided DB connector.

Directory structure:
  queries/
    <key>.sql     — SQL with named params  :param_name
    <key>.yaml    — parameter sources + column mapping (optional)

CSV usage:
  validate | db data | gaps_summary
  validate | db data | gaps_summary?state=INDIANA&network=PPO   ← inline override

Parameter resolution order (highest priority wins):
  1. Inline params from CSV data field  (?key=val&key=val)
  2. UI params read from current page   (selectors in .yaml)
  3. Default values defined in .yaml
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs

import yaml

logger = logging.getLogger(__name__)

QUERIES_DIR = Path("queries")


# ---------------------------------------------------------------------------
# Load template
# ---------------------------------------------------------------------------

def load_query_template(key: str) -> Tuple[str, Dict]:
    """
    Load queries/<key>.sql and (optionally) queries/<key>.yaml.
    Returns (sql_text, config_dict).
    Raises FileNotFoundError if the .sql file does not exist.
    """
    sql_path  = QUERIES_DIR / f"{key}.sql"
    yaml_path = QUERIES_DIR / f"{key}.yaml"

    if not sql_path.exists():
        available = [p.stem for p in QUERIES_DIR.glob("*.sql")]
        raise FileNotFoundError(
            f"Query file not found: '{sql_path}'. "
            f"Available queries: {available}. "
            f"Create queries/{key}.sql to add a new query."
        )

    sql = sql_path.read_text(encoding="utf-8")
    config: Dict = {}
    if yaml_path.exists():
        config = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    return sql, config


# ---------------------------------------------------------------------------
# Inline param parsing
# ---------------------------------------------------------------------------

def parse_data_field(data_field: str) -> Tuple[str, Dict]:
    """
    Split  "query_key?param1=val1&param2=val2"  into  (key, {param: val}).
    If no '?' is present, returns (data_field.strip(), {}).
    """
    data_field = (data_field or "").strip()
    if "?" not in data_field:
        return data_field, {}

    key, qs = data_field.split("?", 1)
    params: Dict[str, str] = {}
    for part in qs.split("&"):
        if "=" in part:
            k, _, v = part.partition("=")
            params[k.strip()] = v.strip()
    return key.strip(), params


# ---------------------------------------------------------------------------
# UI param reading
# ---------------------------------------------------------------------------

async def read_ui_params(page, param_config: Dict) -> Dict:
    """
    Read parameter values from the current browser page.

    Each entry in param_config can be:
      network_group:
        read_from: text          # innerText of a CSS selector
        selector: ".filter-chip[data-key='network']"

      plan_type:
        read_from: value         # input value of a CSS selector
        selector: "select#plan-type"

      state:
        read_from: url_param     # ?state=... in the current URL
        param_name: state

      report_date:
        read_from: static        # fixed value, no page interaction
        value: "2024-01-01"

      network_group:             # shorthand: just a static string
        "PREMIER_VALUE_PPO"
    """
    values: Dict[str, str] = {}

    for param_name, cfg in param_config.items():
        # Shorthand: plain string means static value
        if not isinstance(cfg, dict):
            values[param_name] = str(cfg)
            continue

        source  = cfg.get("read_from", "text")
        default = cfg.get("default", "")

        try:
            if source == "text":
                sel = cfg.get("selector", "")
                raw = (await page.locator(sel).first.inner_text(timeout=3000)).strip()
                values[param_name] = raw

            elif source == "value":
                sel = cfg.get("selector", "")
                raw = await page.locator(sel).first.input_value(timeout=3000)
                values[param_name] = raw.strip()

            elif source == "url_param":
                from urllib.parse import urlparse, parse_qs as pqs
                qp = pqs(urlparse(page.url).query)
                pname = cfg.get("param_name", param_name)
                values[param_name] = qp.get(pname, [default])[0]

            elif source == "static":
                values[param_name] = str(cfg.get("value", default))

            else:
                logger.warning(
                    "[DB-QUERY] Unknown read_from '%s' for param '%s'",
                    source, param_name,
                )
                values[param_name] = default

        except Exception as exc:
            logger.warning(
                "[DB-QUERY] Could not read UI param '%s': %s — using default '%s'",
                param_name, exc, default,
            )
            values[param_name] = default

    return values


# ---------------------------------------------------------------------------
# SQL cleanup
# ---------------------------------------------------------------------------

def strip_sql_comments(sql: str) -> str:
    """Remove single-line (--) and block (/* */) SQL comments."""
    # Block comments
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # Single-line comments
    sql = "\n".join(
        line for line in sql.splitlines()
        if not line.strip().startswith("--")
    )
    return sql.strip()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_query(
    data_field: str,
    connector,
    page=None,
) -> Tuple[List[Dict], Dict]:
    """
    Full pipeline:
      parse key → load SQL + config → read UI params → inject → execute

    Returns (rows: list[dict], config: dict).
    The config dict is passed to data_validator.compare() for column mapping
    and tolerance settings.
    """
    key, inline_params = parse_data_field(data_field)
    sql, config = load_query_template(key)

    # Build params: UI params first, then inline overrides
    params: Dict[str, str] = {}

    if page and config.get("params"):
        ui_params = await read_ui_params(page, config["params"])
        params.update(ui_params)
        logger.info("[DB-QUERY] UI params resolved: %s", ui_params)

    params.update(inline_params)  # inline always wins

    sql_clean = strip_sql_comments(sql)
    logger.info("[DB-QUERY] Executing query '%s' | params: %s", key, params)

    rows = connector.execute(sql_clean, params)
    logger.info("[DB-QUERY] '%s' returned %d row(s)", key, len(rows))

    return rows, config
