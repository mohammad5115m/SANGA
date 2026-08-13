# سنگا (SANGA)

پلتفرم کشف محصول، بازار همکاران، کاتالوگ، فاکتور و دفتر حساب برای کسب‌وکارهای سنگ.

**وعده محصول:** محصول را یک‌بار ثبت کنید؛ همکاران و مشتریان آن را با اطلاعات و
قیمت درست برای خودشان ببینند — و همیشه معلوم باشد این اطلاعات چقدر تازه است.

سنگا وانمود نمی‌کند انبار شما یا حساب بانکی شماست. آخرین چیزی را که تأیید
کرده‌اید ثبت می‌کند و صادقانه می‌گوید چقدر از آن گذشته است.

## ستون‌های محصول

1. کشف محصول
2. بازار همکاران (B2B)
3. جستجوی عمومی برای مشتری (B2C)
4. درخواست خرید روی یک محصول مشخص
5. فروش نهایی و فاکتور
6. دفتر حساب هر همکار
7. کاتالوگ‌های زنده
8. اطلاعات قیمت و موجودی با تاریخ اعتبار

## آنچه سنگا نیست

درگاه پرداخت، سیستم چک، انبارداری، ERP، حسابداری رسمی، لجستیک، مزایده معکوس،
تابلوی تقاضای عمومی، یا CRM. پول بیرون از سنگا جابه‌جا می‌شود و بعداً ثبت می‌شود.

## پیش‌نیازها

1. **Python 3.12+**
2. **Git**
3. **Docker Desktop** (ساده‌ترین راه اجرای کامل با PostgreSQL و Redis)

Node.js لازم نیست: استایل‌ها CSS دست‌نویس در `static/css/app.css` هستند و هیچ
مرحله‌ی build فرانت‌اند وجود ندارد.

## اجرای سریع با Docker

```bash
cp .env.example .env
docker compose up --build
```

- اپ: http://localhost:8000/
- جستجوی عمومی: http://localhost:8000/search/
- Health: http://localhost:8000/health/

ورود توسعه: OTP در لاگ کانتینر `web` چاپ می‌شود (Console SMS provider). در
پروداکشن `SMS_PROVIDER=kavenegar` است و اپلیکیشن بدون کلید و قالب تأییدشده بالا
نمی‌آید؛ ارائه‌دهنده‌ای که پیامک نمی‌فرستد در پروداکشن پذیرفته نمی‌شود.

```bash
docker compose exec web python manage.py seed_demo
```

## اجرای محلی بدون Docker

```bash
python -m venv .venv
source .venv/bin/activate          # ویندوز: .venv\Scripts\activate
pip install -r requirements/development.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

در حالت توسعه می‌توان از SQLite استفاده کرد (`DJANGO_DATABASE=sqlite`).

## ساخت حساب کاربری

سنگا **ثبت‌نام عمومی ندارد**. هر کسب‌وکار و هر کاربر را ادمین پلتفرم می‌سازد:

```bash
python manage.py provision_business \
    --name "سنگبری نمونه" --owner-phone 09121234567 --plan seller --seats 3

python manage.py provision_user \
    --phone 09121234568 --business sangbari-nemune --role staff
```

مشتریان عمومی هرگز حساب کاربری ندارند.

ساخت کسب‌وکار توسط ادمین، خودِ **تأیید** آن است؛ بنابراین
`verification_status=verified` ثبت می‌شود و فقط کسب‌وکارهای تأییدشده در فهرست
همکاران، بازار همکاران، جستجوی عمومی و کاتالوگ‌های اشتراکی دیده می‌شوند.

## ساختار

```text
apps/
  accounts/     احراز هویت OTP (کاربر را نمی‌سازد)
  businesses/   کسب‌وکار، عضویت، پلن، لیست همکاران
  inventory/    محصول، موجودی، سیاست دیده‌شدن، موتور فیلتر
  pricing/      دو کانال قیمت B2B و B2C با اعتبار زمانی
  marketplace/  بازار همکاران
  catalog/      ویترین، جستجوی عمومی، کاتالوگ‌ها، لینک اشتراک
  inquiries/    مشتریان و استعلام‌های چندمحصولی
  trading/      درخواست خرید و معامله نهایی (چندردیفی)
  invoicing/    فاکتور فروش
  accounting/   دفتر حساب تغییرناپذیر
  reporting/    گزارش‌های عملیاتی
config/         تنظیمات، URLها، Celery
docs/           مستندات محصول و معماری
templates/      قالب‌ها (RTL)
static/         CSS و دارایی‌ها
```

## مستندات

| سند | محتوا |
|-----|--------|
| [docs/product.md](docs/product.md) | تعریف محصول و اصول |
| [docs/architecture.md](docs/architecture.md) | معماری فنی |
| [docs/inventory.md](docs/inventory.md) | محصول، چرخه عمر، موجودی، سیاست دیده‌شدن |
| [docs/pricing.md](docs/pricing.md) | دو کانال قیمت، اعتبار، فروش ویژه |
| [docs/trading.md](docs/trading.md) | درخواست خرید و فروش نهایی |
| [docs/accounting.md](docs/accounting.md) | دفتر حساب و فاکتور |
| [docs/catalogs.md](docs/catalogs.md) | کاتالوگ‌های دستی، فیلتری و ترکیبی |
| [docs/customers.md](docs/customers.md) | مشتری عمومی و استعلام |
| [docs/reports.md](docs/reports.md) | گزارش‌ها |
| [docs/permissions.md](docs/permissions.md) | دسترسی، پلن و امنیت قیمت |
| [docs/user-flows.md](docs/user-flows.md) | جریان‌های کاربری و ناوبری |
| [docs/data-model.md](docs/data-model.md) | مدل داده |
| [docs/v2-migration-strategy.md](docs/v2-migration-strategy.md) | قواعد مهاجرت V2 |
| [docs/roadmap.md](docs/roadmap.md) | وضعیت و کارهای بعدی |

## تست

پنج بررسی، و همه باید سبز باشند:

```bash
python manage.py check
python manage.py makemigrations --check
pytest
./scripts/check_fresh_migrate.sh
ruff check .
```

## قواعدی که هرگز نباید نقض شوند

- فقط ادمین پلتفرم کاربر و کسب‌وکار می‌سازد.
- مشتری عمومی کاربر پلتفرم نیست.
- قیمت همکار هرگز در هیچ صفحه عمومی دیده نمی‌شود.
- «ناموجود» و «استعلام موجودی» دو چیز متفاوت‌اند.
- پذیرش درخواست خرید یعنی توافق، نه فروش.
- فروش نهایی موجودی را خودکار کم نمی‌کند.
- هر فروش دقیقاً یک‌بار در دفتر حساب ثبت می‌شود.
- فاکتور تاریخی است؛ کاتالوگ زنده است.
