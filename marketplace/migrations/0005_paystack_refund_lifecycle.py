from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0004_live_support_typing")]

    operations = [
        migrations.AlterField(
            model_name="marketreturnrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("requested", "Requested"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("processing", "Processing"),
                    ("refund_attention", "Refund needs attention"),
                    ("completed", "Completed"),
                ],
                default="requested",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="marketreturnrequest",
            name="refund_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="marketreturnrequest",
            name="provider_refund_id",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="marketreturnrequest",
            name="provider_refund_status",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="marketreturnrequest",
            name="provider_refund_message",
            field=models.CharField(blank=True, default="", max_length=240),
        ),
        migrations.AddField(
            model_name="marketreturnrequest",
            name="refund_initiated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="marketreturnrequest",
            name="refund_processed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
