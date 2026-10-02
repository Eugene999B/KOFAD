# Physical stock counts

Open **Stock operations → Physical stock counts**. Start a sheet for all active products or one category (maximum 500 products per sheet). Quantities are in the product's base unit.

1. Coordinate a quiet counting window. Sales are not automatically paused.
2. Count each product without the system balance being displayed. Enter zero for empty shelves and record an observation for every line.
3. Save incomplete work or submit a complete sheet. Only the original counter can enter quantities.
4. A different colleague with approval permission reviews the expected quantity, physical count and variance, then approves or rejects with a note.
5. Approval posts variance movements atomically. Matching quantities produce no movement. Completed evidence cannot be edited or deleted.

Every sheet records quantities and the latest movement for each included product at its start. Any later movement invalidates approval, including movements whose net quantity is zero. Reject the stale sheet and start a fresh count. Stock sold while counting is never silently overwritten.

Counts use the same branch lock as sales and other stock postings. Duplicate approvals cannot post twice. Closed days cannot accept count adjustments. Cancelling or rejecting a count does not change stock.

The system balances are hidden on the draft sheet to support blind entry; this is not a separate secrecy boundary for staff who also have inventory access. Submitted quantities and original snapshots are protected by PostgreSQL triggers.
