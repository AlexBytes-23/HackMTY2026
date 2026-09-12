from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


OFFICIAL_COLUMNS = {
    "vendors": {
        "rfc",
        "legal_name",
        "registered_date",
        "address",
        "bank_clabe",
        "category",
        "contact_email",
    },
    "invoices": {
        "uuid",
        "issuer_rfc",
        "receiver_rfc",
        "issue_date",
        "subtotal",
        "iva",
        "total",
        "concepto_text",
        "uso_cfdi",
        "forma_pago",
        "metodo_pago",
        "status",
    },
    "ledger": {
        "entry_id",
        "date",
        "account_code",
        "account_name",
        "debit",
        "credit",
        "description",
        "invoice_uuid",
        "cost_center",
        "approver",
    },
    "bank_txns": {
        "txn_id",
        "date",
        "from_clabe",
        "to_clabe",
        "amount",
        "reference",
        "channel",
    },
    "purchase_orders": {
        "po_id",
        "vendor_rfc",
        "date",
        "amount",
        "requester",
        "approver",
        "description",
    },
    "contracts": {
        "contract_id",
        "vendor_rfc",
        "start_date",
        "value",
        "scope_text",
    },
    "employees": {
        "emp_id",
        "name",
        "role",
        "bank_clabe",
        "hire_date",
    },
    "efos_list": {
        "rfc",
        "legal_name",
        "status",
        "publication_date",
    },
}


ID_COLUMN = {
    "vendors": "rfc",
    "invoices": "uuid",
    "ledger": "entry_id",
    "bank_txns": "txn_id",
    "purchase_orders": "po_id",
    "contracts": "contract_id",
    "employees": "emp_id",
    "efos_list": "rfc",
}


AMOUNT_COLUMN = {
    "invoices": "total",
    "bank_txns": "amount",
    "purchase_orders": "amount",
    "contracts": "value",
}


class EstateRepository:
    """
    Acceso único a estate.db.

    No decide qué es sospechoso.
    Sólo devuelve datos objetivos.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

        if not self.db_path.exists():
            raise FileNotFoundError(
                f"No existe el estate: {self.db_path}"
            )

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

        self._validate_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "EstateRepository":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _check_table(self, table: str) -> None:
        if table not in OFFICIAL_COLUMNS:
            raise ValueError(
                f"Tabla no permitida: {table}"
            )

    def _validate_schema(self) -> None:
        rows = self.conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()

        existing_tables = {
            row["name"]
            for row in rows
        }

        missing_tables = (
            set(OFFICIAL_COLUMNS)
            - existing_tables
        )

        if missing_tables:
            raise ValueError(
                "El estate no cumple el esquema oficial. "
                f"Faltan tablas: {sorted(missing_tables)}"
            )

        for table, required_columns in OFFICIAL_COLUMNS.items():
            rows = self.conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()

            actual_columns = {
                row["name"]
                for row in rows
            }

            missing_columns = (
                required_columns
                - actual_columns
            )

            if missing_columns:
                raise ValueError(
                    f"{table}: faltan columnas "
                    f"{sorted(missing_columns)}"
                )

    def table_df(self, table: str) -> pd.DataFrame:
        """
        Devuelve una tabla como DataFrame.
        Alex y Daniel usarán mucho esto.
        """

        self._check_table(table)

        return pd.read_sql_query(
            f"SELECT * FROM {table}",
            self.conn,
        )

    def get_all(
        self,
        table: str,
    ) -> list[dict[str, Any]]:

        self._check_table(table)

        rows = self.conn.execute(
            f"SELECT * FROM {table}"
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    def get_record(
        self,
        table: str,
        record_id: str | int,
    ) -> dict[str, Any] | None:

        self._check_table(table)

        id_column = ID_COLUMN[table]

        row = self.conn.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE {id_column} = ?
            """,
            (record_id,),
        ).fetchone()

        if row is None:
            return None

        return dict(row)

    def record_exists(
        self,
        table: str,
        record_id: str | int,
    ) -> bool:

        return (
            self.get_record(
                table,
                record_id,
            )
            is not None
        )

    def get_record_amount(
        self,
        table: str,
        record_id: str | int,
    ) -> float | None:

        amount_column = AMOUNT_COLUMN.get(
            table
        )

        if amount_column is None:
            return None

        record = self.get_record(
            table,
            record_id,
        )

        if record is None:
            return None

        value = record.get(
            amount_column
        )

        if value is None:
            return None

        return float(value)

    def get_vendor(
        self,
        rfc: str,
    ) -> dict[str, Any] | None:

        return self.get_record(
            "vendors",
            rfc,
        )

    def get_employee(
        self,
        emp_id: str,
    ) -> dict[str, Any] | None:

        return self.get_record(
            "employees",
            emp_id,
        )

    def get_invoice(
        self,
        uuid: str,
    ) -> dict[str, Any] | None:

        return self.get_record(
            "invoices",
            uuid,
        )

    def get_bank_txn(
        self,
        txn_id: str,
    ) -> dict[str, Any] | None:

        return self.get_record(
            "bank_txns",
            txn_id,
        )

    def get_efos_record(
        self,
        rfc: str,
    ) -> dict[str, Any] | None:

        return self.get_record(
            "efos_list",
            rfc,
        )

    def get_vendor_invoices(
        self,
        vendor_rfc: str,
    ) -> list[dict[str, Any]]:

        rows = self.conn.execute(
            """
            SELECT *
            FROM invoices
            WHERE issuer_rfc = ?
            ORDER BY issue_date, uuid
            """,
            (vendor_rfc,),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    def get_vendor_purchase_orders(
        self,
        vendor_rfc: str,
    ) -> list[dict[str, Any]]:

        rows = self.conn.execute(
            """
            SELECT *
            FROM purchase_orders
            WHERE vendor_rfc = ?
            ORDER BY date, po_id
            """,
            (vendor_rfc,),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    def get_vendor_contracts(
        self,
        vendor_rfc: str,
    ) -> list[dict[str, Any]]:

        rows = self.conn.execute(
            """
            SELECT *
            FROM contracts
            WHERE vendor_rfc = ?
            ORDER BY start_date, contract_id
            """,
            (vendor_rfc,),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    def get_bank_transactions_for_clabe(
        self,
        clabe: str,
    ) -> list[dict[str, Any]]:

        rows = self.conn.execute(
            """
            SELECT *
            FROM bank_txns
            WHERE from_clabe = ?
               OR to_clabe = ?
            ORDER BY date, txn_id
            """,
            (clabe, clabe),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    def get_ledger_for_invoice(
        self,
        invoice_uuid: str,
    ) -> list[dict[str, Any]]:

        rows = self.conn.execute(
            """
            SELECT *
            FROM ledger
            WHERE invoice_uuid = ?
            ORDER BY date, entry_id
            """,
            (invoice_uuid,),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    def find_vendor_by_clabe(
        self,
        clabe: str,
    ) -> dict[str, Any] | None:

        row = self.conn.execute(
            """
            SELECT *
            FROM vendors
            WHERE bank_clabe = ?
            """,
            (clabe,),
        ).fetchone()

        if row is None:
            return None

        return dict(row)

    def find_employee_by_clabe(
        self,
        clabe: str,
    ) -> dict[str, Any] | None:

        row = self.conn.execute(
            """
            SELECT *
            FROM employees
            WHERE bank_clabe = ?
            """,
            (clabe,),
        ).fetchone()

        if row is None:
            return None

        return dict(row)