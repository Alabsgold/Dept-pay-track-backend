import csv
import io

from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.utils.crypto import get_random_string
from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.authtoken.models import Token

from .models import ClaimBatch, Department, PasswordResetCode, User
from .permissions import IsAdminUser, IsClassRepOrAdmin
from .serializers import (
    DepartmentSerializer,
    UserRegistrationSerializer,
    LoginSerializer,
    UserProfileSerializer,
)


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        # Rotate the token on every login so a leaked token cannot outlive it.
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        return Response(
            {
                "token": token.key,
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "role": user.role,
                }
            },
            status=status.HTTP_200_OK
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            request.user.auth_token.delete()
        except (AttributeError, Token.DoesNotExist):
            pass
        return Response(
            {"message": "Successfully logged out."},
            status=status.HTTP_200_OK
        )


class CurrentUserView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    def get_object(self):
        return self.request.user


class DepartmentListView(generics.ListAPIView):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [AllowAny]


def _new_code(length=10):
    # Unambiguous alphabet: no O/0, I/1, L/1 confusion when read aloud.
    return get_random_string(
        length, allowed_chars='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    )


class ImportStudentsView(APIView):
    """
    POST /api/auth/import/ — admin-only bulk registration from a CSV.

    Header row required: first_name, last_name, matric_number, level,
    department (+ optional email). Existing matric numbers are reported,
    never duplicated. Created accounts are role=student with NO password —
    students claim their own account via /api/auth/claim/ (matric + first
    name + batch code), so nothing is distributed per student even at 2,000+
    rows. dry_run=true validates the file without writing.
    """

    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get('file')
        if upload is None:
            return Response(
                {'error': 'bad_request', 'message': 'file (CSV) is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        dry_run = str(request.data.get('dry_run', '')).lower() in ('1', 'true', 'yes')

        raw = upload.read()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1252', errors='replace')  # Excel exports

        reader = csv.DictReader(io.StringIO(text))
        # Only name + matric number are guaranteed to be in a department's
        # records (owner decision): everything else may be absent and is
        # filled in later by the student. Required COLUMNS are therefore just
        # these two — a file without a department/level column is still valid.
        required = {'first_name', 'matric_number'}
        headers = {(h or '').strip().lower() for h in (reader.fieldnames or []) if h}
        missing = required - headers
        if missing:
            return Response(
                {
                    'error': 'bad_request',
                    'message': 'Missing CSV columns: %s.' % ', '.join(sorted(missing)),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = {
            'created': 0, 'skipped_existing': 0,
            'errors': [], 'errors_total': 0, 'dry_run': dry_run,
        }
        dept_cache = {}
        seen_matrics = set()
        seen_emails = set()
        pending_rows = []

        for row_number, raw_row in enumerate(reader, start=2):
            row = {
                (k or '').strip().lower(): (v or '').strip()
                for k, v in raw_row.items()
                if k is not None
            }
            matric = row.get('matric_number', '')
            errors = []
            if not matric:
                errors.append('matric_number is required')
            if not row.get('first_name'):
                errors.append('first_name is required')
            # department/level are optional: a partial roster still imports,
            # and the student completes their profile on first login. A name
            # that IS given but matches no department is still an error (typo
            # guard) — see below.
            level = row.get('level', '')
            if level and level not in dict(User.LEVEL_CHOICES):
                errors.append('invalid level: %s' % level)
            email = row.get('email', '') or None

            department = None
            dept_name = row.get('department', '')
            if dept_name:
                if dept_name not in dept_cache:
                    dept_cache[dept_name] = Department.objects.filter(
                        name__iexact=dept_name
                    ).first()
                department = dept_cache[dept_name]
                if department is None:
                    errors.append('unknown department: %s' % dept_name)

            if matric:
                key = matric.casefold()
                if key in seen_matrics:
                    errors.append('duplicate matric_number inside this file')
                seen_matrics.add(key)
                if User.objects.filter(matric_number__iexact=matric).exists():
                    errors.append('a user with this matric_number already exists')
                    results['skipped_existing'] += 1
                elif User.objects.filter(username__iexact=matric).exists():
                    errors.append('that matric_number is already taken as a username')
            if email:
                ekey = email.casefold()
                if ekey in seen_emails:
                    errors.append('duplicate email inside this file')
                seen_emails.add(ekey)
                if User.objects.filter(email__iexact=email).exists():
                    errors.append('a user with this email already exists')

            if errors:
                results['errors_total'] += 1
                if len(results['errors']) < 50:
                    results['errors'].append(
                        {'row': row_number, 'matric_number': matric, 'errors': errors}
                    )
                continue

            pending_rows.append({
                'first_name': row.get('first_name', ''),
                'last_name': row.get('last_name', ''),
                'matric_number': matric,
                'level': level or None,
                'department': department,
                'email': email,
            })

        if dry_run:
            results['would_create'] = len(pending_rows)
            return Response(results, status=status.HTTP_200_OK)

        for data in pending_rows:
            # password=None -> unusable: the account is inert until the
            # student claims it (matric + first name + batch code).
            User.objects.create_user(
                username=data['matric_number'],
                email=data['email'],
                password=None,
                first_name=data['first_name'],
                last_name=data['last_name'],
                matric_number=data['matric_number'],
                department=data['department'],
                level=data['level'],
                role=User.ROLE_STUDENT,
            )
            results['created'] += 1

        batch = ClaimBatch.objects.create(code=_new_code(10), created_by=request.user)
        results['claim_batch_code'] = batch.code
        return Response(results, status=status.HTTP_200_OK)


class ClaimAccountView(APIView):
    """
    POST /api/auth/claim/ — a student sets the first password on their
    imported account (matric + first name + shared batch code).

    Every pre-password failure returns the SAME generic error, so this
    endpoint can never be used to probe which matric numbers exist.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request):
        matric = (request.data.get('matric_number') or '').strip()
        first_name = (request.data.get('first_name') or '').strip()
        batch_code = (request.data.get('batch_code') or '').strip()
        password = request.data.get('password') or ''

        generic_fail = {
            'error': 'claim_failed',
            'message': 'Claim failed. Check your matric number, first name and claim code.',
        }
        if not (matric and first_name and batch_code and password):
            return Response(generic_fail, status=status.HTTP_400_BAD_REQUEST)

        batch = ClaimBatch.objects.filter(
            code__iexact=batch_code, is_active=True
        ).first()
        user = User.objects.filter(matric_number__iexact=matric).first()
        name_ok = (
            user is not None
            and user.first_name.strip().casefold() == first_name.casefold()
        )
        if batch is None or user is None or not name_ok or user.has_usable_password():
            return Response(generic_fail, status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_password(password, user=user)
        except Exception as exc:
            return Response(
                {'error': 'weak_password', 'message': ' '.join(exc.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = (request.data.get('email') or '').strip() or None
        if email and User.objects.filter(
            email__iexact=email
        ).exclude(pk=user.pk).exists():
            return Response(
                {'error': 'email_taken', 'message': 'That email is already in use.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if email:
            # Online payments need a reachable address; imported students may
            # not have had one on file.
            user.email = email
        user.set_password(password)
        user.save()

        return Response(
            {
                'message': 'Account claimed successfully. You can now log in.',
                'username': user.username,
            },
            status=status.HTTP_200_OK,
        )


class IssueResetCodeView(APIView):
    """
    POST /api/auth/reset-code/ — rep/admin: hand a one-time reset code to a
    student in person (Option A, no email dependency). Codes expire in 30
    minutes, are single-use, and the student sets the new password
    themselves — the rep never sees it.
    """

    permission_classes = [IsClassRepOrAdmin]

    def post(self, request):
        matric = (request.data.get('matric_number') or '').strip()
        user = User.objects.filter(matric_number__iexact=matric).first() if matric else None
        if user is None:
            return Response(
                {'error': 'not_found', 'message': 'No user with that matric number.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        # Reps are scoped to their own department; admins can reach anyone.
        if (
            request.user.role == User.ROLE_CLASS_REP
            and user.department_id != request.user.department_id
        ):
            return Response(
                {'error': 'forbidden', 'message': 'That student is not in your department.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not user.has_usable_password():
            return Response(
                {
                    'error': 'bad_request',
                    'message': 'That account has no password yet — the student should claim it first.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only one live code per student: a new code retires the old one.
        PasswordResetCode.objects.filter(
            user=user, used_at__isnull=True
        ).update(used_at=timezone.now())
        code = PasswordResetCode.objects.create(
            user=user, code=_new_code(8), created_by=request.user
        )
        return Response({
            'matric_number': user.matric_number,
            'code': code.code,
            'expires_in_minutes': PasswordResetCode.RESET_TTL_MINUTES,
        })


class ResetPasswordView(APIView):
    """POST /api/auth/reset-password/ — student sets a new password using the
    one-time code they were given. Public + throttled like login."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    def post(self, request):
        matric = (request.data.get('matric_number') or '').strip()
        code_value = (request.data.get('code') or '').strip()
        password = request.data.get('new_password') or ''
        if not (matric and code_value and password):
            return Response(
                {
                    'error': 'bad_request',
                    'message': 'matric_number, code and new_password are required.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        reset_code = (
            PasswordResetCode.objects
            .select_related('user')
            .filter(code__iexact=code_value, used_at__isnull=True)
            .first()
        )
        if (
            reset_code is None
            or (reset_code.user.matric_number or '').casefold() != matric.casefold()
        ):
            return Response(
                {'error': 'invalid_code', 'message': 'Invalid or expired reset code.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if reset_code.is_expired():
            return Response(
                {
                    'error': 'code_expired',
                    'message': 'That reset code has expired. Ask for a new one.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(password, user=reset_code.user)
        except Exception as exc:
            return Response(
                {'error': 'weak_password', 'message': ' '.join(exc.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reset_code.user.set_password(password)
        reset_code.user.save()
        reset_code.used_at = timezone.now()
        reset_code.save(update_fields=['used_at'])
        # Retire any other live codes for this student.
        PasswordResetCode.objects.filter(
            user=reset_code.user, used_at__isnull=True
        ).update(used_at=timezone.now())
        return Response({'message': 'Password updated. You can now log in.'})


class ClaimBatchListView(APIView):
    """GET /api/auth/claim-batches/ — admin view of the shared claim codes."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        batches = ClaimBatch.objects.all()[:50]
        return Response([
            {
                'id': b.id,
                'code': b.code,
                'is_active': b.is_active,
                'created_at': b.created_at,
            }
            for b in batches
        ])


class DeactivateClaimBatchView(APIView):
    """POST /api/auth/claim-batches/{id}/deactivate/ — retire a claim code
    (e.g. once the claim window closes) so it can no longer be used."""

    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        batch = ClaimBatch.objects.filter(pk=pk).first()
        if batch is None:
            return Response(
                {'error': 'not_found', 'message': 'No such claim batch.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        batch.is_active = False
        batch.save(update_fields=['is_active'])
        return Response({'id': batch.id, 'code': batch.code, 'is_active': batch.is_active})


class SetUserRoleView(APIView):
    """
    POST /api/auth/users/{id}/set-role/ — admin-only. Reps are normal
    students until an admin promotes them (owner decision): promotion grants
    the mark-paid power, so it never happens through self-service.
    """

    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        role = request.data.get('role')
        if role not in (User.ROLE_STUDENT, User.ROLE_CLASS_REP):
            return Response(
                {
                    'error': 'bad_request',
                    'message': "role must be 'student' or 'class_rep'.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = User.objects.filter(pk=pk).first()
        if user is None:
            return Response(
                {'error': 'not_found', 'message': 'No such user.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if user.id == request.user.id:
            return Response(
                {'error': 'forbidden', 'message': 'You cannot change your own role.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        user.role = role
        user.save(update_fields=['role'])
        return Response(
            {'user': {'id': user.id, 'username': user.username, 'role': user.role}}
        )
