"""Editable online debt policy, separate from existing manual-debt reminders."""
from decimal import Decimal

from django import forms

from .models import OnlineDebtSettings


class OnlineDebtSettingsForm(forms.ModelForm):
    class Meta:
        model = OnlineDebtSettings
        fields = [
            "plan_creation_enabled", "pause_auto_collection",
            "allow_mtn", "allow_telecel",
            "allow_once", "allow_daily", "allow_weekly", "allow_monthly",
            "allow_quarterly", "allow_yearly", "default_frequency",
            "min_open_balance", "max_open_balance",
            "min_installment", "max_installment",
            "min_first_due_days", "max_first_due_days",
            "auto_collection_grace_days", "rollover_unpaid",
            "skip_weekends", "collection_from_hour", "collection_until_hour",
            "daily_collection_limit", "daily_attempt_limit",
            "pending_review_hours", "unresolved_poll_hours",
        ]
        labels = {
            "plan_creation_enabled": "Staff may offer new Online Debt agreements",
            "pause_auto_collection": "Emergency pause all automatic debits in this location",
            "allow_mtn": "MTN Mobile Money", "allow_telecel": "Telecel Cash",
            "allow_once": "One-time after grace",
            "allow_daily": "Daily", "allow_weekly": "Weekly", "allow_monthly": "Monthly",
            "allow_quarterly": "Quarterly", "allow_yearly": "Yearly",
            "default_frequency": "Default payment schedule",
            "min_open_balance": "Minimum eligible debt (GHS)",
            "max_open_balance": "Maximum eligible debt (GHS)",
            "min_installment": "Minimum installment (GHS)",
            "max_installment": "Maximum installment (GHS)",
            "min_first_due_days": "Earliest first debit after agreement (days)",
            "max_first_due_days": "Latest first debit after agreement (days)",
            "auto_collection_grace_days": "Extra days after each due date before auto debit",
            "rollover_unpaid": "Carry unpaid installments into later agreed periods",
            "skip_weekends": "Do not automatically debit on Saturdays/Sundays",
            "collection_from_hour": "Earliest auto-debit hour (0–23)",
            "collection_until_hour": "Latest auto-debit hour, exclusive (1–23)",
            "daily_collection_limit": "Daily debit submission ceiling per location (GHS)",
            "daily_attempt_limit": "Maximum automated debit requests per location per day",
            "pending_review_hours": "Escalate unresolved debits after (hours)",
            "unresolved_poll_hours": "Recheck unresolved provider status every (hours)",
        }
        help_texts = {
            "plan_creation_enabled": "Disabled by default. Staff may only draft an agreement; Hubtel authorization remains separate.",
            "pause_auto_collection": "On by default. This blocks outgoing direct-debit requests even when the Railway charge switch is active.",
            "max_first_due_days": "A new agreement may not schedule its first payment more than 366 days ahead.",
            "auto_collection_grace_days": "Affects new scheduled collection attempts; it never authorizes collection beyond the customer's signed limit.",
            "rollover_unpaid": "Captured on each new customer's agreement; changing this setting cannot rewrite an already accepted contract.",
            "daily_collection_limit": "Caps attempted debits, including failed or unconfirmed submissions. It cannot be used to infer wallet balance.",
            "daily_attempt_limit": "Separate from the permanent safety rule of one automatic attempt per agreed installment period.",
            "pending_review_hours": "After this period, KOFAD suspends new debits while still checking the original transaction. It never assumes a failed payment.",
            "unresolved_poll_hours": "Used only for old pending or uncertain payments. No repeat charge is generated.",
        }

    def clean(self):
        data = super().clean()
        lower = data.get("min_open_balance")
        upper = data.get("max_open_balance")
        min_installment = data.get("min_installment")
        max_installment = data.get("max_installment")
        if lower is not None and (lower <= 0 or lower > Decimal("10000000")):
            self.add_error("min_open_balance", "Enter a positive balance no greater than GHS 10 million.")
        if upper is not None and (upper <= 0 or upper > Decimal("10000000")):
            self.add_error("max_open_balance", "Enter a positive cap no greater than GHS 10 million.")
        if lower is not None and upper is not None and lower > upper:
            self.add_error("max_open_balance", "Maximum eligible debt must be at least the minimum.")
        if min_installment is not None and min_installment < Decimal("1.00"):
            self.add_error("min_installment", "Minimum automatic installment must be at least GHS 1.")
        if min_installment is not None and max_installment is not None and min_installment > max_installment:
            self.add_error("max_installment", "Maximum installment must not be below its minimum.")
        if upper is not None and max_installment is not None and max_installment > upper:
            self.add_error("max_installment", "An installment limit cannot exceed the total debt cap.")
        if max_installment is not None and max_installment <= 0:
            self.add_error("max_installment", "Enter a positive installment ceiling.")

        first = data.get("min_first_due_days")
        last = data.get("max_first_due_days")
        if first is not None and first > 366:
            self.add_error("min_first_due_days", "First due dates must be no more than one year away.")
        if last is not None and last > 366:
            self.add_error("max_first_due_days", "First due dates must be no more than one year away.")
        if first is not None and last is not None and last < first:
            self.add_error("max_first_due_days", "Latest date cannot be earlier than the minimum.")
        grace = data.get("auto_collection_grace_days")
        if grace is not None and grace > 30:
            self.add_error("auto_collection_grace_days", "Extra debit grace cannot exceed 30 days.")
        hour_from = data.get("collection_from_hour")
        hour_to = data.get("collection_until_hour")
        if hour_from is not None and hour_from > 22:
            self.add_error("collection_from_hour", "Use an hour between 0 and 22.")
        if hour_to is not None and hour_to > 23:
            self.add_error("collection_until_hour", "Use an hour between 1 and 23.")
        if hour_from is not None and hour_to is not None and hour_to <= hour_from:
            self.add_error("collection_until_hour", "End hour must be later than start hour.")
        daily = data.get("daily_collection_limit")
        if daily is not None and (daily < Decimal("1.00") or daily > Decimal("10000000")):
            self.add_error("daily_collection_limit", "Enter GHS 1 to GHS 10 million.")
        requests = data.get("daily_attempt_limit")
        if requests is not None and not 1 <= requests <= 500:
            self.add_error("daily_attempt_limit", "Choose 1 to 500 submissions per location per day.")
        pending_hours = data.get("pending_review_hours")
        if pending_hours is not None and not 6 <= pending_hours <= 168:
            self.add_error("pending_review_hours", "Choose 6 to 168 hours.")
        poll_hours = data.get("unresolved_poll_hours")
        if poll_hours is not None and not 1 <= poll_hours <= 24:
            self.add_error("unresolved_poll_hours", "Choose 1 to 24 hours.")
        if data.get("plan_creation_enabled"):
            if not (data.get("allow_mtn") or data.get("allow_telecel")):
                self.add_error("allow_mtn", "Enable at least one wallet network.")
            frequencies = ["once", "daily", "weekly", "monthly", "quarterly", "yearly"]
            if not any(data.get("allow_" + name) for name in frequencies):
                self.add_error("allow_weekly", "Enable at least one repayment schedule.")
            if not data.get("allow_" + str(data.get("default_frequency"))):
                self.add_error("default_frequency", "Choose an enabled default schedule.")
        return data
