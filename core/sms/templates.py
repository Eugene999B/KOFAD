import re
from django.core.exceptions import ValidationError
from core.models import Company, MessageTemplate
from core.services import balance

TOKENS = {"company","customer","reference","total","paid","balance","due_date","currency","business_phone","location"}
DEFAULTS = {
    "receipt":("Sale receipt","{company}: Receipt {reference}. Total {currency} {total}; paid {paid}; balance {balance}. Thank you."),
    "payment":("Payment confirmation","{company}: Payment {currency} {paid} received. Reference {reference}. Thank you."),
    "debt":("Payment reminder","{company}: Hello {customer}, invoice {reference} has {currency} {balance} outstanding, due {due_date}. Please contact us if already paid."),
}


def validate_template(body):
    keys = set(re.findall(r"\{([^{}]+)\}",body))
    if not keys.issubset(TOKENS) or "{" in re.sub(r"\{[^{}]+\}","",body) or "}" in re.sub(r"\{[^{}]+\}","",body):
        raise ValidationError("Use only the documented message placeholders.")
    if not body.strip() or len(body)>1500:
        raise ValidationError("Template must contain 1–1,500 characters.")


def render_for_document(document,code):
    if not document.party:
        raise ValidationError("The transaction has no customer contact.")
    if code == "receipt" and document.kind != "sale":
        raise ValidationError("Receipts require a sale.")
    if code == "debt" and (document.kind != "sale" or balance(document)<=0):
        raise ValidationError("Debt reminders require an outstanding sale.")
    if code == "payment" and document.kind != "collection":
        raise ValidationError("Payment confirmations require a debt collection.")
    template = MessageTemplate.objects.filter(code=code,active=True).first()
    if not template:
        raise ValidationError("This message template is unavailable.")
    validate_template(template.body)
    company = Company.objects.first() or Company()
    business_phone = " / ".join(
        value.strip() for value in (company.phone, company.secondary_phone) if value and value.strip()
    )
    location = document.branch.address.strip() if document.branch.address else (company.address.strip() if company.address else document.branch.name)
    data = {"company":company.name,"customer":document.party.name,"reference":document.reference,
        "total":str(document.total),"paid":str(document.paid),"balance":str(balance(document)) if document.kind=="sale" else "0.00",
        "due_date":str(document.due_date or ""),"currency":company.currency,
        "business_phone": business_phone, "location": location}
    return re.sub(r"\{([^{}]+)\}",lambda match:data[match.group(1)],template.body)
