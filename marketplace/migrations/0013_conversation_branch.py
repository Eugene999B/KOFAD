from django.db import migrations, models
import django.db.models.deletion


def backfill_conversation_branch(apps, schema_editor):
    Conversation = apps.get_model("marketplace", "Conversation")
    Branch = apps.get_model("core", "Branch")
    OnlineOrder = apps.get_model("marketplace", "OnlineOrder")

    default_branch = Branch.objects.filter(active=True).order_by("pk").first() or Branch.objects.order_by("pk").first()
    order_branches = dict(
        OnlineOrder.objects.filter(
            pk__in=Conversation.objects.exclude(order_id__isnull=True).values_list("order_id", flat=True)
        ).values_list("pk", "branch_id")
    )
    for conversation in Conversation.objects.filter(branch_id__isnull=True).only("pk", "order_id"):
        branch_id = order_branches.get(conversation.order_id) or getattr(default_branch, "pk", None)
        if branch_id:
            Conversation.objects.filter(pk=conversation.pk, branch_id__isnull=True).update(branch_id=branch_id)


class Migration(migrations.Migration):
    dependencies = [
        ("marketplace", "0012_receiving_account_details"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="branch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="market_conversations",
                to="core.branch",
            ),
        ),
        migrations.RunPython(backfill_conversation_branch, migrations.RunPython.noop),
    ]
