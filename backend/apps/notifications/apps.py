from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.notifications'
    label = 'notifications'
    verbose_name = 'Notifications'

    def ready(self):
        # Import the signal handlers so they are registered at startup.
        # Receiving-side only — we never modify users/contributions/payments.
        from . import signals  # noqa: F401
