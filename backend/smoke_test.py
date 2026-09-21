"""Live-HTTP smoke test against a running dev server (read-mostly, no payment created unless gateway works).

Usage: python smoke_test.py http://127.0.0.1:8000
"""
import json
import sys
from urllib import request as rq
from urllib.error import HTTPError

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8000'
ok = True


def call(method, path, body=None, token=None, expect=200):
    global ok
    req = rq.Request(BASE + path, method=method)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Origin', 'http://localhost:5173')
    if token:
        req.add_header('Authorization', 'Token ' + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with rq.urlopen(req, data=data, timeout=15) as r:
            status, payload = r.status, r.read().decode()
    except HTTPError as e:
        status, payload = e.code, e.read().decode()
    parsed = None
    try:
        parsed = json.loads(payload)
    except ValueError:
        pass
    good = status == expect
    ok = ok and good
    print(('PASS' if good else 'FAIL'), method, path, '->', status,
          '(expected', str(expect) + ')', json.dumps(parsed) if parsed else payload[:120])
    return parsed, status


# 1. CORS preflight from the real Vite origin (OPTIONS, expect 200 + ACAO echo)
req = rq.Request(BASE + '/api/auth/me/', method='OPTIONS')
req.add_header('Origin', 'http://localhost:5173')
req.add_header('Access-Control-Request-Method', 'PATCH')
req.add_header('Access-Control-Request-Headers', 'authorization, content-type')
with rq.urlopen(req, timeout=15) as r:
    acao = r.headers.get('Access-Control-Allow-Origin')
    good = r.status == 200 and acao == 'http://localhost:5173'
    ok = ok and good
    print(('PASS' if good else 'FAIL'), 'CORS preflight 5173 ->', r.status, 'ACAO=' + str(acao))

# 2. departments (public)
call('GET', '/api/departments/', expect=200)

# 3. 401 shape without token
call('GET', '/api/auth/me/', expect=401)

# 4. login (rep seeded via manage.py shell)
_, st = call('POST', '/api/auth/login/', body={
    'username': 'smoke.rep', 'password': 'S7rong!Passw0rd'}, expect=200)
token = None
if st == 200:
    token = json.loads(json.dumps(_))['token']
    print('token acquired:', token[:12] + '...')

if not token:
    print('SKIP remaining: no token (seed smoke.rep first)')
    sys.exit(0 if not ok else 1)

auth = {'token': token}

# 5. me
call('GET', '/api/auth/me/', token=token, expect=200)

# 6. create contribution (rep)
c, st = call('POST', '/api/contributions/', body={
    'title': 'Smoke Dues', 'amount': '3500.00',
    'deadline': '2026-12-31T23:59:00Z', 'is_mandatory': True,
}, token=token, expect=201)
cid = (c or {}).get('id')

# 7. list + detail + summary
call('GET', '/api/contributions/', token=token, expect=200)
if cid:
    call('GET', f'/api/contributions/{cid}/', token=token, expect=200)
    call('GET', f'/api/contributions/{cid}/summary/', token=token, expect=200)
    # 8. edit (PATCH) and soft-close
    call('PATCH', f'/api/contributions/{cid}/', body={'amount': '4000.00'}, token=token, expect=200)
    call('DELETE', f'/api/contributions/{cid}/', token=token, expect=204)  # soft close
    # 9. closed fee must not be payable
    call('POST', '/api/payments/initiate/', body={'contribution_id': cid}, token=token, expect=404)

# 10. history / notifications / analytics
call('GET', '/api/payments/history/', token=token, expect=200)
call('GET', '/api/notifications/', token=token, expect=200)
call('GET', '/api/analytics/collection-stats/', token=token, expect=200)
# Unknown/out-of-department id -> not_found (existence is never leaked)
call('GET', '/api/analytics/outstanding-students/?contribution_id=999', token=token, expect=404)

print('\nRESULT:', 'ALL PASS' if ok else 'FAILURES ABOVE')
sys.exit(0 if ok else 1)
