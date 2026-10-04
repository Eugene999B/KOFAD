from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0007_onlineorder_confirmed_reference")]

    operations = [
        migrations.AddField(model_name="onlineorder", name="delivery_distance_km", field=models.DecimalField(blank=True, decimal_places=2, max_digits=9, null=True)),
        migrations.AddField(model_name="onlineorder", name="delivery_distance_source", field=models.CharField(blank=True, default="", max_length=24)),
        migrations.AddField(model_name="onlineorder", name="delivery_pricing_mode", field=models.CharField(blank=True, default="", max_length=16)),
        migrations.AddField(model_name="onlineorder", name="delivery_origin_latitude", field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
        migrations.AddField(model_name="onlineorder", name="delivery_origin_longitude", field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
        migrations.AddField(model_name="onlineorder", name="delivery_route_polyline", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="onlineorder", name="delivery_duration_seconds", field=models.PositiveIntegerField(blank=True, null=True)),
    ]
