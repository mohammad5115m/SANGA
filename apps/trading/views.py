from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.businesses.decorators import business_login_required, require_capability
from apps.businesses.permissions import SALE_FINALIZE, TRADE_CONFIRM, TRADE_PROPOSE
from apps.core.pagination import ROW_PAGE_SIZE, paginate
from apps.inventory.policy import eligible_items, get_eligible_item
from apps.inventory.selectors import lots_for_business
from apps.invoicing.services import InvoiceError, create_invoice_for_trade

from .forms import TradeProposalForm, TradeProposalLineFormSet
from .models import PurchaseRequest, TradeProposal
from .selectors import (
    filter_requests,
    get_proposal,
    get_received_request,
    get_sent_request,
    get_trade,
    proposals_for_business,
    received_requests,
    sent_requests,
    trades_for_business,
)
from .services import (
    TradingError,
    cancel_trade_proposal,
    confirm_trade_proposal,
    reject_trade_proposal,
    save_trade_proposal,
    submit_trade_proposal,
)

STATUS_FILTERS = (
    ("", "همه"),
    ("open", "در جریان"),
    (PurchaseRequest.Status.SENT, "در انتظار پاسخ"),
    (PurchaseRequest.Status.ACCEPTED, "توافق شده"),
    (PurchaseRequest.Status.COMPLETED, "فروش نهایی شد"),
    (PurchaseRequest.Status.REJECTED, "رد شده"),
)


def _proposal_item_queryset(*, seller, viewer):
    if seller.id == viewer.id:
        return lots_for_business(seller)
    return eligible_items(audience="colleague", viewer_business=viewer, seller_business=seller)


def _proposal_form_context(*, form, lines, proposal=None):
    return {
        "form": form,
        "lines": lines,
        "proposal": proposal,
        "page_title": "ویرایش پیش‌نویس توافق" if proposal else "ثبت توافق معامله",
    }


def _trade_view_allowed(request) -> bool:
    return request.membership is not None and (
        request.membership.has_capability(TRADE_PROPOSE)
        or request.membership.has_capability(TRADE_CONFIRM)
    )


def _trade_view_denied(request):
    if request.membership is None:
        return redirect("businesses:no_business")
    messages.error(request, "دسترسی لازم برای مشاهده معاملات را ندارید.")
    return redirect("businesses:dashboard")


# --- bilateral trade agreements ---------------------------------------------


@business_login_required
def proposal_list(request: HttpRequest) -> HttpResponse:
    if not _trade_view_allowed(request):
        return _trade_view_denied(request)
    tab = request.GET.get("tab", "action")
    qs = proposals_for_business(request.business)
    if tab == "mine":
        qs = qs.filter(
            initiated_by_business=request.business,
            status__in=(TradeProposal.Status.DRAFT, TradeProposal.Status.PENDING),
        )
    elif tab == "final":
        qs = qs.filter(status=TradeProposal.Status.CONFIRMED)
    elif tab == "closed":
        qs = qs.filter(status__in=(TradeProposal.Status.REJECTED, TradeProposal.Status.CANCELLED))
    else:
        tab = "action"
        qs = qs.filter(status=TradeProposal.Status.PENDING).exclude(
            initiated_by_business=request.business
        )
    page = paginate(request, qs, per_page=ROW_PAGE_SIZE)
    return render(
        request,
        "trading/proposal_list.html",
        {"proposals": page.object_list, "page": page, "tab": tab},
    )


@business_login_required
@require_capability(TRADE_PROPOSE)
@require_http_methods(["GET", "POST"])
def proposal_create(request: HttpRequest) -> HttpResponse:
    initial = {}
    item_initial = []
    counterparty_id = request.GET.get("counterparty")
    direction = request.GET.get("direction")
    if counterparty_id:
        initial["counterparty"] = counterparty_id
    if direction in {TradeProposalForm.Direction.SELL, TradeProposalForm.Direction.BUY}:
        initial["direction"] = direction

    form = TradeProposalForm(request.POST or None, business=request.business, initial=initial)
    seller = request.business
    header_valid = False
    if request.method == "POST":
        header_valid = form.is_valid()
        if header_valid:
            seller = form.cleaned_data["seller_business"]
    elif direction == TradeProposalForm.Direction.BUY and counterparty_id:
        seller = form.fields["counterparty"].queryset.filter(pk=counterparty_id).first() or seller

    item_qs = _proposal_item_queryset(seller=seller, viewer=request.business)
    requested_item = request.GET.get("item")
    if request.method == "GET" and requested_item and item_qs.filter(pk=requested_item).exists():
        item_initial = [{"item": requested_item}]
    lines = TradeProposalLineFormSet(
        request.POST or None,
        prefix="lines",
        item_queryset=item_qs,
        initial=item_initial,
    )

    if request.method == "POST":
        lines_valid = lines.is_valid()
        if header_valid and lines_valid:
            try:
                proposal = save_trade_proposal(
                    seller_business=form.cleaned_data["seller_business"],
                    buyer_business=form.cleaned_data["buyer_business"],
                    membership=request.membership,
                    lines=lines.lines,
                    note=form.cleaned_data.get("note", ""),
                    submission_id=form.cleaned_data["submission_id"],
                    submit=request.POST.get("action") != "draft",
                )
            except TradingError as exc:
                form.add_error(None, exc.message)
            else:
                if proposal.status == TradeProposal.Status.DRAFT:
                    messages.success(request, "پیش‌نویس توافق ذخیره شد.")
                else:
                    messages.success(request, "توافق برای تأیید طرف مقابل ارسال شد.")
                return redirect("trading:proposal_detail", proposal_id=proposal.id)

    return render(
        request,
        "trading/proposal_form.html",
        _proposal_form_context(form=form, lines=lines),
    )


@business_login_required
@require_capability(TRADE_PROPOSE)
@require_http_methods(["GET", "POST"])
def proposal_edit(request: HttpRequest, proposal_id) -> HttpResponse:
    proposal = get_proposal(request.business, proposal_id)
    if proposal is None:
        messages.error(request, "توافق یافت نشد.")
        return redirect("trading:proposal_list")
    if (
        proposal.initiated_by_business_id != request.business.id
        or proposal.status != TradeProposal.Status.DRAFT
    ):
        messages.error(request, "فقط پیش‌نویس خودتان قابل ویرایش است.")
        return redirect("trading:proposal_detail", proposal_id=proposal.id)

    is_seller = proposal.seller_business_id == request.business.id
    initial = {
        "submission_id": proposal.submission_id,
        "direction": (
            TradeProposalForm.Direction.SELL if is_seller else TradeProposalForm.Direction.BUY
        ),
        "counterparty": proposal.buyer_business if is_seller else proposal.seller_business,
        "note": proposal.note,
    }
    form = TradeProposalForm(request.POST or None, business=request.business, initial=initial)
    seller = proposal.seller_business
    header_valid = False
    if request.method == "POST":
        header_valid = form.is_valid()
        if header_valid:
            seller = form.cleaned_data["seller_business"]
    item_qs = _proposal_item_queryset(seller=seller, viewer=request.business)
    line_initial = [
        {
            "item": line.item_id,
            "product_name": "" if line.item_id else line.product_name,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
        }
        for line in proposal.items.all()
    ]
    lines = TradeProposalLineFormSet(
        request.POST or None,
        prefix="lines",
        item_queryset=item_qs,
        initial=line_initial,
    )
    if request.method == "POST":
        lines_valid = lines.is_valid()
        if header_valid and lines_valid:
            try:
                proposal = save_trade_proposal(
                    proposal=proposal,
                    seller_business=form.cleaned_data["seller_business"],
                    buyer_business=form.cleaned_data["buyer_business"],
                    membership=request.membership,
                    lines=lines.lines,
                    note=form.cleaned_data.get("note", ""),
                    submit=request.POST.get("action") != "draft",
                )
            except TradingError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(
                    request,
                    "توافق برای تأیید ارسال شد."
                    if proposal.status == TradeProposal.Status.PENDING
                    else "پیش‌نویس توافق ذخیره شد.",
                )
                return redirect("trading:proposal_detail", proposal_id=proposal.id)

    return render(
        request,
        "trading/proposal_form.html",
        _proposal_form_context(form=form, lines=lines, proposal=proposal),
    )


@business_login_required
def proposal_detail(request: HttpRequest, proposal_id) -> HttpResponse:
    if not _trade_view_allowed(request):
        return _trade_view_denied(request)
    proposal = get_proposal(request.business, proposal_id)
    if proposal is None:
        messages.error(request, "توافق یافت نشد.")
        return redirect("trading:proposal_list")
    is_initiator = proposal.initiated_by_business_id == request.business.id
    can_answer = (
        proposal.status == TradeProposal.Status.PENDING
        and not is_initiator
        and request.membership.has_capability(TRADE_CONFIRM)
    )
    return render(
        request,
        "trading/proposal_detail.html",
        {
            "proposal": proposal,
            "is_initiator": is_initiator,
            "can_answer": can_answer,
            "other_business": proposal.other_business(request.business),
        },
    )


def _proposal_action(request, proposal_id, action):
    proposal = get_proposal(request.business, proposal_id)
    if proposal is None:
        messages.error(request, "توافق یافت نشد.")
        return redirect("trading:proposal_list")
    try:
        result = action(proposal=proposal, membership=request.membership)
    except (TradingError, InvoiceError) as exc:
        messages.error(request, exc.message)
    else:
        if action is confirm_trade_proposal:
            messages.success(request, "معامله تأیید شد؛ فاکتور و حساب دفتری هر دو طرف ثبت شدند.")
            return redirect("trading:trade_detail", trade_id=result.id)
        messages.success(request, "وضعیت توافق به‌روز شد.")
    return redirect("trading:proposal_detail", proposal_id=proposal.id)


@business_login_required
@require_capability(TRADE_PROPOSE)
@require_POST
def proposal_submit(request: HttpRequest, proposal_id) -> HttpResponse:
    return _proposal_action(request, proposal_id, submit_trade_proposal)


@business_login_required
@require_capability(TRADE_CONFIRM)
@require_POST
def proposal_confirm(request: HttpRequest, proposal_id) -> HttpResponse:
    return _proposal_action(request, proposal_id, confirm_trade_proposal)


@business_login_required
@require_capability(TRADE_CONFIRM)
@require_POST
def proposal_reject(request: HttpRequest, proposal_id) -> HttpResponse:
    return _proposal_action(request, proposal_id, reject_trade_proposal)


@business_login_required
@require_capability(TRADE_PROPOSE)
@require_POST
def proposal_cancel(request: HttpRequest, proposal_id) -> HttpResponse:
    return _proposal_action(request, proposal_id, cancel_trade_proposal)


# --- retired purchase-request history ---------------------------------------


@business_login_required
@require_capability(TRADE_PROPOSE)
def request_create(request: HttpRequest, item_id) -> HttpResponse:
    """Old marketplace links become a pre-filled bilateral agreement."""
    item = get_eligible_item(audience="colleague", viewer_business=request.business, item_id=item_id)
    if item is None:
        messages.error(request, "این محصول برای معامله در دسترس نیست.")
        return redirect("marketplace:home")
    query = urlencode({"direction": "buy", "counterparty": item.business_id, "item": item.id})
    return redirect(f"{reverse('trading:proposal_create')}?{query}")


def _legacy_request_list(request, *, sent):
    status = request.GET.get("status", "")
    qs = sent_requests(request.business) if sent else received_requests(request.business)
    page = paginate(request, filter_requests(qs, status=status), per_page=ROW_PAGE_SIZE)
    return render(
        request,
        "trading/legacy_request_list.html",
        {
            "requests": page.object_list,
            "page": page,
            "status": status,
            "status_filters": STATUS_FILTERS,
            "sent": sent,
        },
    )


@business_login_required
@require_capability(TRADE_PROPOSE)
def sent_list(request: HttpRequest) -> HttpResponse:
    return _legacy_request_list(request, sent=True)


@business_login_required
@require_capability(TRADE_PROPOSE)
def received_list(request: HttpRequest) -> HttpResponse:
    return _legacy_request_list(request, sent=False)


def _legacy_request_detail(request, *, request_id, sent):
    resolver = get_sent_request if sent else get_received_request
    purchase_request = resolver(request.business, request_id)
    if purchase_request is None:
        messages.error(request, "درخواست یافت نشد.")
        return redirect("trading:sent_list" if sent else "trading:received_list")
    return render(
        request,
        "trading/legacy_request_detail.html",
        {"pr": purchase_request, "sent": sent},
    )


@business_login_required
@require_capability(TRADE_PROPOSE)
def sent_detail(request: HttpRequest, request_id) -> HttpResponse:
    return _legacy_request_detail(request, request_id=request_id, sent=True)


@business_login_required
@require_capability(TRADE_PROPOSE)
def received_detail(request: HttpRequest, request_id) -> HttpResponse:
    return _legacy_request_detail(request, request_id=request_id, sent=False)


@business_login_required
@require_capability(TRADE_CONFIRM)
def finalize(request: HttpRequest, request_id) -> HttpResponse:
    messages.info(request, "روند قدیمی درخواست خرید فقط برای مشاهده سوابق نگه‌داری می‌شود.")
    return redirect("trading:received_detail", request_id=request_id)


@business_login_required
@require_capability(TRADE_PROPOSE)
def direct_sale(request: HttpRequest) -> HttpResponse:
    messages.info(request, "معامله همکار اکنون با توافق و تأیید دوطرفه ثبت می‌شود.")
    return redirect("trading:proposal_create")


# --- finalized trades --------------------------------------------------------


@business_login_required
def trade_list(request: HttpRequest) -> HttpResponse:
    if not _trade_view_allowed(request):
        return _trade_view_denied(request)
    page = paginate(request, trades_for_business(request.business), per_page=ROW_PAGE_SIZE)
    return render(request, "trading/trade_list.html", {"trades": page.object_list, "page": page})


@business_login_required
def trade_detail(request: HttpRequest, trade_id) -> HttpResponse:
    if not _trade_view_allowed(request):
        return _trade_view_denied(request)
    trade = get_trade(request.business, trade_id)
    if trade is None:
        messages.error(request, "معامله یافت نشد.")
        return redirect("trading:trade_list")
    is_seller = trade.seller_business_id == request.business.id
    return render(
        request,
        "trading/trade_detail.html",
        {
            "trade": trade,
            "is_seller": is_seller,
            "invoice": trade.invoices.first(),
            "can_create_invoice": is_seller and request.membership.has_capability(SALE_FINALIZE),
        },
    )


@business_login_required
@require_capability(SALE_FINALIZE)
@require_POST
def trade_create_invoice(request: HttpRequest, trade_id) -> HttpResponse:
    """Recovery path for historical finalized trades that have no invoice."""
    trade = get_trade(request.business, trade_id)
    if trade is None or trade.seller_business_id != request.business.id:
        messages.error(request, "معامله یافت نشد.")
        return redirect("trading:trade_list")
    try:
        invoice = create_invoice_for_trade(trade=trade, membership=request.membership)
    except InvoiceError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, f"فاکتور {invoice.number} ساخته شد.")
    return redirect("trading:trade_detail", trade_id=trade.id)
