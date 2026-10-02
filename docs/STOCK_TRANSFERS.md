# Transfer receipts and discrepancies

Transfer approval does not move stock. Dispatch removes units from the source. Receipt adds only the explicitly entered **sellable units** to the destination.

At the destination, open **Stock operations**, enter the number received (zero is valid), and explain missing or damaged units. A quantity below the dispatched total creates an unresolved discrepancy. The original receipt quantity and explanation remain permanent.

A different colleague with approval permission at the destination must choose one outcome:

- **Confirm remainder arrived:** add the remaining units to destination stock once, with an explanation.
- **Confirm missing / damaged loss:** close the discrepancy without adding stock at either branch. Retain the quantity, reason, reviewer and time in the audit trail.

Use the arrival option only after all remaining units are physically present and sellable. Multiple partial follow-up deliveries, damaged-goods quarantine, carrier claims and general-ledger loss postings are not implemented. Keep unresolved partial deliveries under review until the full remainder arrives or is classified as lost/damaged.

Identical receipt retries do not duplicate inventory. A different submitted receipt is rejected. PostgreSQL protects receipt evidence, and branch permissions and daily closing locks apply to dispatch, receipt and resolution.

Historical transfers completed before this feature remain historical records; the migration does not invent receiver evidence.
