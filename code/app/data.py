"""Read-only access to the data pack under DATA_DIR. Nothing here writes."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from functools import cached_property
from pathlib import Path


class DataError(Exception):
    """A record the run needs is missing or unreadable."""


@dataclass(frozen=True)
class Agreement:
    agreement_id: str
    vendor_id: str
    item_code: str
    revised_price: float
    effective_from: date
    valid_until: date | None
    signed: bool
    source: str  # file name under docs/vendor_agreements


def _parse_long_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%d %B %Y").date()


def parse_agreement(text: str, source: str) -> list[Agreement]:
    """Turn one signed price-revision document into rows. Unparseable rows are skipped, never guessed."""
    agr = re.search(r"\b(AGR-\d+)\b", text)
    vendor = re.search(r"\((V-\d+)\)", text)
    if not agr or not vendor:
        return []
    status = re.search(r"\*\*Status:\*\*\s*(.+)", text)
    signed = bool(status and re.search(r"\bsigned\b", status.group(1), re.I))
    until = re.search(r"fixed until (\d{1,2} \w+ \d{4})", text)
    valid_until = _parse_long_date(until.group(1)) if until else None
    rows = []
    for m in re.finditer(r"^\|\s*(IT-\d+)\s*\|[^|]*\|\s*₹\s*([\d,]+(?:\.\d+)?)\s*\|\s*(\d{1,2} \w+ \d{4})\s*\|", text, re.M):
        rows.append(Agreement(agr.group(1), vendor.group(1), m.group(1), float(m.group(2).replace(",", "")),
                              _parse_long_date(m.group(3)), valid_until, signed, source))
    return rows


class Repo:
    """One read-only connection to ap.sqlite plus the documents folder."""

    def __init__(self, root: Path):
        self.root = Path(root)
        db = self.root / "data" / "ap.sqlite"
        if not db.is_file():
            raise DataError(f"ap.sqlite not found under DATA_DIR={self.root}")
        self.con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row

    def close(self) -> None:
        self.con.close()

    def _all(self, sql: str, *args) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def _one(self, sql: str, *args) -> dict | None:
        row = self.con.execute(sql, args).fetchone()
        return dict(row) if row else None

    # --- tables -----------------------------------------------------------
    def invoice(self, invoice_id: str) -> dict:
        inv = self._one("SELECT * FROM invoices WHERE invoice_id = ?", invoice_id)
        if inv is None:
            raise DataError(f"invoice {invoice_id} not found")
        return inv

    def invoice_lines(self, invoice_id: str) -> list[dict]:
        return self._all("SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY line_no", invoice_id)

    def vendor(self, vendor_id: str) -> dict | None:
        return self._one("SELECT * FROM vendors WHERE vendor_id = ?", vendor_id)

    def purchase_order(self, po_id: str) -> dict | None:
        return self._one("SELECT * FROM purchase_orders WHERE po_id = ?", po_id)

    def po_lines(self, po_id: str) -> dict[int, dict]:
        return {r["line_no"]: r for r in self._all("SELECT * FROM po_lines WHERE po_id = ?", po_id)}

    def goods_receipts(self, po_id: str) -> list[dict]:
        return self._all("SELECT * FROM goods_receipts WHERE po_id = ? ORDER BY grn_id", po_id)

    def received_qty(self, po_id: str) -> dict[int, int]:
        """Quantity received per PO line, summed over every GRN for the PO."""
        rows = self._all(
            "SELECT gl.po_line_no, SUM(gl.qty_received) AS qty FROM grn_lines gl "
            "JOIN goods_receipts g ON g.grn_id = gl.grn_id WHERE g.po_id = ? GROUP BY gl.po_line_no", po_id)
        return {r["po_line_no"]: r["qty"] for r in rows}

    def item(self, item_code: str) -> dict | None:
        return self._one("SELECT * FROM items WHERE item_code = ?", item_code)

    def vendor_invoices(self, vendor_id: str) -> list[dict]:
        return self._all("SELECT * FROM invoices WHERE vendor_id = ?", vendor_id)

    def payments_for(self, invoice_id: str) -> list[dict]:
        return self._all("SELECT * FROM payments WHERE invoice_id = ?", invoice_id)

    # --- documents --------------------------------------------------------
    @cached_property
    def agreements(self) -> list[Agreement]:
        folder = self.root / "docs" / "vendor_agreements"
        rows: list[Agreement] = []
        if folder.is_dir():
            for f in sorted(folder.glob("*.md")):
                rows.extend(parse_agreement(f.read_text(encoding="utf-8"), f.name))
        return rows

    def vendor_email(self, invoice_id: str) -> str | None:
        f = self.root / "docs" / "vendor_emails" / f"{invoice_id}.md"
        return f.read_text(encoding="utf-8") if f.is_file() else None
