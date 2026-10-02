import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_stock_counts"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="TransferReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField()),
                ("note", models.TextField(blank=True)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("resolution", models.CharField(blank=True, max_length=12)),
                ("resolution_note", models.TextField(blank=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("operation", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="receipt", to="core.operation")),
                ("recorded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RunSQL(
            sql="""
CREATE FUNCTION protect_transfer_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE dispatched integer; transfer_status text; transfer_kind text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Transfer receipt evidence cannot be deleted';
    END IF;
    SELECT quantity, status, kind INTO dispatched, transfer_status, transfer_kind
        FROM core_operation WHERE id = NEW.operation_id FOR UPDATE;
    IF TG_OP = 'INSERT' THEN
        IF transfer_kind IS DISTINCT FROM 'transfer' OR transfer_status IS DISTINCT FROM 'dispatched'
           OR NEW.quantity > dispatched OR NEW.resolution <> '' THEN
            RAISE EXCEPTION 'Invalid transfer receipt';
        END IF;
    ELSE
        IF ROW(NEW.id, NEW.operation_id, NEW.quantity, NEW.note, NEW.recorded_by_id, NEW.recorded_at)
           IS DISTINCT FROM ROW(OLD.id, OLD.operation_id, OLD.quantity, OLD.note, OLD.recorded_by_id, OLD.recorded_at)
           OR OLD.resolution <> '' OR NEW.resolution NOT IN ('arrived', 'loss')
           OR transfer_status IS DISTINCT FROM 'discrepancy'
           OR NEW.resolved_by_id IS NULL OR NEW.resolved_by_id = OLD.recorded_by_id OR NEW.resolved_at IS NULL THEN
            RAISE EXCEPTION 'Transfer receipt evidence is immutable or review is invalid';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_transfer_receipt BEFORE INSERT OR UPDATE OR DELETE ON core_transferreceipt
FOR EACH ROW EXECUTE FUNCTION protect_transfer_receipt();
""",
            reverse_sql="""
DROP TRIGGER protect_transfer_receipt ON core_transferreceipt;
DROP FUNCTION protect_transfer_receipt();
""",
        ),
    ]
