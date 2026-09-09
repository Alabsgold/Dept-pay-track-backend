from django.urls import path

from .views import (
    PaymentListView,
    InitializePaymentView,
    VerifyPaymentView,
    PaymentDetailView,
    AdminPaymentListView,
    PaystackWebhookView,
)


urlpatterns = [
    path('', PaymentListView.as_view(), name='payment-list'),
    path('initialize/', InitializePaymentView.as_view(), name='payment-initialize'),
    path('verify/<str:reference>/', VerifyPaymentView.as_view(), name='payment-verify'),
    path('<int:pk>/', PaymentDetailView.as_view(), name='payment-detail'),
    path('admin/', AdminPaymentListView.as_view(), name='admin-payment-list'),
    path('webhook/', PaystackWebhookView.as_view(), name='paystack-webhook'),
]