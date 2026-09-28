from django.urls import path

from .views import (
    PaymentListView,
    InitializePaymentView,
    VerifyPaymentView,
    PaymentDetailView,
    PaystackWebhookView,
    UnverifiedPaymentsView,
    DepartmentBankAccountView,
    BMONIWebhookView,
)


urlpatterns = [
    path('history/', PaymentListView.as_view(), name='payment-history'),
    path('initiate/', InitializePaymentView.as_view(), name='payment-initiate'),
    path('verify/<str:reference>/', VerifyPaymentView.as_view(), name='payment-verify'),
    path('<int:pk>/receipt/', PaymentDetailView.as_view(), name='payment-receipt'),
    path('webhook/', PaystackWebhookView.as_view(), name='paystack-webhook'),
    path('unverified/', UnverifiedPaymentsView.as_view(), name='payment-unverified'),

    # BMONI Embedded: the department's own NGN account, plus BMONI's callbacks.
    # Declared after the Paystack routes so the literal prefixes above can never
    # be shadowed by a future `<int:pk>`-style pattern.
    path(
        'departments/<int:pk>/bank-account/',
        DepartmentBankAccountView.as_view(),
        name='department-bank-account',
    ),
    path('bmoni/webhook/', BMONIWebhookView.as_view(), name='bmoni-webhook'),
]