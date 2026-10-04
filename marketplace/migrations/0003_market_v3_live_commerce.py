from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_worker_identity_credentials"),
        ("marketplace", "0002_premium_market_catalog_and_attachments"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="onlineorder",
            name="dispatched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="onlineorder",
            name="estimated_delivery_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="onlineorderline",
            name="sale_line",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="online_order_lines", to="core.line",
            ),
        ),
        migrations.CreateModel(
            name="MarketListingImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image_data", models.BinaryField(blank=True, editable=False, null=True)),
                ("image_thumb", models.BinaryField(blank=True, editable=False, null=True)),
                ("image_mime", models.CharField(blank=True, default="image/webp", max_length=40)),
                ("image_name", models.CharField(blank=True, max_length=180)),
                ("image_url", models.URLField(blank=True, default="")),
                ("image_credit", models.CharField(blank=True, default="", max_length=180)),
                ("alt_text", models.CharField(blank=True, default="", max_length=180)),
                ("caption", models.CharField(blank=True, default="", max_length=220)),
                ("sort_order", models.PositiveIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="gallery_images", to="marketplace.marketlisting")),
            ],
            options={"ordering": ["sort_order", "pk"]},
        ),
        migrations.CreateModel(
            name="DeliveryTrackingUpdate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(blank=True, default="", max_length=40)),
                ("note", models.CharField(blank=True, default="", max_length=320)),
                ("latitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("longitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("customer_visible", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="delivery_updates", to="marketplace.onlineorder")),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.CreateModel(
            name="WishlistItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="wishlist_items", to="marketplace.customeraccount")),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="wishlist_items", to="marketplace.marketlisting")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="wishlistitem",
            constraint=models.UniqueConstraint(fields=("customer", "listing"), name="unique_market_wishlist_item"),
        ),
        migrations.CreateModel(
            name="RecentView",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("view_count", models.PositiveIntegerField(default=1)),
                ("last_viewed_at", models.DateTimeField(auto_now=True)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recent_views", to="marketplace.customeraccount")),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recent_views", to="marketplace.marketlisting")),
            ],
            options={"ordering": ["-last_viewed_at"]},
        ),
        migrations.AddConstraint(
            model_name="recentview",
            constraint=models.UniqueConstraint(fields=("customer", "listing"), name="unique_market_recent_view"),
        ),
        migrations.CreateModel(
            name="MarketReturnRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("requested", "Requested"), ("approved", "Approved"), ("rejected", "Rejected"), ("processing", "Processing"), ("completed", "Completed")], default="requested", max_length=16)),
                ("resolution", models.CharField(choices=[("refund", "Refund"), ("exchange", "Exchange")], default="refund", max_length=12)),
                ("reason", models.TextField()),
                ("customer_note", models.TextField(blank=True)),
                ("staff_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("core_return_request", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.customerreturnrequest")),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="return_requests", to="marketplace.customeraccount")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="return_requests", to="marketplace.onlineorder")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="MarketReturnRequestLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("condition", models.CharField(choices=[("sellable", "Unused / sellable"), ("damaged", "Damaged / faulty")], default="sellable", max_length=16)),
                ("order_line", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="market_return_lines", to="marketplace.onlineorderline")),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="marketplace.marketreturnrequest")),
            ],
            options={"ordering": ["pk"]},
        ),
        migrations.AddConstraint(
            model_name="marketreturnrequestline",
            constraint=models.UniqueConstraint(fields=("request", "order_line"), name="unique_market_return_order_line"),
        ),
        migrations.CreateModel(
            name="MarketReturnAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_name", models.CharField(max_length=220)),
                ("mime_type", models.CharField(max_length=100)),
                ("size", models.PositiveIntegerField(default=0)),
                ("sha256", models.CharField(max_length=64)),
                ("data", models.BinaryField(editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="marketplace.marketreturnrequest")),
            ],
            options={"ordering": ["pk"]},
        ),
    ]
