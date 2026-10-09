"""Actionable, non-misleading guidance for provider payment failures.

This does not alter transaction status, initiate retries, or bypass the
customer's spending limits. Only the trusted gateway verification decides
whether a sale can be posted.
"""


def explain_provider_error(raw_message):
    text = str(raw_message or "").strip()[:240]
    normalized = " ".join(text.casefold().split())
    if "payer has reached" in normalized and "limit" in normalized:
        return (
            "Payment not approved: the customer's payment provider reports a spending or "
            "transaction-count limit. KOFAD cannot remove this restriction. The customer "
            "should check their limits with their mobile money network or bank, or use "
            "another payment method. Do not send repeated prompts before checking status."
        )
    if "target authorization error" in normalized or "wallet limit" in normalized:
        return (
            "Mobile Money authorisation was declined. The wallet may have insufficient "
            "funds or a transaction limit. Ask the customer to confirm with their network; "
            "do not record this payment unless Paystack independently verifies success."
        )
    if "insufficient" in normalized and ("fund" in normalized or "balance" in normalized):
        return (
            "The customer's payment provider reports insufficient funds. No payment is "
            "confirmed. Ask the customer to check their available balance or use another "
            "payment method."
        )
    if not text:
        return "Payment not confirmed. Verify the provider status before sending another request."
    return text
