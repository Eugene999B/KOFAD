from django.db import migrations

SQL = """
CREATE FUNCTION kofad_reject_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'KOFAD ledger records are immutable; post a linked correction instead';
END;
$$;
CREATE FUNCTION kofad_protect_document() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.finalized THEN
        RAISE EXCEPTION 'Posted KOFAD documents are immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION kofad_protect_closing() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'KOFAD closings cannot be deleted';
    END IF;
    IF (to_jsonb(NEW) - 'verified_by_id') IS DISTINCT FROM (to_jsonb(OLD) - 'verified_by_id')
       OR OLD.verified_by_id IS NOT NULL
       OR NEW.verified_by_id IS NULL
       OR NEW.verified_by_id = OLD.submitted_by_id THEN
        RAISE EXCEPTION 'Only independent first verification may update a closing';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_document BEFORE UPDATE OR DELETE ON core_document
FOR EACH ROW EXECUTE FUNCTION kofad_protect_document();
CREATE TRIGGER protect_closing BEFORE UPDATE OR DELETE ON core_closing
FOR EACH ROW EXECUTE FUNCTION kofad_protect_closing();
"""
TABLES = ["core_line", "core_payment", "core_allocation", "core_movement", "core_audit"]
for table in TABLES:
    SQL += f"CREATE TRIGGER protect_ledger BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION kofad_reject_ledger_mutation();\n"
REVERSE = "DROP TRIGGER protect_document ON core_document; DROP TRIGGER protect_closing ON core_closing;"
for table in TABLES:
    REVERSE += f"DROP TRIGGER protect_ledger ON {table};"
REVERSE += "DROP FUNCTION kofad_reject_ledger_mutation(); DROP FUNCTION kofad_protect_document(); DROP FUNCTION kofad_protect_closing();"


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
