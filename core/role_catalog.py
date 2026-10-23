"""Canonical list of *enforced, delegable* KOFAD staff permissions.

Only grants checked by the application may appear in the role designer.
Django-generated add/change/delete/view model permissions do not necessarily
correspond to a safely delegated business workflow and must not silently
become grantable via the generic role form.
"""

PERMISSION_HELP = {
    "operate_sales": ("Sales", "Create sales, use the counter and work with customer-facing sales records."),
    "operate_inventory": ("Inventory", "Receive purchases, manage inventory, Supplier Returns and Inventory Verification."),
    "operate_finance": ("Finance", "Record expenses, customer collections, supplier payments and daily closing."),
    "approve_operations": ("Approvals", "Review controlled returns, inventory verification, accounting, payroll, corrections and closing verification."),
    "view_reports": ("Reports", "View business reports, cost/profit information and audit evidence."),
    "manage_company": ("Company settings", "Manage business configuration. Only the system administrator can change staff access and roles."),
    "send_messages": ("Communications", "Queue and retry approved customer SMS messages."),
    "add_product": ("Products · create", "Create new products."),
    "change_product": ("Products · edit", "Edit product setup and selling prices."),
    "add_party": ("Contacts · create", "Create customers and suppliers where the role also has the relevant business access."),
    "change_party": ("Contacts · edit", "Edit customers and suppliers where the role also has the relevant business access."),
}
ROLE_PERMISSION_CODES = tuple(PERMISSION_HELP)

ROLE_DESCRIPTIONS = {
    "Owner": "Full business control, reporting, administration and approvals.",
    "Manager": "Daily operational control across sales, stock, finance, reports and approvals.",
    "Cashier": "Fast counter sales and quick customer creation.",
    "Storekeeper": "Purchasing, inventory, Supplier Returns and Inventory Verification.",
    "Accountant": "Finance, receivables/payables and reporting.",
    "Auditor": "Read-only business reporting and audit visibility.",
}

# Fresh start recreates only templates. Existing custom roles, employee grants,
# branch assignments and selected-role memberships belong in an encrypted full
# backup and are preserved by RESTORE, but deliberately not by RESET.
FRESH_START_ROLES = {
    "Owner": list(ROLE_PERMISSION_CODES),
    "Manager": [code for code in ROLE_PERMISSION_CODES if code != "manage_company"],
    "Cashier": ["operate_sales", "add_party"],
    "Storekeeper": ["operate_inventory"],
    "Accountant": ["operate_finance", "view_reports", "change_party", "add_party"],
    "Auditor": ["view_reports"],
}
