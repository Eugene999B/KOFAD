import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0002_ledger_integrity"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AlterField(model_name="document", name="kind", field=models.CharField(max_length=20, choices=[
            ("sale","Sale"),("purchase","Purchase"),("return","Return"),("expense","Expense"),
            ("collection","Debt payment"),("supplier_payment","Supplier payment"),("reversal","Reversal")])),
        migrations.CreateModel(name="Correction", fields=[
            ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
            ("refund_method",models.CharField(choices=[("cash","Cash"),("momo","MoMo"),("bank","Bank"),("card","Card")],default="cash",max_length=8)),
            ("reason",models.TextField()),
            ("status",models.CharField(default="requested",max_length=12)),
            ("created_at",models.DateTimeField(auto_now_add=True)),
            ("original",models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name="correction",to="core.document")),
            ("posted",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name="+",to="core.document")),
            ("requested_by",models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name="+",to=settings.AUTH_USER_MODEL)),
            ("reviewed_by",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name="+",to=settings.AUTH_USER_MODEL)),
        ], options={"ordering":["-created_at"]}),
    ]
