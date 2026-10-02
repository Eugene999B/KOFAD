import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_sms_templates_and_evidence"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="StockCount",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("scope", models.CharField(blank=True, max_length=80)),
                ("status", models.CharField(default="draft", max_length=12)),
                ("note", models.TextField(blank=True)),
                ("review_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.branch")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="StockCountLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("expected", models.PositiveIntegerField()),
                ("movement_id", models.PositiveBigIntegerField(default=0)),
                ("counted", models.PositiveIntegerField(blank=True, null=True)),
                ("reason", models.CharField(blank=True, max_length=240)),
                ("count", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lines", to="core.stockcount")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.product")),
            ],
            options={
                "ordering": ["product__name", "pk"],
                "constraints": [
                    models.UniqueConstraint(fields=("count", "product"), name="one_product_per_count"),
                    models.CheckConstraint(condition=models.Q(("counted__isnull", True), ("counted__lte", 2000000000), _connector="OR"), name="count_quantity_limit"),
                ],
            },
        ),
        migrations.RunSQL(
            sql="""
CREATE FUNCTION protect_stock_count() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Stock count evidence cannot be deleted';
    END IF;
    IF OLD.status NOT IN ('draft', 'submitted') THEN
        RAISE EXCEPTION 'Completed stock count evidence is immutable';
    END IF;
    IF ROW(NEW.id, NEW.branch_id, NEW.scope, NEW.created_by_id, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id, OLD.branch_id, OLD.scope, OLD.created_by_id, OLD.created_at) THEN
        RAISE EXCEPTION 'Stock count identity is immutable';
    END IF;
    IF OLD.status = 'submitted' AND (NEW.status NOT IN ('approved', 'rejected', 'cancelled')
       OR NEW.note IS DISTINCT FROM OLD.note OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at) THEN
        RAISE EXCEPTION 'Submitted count evidence is immutable';
    END IF;
    IF OLD.status = 'draft' AND NEW.status NOT IN ('draft', 'submitted', 'cancelled') THEN
        RAISE EXCEPTION 'Invalid count transition';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_stock_count BEFORE UPDATE OR DELETE ON core_stockcount
FOR EACH ROW EXECUTE FUNCTION protect_stock_count();

CREATE FUNCTION protect_stock_count_line() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Count lines cannot be deleted';
    END IF;
    SELECT status INTO parent_status FROM core_stockcount WHERE id = NEW.count_id FOR UPDATE;
    IF parent_status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'Only draft count quantities can change';
    END IF;
    IF TG_OP = 'UPDATE' AND
       ROW(NEW.id, NEW.count_id, NEW.product_id, NEW.expected, NEW.movement_id)
       IS DISTINCT FROM ROW(OLD.id, OLD.count_id, OLD.product_id, OLD.expected, OLD.movement_id) THEN
        RAISE EXCEPTION 'Count snapshots are immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_stock_count_line BEFORE INSERT OR UPDATE OR DELETE ON core_stockcountline
FOR EACH ROW EXECUTE FUNCTION protect_stock_count_line();
""",
            reverse_sql="""
DROP TRIGGER protect_stock_count_line ON core_stockcountline;
DROP FUNCTION protect_stock_count_line();
DROP TRIGGER protect_stock_count ON core_stockcount;
DROP FUNCTION protect_stock_count();
""",
        ),
    ]
