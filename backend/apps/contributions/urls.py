from django.urls import path

from .views import (
    ContributionDetailView,
    ContributionListCreateView,
    ContributionPaymentsView,
    ContributionSummaryView,
)

urlpatterns = [
    path('', ContributionListCreateView.as_view(), name='contribution-list-create'),
    path('<int:pk>/', ContributionDetailView.as_view(), name='contribution-detail'),
    path('<int:pk>/summary/', ContributionSummaryView.as_view(), name='contribution-summary'),
    path('<int:pk>/payments/', ContributionPaymentsView.as_view(), name='contribution-payments'),
]