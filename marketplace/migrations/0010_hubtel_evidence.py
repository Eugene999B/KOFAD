from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0009_conversation_support_lifecycle")]

    operations = [
        migrations.CreateModel(
            name="HubtelEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.CharField(db_index=True, max_length=100)),
                ("direction", models.CharField(choices=[("callback", "Callback"), ("status_check", "Status check")], max_length=16)),
                ("http_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("encrypted_body", models.TextField(editable=False)),
                ("body_sha256", models.CharField(editable=False, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("attempt", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="hubtel_evidence", to="marketplace.marketpaymentattempt")),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.AddIndex(
            model_name="hubtelevidence",
            index=models.Index(fields=["reference", "-created_at"], name="market_hubtel_ref_time_idx"),
        ),
    ]
