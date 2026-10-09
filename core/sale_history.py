"""Human-readable and evidence-backed classifications for the sales ledger.

A cashier-entered MoMo payment is NOT the same as a Paystack-verified deposit.
Only a posted Paystack intent pointing to this exact sale may display the
"provider verified" badge. Historic cash/bank/manual MoMo records remain
honestly classified as staff-recorded.
"""
from .models import HeldSale
from . import pos_paystack


def decorate_sales(branch, rows):
    documents = list(rows)
    if not documents:
        return documents
    by_pk = {str(doc.pk): doc for doc in documents}
    paystack_refs = {}
    for held in HeldSale.objects.filter(
        branch=branch,
        label__startswith=pos_paystack.LABEL_PREFIX,
        cart__payment_request__status="success",
        cart__payment_request__document_id__in=list(by_pk),
    ).only("label", "cart"):
        state = (held.cart or {}).get("payment_request") or {}
        pk = str(state.get("document_id") or "")
        reference = str(state.get("reference") or "")
        if (pk in by_pk
                and held.label == pos_paystack.LABEL_PREFIX + reference
                and any(payment.method == "momo" and payment.reference == reference
                        for payment in by_pk[pk].payments.all())):
            paystack_refs[pk] = reference

    for doc in documents:
        methods = [payment.get_method_display() for payment in doc.payments.all()]
        doc.payment_method_summary = ", ".join(methods) if methods else "No payment recorded"
        doc.paystack_verified_reference = paystack_refs.get(str(doc.pk), "")
        doc.balance_at_posting = doc.total - doc.paid
        if doc.kind == "sale":
            if doc.paystack_verified_reference:
                doc.transaction_type = ("Verified MoMo deposit + debt" if doc.balance_at_posting > 0
                                        else "Verified MoMo sale")
                doc.payment_evidence = "Paystack verified and sale posted"
            elif doc.paid == 0:
                doc.transaction_type = "Credit sale"
                doc.payment_evidence = "No initial payment"
            elif doc.balance_at_posting > 0:
                doc.transaction_type = "Part payment + debt"
                doc.payment_evidence = "Payment recorded by staff"
            else:
                doc.transaction_type = "Paid sale"
                doc.payment_evidence = "Payment recorded by staff"
        else:
            doc.transaction_type = doc.get_kind_display()
            doc.payment_evidence = "Business transaction record"
    return documents
