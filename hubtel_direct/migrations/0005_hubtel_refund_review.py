"""Allow refund review before the ORIGINAL Hubtel OrderId is independently known.

This is not permission to issue a live refund. Unknown payment outcomes remain
locked until finance resolves them; the original OrderId is never staff-guessed.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("hubtel_direct", "0004_online_debt_policy"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hubtelrefundcase",
            name="original_hubtel_order_id",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="hubtelrefundcase",
            name="callback_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hubtelrefundcase",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hubtelrefundcase",
            name="review_note",
            field=models.CharField(blank=True, max_length=240),
        ),
    ]
