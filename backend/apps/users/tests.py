from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from rest_framework.authtoken.models import Token
from apps.users.models import Department, User


class UsersAuthTests(APITestCase):
    def setUp(self):
        self.department = Department.objects.create(
            name="Computer Science",
            faculty="Physical Sciences"
        )
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@school.edu.ng",
            password="securepassword123",
            matric_number="CSC/2021/001",
            department=self.department,
            level="400",
            role=User.ROLE_STUDENT,
            phone_number="08012345678"
        )
        self.token = Token.objects.create(user=self.user)

    def test_list_departments(self):
        response = self.client.get(reverse('departments-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], "Computer Science")
        self.assertEqual(response.data[0]['faculty'], "Physical Sciences")

    def test_register_student_success(self):
        payload = {
            "username": "jdoe",
            "email": "jdoe@school.edu.ng",
            "password": "S7rong!Passw0rd",
            "matric_number": "CSC/2021/045",
            "department_id": self.department.id,
            "level": "400"
        }
        response = self.client.post(reverse('auth-register'), payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['username'], "jdoe")
        self.assertEqual(response.data['role'], "student")
        self.assertEqual(response.data['department'], "Computer Science")
        self.assertEqual(response.data['level'], "400")
        self.assertIn('id', response.data)

        # Check DB
        new_user = User.objects.get(username="jdoe")
        self.assertEqual(new_user.role, "student")
        self.assertEqual(new_user.matric_number, "CSC/2021/045")

    def test_register_duplicate_matric_number(self):
        payload = {
            "username": "jdoe2",
            "email": "jdoe2@school.edu.ng",
            "password": "S7rong!Passw0rd",
            "matric_number": "CSC/2021/001",  # Duplicate of self.user
            "department_id": self.department.id,
            "level": "400"
        }
        response = self.client.post(reverse('auth-register'), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], "bad_request")

    def test_register_duplicate_matric_number_case_insensitive(self):
        payload = {
            "username": "lowercase",
            "email": "lowercase@school.edu.ng",
            "password": "S7rong!Passw0rd",
            "matric_number": "csc/2021/001",  # lowercase duplicate of self.user
            "department_id": self.department.id,
            "level": "400"
        }
        response = self.client.post(reverse('auth-register'), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], "bad_request")

    def test_register_weak_password_rejected(self):
        payload = {
            "username": "weakpw",
            "email": "weakpw@school.edu.ng",
            "password": "password123",  # Common password — must be rejected
            "matric_number": "CSC/2021/099",
            "department_id": self.department.id,
            "level": "400"
        }
        response = self.client.post(reverse('auth-register'), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], "bad_request")

    def test_login_success(self):
        payload = {
            "username": "testuser",
            "password": "securepassword123"
        }
        response = self.client.post(reverse('auth-login'), payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("token", response.data)
        self.assertEqual(response.data["user"]["username"], "testuser")
        self.assertEqual(response.data["user"]["role"], "student")
        self.assertEqual(response.data["user"]["id"], self.user.id)

    def test_login_rotates_token(self):
        payload = {"username": "testuser", "password": "securepassword123"}
        first = self.client.post(reverse('auth-login'), payload).data["token"]
        second_response = self.client.post(reverse('auth-login'), payload)
        self.assertNotEqual(second_response.data["token"], first)
        self.assertFalse(Token.objects.filter(key=first).exists())
        self.assertTrue(Token.objects.filter(key=second_response.data["token"]).exists())

    def test_login_invalid_credentials(self):
        payload = {
            "username": "testuser",
            "password": "wrongpassword"
        }
        response = self.client.post(reverse('auth-login'), payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], "bad_request")

    def test_current_user_me_unauthorized(self):
        response = self.client.get(reverse('auth-me'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data['error'], "unauthorized")

    def test_current_user_me_authorized(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        response = self.client.get(reverse('auth-me'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.user.id)
        self.assertEqual(response.data['username'], "testuser")
        self.assertEqual(response.data['email'], "testuser@school.edu.ng")
        self.assertEqual(response.data['matric_number'], "CSC/2021/001")
        self.assertEqual(response.data['department'], "Computer Science")
        self.assertEqual(response.data['level'], "400")
        self.assertEqual(response.data['role'], "student")
        self.assertEqual(response.data['phone_number'], "08012345678")

    def test_patch_current_user_me(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        payload = {
            "phone_number": "08099999999",
            "level": "500",
            "matric_number": "TAMPERED/001",  # Non-editable field
            "role": "admin"  # Non-editable field
        }
        response = self.client.patch(reverse('auth-me'), payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['phone_number'], "08099999999")
        self.assertEqual(response.data['level'], "500")
        self.assertEqual(response.data['matric_number'], "CSC/2021/001")  # Untouched
        self.assertEqual(response.data['role'], "student")  # Untouched

    def test_logout(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token.key)
        response = self.client.post(reverse('auth-logout'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Token.objects.filter(key=self.token.key).exists())

    def test_login_is_rate_limited(self):
        # Real configured scope/rate is 'auth': 10/min (settings.py).
        # 10 attempts pass, the 11th within the same minute is throttled.
        from django.core.cache import cache

        cache.clear()
        payload = {"username": "testuser", "password": "securepassword123"}
        for _ in range(10):
            self.client.post(reverse('auth-login'), payload)
        response = self.client.post(reverse('auth-login'), payload)
        cache.clear()
        self.assertEqual(response.status_code, 429)
