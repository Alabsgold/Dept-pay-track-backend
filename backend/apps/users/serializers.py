from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from .models import Department, User


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name', 'faculty']


class UserRegistrationSerializer(serializers.ModelSerializer):
    # These three are declared EXPLICITLY (not left to ModelSerializer
    # auto-generation) on purpose: auto-generated fields get DRF's
    # UniqueValidator, whose message leaks WHICH identifier already exists
    # ("User with this matric number already exists.") — a §8 account-
    # enumeration violation caught by QA (M-5). Duplicates are handled
    # generically, field-anonymously, in validate() below. The model's DB
    # unique constraints remain as the backstop (a concurrent exact race
    # fails safe with a 500, creating no account).
    username = serializers.CharField(
        max_length=150,
        validators=[UnicodeUsernameValidator()],
    )
    email = serializers.EmailField()
    matric_number = serializers.CharField(max_length=50)
    password = serializers.CharField(write_only=True, min_length=8)
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        source='department',
        write_only=True
    )
    department = serializers.CharField(source='department.name', read_only=True)
    level = serializers.ChoiceField(choices=User.LEVEL_CHOICES)

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'password',
            'matric_number',
            'department_id',
            'department',
            'level',
            'role',
        ]
        read_only_fields = ['id', 'role']

    def validate_username(self, value):
        if '@' in value:
            raise serializers.ValidationError("Username cannot contain '@'.")
        return value

    def validate_password(self, value):
        # Enforce Django's built-in validators (length, common passwords, etc.)
        validate_password(value)
        return value

    def validate_matric_number(self, value):
        if not value:
            raise serializers.ValidationError("Matric number is required.")
        return value

    def validate(self, attrs):
        # §8 anti-enumeration: ONE generic message for ANY duplicate
        # identifier (username, email or matric — case-insensitive). Raised
        # as a NON-FIELD error so core.exceptions cannot prefix the field
        # name (QA M-6: the prefix revealed which identifier collided).
        if (
            User.objects.filter(
                username__iexact=attrs.get('username', '')
            ).exists()
            or User.objects.filter(
                email__iexact=attrs.get('email', '')
            ).exists()
            or User.objects.filter(
                matric_number__iexact=attrs.get('matric_number', '')
            ).exists()
        ):
            raise serializers.ValidationError(
                "Unable to register with the provided details."
            )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User.objects.create_user(
            password=password,
            role=User.ROLE_STUDENT,
            **validated_data
        )
        return user

    def to_representation(self, instance):
        return {
            "id": instance.id,
            "username": instance.username,
            "role": instance.role,
            "department": instance.department.name if instance.department else None,
            "level": instance.level,
        }


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(required=False)
    email = serializers.EmailField(required=False)
    matric_number = serializers.CharField(required=False)
    password = serializers.CharField(required=True, write_only=True)

    def validate(self, attrs):
        password = attrs.get('password')
        email = attrs.get('email')
        matric_number = attrs.get('matric_number')
        username = attrs.get('username')

        if email:
            user = User.objects.filter(email__iexact=email).first()
        elif matric_number:
            user = User.objects.filter(
                matric_number__iexact=matric_number
            ).first()
        else:
            # Username lookup intentionally remains case-sensitive. Alternate
            # identifiers get their own case-insensitive resolution above.
            user = authenticate(username=username, password=password)

        if email or matric_number:
            if user is None or not user.check_password(password):
                user = None
        if not user or not user.is_active:
            raise serializers.ValidationError("Invalid username or password.")

        attrs['user'] = user
        return attrs


class UserProfileSerializer(serializers.ModelSerializer):
    department = serializers.CharField(source='department.name', read_only=True)
    full_name = serializers.SerializerMethodField(read_only=True)
    # Imported rosters may have no department on file, so the student must be
    # able to supply it. It can be filled in ONCE, then it is locked: letting
    # a student change department freely would also let them switch out of a
    # department that is collecting dues from them (an escape route).
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        source='department',
        write_only=True,
        required=False,
    )

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'matric_number',
            'department_id',
            'department',
            'full_name',
            'level',
            'role',
            'phone_number',
        ]
        read_only_fields = [
            'id',
            'username',
            'email',
            'matric_number',
            'department',
            'full_name',
            'role',
        ]

    def get_full_name(self, obj):
        name_parts = [
            part.strip()
            for part in (obj.first_name, obj.last_name)
            if part and part.strip()
        ]
        return ' '.join(name_parts) or obj.username

    def validate_level(self, value):
        # ChoiceField already rejects invalid choices; this also rejects blank
        # strings (the generated field has allow_blank=True) so level can't be
        # wiped to "".
        if value not in dict(User.LEVEL_CHOICES):
            raise serializers.ValidationError(f"Invalid level. Must be one of {[c[0] for c in User.LEVEL_CHOICES]}.")
        return value

    def validate_department_id(self, value):
        # Fill-once: allowed only while the field is empty (or unchanged).
        # Changing it afterwards is an admin action, not self-service.
        current = self.instance.department_id if self.instance else None
        if current and current != value.id:
            raise serializers.ValidationError(
                "Department is already set. Ask an admin to change it."
            )
        return value
