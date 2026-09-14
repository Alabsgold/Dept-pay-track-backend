"""Simulate a signed Paystack webhook against the local server — no ngrok
needed, because the signature is computed with the same secret key Paystack
uses. This lets you test the full webhook flow (success, amount mismatch,
duplicate delivery) from your own machine.

Usage (from backend/, with venv active and runserver running):
    python simulate_webhook.py <payment_reference>            # correct amount
    python simulate_webhook.py <payment_reference> 35000      # wrong amount
Run the same command twice to prove idempotency.
"""
import hashlib
import hmac
import json
import sys

import requests
from decouple import config


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    reference = sys.argv[1]
    payload_obj = {
        'event': 'charge.success',
        'data': {'reference': reference},
    }
    if len(sys.argv) > 2:
        payload_obj['data']['amount'] = int(sys.argv[2])

    body = json.dumps(payload_obj).encode()
    signature = hmac.new(
        config('PAYSTACK_SECRET_KEY').encode(),
        body,
        hashlib.sha512,
    ).hexdigest()

    response = requests.post(
        'http://localhost:8000/api/payments/webhook/',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'x-paystack-signature': signature,
        },
        timeout=10,
    )
    print(response.status_code, response.json())


if __name__ == '__main__':
    main()
