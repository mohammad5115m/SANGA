from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import CATALOG_MANAGE
from apps.inventory.catalog_selection import MAX_CATALOG_ITEMS, get_selection, resolve_selection

from .forms import CustomCatalogForm, StorefrontCollectionForm
from .models import CustomCatalog, StorefrontCollection, StorefrontCollectionItem
from .selectors import active_special_lots, catalogs_for_business, resolve_catalog
from .services import (
    CatalogError,
    apply_storefront_suggestions,
    create_custom_catalog,
    duplicate_catalog,
    move_catalog_lot,
    move_storefront_collection,
    move_storefront_collection_item,
    public_lot_card,
    regenerate_catalog_token,
    regenerate_storefront_token,
    remove_catalog_lot,
    save_storefront_collection,
    update_catalog,
)


@business_login_required
@require_capability(CATALOG_MANAGE)
def catalog_list(request: HttpRequest) -> HttpResponse:
    collections = request.business.storefront_collections.prefetch_related(
        "items", "items__lot", "items__lot__product"
    )
    special_lots = list(active_special_lots(request.business))
    return render(
        request,
        "catalog/manage_list.html",
        {
            "catalogs": catalogs_for_business(request.business),
            "collections": collections,
            "storefront_url": request.build_absolute_uri(
                f"/store/{request.business.storefront_token}/"
            ),
            "special_count": special_lots[0]._special_total if special_lots else 0,
            "special_cards": [public_lot_card(lot) for lot in special_lots],
        },
    )


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def catalog_create(request: HttpRequest) -> HttpResponse:
    token = request.GET.get("selection") or request.POST.get("selection") or ""
    selection = get_selection(request, business=request.business, token=token)
    if selection is None:
        messages.info(request, "ابتدا محصولات کاتالوگ را از موجودی انتخاب کنید.")
        return redirect("inventory:lot_list")
    selected = resolve_selection(business=request.business, record=selection)
    form = CustomCatalogForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        # Re-resolve on submit: a filter selection means "all matching now", and
        # ownership/deletion may have changed since the confirmation page loaded.
        selected_ids = list(
            resolve_selection(business=request.business, record=selection)
            .values_list("pk", flat=True)[: MAX_CATALOG_ITEMS + 1]
        )
        if not selected_ids:
            form.add_error(None, "هیچ محصول معتبری برای ساخت کاتالوگ باقی نمانده است.")
        elif len(selected_ids) > MAX_CATALOG_ITEMS:
            form.add_error(
                None,
                f"هر کاتالوگ حداکثر {MAX_CATALOG_ITEMS} محصول دارد؛ فیلتر را محدودتر کنید.",
            )
        else:
            try:
                catalog = create_custom_catalog(
                    business=request.business,
                    membership=request.membership,
                    title=form.cleaned_data["title"],
                    customer_name=form.cleaned_data.get("customer_name", ""),
                    custom_message=form.cleaned_data.get("custom_message", ""),
                    expires_at=form.cleaned_data.get("expires_at"),
                    lot_ids=selected_ids,
                )
                if not form.cleaned_data.get("is_active", True):
                    update_catalog(catalog=catalog, membership=request.membership, is_active=False)
            except CatalogError as exc:
                form.add_error(None, exc.message)
            else:
                get_selection(request, business=request.business, token=token, consume=True)
                messages.success(request, "کاتالوگ ساخته شد.")
                return redirect("catalog_manage:detail", catalog_id=catalog.id)
    return render(
        request,
        "catalog/manage_form.html",
        {"form": form, "mode": "create", "selection": token, "selected_count": selected.count()},
    )


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def catalog_edit(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    form = CustomCatalogForm(request.POST or None, instance=catalog)
    if request.method == "POST" and form.is_valid():
        try:
            update_catalog(
                catalog=catalog,
                membership=request.membership,
                title=form.cleaned_data["title"],
                customer_name=form.cleaned_data.get("customer_name", ""),
                custom_message=form.cleaned_data.get("custom_message", ""),
                expires_at=form.cleaned_data.get("expires_at"),
                is_active=form.cleaned_data.get("is_active", True),
            )
        except CatalogError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "کاتالوگ به‌روزرسانی شد.")
            return redirect("catalog_manage:detail", catalog_id=catalog.id)
    return render(request, "catalog/manage_form.html", {"form": form, "mode": "edit", "catalog": catalog})


@business_login_required
@require_capability(CATALOG_MANAGE)
def catalog_detail(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    memberships = list(
        catalog.items.select_related("lot", "lot__product", "lot__product__stone").order_by(
            "sort_order", "id"
        )
    )
    return render(
        request,
        "catalog/manage_detail.html",
        {
            "catalog": catalog,
            "memberships": memberships,
            "selected_count": len(memberships),
            "public_count": resolve_catalog(catalog).count(),
            "share_url": request.build_absolute_uri(f"/c/{catalog.share_token}/"),
            "storefront_url": request.build_absolute_uri(
                f"/store/{request.business.storefront_token}/"
            ),
        },
    )


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def catalog_remove_item(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    try:
        remove_catalog_lot(
            catalog=catalog, membership=request.membership, lot_id=request.POST.get("lot_id")
        )
    except CatalogError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "محصول از کاتالوگ حذف شد.")
    return redirect("catalog_manage:detail", catalog_id=catalog.id)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def catalog_toggle_active(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    try:
        update_catalog(catalog=catalog, membership=request.membership, is_active=not catalog.is_active)
    except CatalogError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "وضعیت کاتالوگ تغییر کرد.")
    return redirect("catalog_manage:detail", catalog_id=catalog.id)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def catalog_item_move(request: HttpRequest, catalog_id, item_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    try:
        moved = move_catalog_lot(
            catalog=catalog,
            membership=request.membership,
            membership_id=item_id,
            direction=request.POST.get("direction", "down"),
        )
    except CatalogError as exc:
        messages.error(request, exc.message)
    else:
        if moved:
            messages.success(request, "ترتیب محصولات به‌روزرسانی شد.")
    return redirect("catalog_manage:detail", catalog_id=catalog.id)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def catalog_token_regenerate(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    try:
        regenerate_catalog_token(catalog=catalog, membership=request.membership)
    except CatalogError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "لینک قبلی برای همیشه باطل شد و لینک تازه آماده است.")
    return redirect("catalog_manage:detail", catalog_id=catalog.id)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def catalog_duplicate(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    try:
        copied = duplicate_catalog(catalog=catalog, membership=request.membership)
    except CatalogError as exc:
        messages.error(request, exc.message)
        return redirect("catalog_manage:detail", catalog_id=catalog.id)
    messages.success(request, "نسخه مشابه در حالت غیرفعال ساخته شد؛ اطلاعات مشتری را تکمیل کنید.")
    return redirect("catalog_manage:edit", catalog_id=copied.id)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def catalog_delete(request: HttpRequest, catalog_id) -> HttpResponse:
    catalog = get_object_or_404(CustomCatalog, pk=catalog_id, business=request.business)
    if request.method == "POST":
        catalog.delete()
        messages.success(request, "کاتالوگ حذف شد.")
        return redirect("catalog_manage:list")
    return render(request, "catalog/manage_confirm_delete.html", {"catalog": catalog})


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def collection_create(request: HttpRequest) -> HttpResponse:
    form = StorefrontCollectionForm(request.POST or None, business=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            collection = save_storefront_collection(
                business=request.business,
                membership=request.membership,
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description", ""),
                is_active=form.cleaned_data.get("is_active", False),
                suggestion_kind=form.cleaned_data.get("suggestion_kind", ""),
                lot_ids=list(form.cleaned_data["products"].values_list("pk", flat=True)),
            )
        except CatalogError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "مجموعه ویترین ساخته شد.")
            return redirect("catalog_manage:collection_edit", collection_id=collection.pk)
    return render(request, "catalog/collection_form.html", {"form": form, "mode": "create"})


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def collection_edit(request: HttpRequest, collection_id) -> HttpResponse:
    collection = get_object_or_404(
        StorefrontCollection.objects.prefetch_related("items", "items__lot", "items__lot__product"),
        pk=collection_id,
        business=request.business,
    )
    form = StorefrontCollectionForm(
        request.POST or None,
        instance=collection,
        business=request.business,
    )
    if request.method == "POST" and form.is_valid():
        selected_ids = list(form.cleaned_data["products"].values_list("pk", flat=True))
        selected_set = set(selected_ids)
        current_ids = list(
            collection.items.order_by("sort_order", "id").values_list("lot_id", flat=True)
        )
        ordered_ids = [lot_id for lot_id in current_ids if lot_id in selected_set]
        preserved_ids = set(ordered_ids)
        ordered_ids.extend(lot_id for lot_id in selected_ids if lot_id not in preserved_ids)
        try:
            save_storefront_collection(
                business=request.business,
                membership=request.membership,
                collection=collection,
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description", ""),
                is_active=form.cleaned_data.get("is_active", False),
                suggestion_kind=form.cleaned_data.get("suggestion_kind", ""),
                lot_ids=ordered_ids,
            )
        except CatalogError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, "مجموعه ویترین ذخیره شد.")
            return redirect("catalog_manage:collection_edit", collection_id=collection.pk)
    return render(
        request,
        "catalog/collection_form.html",
        {"form": form, "mode": "edit", "collection": collection},
    )


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def collection_suggest(request: HttpRequest, collection_id) -> HttpResponse:
    collection = get_object_or_404(StorefrontCollection, pk=collection_id, business=request.business)
    try:
        apply_storefront_suggestions(collection=collection, membership=request.membership)
    except CatalogError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "پیشنهادهای قابل ویرایش به مجموعه افزوده شد.")
    return redirect("catalog_manage:collection_edit", collection_id=collection.pk)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def collection_move(request: HttpRequest, collection_id) -> HttpResponse:
    collection = get_object_or_404(StorefrontCollection, pk=collection_id, business=request.business)
    move_storefront_collection(
        collection=collection,
        membership=request.membership,
        direction=request.POST.get("direction", "down"),
    )
    return redirect("catalog_manage:list")


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def collection_item_move(request: HttpRequest, collection_id, item_id) -> HttpResponse:
    item = get_object_or_404(
        StorefrontCollectionItem.objects.select_related("collection"),
        pk=item_id,
        collection_id=collection_id,
        collection__business=request.business,
    )
    move_storefront_collection_item(
        membership_item=item,
        membership=request.membership,
        direction=request.POST.get("direction", "down"),
    )
    return redirect("catalog_manage:collection_edit", collection_id=collection_id)


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_http_methods(["GET", "POST"])
def collection_delete(request: HttpRequest, collection_id) -> HttpResponse:
    collection = get_object_or_404(StorefrontCollection, pk=collection_id, business=request.business)
    if request.method == "POST":
        collection.delete()
        messages.success(request, "مجموعه ویترین حذف شد.")
        return redirect("catalog_manage:list")
    return render(request, "catalog/collection_confirm_delete.html", {"collection": collection})


@business_login_required
@require_capability(CATALOG_MANAGE)
@require_POST
def storefront_token_regenerate(request: HttpRequest) -> HttpResponse:
    try:
        regenerate_storefront_token(business=request.business, membership=request.membership)
    except CatalogError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "لینک قبلی باطل شد و لینک تازه آماده است.")
    return redirect("catalog_manage:list")
