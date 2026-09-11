from django.urls import path

from .views import (
    PaymentListView,
    InitializePaymentView,
    VerifyPaymentView,
    PaymentDetailView,
    PaystackWebhookView,
)


urlpatterns = [
    path('history/', PaymentListView.as_view(), name='payment-history'),
    path('initiate/', InitializePaymentView.as_view(), name='payment-initiate'),
    path('verify/<str:reference>/', VerifyPaymentView.as_view(), name='payment-verify'),
    path('<int:pk>/receipt/', PaymentDetailView.as_view(), name='payment-receipt'),
    path('webhook/', PaystackWebhookView.as_view(), name='paystack-webhook'),
]