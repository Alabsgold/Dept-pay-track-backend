from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from .models import Department, User


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name', 'faculty']


class UserRegistrationSerializer(serializers.ModelSerializer):
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

    def validate_password(self, value):
        # Enforce Django's built-in validators (length, common passwords, etc.)
        validate_password(value)
        return value

    def validate_matric_number(self, value):
        if not value:
            raise serializers.ValidationError("Matric number is required.")
        # Case-insensitive check + generic message to prevent account enumeration.
        if User.objects.filter(matric_number__iexact=value).exists():
            raise serializers.ValidationError("Unable to register with the provided details.")
        return value

    def validate_email(self, value):
        if not value:
            raise serializers.ValidationError("Email is required.")
        # Case-insensitive check + generic message to prevent account enumeration.
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Unable to register with the provided details.")
        return value

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
    username = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)

    def validate(self, attrs):
        username = attrs.get('username')
        password = attrs.get('password')

        user = authenticate(username=username, password=password)
        if not user:
            raise serializers.ValidationError("Invalid username or password.")
        if not user.is_active:
            raise serializers.ValidationError("User account is disabled.")

        attrs['user'] = user
        return attrs


class UserProfileSerializer(serializers.ModelSerializer):
    department = serializers.CharField(source='department.name', read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'matric_number',
            'department',
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
            'role',
        ]

    def validate_level(self, value):
        if value not in dict(User.LEVEL_CHOICES):
            raise serializers.ValidationError(f"Invalid level. Must be one of {[c[0] for c in User.LEVEL_CHOICES]}.")
        return value
