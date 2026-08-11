# سنگا (SANGA)

پلتفرم مدیریت موجودی سنگ، شبکه شرکای B2B و کاتالوگ B2C.

**وعده محصول:** یک‌بار ثبت موجودی؛ فروش در چند کانال با قیمت و اطلاعات درست برای هر مخاطب.

## وضعیت فعلی

- ✅ فاز ۰: معماری و مستندات محصول
- ✅ فاز ۱: زیربنای فنی (Django، احراز هویت OTP، کسب‌وکار، انبار، شِل RTL)
- ✅ فاز ۲: هسته موجودی (ویزارد افزودن سریع، لیست/جزئیات، قیمت B2B/B2C، تازگی)
- ✅ فاز ۳: کاتالوگ B2C (ویترین، جزئیات، استعلام، کاتالوگ اختصاصی)

مستندات: پوشه [`docs/`](docs/)

## پیش‌نیازها

روی سیستم خودتان نصب کنید (اگر ندارید):

1. **Python 3.12+**
2. **Git** (اختیاری ولی توصیه‌شده)
3. **Docker Desktop** (ساده‌ترین راه اجرای کامل با PostgreSQL و Redis)
4. **Node.js 20+** (فقط برای ساخت Tailwind؛ در Docker هم قابل انجام است)

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

داده دمو (بعداً در فاز ۲ تکمیل‌تر می‌شود):

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
| [docs/user-flows.md](docs/user-flows.md) | جریان‌های کاربری و ناوبری |
| [docs/roadmap.md](docs/roadmap.md) | نقشه راه فازها |

## تست

```bash
pytest
python manage.py check
```

## امنیت قیمت

قیمت B2B هرگز نباید در صفحات/API عمومی B2C ظاهر شود. جزئیات در `docs/pricing.md` و `docs/permissions.md`.
