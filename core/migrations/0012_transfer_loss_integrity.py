from django.db import migrations


FORWARD = r"""
CREATE OR REPLACE FUNCTION protect_transfer_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    dispatched integer;
    transfer_status text;
    transfer_kind text;
    destination bigint;
    loss_kind text;
    loss_branch bigint;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Transfer receipt evidence cannot be deleted';
    END IF;

    SELECT quantity, status, kind, destination_id
      INTO dispatched, transfer_status, transfer_kind, destination
      FROM core_operation
     WHERE id = NEW.operation_id
     FOR UPDATE;

    IF TG_OP = 'INSERT' THEN
        IF transfer_kind IS DISTINCT FROM 'transfer'
           OR transfer_status IS DISTINCT FROM 'dispatched'
           OR NEW.quantity > dispatched
           OR NEW.resolution <> ''
           OR NEW.unit_cost IS NULL
           OR NEW.loss_document_id IS NOT NULL THEN
            RAISE EXCEPTION 'Invalid transfer receipt';
        END IF;
    ELSE
        IF ROW(
               NEW.id, NEW.operation_id, NEW.quantity, NEW.unit_cost, NEW.note,
               NEW.recorded_by_id, NEW.recorded_at
           ) IS DISTINCT FROM ROW(
               OLD.id, OLD.operation_id, OLD.quantity, OLD.unit_cost, OLD.note,
               OLD.recorded_by_id, OLD.recorded_at
           )
           OR OLD.resolution <> ''
           OR OLD.loss_document_id IS NOT NULL
           OR NEW.resolution NOT IN ('arrived', 'loss')
           OR transfer_status IS DISTINCT FROM 'discrepancy'
           OR NEW.resolved_by_id IS NULL
           OR NEW.resolved_by_id = OLD.recorded_by_id
           OR NEW.resolved_at IS NULL
           OR char_length(trim(NEW.resolution_note)) < 10 THEN
            RAISE EXCEPTION 'Transfer receipt evidence is immutable or review is invalid';
        END IF;

        IF NEW.resolution = 'arrived' AND NEW.loss_document_id IS NOT NULL THEN
            RAISE EXCEPTION 'An arrived transfer remainder cannot have a loss document';
        END IF;

        IF NEW.resolution = 'loss' THEN
            IF NEW.loss_document_id IS NULL THEN
                RAISE EXCEPTION 'A confirmed transfer loss requires accounting evidence';
            END IF;
            SELECT kind, branch_id INTO loss_kind, loss_branch
              FROM core_document
             WHERE id = NEW.loss_document_id;
            IF loss_kind IS DISTINCT FROM 'inventory_writeoff'
               OR loss_branch IS DISTINCT FROM destination THEN
                RAISE EXCEPTION 'Transfer loss evidence must be an inventory write-off at the destination';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""


REVERSE = r"""
CREATE OR REPLACE FUNCTION protect_transfer_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
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
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0011_supplier_returns_quarantine")]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
    ]
