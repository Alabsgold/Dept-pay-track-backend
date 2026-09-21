from django.urls import path

from .views import CollectionStatsView, OutstandingStudentsView

urlpatterns = [
    path(
        'collection-stats/',
        CollectionStatsView.as_view(),
        name='analytics-collection-stats',
    ),
    path(
        'outstanding-students/',
        OutstandingStudentsView.as_view(),
        name='analytics-outstanding-students',
    ),
]
