from django.db import migrations
from django.db.models import F

TEMPLATES = {
    "receipt":("Sale receipt","{company}: Receipt {reference}. Total {currency} {total}; paid {paid}; balance {balance}. Thank you."),
    "payment":("Payment confirmation","{company}: Payment {currency} {paid} received. Reference {reference}. Thank you."),
    "debt":("Payment reminder","{company}: Hello {customer}, invoice {reference} has {currency} {balance} outstanding, due {due_date}. Please contact us if already paid."),
}


def seed(apps,schema_editor):
    Template = apps.get_model("core","MessageTemplate")
    for code,(name,body) in TEMPLATES.items():
        Template.objects.get_or_create(code=code,defaults={"name":name,"body":body})
    ContentType = apps.get_model("contenttypes","ContentType")
    Permission = apps.get_model("auth","Permission")
    Group = apps.get_model("auth","Group")
    content_type,_ = ContentType.objects.get_or_create(app_label="core",model="message")
    permission,_ = Permission.objects.get_or_create(content_type=content_type,codename="send_messages",
        defaults={"name":"Queue and retry customer SMS"})
    groups = Group.objects.filter(name__in=["Owner","Manager"])
    for group in groups:
        group.permissions.add(permission)
    apps.get_model("core","Access").objects.filter(user__groups__in=groups).update(session_version=F("session_version")+1)


SQL = """
CREATE TRIGGER protect_sms_event BEFORE UPDATE OR DELETE ON core_smsevent
FOR EACH ROW EXECUTE FUNCTION kofad_reject_ledger_mutation();
CREATE FUNCTION kofad_protect_message_content() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Message history cannot be deleted';
    END IF;
    IF OLD.status <> 'draft' AND (
       NEW.body IS DISTINCT FROM OLD.body OR NEW.recipient IS DISTINCT FROM OLD.recipient
       OR NEW.party_id IS DISTINCT FROM OLD.party_id OR NEW.branch_id IS DISTINCT FROM OLD.branch_id
       OR NEW.channel IS DISTINCT FROM OLD.channel OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.sender IS DISTINCT FROM OLD.sender OR NEW.sandbox IS DISTINCT FROM OLD.sandbox) THEN
        RAISE EXCEPTION 'Queued message content and routing are immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_message_content BEFORE UPDATE OR DELETE ON core_message
FOR EACH ROW EXECUTE FUNCTION kofad_protect_message_content();
"""


class Migration(migrations.Migration):
    dependencies = [("core","0004_sms_and_admin_setup")]
    operations = [
        migrations.RunPython(seed,migrations.RunPython.noop),
        migrations.RunSQL(SQL,"DROP TRIGGER protect_sms_event ON core_smsevent; DROP TRIGGER protect_message_content ON core_message; DROP FUNCTION kofad_protect_message_content();"),
    ]
