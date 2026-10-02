from django.contrib.auth.models import User, Group
from django.db.models import F
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver
from .models import Access


@receiver(post_save, sender=User)
def user_saved(sender, instance, created, **kwargs):
    if created:
        Access.objects.get_or_create(user=instance)
    elif getattr(instance, "_security_changed", False):
        Access.objects.filter(user=instance).update(session_version=F("session_version") + 1)


@receiver(pre_save, sender=User)
def security_changed(sender, instance, **kwargs):
    if instance.pk:
        old = User.objects.filter(pk=instance.pk).values("password", "is_active", "is_staff", "is_superuser").first()
        instance._security_changed = bool(old and any(old[k] != getattr(instance, k) for k in old))



@receiver(m2m_changed, sender=User.groups.through)
@receiver(m2m_changed, sender=User.user_permissions.through)
@receiver(m2m_changed, sender=Group.permissions.through)
@receiver(m2m_changed, sender=Access.branches.through)
def permissions_changed(sender, action, **kwargs):
    if action in ("post_add", "post_remove", "post_clear"):
        # Permission changes are rare; revoke every session to avoid stale privileges,
        # including reverse-side changes and group clears.
        Access.objects.all().update(session_version=F("session_version") + 1)
