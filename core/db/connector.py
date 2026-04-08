"""
DB Connector
============
Generic database connector using SQLAlchemy.

Supported databases:
  - PostgreSQL  (including AWS RDS Postgres)
  - Snowflake
  - MySQL / AWS RDS MySQL
  - SQL Server  (via pyodbc)

All connectors implement the same interface:
  connect()               → self
  execute(sql, params)    → list[dict]  (lowercase column names)
  test()                  → bool
  close()

Usage:
  from core.db.connector import get_connector
  from core.db.profile_loader import get_profile

  conn = get_connector(get_profile("hilabs_rds"))
  rows = conn.execute("SELECT state, gaps FROM na WHERE state = :state",
                      {"state": "INDIANA"})
  conn.close()
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Any
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BaseConnector(ABC):

    @abstractmethod
    def connect(self) -> "BaseConnector": ...

    @abstractmethod
    def execute(self, sql: str, params: Dict = None) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def test(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# SQL connector  (PostgreSQL, Snowflake, MySQL, SQL Server)
# ---------------------------------------------------------------------------

class SQLConnector(BaseConnector):
    """
    SQLAlchemy-backed connector.  A connection pool is created once on
    first use and reused across multiple execute() calls.
    """

    # Maps the user-facing db type string → SQLAlchemy driver prefix
    DRIVER_MAP: Dict[str, str] = {
        "postgres":    "postgresql+psycopg2",
        "postgresql":  "postgresql+psycopg2",
        "rds":         "postgresql+psycopg2",
        "rds_postgres":"postgresql+psycopg2",
        "snowflake":   "snowflake",
        "mysql":       "mysql+pymysql",
        "rds_mysql":   "mysql+pymysql",
        "mssql":       "mssql+pyodbc",
        "sqlserver":   "mssql+pyodbc",
        "sql_server":  "mssql+pyodbc",
    }

    def __init__(self, profile: Dict):
        self.profile = profile
        self._engine = None

    # ------------------------------------------------------------------
    # Connection URL builders
    # ------------------------------------------------------------------

    def _build_url(self) -> str:
        p = self.profile
        db_type = p.get("type", "postgres").lower()

        if db_type == "snowflake":
            return self._snowflake_url(p)

        driver  = self.DRIVER_MAP.get(db_type, db_type)
        host    = p.get("host", "localhost")
        port    = p.get("port", 5432)
        db      = p.get("database", "")
        user    = quote_plus(str(p.get("username", "")))
        pwd     = quote_plus(str(p.get("password", "")))

        if db_type in ("mssql", "sqlserver", "sql_server"):
            odbc_driver = quote_plus("ODBC Driver 17 for SQL Server")
            return (
                f"{driver}://{user}:{pwd}@{host}:{port}/{db}"
                f"?driver={odbc_driver}"
            )

        return f"{driver}://{user}:{pwd}@{host}:{port}/{db}"

    @staticmethod
    def _snowflake_url(p: Dict) -> str:
        user      = quote_plus(str(p.get("username", "")))
        pwd       = quote_plus(str(p.get("password", "")))
        account   = p.get("account", "")
        database  = p.get("database", "")
        schema    = p.get("schema", "PUBLIC")
        warehouse = p.get("warehouse", "")
        role      = p.get("role", "")

        url = f"snowflake://{user}:{pwd}@{account}/{database}/{schema}"
        qs  = []
        if warehouse:
            qs.append(f"warehouse={warehouse}")
        if role:
            qs.append(f"role={role}")
        if qs:
            url += "?" + "&".join(qs)
        return url

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def connect(self) -> "SQLConnector":
        import sqlalchemy
        url = self._build_url()
        db_type = self.profile.get("type", "postgres").lower()

        connect_args = {}
        if db_type == "snowflake":
            connect_args["connect_timeout"] = 30
        elif db_type in ("postgres", "postgresql", "rds", "rds_postgres"):
            connect_args["connect_timeout"] = 10

        self._engine = sqlalchemy.create_engine(
            url,
            pool_pre_ping=True,   # verify connection alive before use
            pool_size=2,
            max_overflow=3,
            connect_args=connect_args,
        )
        logger.info(
            "[DB] Connected to %s (%s)",
            self.profile.get("host") or self.profile.get("account", ""),
            self.profile.get("type", ""),
        )
        return self

    def execute(self, sql: str, params: Dict = None) -> List[Dict[str, Any]]:
        from sqlalchemy import text as sa_text

        if self._engine is None:
            self.connect()

        with self._engine.connect() as conn:
            result = conn.execute(sa_text(sql), params or {})
            columns = list(result.keys())
            rows = [
                {col.lower(): val for col, val in zip(columns, row)}
                for row in result.fetchall()
            ]

        logger.info("[DB] Query returned %d rows", len(rows))
        return rows

    def test(self) -> bool:
        try:
            self.execute("SELECT 1 AS ok")
            return True
        except Exception as e:
            logger.warning("[DB] Connection test failed: %s", e)
            return False

    def close(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = None
            logger.info("[DB] Connection pool closed")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_connector(profile: Dict) -> BaseConnector:
    """
    Return the appropriate connector for the given profile dict.
    Currently routes all supported types through SQLConnector.
    """
    db_type = profile.get("type", "postgres").lower()

    if db_type not in SQLConnector.DRIVER_MAP:
        raise ValueError(
            f"Unsupported DB type: '{db_type}'. "
            f"Supported types: {sorted(SQLConnector.DRIVER_MAP.keys())}."
        )

    return SQLConnector(profile)
