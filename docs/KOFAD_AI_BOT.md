# KOFAD Bot — Public company and customer AI

## What visitors see

On the company homepage at \`https://kofadimpex.com/\`, the Market storefront,
and signed-in customer pages, a floating, responsive robot button labeled
**KOFAD Bot · Ask me anything** opens a built-in chat. The widget can answer
questions about retail and wholesale, current published products with price and
selling unit, returns, fulfilment, contact details and official business FAQs.
It is not a separate Telegram bot; the three existing Telegram bots also
support the explicit \`/ask PUBLIC QUESTION\` command.

Signed-in customers may ask for their own recent orders: the backend checks
their existing KOFAD customer session and queries their own order records.
Neither order information nor account/phone/payment information is sent to
OpenRouter. The public chatbot cannot act as a login, approve payments or modify
transactions.

## Provider configuration

**Only the free OpenRouter router is enabled.** The code has a constant
\`FREE_MODEL = "openrouter/free"\` and does not accept paid model IDs from
Railway, browsers, customers or the API. It never retries with another paid
model. OpenRouter request body is bounded with \`max_tokens=320\`, only public
company and published product context, and no tools, images or web-search addons. Every request also adds an
OpenRouter provider-level `max_price.prompt=0` and
`max_price.completion=0`, plus `data_collection=deny` so only providers
that disclose no user-data collection are eligible. If no provider satisfies
all conditions, KOFAD falls back to its local public-help answers.

The OpenRouter key must be entered in Railway for **kofad-web only**:

- \`OPENROUTER_API_KEY\`: an API key created in your OpenRouter account
- \`KOFAD_OPENROUTER_FREE_ENABLED=1\`: enable outbound free-model calls

Never put your API key in a git commit, browser JavaScript, public website,
customer support conversation, Telegram message or staff report. **Without the
key**, the KOFAD Bot still handles public FAQs, product search, contact and
private customer orders using local KOFAD data.

OpenRouter free model availability and free rate limits are outside KOFAD's
control. The site limits guest sessions to 15 messages/day, signed-in customer
sessions to 30/day, each incoming IP source to 50/day, and provider calls to
40/day (best-effort cache rate limits). There is no conversion to a paid
model when the free daily quota is exhausted. This avoids inference charges,
but Railway hosting, existing SMS/email and other services have their own
independent costs.

## Privacy and accuracy

The chat requests explicit consent before forwarding public questions to
OpenRouter. Submitted public questions may be processed or retained by an
external provider under that provider's data policy. The system rejects
obvious phone, email, password, OTP, card/bank and account identifiers as well
as internal-financial or private-profile requests instead of forwarding them.

Provider context contains only published products, KOFAD's public company
contact details and approved KOFAD FAQ/policy text. Private sessions, customer
identifiers, order references, staff roles, payroll, cash balances, or
internal accounting records are **never supplied** in model messages.
The AI is instructed to avoid inventing pricing, stock, delivery periods,
terms, business hours or availability. Returned text is rendered as text,
not executable HTML. Only canonical KOFAD HTTPS domains are hyperlinkable.

KOFAD should review OpenRouter's current privacy policies and free-provider
terms before enabling external AI. Do not claim free models guarantee the
privacy requirements of highly sensitive financial or payment systems.

## Tests and rollout

The focused \`Verify free-only KOFAD AI\` workflow tests privacy barriers,
zero-paid-model routing, external-AI opt-in, customer session isolation,
free-provider outage fallback and homepage widget. The existing full KOFAD
test suite and Telegram checks must also pass before release.

The bots and homepage continue working if OpenRouter is unavailable. Existing
Telegram account linking and webhook routing are unchanged.
