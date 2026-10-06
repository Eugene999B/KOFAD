# WhatsApp Business delivery

KOFAD supports Meta Cloud API notifications and verified delivery callbacks. SMS remains independent and uses the existing provider adapter (including Arkesel).

## Connect the business account

Set these secrets in Railway on both the web and messaging-worker services:
- WHATSAPP_ENABLED=1
- WHATSAPP_ACCESS_TOKEN: a system-user access token authorized for the business phone
- WHATSAPP_PHONE_NUMBER_ID: the numeric Meta phone-number ID
- WHATSAPP_APP_SECRET: the Meta application secret used to verify callbacks
- WHATSAPP_WEBHOOK_VERIFY_TOKEN: a private webhook verification value
- WHATSAPP_PUBLIC_ORIGIN: the public HTTPS site origin
- WHATSAPP_GRAPH_VERSION: a currently supported Graph API version

Set the Meta callback URL to https://YOUR-DOMAIN/whatsapp/webhook/. Subscribe the Meta application to messages. Never commit tokens to GitHub.

In Settings → SMS & WhatsApp, enter the exact approved template name and language. The notification template must have **one body text parameter**, which receives the complete notification text (maximum 1,024 characters). Meta must approve that template for the intended business use before automatic delivery is enabled.

## Delivery behavior

Each channel has independent event switches for sales, payment confirmations, closing and stock alerts; WhatsApp also follows the debt reminder schedule. At the sales counter, the cashier's SMS and WhatsApp choices override the automatic receipt defaults for that individual sale. Unticked means no receipt on that channel.

Manual WhatsApp messages use free-form text only within 24 hours of a verified inbound customer message; otherwise an approved template is required. Without Cloud credentials, Communications can still prepare manual WhatsApp chat links, which are never claimed as delivered.

Submissions are queued durably and processed by the messaging worker, so checkout and closing do not wait for Meta. Provider acceptance is not delivery. Signed Meta callbacks advance accepted → sent → delivered → read. Network timeouts and interrupted submissions are marked unknown and are not automatically retried, to avoid duplicate customer messages. Check delivery before attempting another send. Customers require consent for automatic notifications; management alerts use the configured active management contacts.

## Verification

GitHub Actions uses fake providers, isolated customers and sample records. No test messages are sent to production customers. Confirm a real provider delivery only with an explicitly authorized test recipient after account activation.
