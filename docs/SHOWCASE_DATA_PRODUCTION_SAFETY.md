# Synthetic KOFAD showcase data: production safety

KOFAD includes management commands that create **synthetic** orders, bills, staff, payroll and other demo records. These are useful for a disposable sandbox, but must not be automatically added to an operational company database.

## Deployment behavior

`initialize_deployment` applies migrations and performs the existing guarded account setup. It **never** calls `load_showcase_data` or `complete_showcase_data` as part of a web or SMS redeployment. A previously configured `KOFAD_LOAD_SHOWCASE_DATA=1` is deliberately ignored with a warning.

The showcase commands remain available for an operator to run **manually on an isolated test database**, with their existing explicit confirmation flags:

- `python manage.py load_showcase_data --confirm-live-showcase`
- `python manage.py complete_showcase_data --confirm-live-showcase`

These commands are not safe ways to test on a real operating business database, even with confirmation flags.

## Existing production demo records

This change **does not remove, alter, hide, or retroactively reclassify** any existing records. In particular, the presence of the `SHOWCASE-DATA-V1` and `SHOWCASE-EXTENDED-V2-20261004` audit markers is evidence that synthetic showcase loading took place; the markers alone do not establish which business totals are affected.

Before relying on historical production KPIs for audits or tax/accounting decisions:

1. Take and verify an encrypted, offsite backup; exercise a restore on an isolated database.
2. Enumerate all synthetic seed references and cross-linked orders, inventory movements, creditors, payroll and journals, including the marker audit events.
3. Produce a read-only reconciliation distinguishing genuine business activity from synthetic fixtures.
4. Review any proposed adjustments with an authorized accountant and owner. Apply only independently checked changes, using KOFAD's normal correction and audit controls.

Never bulk-delete records based on a name prefix or the existence of a showcase marker, since genuine transactions may reference seeded suppliers, products or opening stock.
