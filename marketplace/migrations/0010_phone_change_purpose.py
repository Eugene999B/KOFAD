from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0009_conversation_support_lifecycle")]
    operations = [
        migrations.AlterField(
            model_name="otpthrottle", name="purpose",
            field=models.CharField(max_length=12, choices=[
                ("register", "Register"), ("reset", "Reset password"),
                ("login", "Customer login"), ("change_phone", "Change phone"),
            ]),
        ),
    ]
