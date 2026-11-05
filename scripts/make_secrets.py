"""Generate secure authentication environment variables (keep the output private)."""
import hashlib
import getpass
import secrets

password = getpass.getpass('اختر كلمة مرور قوية للمدير: ')
confirmation = getpass.getpass('أعد كتابة كلمة المرور: ')
if not password or len(password) < 12 or password != confirmation:
    raise SystemExit('خطأ: يجب تطابق كلمتي المرور وألا تقل عن 12 حرفًا.')
salt = secrets.token_bytes(16)
rounds = 390000
hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, rounds)
print('\nانسخ السطرين التاليين إلى .env محليًا أو Environment Variables في Vercel:')
print('APP_SECRET=' + secrets.token_urlsafe(48))
print('ADMIN_PASSWORD_HASH=pbkdf2_sha256$%d$%s$%s' % (rounds, salt.hex(), hashed.hex()))
print('ADMIN_USERNAME=admin')
print('\nلا تضع هذه القيم في GitHub أو ملفات عامة.')
