# معماری فاکتور فروش

> از چرخهٔ invoice-first استفاده می‌شود. جزئیات وضعیت‌ها، امضا، تسویه، همکار محلی و راهنمای انتشار
> در [invoice-first-workflows.md](invoice-first-workflows.md) آمده است. بخش‌های قدیمی این سند که
> فاکتور همکار را پیامد `TradeProposal` می‌دانند دیگر مرجع رفتار جاری نیستند.

این سند تصمیم‌های محصولی و فنی سیستم فاکتور را ثبت می‌کند. هدف، یک سند مالی قابل اتکا است که
پیش‌نمایش، چاپ، PDF و تصویر آن از یک منبع تولید شوند و صدور یا خروجی‌گرفتن هیچ اثر جانبی روی
دفتر حساب نداشته باشد.

## نقشه جریان

1. فرم سه‌مرحله‌ای اطلاعات خریدار، اقلام، مبالغ و ظاهر را دریافت می‌کند.
2. JavaScript فقط برآورد سریع و پیش‌نمایش debounced را نمایش می‌دهد.
3. سرور همه ورودی‌ها را دوباره normalize و با `Decimal` محاسبه می‌کند.
4. پیش‌نویس بدون شماره ذخیره و قابل ویرایش است.
5. هنگام صدور، شماره ترتیبی تحت lock تخصیص داده و اطلاعات فروشنده/ظاهر snapshot می‌شود.
6. `document.html` تنها قالب سند است؛ صفحه جزئیات و چاپ همان HTML را نمایش می‌دهند.
7. WeasyPrint همان HTML را به PDF متنی تبدیل می‌کند و PyMuPDF فقط صفحات PDF را برای PNG
   rasterize می‌کند.

## مدل و چرخه عمر

- `BusinessInvoiceSettings`: هویت رسمی، اطلاعات بانکی، شرایط پرداخت، لوگو، مهر، امضا و ظاهر
  پیش‌فرض هر کسب‌وکار.
- `SalesInvoice`: سند یگانهٔ مشتری، همکار ثبت‌شده یا همکار محلی؛ شامل وضعیت تأیید، روش و تفکیک
  تسویه، snapshotها و ارجاع به نسخه‌های ارسال‌شده است.
- `SalesInvoiceItem`: snapshot محصول به‌همراه مبلغ ناخالص و تخفیف ردیف.
- `InvoiceRevision`: نسخهٔ تغییرناپذیر هر ارسال با امضاهای طرفین و اطلاعات تصمیم.
- `SettlementEvent` و `ChequeReceivable`: رویدادهای تغییرناپذیر تسویه و چرخهٔ چک.
- `InvoiceTemplate`: payload قابل استفاده مجدد، محدود به همان tenant.

پیش‌نویس شماره ندارد. با صدور، شماره در محدوده فروشنده و زیر قفل ردیف `Business` تخصیص می‌یابد.
سند صادرشده و اقلام آن تغییرناپذیرند؛ اصلاح تجاری باید با ابطال یا workflow مالی مربوط انجام شود.
تکثیر همیشه شناسه جدید، وضعیت پیش‌نویس، شماره خالی و مبلغ پرداخت‌شده صفر دارد.

## ترتیب محاسبه

برای هر ردیف:

`gross = quantity × unit_price`

`line_net = gross − line_discount`

برای کل سند:

`total = sum(line_net) − invoice_discount + tax + shipping + adjustment`

`amount_due = total − paid + (previous_balance if explicitly_included else 0)`

همه مقادیر پولی با `Decimal` و دقت دو رقم و مقادیر مقدار/مساحت با سه رقم محاسبه می‌شوند.
تخفیف منفی، درصد بیش از ۱۰۰، تخفیف بیش از پایه، پرداخت بیش از مبلغ نهایی، عدد نامتناهی و بیش از
۱۰۰ ردیف رد می‌شوند.

## ارز و ریال/تومان

ارزهای مجاز `IRR`، `EUR` و `USD` هستند. `IRT` ارز یا نرخ تبدیل نیست؛ فقط واحد نمایش IRR است.
مبالغی که کاربر به تومان وارد می‌کند با ضریب ثابت ۱۰ به IRR ذخیره و هنگام نمایش برعکس تبدیل می‌شوند.
برای ارزهای دیگر هیچ تبدیل ضمنی یا نرخ ساختگی وجود ندارد و واحد نمایش باید با ارز برابر باشد.

## دفتر حساب و مانده قبلی

فاکتور مشتری عادی ledger entry ایجاد نمی‌کند و فقط در صورت دریافت کامل نهایی می‌شود. فاکتور همکار
ثبت‌شده هنگام تأیید خریدار و فاکتور همکار محلی هنگام ثبت تأیید آفلاین، در یک تراکنش اتمیک یک
`Trade` تاریخی و ورودی‌های دفتر را ایجاد می‌کند.

مانده قبلی برای فاکتور همکار از entry همان معامله snapshot می‌شود. پیش‌فرض صرفاً اطلاع‌رسانی است
و در مبلغ قابل پرداخت جمع نمی‌شود. این وضعیت و تصمیم inclusion صریحاً روی سند چاپ می‌شوند. چاپ،
PDF، PNG و تکثیر هیچ ledger entry جدیدی ایجاد نمی‌کنند و عملیات تکراری با کلید idempotency خنثی می‌شود.

## سند canonical و خروجی

- قالب یگانه: `templates/invoicing/document.html`
- A4، RTL، فونت محلی/fallback، تکرار سرستون جدول و جلوگیری از شکستن ردیف‌ها
- footer شماره صفحه با `@page`
- PDF متنی و قابل جست‌وجو با WeasyPrint
- PNG تک‌صفحه‌ای یا ZIP صفحات PNG برای سند چندصفحه‌ای با PyMuPDF
- تولید on-demand و بدون ذخیره PDF/PNG در دیتابیس یا object storage؛ این انتخاب از stale cache و
  دورزدن بررسی دسترسی در لینک‌های قدیمی جلوگیری می‌کند
- هیچ URL خارجی هنگام render دریافت نمی‌شود؛ فقط data URI دارایی‌های پاک‌سازی‌شده مجاز است
- خروجی‌ها محدودیت تعداد درخواست، تعداد صفحه و اندازه دارند

## امنیت و جداسازی tenant

- همه viewها از business جاری و capabilityهای `invoice.view`/`invoice.manage` استفاده می‌کنند.
- template و asset از شناسه tenant در session استخراج می‌شوند و مسیر عمومی cross-tenant ندارند.
- آپلود فقط با تشخیص محتوای واقعی PNG/WebP/JPEG پذیرفته، EXIF آن اعمال و سپس به PNG بدون metadata
  بازنویسی می‌شود؛ حجم ورودی/خروجی و تعداد pixel محدود است.
- نام فایل تصادفی و مسیر آن tenant-scoped است. نام asset داخل snapshot نیز قبل از خواندن prefix-check
  می‌شود.
- جایگزینی یا حذف یک دارایی از تنظیمات، ارجاع آن را از فاکتورهای جدید حذف می‌کند؛ فایل قدیمی تا
  زمانی که snapshot فاکتور صادرشده به آن وابسته است خصوصی نگه داشته می‌شود تا سند تاریخی نشکند.
- قالب چاپ CSP محدود و `frame-ancestors 'self'` دارد تا تنها داخل صفحه خود برنامه نمایش داده شود.
- محتوای کاربر توسط autoescape قالب Django نمایش داده می‌شود و renderer اجازه network fetch ندارد.

## استقرار و آزمون

وابستگی‌های مستقیم `WeasyPrint` و `PyMuPDF` pin شده‌اند. runtime کانتینر کتابخانه‌های Pango مورد
نیاز WeasyPrint را نصب می‌کند. CI باید این فرمان‌ها را اجرا کند:

```bash
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
pytest
./scripts/check_fresh_migrate.sh
```

تست‌های فاکتور محاسبات و خطاهای مرزی، lifecycle، snapshot، template/duplicate، جداسازی tenant،
پاک‌سازی upload، rate limit، متن قابل استخراج PDF، PNG و صفحه‌بندی سند بلند را پوشش می‌دهند.

## منابع پیاده‌سازی renderer

- [WeasyPrint documentation](https://doc.courtbouillon.org/weasyprint/stable/)
- [PyMuPDF image recipes](https://pymupdf.readthedocs.io/en/latest/recipes-images.html)
- [MDN: Printing](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Media_queries/Printing)
- [MDN: `@page`](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/%40page)
