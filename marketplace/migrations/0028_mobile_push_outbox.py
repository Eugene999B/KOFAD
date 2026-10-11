from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[("marketplace","0027_mobile_push_subscriptions")]
    operations=[
        migrations.CreateModel(
            name="MobilePushOutbox",
            fields=[
                ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
                ("event_key",models.CharField(max_length=160,unique=True)),
                ("kind",models.CharField(max_length=12,choices=[("service","Service"),("promotion","Promotion")],default="service")),
                ("title",models.CharField(max_length=90)),
                ("body",models.CharField(max_length=200)),
                ("status",models.CharField(max_length=12,default="queued",choices=[("queued","Queued"),("sending","Sending"),("sent","Sent"),("failed","Failed"),("uncertain","Needs review")],db_index=True)),
                ("created_at",models.DateTimeField(auto_now_add=True)),
                ("attempted_at",models.DateTimeField(null=True,blank=True)),
                ("subscription",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="outbox",to="marketplace.mobilepushsubscription")),
            ],
            options={"indexes":[models.Index(fields=["status","created_at"],name="kof_push_outbox_due_idx")]},
        ),
    ]
