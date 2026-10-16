from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0027_hubtel_diagnostic_evidence")]

    operations = [
        migrations.AlterField(
            model_name="hubtelevidence", name="direction",
            field=models.CharField(max_length=16, choices=[
                ("callback", "Callback"),
                ("status_check", "Status check"),
                ("wallet_verify", "Wallet name verification"),
                ("bank_verify", "Bank name verification"),
                ("direct_submit", "Direct transfer/collection"),
                ("direct_status", "Direct transaction status"),
                ("wallet_query", "Wallet verification request URL"),
                ("direct_request", "Direct payment request body"),
                ("preapprove_req", "Direct Debit approval request body"),
                ("preapprove_rsp", "Direct Debit approval response"),
            ]),
        ),
    ]
