# KOFAD IMPEX ENTERPRISE — owner's official logo

The **sole master asset** is `static/brand/kofad-original-logo.png`, imported
without re-encoding from the owner-supplied GitHub upload (which was mistakenly
named `kofad-original-logo.png.png` on the main branch).

Do not bring back any historical photo, monogram, JPEG-in-SVG artwork, or
earlier `kofad-emblem` image. The master contains the official appearance.

During Docker, CI and any local brand preparation, run:

```sh
python scripts/prepare_logo.py
```

The script builds the official transparent-padded full wordmark PNG and the
legacy-compatible SVG path from **only** the master PNG. It also generates
`favicon.ico`, square PNG icons at 16/32/48/96/180/192/512, and
`apple-touch-icon.png` from the same complete official logo used in the brand
header. The favicon is square with transparent padding and preserves the
whole logo instead of making an unreliable crop.

The same generated `kofad-official-logo.svg` appears in all KOFAD customer,
staff and public templates. The print and PDF source is the generated
`kofad-logo-transparent.png`, read by `core.brand_art.draw_mark`. This
covers receipts, invoices, staff credentials, PDF statements and other
ReportLab documents. Social sharing uses the PNG.

`/favicon.ico` is intentionally accessible on the company, market and staff
domains, including to Googlebot. Search engines cache favicons independently;
Google's result thumbnail can take time to change even after deployment.

Use object-fit contain for all full-logo slots. Never crop the business name,
stretch the logo, place a white tile behind it, or redraw it from scratch.
When the uploaded source already contains transparency, keep its alpha as is.
For opaque uniform-colour backgrounds, remove only the edge-connected colour.
Do not aggressively threshold a photographic background and erase lettering.

Colours (interface only): primary navy #0F3B58, deep navy #102A3D,
charcoal #1F252A, champagne gold #D1BB7F.
