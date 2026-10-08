# KOFAD WhatsApp assistant

The assistant is off by default. Existing SMS and WhatsApp receipt automation retain their own independent settings.

## Activation

After Meta business verification and phone registration:
1. Configure WHATSAPP_ACCESS_TOKEN, WHATSAPP_APP_SECRET, WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_BUSINESS_ACCOUNT_ID in Railway, on web and worker. Never commit secrets.
2. Keep the existing private WHATSAPP_WEBHOOK_VERIFY_TOKEN aligned with Meta. Configure the HTTPS /whatsapp/webhook/ callback and subscribe to messages. Publish the Meta app when requirements are met.
3. Set a supported WHATSAPP_GRAPH_VERSION, WHATSAPP_ENABLED=1 and WHATSAPP_BOT_ENABLED=1 on both services.
4. Send an explicitly authorized test message; verify inbound events, queued/accepted/delivered status, HUMAN staff handoff, STOP and expired-window behaviour. Business-initiated notifications separately require approved templates and any Meta billing setup.

## Customer commands

SHOP, SEARCH product name, ORDERS, RECEIPTS, DELIVERY, RETURNS, ACCOUNT, PAYMENT, PRIVACY, STAFF, HUMAN, STOP, START.

Orders, receipts and account information open secure account pages. Staff commands open the staff workspace; no permissions are granted through WhatsApp. Checkout remains on the verified provider flow.

HUMAN opens a Customer Inbox conversation with the sender's WhatsApp number. Staff accept and reply from the existing inbox; text replies queue to WhatsApp. Attachments are not forwarded automatically. Identify the customer through the authenticated website before sharing private information. START leaves the automated handoff state.

## Operations

Settings → SMS & WhatsApp → Assistant setup & delivery status shows configuration and recent reply outcomes. Customer conversations are in Customer Inbox. The existing process_sms worker handles replies every five seconds, without extra always-on services.

Accepted is not delivered. Signed Meta status callbacks update delivery state. Unknown submissions are not retried automatically. Messages older than 24 hours cannot reopen a conversation window. The bot has no arbitrary code, SQL, financial or permission-changing tools.

The bot uses public product information and account links; it is intentionally not an unrestricted generative AI agent. Add new commands only with explicit authorization checks and regression tests.
