"""The main section's password is its own thing once it has been changed."""
import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.routers.auth import _set_signup_password, verify_admin_password
from app.schemas import AdminPasswordConfirm, AdminPasswordVerify
from app.security import hash_password, verify_password


class SignupPasswordTest(unittest.TestCase):
    def test_a_new_account_opens_with_one_password_in_both_places(self):
        user = SimpleNamespace(password_hash=None, admin_password_hash=None)

        _set_signup_password(user, "sirop123")

        self.assertTrue(verify_password("sirop123", user.password_hash))
        self.assertTrue(verify_password("sirop123", user.admin_password_hash))


class AdminPasswordVerifyTest(unittest.TestCase):
    def _verify(self, user, password):
        return verify_admin_password(AdminPasswordVerify(password=password), user)

    def test_an_account_without_its_own_main_password_uses_the_email_one(self):
        user = SimpleNamespace(password_hash=hash_password("email-parol"), admin_password_hash=None)

        self._verify(user, "email-parol")

        with self.assertRaises(HTTPException) as raised:
            self._verify(user, "boshqa-parol")
        self.assertEqual(raised.exception.status_code, 401)

    def test_once_set_the_main_password_is_the_only_one_accepted(self):
        user = SimpleNamespace(
            password_hash=hash_password("email-parol"),
            admin_password_hash=hash_password("asosiy-parol"),
        )

        self._verify(user, "asosiy-parol")

        with self.assertRaises(HTTPException) as raised:
            self._verify(user, "email-parol")
        self.assertEqual(raised.exception.status_code, 401)


class AdminPasswordConfirmSchemaTest(unittest.TestCase):
    def test_a_spaced_out_code_is_still_six_digits(self):
        payload = AdminPasswordConfirm(code=" 12 34 56 ", new_password="yangi123")
        self.assertEqual(payload.code, "123456")

    def test_a_short_code_is_refused(self):
        with self.assertRaises(ValueError):
            AdminPasswordConfirm(code="12345", new_password="yangi123")


if __name__ == "__main__":
    unittest.main()
