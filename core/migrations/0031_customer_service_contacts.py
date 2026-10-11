from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0030_email_center")]

    operations = [
        migrations.CreateModel(
            name="CustomerServiceContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(max_length=70)),
                ("channel", models.CharField(max_length=12, choices=[("call", "Customer care phone"), ("whatsapp", "Customer care WhatsApp")])),
                ("number", models.CharField(max_length=20)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"ordering": ["sort_order", "pk"]},
        ),
    ]
