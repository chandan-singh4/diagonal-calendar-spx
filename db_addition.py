# ─────────────────────────────────────────────────────────────────────────────
# ADD THIS FUNCTION to db.py, directly after your existing update_trade()
# ─────────────────────────────────────────────────────────────────────────────

def delete_trade(db_path: str, trade_id: str) -> None:
    """Permanently removes a trade record by trade_id.
    Called only from pages/journal.py after explicit user confirmation.
    No cascade needed — trades have no child rows in other tables.
    """
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM trades WHERE trade_id = ?", (trade_id,))
