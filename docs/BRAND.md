# KOFAD IMPEX ENTERPRISE identity

KOFAD uses KOFAD's original trade emblem: a parcel with interwoven navy, gold and teal routes representing goods moving through a connected business. The emblem is intentionally separate from the wordmark so it remains clear at favicon, ID-card and receipt sizes.

- Navy: #102B46
- Gold: #E9AC32
- Teal: #138C94
- Workspace background: #F3F6FA
- Text: #172F47

The official transparent emblem is `static/brand/kofad-emblem.png`. The shared `templates/brand.html` component pairs it with a concise **KOFAD / IMPEX ENTERPRISE** wordmark. The same emblem is used for sign-in, navigation, welcome, receipts, workforce credentials and browser icons.

Keep the emblem aspect ratio unchanged. Use a clean white tile behind it on dark surfaces. Navy is the trust/control colour, teal is the interactive/verification colour, and gold is reserved for premium highlights and credential security accents.

The product internals and historical KOFAD references remain unchanged; this document describes the visible brand treatment only.

## Origin

The heritage emblem was the first production identity used by this system. Its original generation brief described a navy, gold and teal circular trade emblem with interwoven routes around a parcel cube and no embedded lettering. The KOFAD IMPEX ENTERPRISE wordmark is rendered separately for legibility and easy future brand maintenance.

Printed records must call `core.brand_art.draw_mark`, which embeds this exact PNG. Do not substitute a monogram or redraw the emblem. ID credentials use navy, gold and teal, a quiet emblem watermark, a clear portrait, and QR verification on the reverse.
