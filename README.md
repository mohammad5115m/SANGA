# سنگا (SANGA)

پلتفرم مدیریت موجودی سنگ، شبکه شرکای B2B و کاتالوگ B2C.

**وعده محصول:** یک‌بار ثبت موجودی؛ فروش در چند کانال با قیمت و اطلاعات درست برای هر مخاطب.

## وضعیت فعلی

- ✅ فاز ۰: معماری و مستندات محصول
- ✅ فاز ۱: زیربنای فنی (Django، احراز هویت OTP، کسب‌وکار، انبار، شِل RTL)
- ✅ فاز ۲: هسته موجودی (ویزارد افزودن سریع، لیست/جزئیات، قیمت B2B/B2C، تازگی)
- ✅ فاز ۳: کاتالوگ B2C (ویترین، جزئیات، استعلام، کاتالوگ اختصاصی)
- ✅ فاز ۴: شبکه همکاران B2B (بازار، همکاری، دنبال‌کردن، جستجوی ذخیره‌شده)
- ✅ فاز ۵: شبکه تقاضا (درخواست خرید، پیشنهاد خصوصی، تطبیق قاعده‌محور)
- ✅ فاز ۶: رزروها (درخواست/تأیید/تمدید/لغو/تبدیل، قفل متراژ، انقضا)
- 🚧 فاز ۷: مخاطبین، حسابداری و قیمت اختصاصی
  - ✅ مخاطبین خصوصی هر کسب‌وکار (مشتری/تأمین‌کننده/واسطه) با اتصال اختیاری به همکار تأییدشده
  - ✅ دفتر حساب هر مخاطب: سند تغییرناپذیر، مانده برچسب‌دار، برگشت سند، چاپ صورت‌حساب
  - ✅ اتصال معامله به دفتر حساب (سمت فروشنده) با امکان برگشت و ثبت دوباره‌ی سند اصلاح‌شده
  - ✅ قیمت اختصاصی برای همکار مشخص (اولویت: قیمت اختصاصی ← قیمت همکار/مشتری ← «استعلام بگیرید»)
  - ⬜ باقی‌مانده: جریان کاری استعلام‌ها، داشبورد تحلیلی

مستندات: پوشه [`docs/`](docs/)

## پیش‌نیازها

روی سیستم خودتان نصب کنید (اگر ندارید):

1. **Python 3.12+**
2. **Git** (اختیاری ولی توصیه‌شده)
3. **Docker Desktop** (ساده‌ترین راه اجرای کامل با PostgreSQL و Redis)

Node.js لازم نیست: استایل‌ها CSS دست‌نویس در `static/css/app.css` هستند و هیچ مرحله‌ی
build فرانت‌اند (Tailwind یا غیر آن) وجود ندارد.

> این مخزن کد را آماده می‌کند. اگر Python/Git هنوز نصب نیست، ابتدا آن‌ها را نصب کنید، سپس دستورات زیر را اجرا کنید.

## اجرای سریع با Docker (توصیه‌شده)

این دستورات فایل‌ها را تغییر می‌دهند/دیتابیس می‌سازند و سرویس‌ها را بالا می‌آورند.

```bash
cp .env.example .env
docker compose up --build
```

سپس در مرورگر:

- اپ: http://localhost:8000/
- Health: http://localhost:8000/health/

ورود توسعه: OTP در لاگ کانتینر `web` چاپ می‌شود (Console SMS provider).

داده دمو:

```bash
docker compose exec web python manage.py seed_demo
```

## اجرای محلی بدون Docker

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements/development.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

برای PostgreSQL/Redis واقعی، مقادیر `.env` را مطابق سرویس‌های محلی تنظیم کنید. در حالت توسعه می‌توان از SQLite هم استفاده کرد (`DJANGO_DATABASE=sqlite`).

## ساختار اصلی

```text
apps/           # دامنه‌های کسب‌وکار
config/         # تنظیمات Django / URLs / Celery
design/         # یادداشت‌های دیزاین سیستم
docs/           # مستندات محصول و معماری
templates/      # قالب‌های سراسری
static/         # دارایی‌های استاتیک
```

## مستندات مهم

| سند | محتوا |
|-----|--------|
| [docs/product.md](docs/product.md) | مأموریت و اصول محصول |
| [docs/architecture.md](docs/architecture.md) | معماری فنی |
| [docs/data-model.md](docs/data-model.md) | مدل داده |
| [docs/permissions.md](docs/permissions.md) | دسترسی و امنیت قیمت |
| [docs/pricing.md](docs/pricing.md) | لایه‌های قیمت، قیمت اختصاصی مخاطب و ترتیب اولویت |
| [docs/accounting.md](docs/accounting.md) | دفتر حساب و قرارداد مانده |
| [docs/user-flows.md](docs/user-flows.md) | جریان‌های کاربری و ناوبری |
| [docs/roadmap.md](docs/roadmap.md) | نقشه راه فازها |

## تست

```bash
pytest
python manage.py check
```

## امنیت قیمت

قیمت B2B هرگز نباید در صفحات/API عمومی B2C ظاهر شود. جزئیات در `docs/pricing.md` و `docs/permissions.md`.
