from django.urls import path

from .views import MarkNotificationReadView, NotificationListView

urlpatterns = [
    path('', NotificationListView.as_view(), name='notification-list'),
    path(
        '<int:pk>/read/',
        MarkNotificationReadView.as_view(),
        name='notification-read',
    ),
]
