# KOFAD IMPEX ENTERPRISE identity

KOFAD uses one approved official logo across every customer, staff and printed surface.

- Canonical asset: `static/brand/kofad-official-logo.jpg`
- Primary navy: #0F3B58
- Deep navy: #102A3D
- Charcoal: #1F252A
- Champagne gold: #D1BB7F
- Warm paper: #F7F4ED

Do not redraw the logo, substitute a monogram, pair it with a competing wordmark, or introduce another KOFAD logo file. The official asset is used for the public website, Market, customer authentication, staff authentication, workspace navigation, browser icon, receipts, invoices, statements, workforce credentials and other generated documents.

Printed records call `core.brand_art.draw_mark`, which embeds the same canonical asset. Web templates should reference the same file through Django static files.

The visual system should use navy/charcoal for trust and structure, champagne gold for restrained highlights, and warm neutral surfaces for readability. Avoid unrelated accent palettes that make KOFAD authentication or customer pages appear to belong to different products.
