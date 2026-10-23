# Staff password login rate limits (production)

Account budget: five failed passwords within the active 15-minute lock window.
A locked account is rejected **before** running `authenticate()`; a fresh
browser cookie or correct password cannot bypass an active account lock.
Use the existing independently verified password-recovery process or wait
for expiry. Account locks can be targeted by an attacker, so Cloudflare
Turnstile/WAF and MFA remain essential complementary controls.

Source budget: 50 failed passwords by default before a 15-minute hold;
configure `KOFAD_LOGIN_SOURCE_MAX_FAILURES` in [20, 200] if justified by
traffic and operational review. The key is SHA-256 of the network peer from
`REMOTE_ADDR`—never an untrusted `CF-Connecting-IP` or
`X-Forwarded-For` HTTP header, which an attacker could forge if origin
access is not independently restricted. Behind Railway/Cloudflare this
peer can be shared across customers. Monitor aggregate 429/lockout trends
and configure Cloudflare WAF using its own verified source-IP context;
do not blindly trust arbitrary forwarded headers in Django.

This change does not enable Cloudflare Turnstile or MFA by itself. Verify
both live feature switches, site keys, WAF rules and emergency staff
recovery before declaring the entire production authentication perimeter
fully protected.

Future hardening: a reviewed authenticated-proxy policy for verified
client-IP signals, self-service recovery after repeated abuse, and
retention cleanup for expired LoginAttempt rows. Never purge a currently
active throttle row while an account is under attack.
