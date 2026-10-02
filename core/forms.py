from django import forms
from .models import Company, Party, Product


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "barcode", "category", "base_unit", "pack_name", "pack_size",
                  "cost", "retail_unit", "retail_pack", "wholesale_unit", "wholesale_pack", "reorder_level", "active"]

    def clean(self):
        data = super().clean()
        if all(data.get(k) is None for k in ("retail_unit", "retail_pack", "wholesale_unit", "wholesale_pack")):
            raise forms.ValidationError("Enable at least one selling price. A blank price disables that mode.")
        return data


class PartyForm(forms.ModelForm):
    class Meta:
        model = Party
        fields = ["name", "phone", "email", "address", "credit_limit", "consent"]


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name", "phone", "address", "receipt_footer", "closing_tolerance"]


class MessageTemplateForm(forms.ModelForm):
    class Meta:
        from .models import MessageTemplate
        model = MessageTemplate
        fields = ["name","body","active"]
    def clean_body(self):
        from .sms.templates import validate_template
        body = self.cleaned_data["body"]
        validate_template(body)
        return body
