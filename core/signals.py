from django.contrib.auth.models import Group, User
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
def user_permissions_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if isinstance(instance, User):
        Access.objects.filter(user=instance).update(session_version=F("session_version") + 1)
    elif reverse and pk_set:
        Access.objects.filter(user_id__in=pk_set).update(session_version=F("session_version") + 1)


@receiver(m2m_changed, sender=Group.permissions.through)
def group_permissions_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if isinstance(instance, Group):
        Access.objects.filter(user__groups=instance).update(session_version=F("session_version") + 1)
    elif reverse and pk_set:
        Access.objects.filter(user__groups__pk__in=pk_set).distinct().update(session_version=F("session_version") + 1)


@receiver(m2m_changed, sender=Access.branches.through)
def location_access_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if isinstance(instance, Access):
        Access.objects.filter(pk=instance.pk).update(session_version=F("session_version") + 1)
    elif reverse and pk_set:
        Access.objects.filter(pk__in=pk_set).update(session_version=F("session_version") + 1)
