# نصب بستهٔ آماده روی VPS بدون دریافت وابستگی‌های ساخت

این مسیر برای Ubuntu با معماری `x86_64` و فایل‌های VPS موجود در `/srv/sanga`
است. بسته روی GitHub ساخته می‌شود و تصویر برنامه پس از بررسی تنظیمات، اجرای
migration و پاسخ موفق health صادر می‌شود. تصاویر PostgreSQL، Redis، Alpine
و Caddy نیز همراه آن هستند. نیازی به دسترسی VPS به Debian، PyPI یا رجیستری
Docker هنگام راه‌اندازی نیست. دسترسی عمومی برای DNS و صدور/تمدید گواهی HTTPS
همچنان لازم است.

## دریافت و انتقال

در Pull Request شاخهٔ `codex/vps-offline-...`، اجرای CI را باز کنید. پس از
موفقیت هر هفت job، از بخش Artifacts فایل `sanga-vps-linux-amd64` را دانلود کنید.
برای دانلود وارد حساب GitHub باشید. بسته هفت روز نگهداری می‌شود؛ برای بازتولید،
job مربوط به container را دوباره اجرا کنید.

ZIP را روی رایانه باز کنید. در ترمینال remote مربوط به VPS اجرا کنید:

```bash
mkdir -p "$HOME/sanga-offline"
```

در VS Code متصل به VPS، پوشهٔ `/home/deploy/sanga-offline` را باز کنید و فایل‌های
استخراج‌شده را از File Explorer ویندوز به Explorer آن منتقل کنید. سپس:

```bash
cd "$HOME/sanga-offline"
sha256sum -c SHA256SUMS
sudo docker image load -i sanga-images.tar.gz
```

تمام بررسی‌های checksum باید `OK` باشند. برای تطبیق منبع، `RELEASE.txt` را با
شناسهٔ commit در اجرای موفق CI مقایسه کنید. فایل‌های `.env`، دیتابیس، رسانه یا
کلید خصوصی VPS در بسته نیستند. checksum سلامت انتقال را بررسی می‌کند؛ بسته را
فقط از اجرای شناخته‌شدهٔ مخزن خودتان دریافت کنید.

## استفاده از تنظیمات فعلی VPS

اگر برنامه هم‌اکنون اجرا می‌شود، بخش «به‌روزرسانی برنامهٔ در حال اجرا» را در
پایین دنبال کنید. دستورهای این بخش برای راه‌اندازی اولیه هستند.

فایل `.env` و فایل‌های موجود VPS را حفظ کنید. فقط override بسته را اضافه کنید:

```bash
cp "$HOME/sanga-offline/docker-compose.offline.yml" /srv/sanga/docker-compose.offline.yml
cd /srv/sanga
dc() {
  sudo docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.vps.yml -f docker-compose.offline.yml "$@"
}
dc config --quiet
dc config --images
```

همین پوشه و نام پروژهٔ قبلی را حفظ کنید تا volumeهای موجود عوض نشوند. دستور
`build` اجرا نکنید. برای جلوگیری صریح از ساخت و دانلود:

```bash
dc up -d --no-build --pull never db redis media-init
dc up -d --no-build --pull never web
dc ps --all
curl -i --max-time 10 -H 'Host: sangesfahan.com' http://127.0.0.1:8000/health/
```

`db`، `redis` و `web` باید healthy باشند و `media-init` و `migrate` با کد صفر
پایان یابند. health باید ۲۰۰ بدهد. در صورت خطا توقف کنید و گزارش سرویس مربوط
را با حذف اطلاعات محرمانه بررسی کنید؛ volumeها را حذف نکنید.

پس از بررسی سلامت داخلی و اگر مدیر از قبل ساخته نشده است:

```bash
dc exec web python manage.py createsuperuser
```

پس از تطبیق DNS، کنترل پورت‌های ۸۰ و ۴۴۳ و اعتبارسنجی Caddyfile:

```bash
dc run --rm --no-deps --pull never edge caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
dc up -d --no-build --pull never edge
curl -i --max-time 20 https://sangesfahan.com/health/
```

قبل از استفادهٔ تجاری، ورود، ثبت‌نام و تأیید مدیر، دسترسی اعضا، آپلود و PDF،
مسدودبودن دسترسی مستقیم به فایل امضای واقعاً موجود، پشتیبان‌گیری مستقل و
بازیابی آزمایشی را بررسی کنید. ساخت موفق بسته معادل آماده‌بودن VPS برای تولید
نیست. بسته روی سرور چیزی نصب یا منتشر نمی‌کند؛ فرمان‌های بالا را مالک اجرا می‌کند.

## به‌روزرسانی برنامهٔ در حال اجرا

این مراحل برای برنامهٔ موجود در `/srv/sanga` است که هر سه فایل Compose را
استفاده می‌کند. ابتدا بستهٔ جدید را مطابق بخش دریافت، بررسی و بارگذاری کنید.
محتوای `.env`، فایل VPS، Caddyfile و volumeها را تغییر ندهید. هر مرحله را جدا
اجرا کنید و در صورت خطا ادامه ندهید. در این مراحل نیازی به ساخت مدیر مجدد نیست.

۱. در زمان کم‌ترافیک، پوشهٔ خصوصی پشتیبان بسازید و تنظیم تصویر قبلی را نگه دارید:

```bash
cd /srv/sanga
umask 077
backup_dir="$HOME/sanga-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
cp -p docker-compose.offline.yml "$backup_dir/docker-compose.offline.yml"
sudo docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.vps.yml -f docker-compose.offline.yml exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/database.dump"
```

۲. صحت ساختار پشتیبان دیتابیس را کنترل و از رسانه‌ها نیز نسخه بگیرید. این کنترل
جای بازیابی آزمایشی در محیط جداگانه را نمی‌گیرد. پشتیبان‌ها خصوصی هستند؛ نسخه‌ای
از آن‌ها را در محل امن دیگری هم نگه دارید.

```bash
sudo docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.vps.yml -f docker-compose.offline.yml exec -T db pg_restore --list < "$backup_dir/database.dump" > "$backup_dir/database-contents.txt"
sudo docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.vps.yml -f docker-compose.offline.yml run --rm --no-deps --pull never -T --entrypoint sh media-init -c 'tar -C /data -czf - .' > "$backup_dir/media.tar.gz"
gzip -t "$backup_dir/media.tar.gz"
printf 'Backup: %s\n' "$backup_dir"
```

۳. override جدید را جایگزین و تنظیمات آن را بررسی کنید:

```bash
cp "$HOME/sanga-offline/docker-compose.offline.yml" /srv/sanga/docker-compose.offline.yml
sudo docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.vps.yml -f docker-compose.offline.yml config --quiet
sudo docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.vps.yml -f docker-compose.offline.yml config --images
```

تصویر `web` و `migrate` باید با `Application image` در `RELEASE.txt` جدید یکسان
باشد. برای اصلاحات مدیریت مورخ ۲۰۲۶/۱۱/۰۸ هیچ migration جدیدی اضافه نشده است.

۴. migration را با تصویر جدید بررسی و فقط برنامه را به‌روزرسانی کنید. دیتابیس،
Redis و Caddy موجود نیاز به راه‌اندازی مجدد ندارند:

```bash
sudo docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.vps.yml -f docker-compose.offline.yml run --rm --no-deps --pull never migrate
sudo docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.vps.yml -f docker-compose.offline.yml up -d --no-deps --no-build --pull never web
sudo docker compose --env-file .env -f docker-compose.prod.yml -f docker-compose.vps.yml -f docker-compose.offline.yml ps --all
curl --fail --show-error --max-time 20 https://sangesfahan.com/health/
```

سپس `/admin/` را با `Ctrl+F5` باز کنید، ورود و صفحات مدیریت را بررسی کنید. تصویر
قبلی را تا تأیید نتیجه پاک نکنید. برای بازگشت همین اصلاحات بدون تغییر schema،
فایل override ذخیره‌شده در پوشهٔ پشتیبان را برگردانید و دستور `up` برای `web`
را دوباره اجرا کنید. از دستور حذف volume یا بازیابی دیتابیس روی دادهٔ زنده
استفاده نکنید؛ این کارها بخشی از به‌روزرسانی نیستند.
