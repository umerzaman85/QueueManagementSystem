from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.middleware.csrf import get_token
from django.db.models import Count, Q, Prefetch, F, Func, Value, CharField, DateField
from django.utils import timezone
# TruncDate/TruncMonth replaced with SQLite built-in functions for Python 3.14 compat
from datetime import date, timedelta
import csv
import logging
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.views import LoginView
from django.contrib.auth.hashers import check_password
from functools import wraps
from django.contrib import messages
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import get_user_model
from django.views.decorators.csrf import csrf_protect
from django.db import transaction
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from .models import Service, Counter, Ticket, CallEvent
from .utils import (
    create_ticket, log_action, broadcast_ticket_call,
    claim_next_ticket, claim_transferred_ticket, claim_previous_ticket,
    recall_current, mark_done, skip_current,
)

logger = logging.getLogger(__name__)
User = get_user_model()

# ------------------------------------------------------------
# ROLE CHECKS
# ------------------------------------------------------------
def is_staff_user(u):
    return u.is_authenticated and (u.is_staff or u.groups.filter(name__in=['Staff']).exists())

def is_supervisor(u):
    return u.is_authenticated and (u.is_superuser or u.groups.filter(name__in=['Supervisor']).exists())

def role_required(role_check, login_url='login', template='queue/not_authorized.html'):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(login_url)
            if not role_check(request.user):
                return render(request, template, status=403)
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


# ------------------------------------------------------------
# AFTER LOGIN REDIRECT
# ------------------------------------------------------------
@login_required
def after_login(request):
    if is_supervisor(request.user):
        return redirect('supervisor_dashboard')
    assigned = getattr(request.user, 'assigned_counter', None)
    if assigned:
        request.session['counter_id'] = assigned.id
        return redirect('staff_dashboard')
    return redirect('staff_dashboard')


# ------------------------------------------------------------
# STAFF DASHBOARD
# ------------------------------------------------------------
@login_required
@role_required(is_staff_user)
def staff_dashboard(request):
    user = request.user
    is_sup = is_supervisor(user)
    assigned_counter = getattr(user, 'assigned_counter', None)

    if is_sup:
        counter_id = request.GET.get('counter') or request.session.get('counter_id')
        counter = get_object_or_404(Counter, id=counter_id) if counter_id else None
        if counter:
            request.session['counter_id'] = counter.id
    else:
        if not assigned_counter:
            return render(request, 'queue/not_authorized.html', {
                'message': 'No counter has been assigned to you. Please contact your supervisor.'
            }, status=403)
        counter = assigned_counter
        request.session['counter_id'] = counter.id

    if not counter:
        return render(request, 'queue/not_authorized.html', {
            'message': 'Please select a counter from the dropdown to continue.'
        }, status=403)

    tickets = Ticket.objects.filter(current_counter=counter)
    waiting_tickets = tickets.filter(
        status__in=[Ticket.STATUS_WAITING, Ticket.STATUS_CALLING]
    ).order_by('created_at')
    served_tickets = tickets.filter(status=Ticket.STATUS_SERVED)
    skipped_tickets = tickets.filter(status=Ticket.STATUS_SKIPPED)

    if not counter.current_ticket and waiting_tickets.exists():
        counter.current_ticket = waiting_tickets.first()
        counter.save(update_fields=['current_ticket'])

    # Unassigned WAITING tickets in the shared pool for this counter's services.
    init_service_ids = list(counter.services.values_list('id', flat=True))
    init_unassigned_waiting = (
        Ticket.objects
        .filter(
            status=Ticket.STATUS_WAITING,
            current_counter__isnull=True,
            service_id__in=init_service_ids,
        )
        .count()
    ) if init_service_ids else 0

    ticket_counts = {
        'total': tickets.count(),
        # Include TRANSFERRED + unassigned pool tickets for an accurate waiting depth
        'waiting': tickets.filter(
            status__in=[Ticket.STATUS_WAITING, Ticket.STATUS_CALLING, Ticket.STATUS_TRANSFERRED]
        ).count() + init_unassigned_waiting,
        'served': served_tickets.count(),
        'skipped': skipped_tickets.count(),
    }

    transfer_counters = Counter.objects.exclude(id=counter.id)

    return render(request, 'queue/staff_dashboard.html', {
        'counters': Counter.objects.all().order_by('name') if is_sup else Counter.objects.none(),
        'counter': counter,
        'current_ticket': counter.current_ticket,
        'transfer_counters': transfer_counters,
        'csrf_token': get_token(request),
        'is_supervisor': is_sup,
        'ticket_counts': ticket_counts,
    })


# ------------------------------------------------------------
# STAFF ACTIONS (Next, Prev, Recall, Done, Skip)
# ------------------------------------------------------------
@login_required
@user_passes_test(is_staff_user)
@require_POST
@log_action("staff_action")
def staff_action(request):
    """
    Handles Next, Prev, Recall, Done, Skip actions.
    All broadcasts are handled inside utils.py — NO manual broadcasts here.
    """
    try:
        action = request.POST.get("action")
        counter_id = request.session.get("counter_id")

        if not counter_id:
            return JsonResponse({"ok": False, "message": "No counter assigned."})

        counter = get_object_or_404(
            Counter.objects.select_related('current_ticket'),
            id=counter_id
        )
        user = request.user
        current_ticket = counter.current_ticket

        def get_counts():
            """Single-query aggregate for counter ticket stats."""
            return counter.tickets.aggregate(
                total=Count('id'),
                # Include TRANSFERRED so received transfers count as waiting
                waiting=Count('id', filter=Q(status__in=[Ticket.STATUS_WAITING, Ticket.STATUS_CALLING, Ticket.STATUS_TRANSFERRED])),
                served=Count('id', filter=Q(status=Ticket.STATUS_SERVED)),
                skipped=Count('id', filter=Q(status=Ticket.STATUS_SKIPPED)),
            )

        # -------- NEXT --------
        if action == "next":
            # FIX: use claim_transferred_ticket() which properly updates state & broadcasts,
            # instead of raw queryset fetch that left the ticket in a broken state.
            t = None
            if request.POST.get("prefer") == "transfer":
                t = claim_transferred_ticket(counter, user=user)

            if not t:
                t = claim_next_ticket(counter, user=user)

            return JsonResponse({
                "ok": True,
                "message": f"Next ticket: {t.display_code if t else 'No more tickets'}",
                "current": {
                    "id": t.id,
                    "code": t.display_code,
                    "service": t.service.name,
                } if t else {},
                "ticket_counts": get_counts(),
            })

        # -------- PREV --------
        elif action == "prev":
            # FIX: same as NEXT — use claim_transferred_ticket for transferred path.
            t = None
            if request.POST.get("prefer") == "transfer":
                t = claim_transferred_ticket(counter, user=user)

            if not t:
                t = claim_previous_ticket(counter, user=user)

            return JsonResponse({
                "ok": True,
                "message": f"Previous ticket: {t.display_code if t else 'No previous ticket'}",
                "current": {
                    "id": t.id,
                    "code": t.display_code,
                    "service": t.service.name,
                } if t else {},
                "ticket_counts": get_counts(),
            })

        # -------- RECALL --------
        elif action == "recall":
            if current_ticket:
                t = recall_current(counter, user=user)
                return JsonResponse({
                    "ok": True,
                    "message": f"Ticket {t.display_code} recalled.",
                    "current": {
                        "id": t.id,
                        "code": t.display_code,
                        "service": t.service.name,
                    },
                    "ticket_counts": get_counts(),
                })
            return JsonResponse({
                "ok": False,
                "message": "No current ticket to recall.",
                "current": {}
            })

        # -------- DONE --------
        elif action == "done":
            if current_ticket:
                mark_done(counter, user=user)
                return JsonResponse({
                    "ok": True,
                    "message": f"Ticket {current_ticket.display_code} completed.",
                    "current": {},
                    "ticket_counts": get_counts(),
                })
            return JsonResponse({
                "ok": False,
                "message": "No current ticket to complete.",
                "current": {}
            })

        # -------- SKIP --------
        elif action == "skip":
            if current_ticket:
                skip_current(counter, user=user)
                return JsonResponse({
                    "ok": True,
                    "message": f"Ticket {current_ticket.display_code} skipped.",
                    "current": {},
                    "ticket_counts": get_counts(),
                })
            return JsonResponse({
                "ok": False,
                "message": "No current ticket to skip.",
                "current": {}
            })

        return JsonResponse({
            "ok": False,
            "message": "Invalid action.",
            "current": {}
        })

    except Exception as e:
        logger.exception("Staff action error")
        return JsonResponse({
            "ok": False,
            "message": "An unexpected error occurred. Please try again.",
            "current": {}
        })


# ------------------------------------------------------------
# KIOSK
# FIX: removed @csrf_exempt — kiosk already refreshes CSRF via /ping/
# ------------------------------------------------------------
def kiosk(request):
    services = Service.objects.filter(is_active=True).order_by('name')
    return render(request, 'queue/kiosk.html', {'services': services})


@require_POST
def kiosk_print(request):
    try:
        service_id = request.POST.get('service_id')
        if not service_id:
            return JsonResponse({'success': False, 'error': 'No service selected'}, status=400)

        service = get_object_or_404(Service, id=service_id, is_active=True)
        ticket = create_ticket(service)

        print_success = False
        try:
            from .printing import print_ticket
            result = print_ticket(ticket)
            print_success = result if result is not None else True
            logger.info(f"Ticket {ticket.id} printed successfully")
        except Exception as e:
            logger.error(f"Print failed for ticket {ticket.id}: {e}", exc_info=True)
            print_success = False

        return JsonResponse({
            'success': print_success,
            'ticket_number': str(ticket.display_code),
            'service': service.name,
            'ticket_id': ticket.id,
        })

    except Exception as e:
        logger.error(f"Kiosk print error: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': 'An error occurred while printing'}, status=500)


# ------------------------------------------------------------
# STAFF CURRENT TICKET (polling endpoint)
# ------------------------------------------------------------
@login_required
@role_required(is_staff_user)
def staff_current_ticket(request):
    """Get current ticket and counts for a counter. Used by the dashboard polling."""
    counter_id = request.session.get('counter_id')
    if not counter_id:
        return JsonResponse({'ok': False, 'message': 'No counter specified'})

    try:
        counter = Counter.objects.select_related(
            'current_ticket', 'current_ticket__service'
        ).get(id=counter_id)

        # FIX 1: was filtering by status='serving' — correct status is STATUS_CALLING.
        # FIX 2: use counter.current_ticket directly (set by staff actions) instead of
        #         re-querying by status, which missed tickets in other valid states.
        current_ticket = counter.current_ticket

        # Tickets in the shared pool not yet claimed by any counter but serviceable here.
        # These are created by the kiosk and have current_counter=NULL until claimed via Next.
        counter_service_ids = list(counter.services.values_list('id', flat=True))
        unassigned_waiting = (
            Ticket.objects
            .filter(
                status=Ticket.STATUS_WAITING,
                current_counter__isnull=True,
                service_id__in=counter_service_ids,
            )
            .count()
        ) if counter_service_ids else 0

        base = counter.tickets.aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(status__in=[Ticket.STATUS_WAITING, Ticket.STATUS_CALLING, Ticket.STATUS_TRANSFERRED])),
            served=Count('id', filter=Q(status=Ticket.STATUS_SERVED)),
            skipped=Count('id', filter=Q(status=Ticket.STATUS_SKIPPED)),
        )

        counts = {
            'total': base['total'],
            # Add unassigned pool tickets so the count rises as the kiosk generates new tickets,
            # without waiting for staff to press Next.
            'waiting': base['active'] + unassigned_waiting,
            'served': base['served'],
            'skipped': base['skipped'],
        }

        response_data = {
            'ok': True,
            'current_ticket': None,
            'ticket_counts': counts,
        }

        if current_ticket:
            response_data['current_ticket'] = {
                'id': current_ticket.id,
                # FIX 4: was current_ticket.code — Ticket has no .code field, use .display_code
                'code': current_ticket.display_code,
                'service': current_ticket.service.name,
            }

        return JsonResponse(response_data)

    except Counter.DoesNotExist:
        return JsonResponse({'ok': False, 'message': 'Counter not found'})
    except Exception as e:
        logger.exception("staff_current_ticket error")
        return JsonResponse({'ok': False, 'message': 'An unexpected error occurred.'}, status=500)


# ------------------------------------------------------------
# TRANSFER TICKET
# ------------------------------------------------------------
@login_required
@role_required(is_staff_user)
@require_POST
def staff_transfer_ticket(request):
    try:
        ticket_id = request.POST.get('ticket_id')
        target_counter_id = request.POST.get('counter_id')
        counter_id = request.session.get('counter_id')

        # Only allow transferring tickets belonging to the staff's own counter
        ticket = get_object_or_404(Ticket, id=ticket_id, current_counter_id=counter_id)
        target_counter = get_object_or_404(Counter, id=target_counter_id)

        with transaction.atomic():
            # Lock original counter to prevent race conditions during transfer
            original_counter = Counter.objects.select_for_update().get(id=counter_id)

            ticket.current_counter = target_counter
            ticket.status = Ticket.STATUS_TRANSFERRED
            ticket.save(update_fields=['current_counter', 'status'])

            # If the transferred ticket was the original counter's current ticket,
            # clear it immediately so no poll window shows a stale value
            if original_counter.current_ticket_id == ticket.id:
                original_counter.current_ticket = None
                original_counter.save(update_fields=['current_ticket'])

            if not target_counter.current_ticket:
                target_counter.current_ticket = ticket
                target_counter.save(update_fields=['current_ticket'])

        new_current = claim_next_ticket(original_counter, user=request.user)

        # Return fresh counts so the dashboard updates immediately without waiting for a poll
        updated_counts = original_counter.tickets.aggregate(
            total=Count('id'),
            waiting=Count('id', filter=Q(status__in=[Ticket.STATUS_WAITING, Ticket.STATUS_CALLING, Ticket.STATUS_TRANSFERRED])),
            served=Count('id', filter=Q(status=Ticket.STATUS_SERVED)),
            skipped=Count('id', filter=Q(status=Ticket.STATUS_SKIPPED)),
        )

        return JsonResponse({
            "ok": True,
            "message": f"Ticket {ticket.display_code} transferred to {target_counter.name}.",
            "current_ticket": {
                "id": new_current.id if new_current else None,
                "code": new_current.display_code if new_current else None,
                "service": new_current.service.name if new_current else None,
            },
            "ticket_counts": updated_counts,
        })

    except Exception as e:
        logger.exception("Transfer ticket error")
        return JsonResponse({'ok': False, 'message': 'An error occurred during transfer.', 'current_ticket': {}})


@login_required
@role_required(is_staff_user)
def staff_transfer_list(request):
    """Return counters that may receive transferred tickets."""
    counter_id = request.GET.get("counter_id")
    qs = Counter.objects.exclude(id=counter_id).order_by("name")
    return JsonResponse({"counters": [{"id": c.id, "name": c.name} for c in qs]})


# ------------------------------------------------------------
# FORGOT PASSWORD
# FIX: this endpoint allowed unauthenticated password resets with only a username.
# It is now login-required. For a true "forgot password" flow that works without
# login, integrate Django's built-in PasswordResetView with email tokens instead.
# ------------------------------------------------------------
@login_required
@require_POST
@csrf_protect
def forgot_password_api(request):
    """
    Allows an already-authenticated user to change their own password.
    SECURITY FIX: was unauthenticated — any caller knowing a username could
    reset that account. Now restricted to the logged-in user's own account.
    """
    new_pw = request.POST.get('new_password', '').strip()
    if not new_pw:
        return JsonResponse({'ok': False, 'message': 'New password is required.'})

    user = request.user

    # Validate password strength using Django's configured validators
    try:
        validate_password(new_pw, user)
    except ValidationError as errs:
        return JsonResponse({'ok': False, 'message': ' '.join(errs.messages)})

    user.set_password(new_pw)
    user.save(update_fields=['password'])
    update_session_auth_hash(request, user)  # Keep user logged in after password change

    return JsonResponse({'ok': True, 'message': 'Password changed successfully.'})


# ------------------------------------------------------------
# PING (session keepalive for kiosk)
# ------------------------------------------------------------
def ping(request):
    """Keep session alive and return fresh CSRF token."""
    return JsonResponse({'ok': True, 'csrftoken': get_token(request)})


# ------------------------------------------------------------
# SUPERVISOR VIEWS
# ------------------------------------------------------------
@login_required
@role_required(is_supervisor)
def supervisor_stats(request):
    """JSON endpoint returning the four summary stats for real-time polling."""
    today = timezone.localdate()
    def _parse(key, default):
        v = request.GET.get(key)
        try:
            return date.fromisoformat(v) if v else default
        except Exception:
            return default
    start = _parse('start', today)
    end = _parse('end', today)
    if end < start:
        start, end = end, start

    qs_gen = Ticket.objects.filter(date__range=(start, end))
    total_generated = qs_gen.count()
    # Use qs_gen as the base for all stats so every circle refers to the same
    # population (tickets created in the selected period).
    total_served = qs_gen.filter(status=Ticket.STATUS_SERVED).count()
    total_skipped = qs_gen.filter(status=Ticket.STATUS_SKIPPED).count()
    total_waiting_now = qs_gen.filter(
        status__in=[Ticket.STATUS_WAITING, Ticket.STATUS_CALLING, Ticket.STATUS_TRANSFERRED],
    ).count()

    return JsonResponse({
        'ok': True,
        'total_generated': total_generated,
        'total_served': total_served,
        'total_skipped': total_skipped,
        'total_waiting_now': total_waiting_now,
    })


@login_required
@role_required(is_supervisor)
def supervisor_dashboard(request):
    def parse_date_param(key, default):
        v = request.GET.get(key)
        try:
            return date.fromisoformat(v) if v else default
        except Exception:
            return default

    today = timezone.localdate()
    start = parse_date_param('start', today)
    end = parse_date_param('end', today)
    if end < start:
        start, end = end, start
    gran = request.GET.get('gran', 'day')

    qs_generated = Ticket.objects.filter(date__range=(start, end))
    total_generated = qs_generated.count()
    # All four circles use qs_generated as the base so they describe the same
    # population (tickets created in the selected period).  Previously total_served
    # filtered on served_at which is a different population and caused the counts
    # to not add up.
    total_served = qs_generated.filter(status=Ticket.STATUS_SERVED).count()
    total_skipped = qs_generated.filter(status=Ticket.STATUS_SKIPPED).count()
    # Waiting Now = still-active tickets (WAITING / CALLING / TRANSFERRED) that
    # were created within the selected date range.
    total_waiting_now = qs_generated.filter(
        status__in=[Ticket.STATUS_WAITING, Ticket.STATUS_CALLING, Ticket.STATUS_TRANSFERRED],
    ).count()

    qs_served_in_range = qs_generated.filter(status=Ticket.STATUS_SERVED)

    if gran == 'month':
        gen_series = list(
            qs_generated
            .annotate(month_str=Func(Value('%Y-%m-01'), 'date', function='STRFTIME', output_field=CharField()))
            .values('month_str').annotate(count=Count('id')).order_by('month_str')
        )
        for r in gen_series:
            r['period'] = date.fromisoformat(r.pop('month_str'))

        # Group served tickets (created in range) by the date they were actually served
        srv_series = list(
            qs_served_in_range
            .annotate(month_str=Func(Value('%Y-%m-01'), 'served_at', function='STRFTIME', output_field=CharField()))
            .values('month_str').annotate(count=Count('id')).order_by('month_str')
        )
        for r in srv_series:
            r['period'] = date.fromisoformat(r.pop('month_str'))
    else:
        # Daily: 'date' is already a DateField — group directly, no UDF needed
        gen_series = list(
            qs_generated.values('date')
            .annotate(count=Count('id')).order_by('date')
        )
        for r in gen_series:
            r['period'] = r.pop('date')

        # Group served tickets (created in range) by the date they were actually served
        srv_series = list(
            qs_served_in_range
            .annotate(srv_date=Func('served_at', function='DATE', output_field=DateField()))
            .values('srv_date').annotate(count=Count('id')).order_by('srv_date')
        )
        for r in srv_series:
            r['period'] = r.pop('srv_date')

    # Both tables use qs_served_in_range so their totals match the "Served" circle.
    per_counter = (qs_served_in_range
                   .values('current_counter__name')
                   .annotate(count=Count('id')).order_by('current_counter__name'))
    # Count distinct tickets per user to avoid double-counting recalled tickets.
    per_staff = (CallEvent.objects
                 .filter(action='DONE', ticket__in=qs_served_in_range)
                 .values('user__username')
                 .annotate(count=Count('ticket', distinct=True))
                 .order_by('user__username'))

    recent_generated = qs_generated.select_related('service', 'current_counter').order_by('-created_at')[:100]
    # Prefetch DONE events to avoid N+1 from Ticket.served_by property
    recent_served = (
        qs_served_in_range
        .select_related('service', 'current_counter')
        .prefetch_related(
            Prefetch(
                'events',
                queryset=CallEvent.objects.filter(action='DONE').select_related('user').order_by('-timestamp'),
                to_attr='_done_events',
            )
        )
        .order_by('-served_at')[:100]
    )

    all_counters = Counter.objects.all().order_by('name')

    context = {
        'start': start, 'end': end, 'gran': gran,
        'total_generated': total_generated,
        'total_served': total_served,
        'total_skipped': total_skipped,
        'total_waiting_now': total_waiting_now,
        'gen_series': gen_series,
        'srv_series': srv_series,
        'per_counter': list(per_counter),
        'per_staff': list(per_staff),
        'recent_generated': recent_generated,
        'recent_served': recent_served,
        'all_counters': all_counters,
    }
    return render(request, 'queue/supervisor_dashboard.html', context)


@login_required
@role_required(is_supervisor)
@require_POST
def supervisor_transfer_ticket(request):
    """Allows a supervisor to transfer any ticket to a target counter."""
    try:
        ticket_id = request.POST.get('ticket_id')
        target_counter_id = request.POST.get('counter_id')

        if not ticket_id or not target_counter_id:
            return JsonResponse({'ok': False, 'message': 'Ticket and counter are required.'})

        ticket = get_object_or_404(Ticket, id=ticket_id)
        target_counter = get_object_or_404(Counter, id=target_counter_id)

        if ticket.status in [Ticket.STATUS_SERVED, Ticket.STATUS_CANCELED]:
            return JsonResponse({
                'ok': False,
                'message': f'Ticket {ticket.display_code} is already {ticket.get_status_display().lower()}.'
            })

        ticket.current_counter = target_counter
        ticket.status = Ticket.STATUS_TRANSFERRED
        ticket.save(update_fields=['current_counter', 'status'])

        if not target_counter.current_ticket:
            target_counter.current_ticket = ticket
            target_counter.save(update_fields=['current_ticket'])

        logger.info(
            f"Supervisor {request.user} transferred ticket {ticket.display_code} "
            f"to counter {target_counter.name}"
        )

        return JsonResponse({
            'ok': True,
            'message': f'Ticket {ticket.display_code} transferred to {target_counter.name}.',
        })

    except Exception as e:
        logger.exception("Supervisor transfer error")
        return JsonResponse({'ok': False, 'message': 'An error occurred during transfer.'})


@login_required
@role_required(is_supervisor)
def supervisor_active_tickets_api(request):
    """Return all active (non-finished) tickets as JSON.
    Shows ANY ticket still in play, regardless of creation date.
    This powers the standalone Transfer section — independent of the date-range filter.
    """
    tickets = (
        Ticket.objects.filter(
            status__in=[
                Ticket.STATUS_WAITING,
                Ticket.STATUS_CALLING,
                Ticket.STATUS_SKIPPED,
                Ticket.STATUS_TRANSFERRED,
            ],
        )
        .select_related('service', 'current_counter')
        .order_by('-date', 'created_at')[:200]
    )
    counters = Counter.objects.all().order_by('name')
    return JsonResponse({
        'ok': True,
        'tickets': [
            {
                'id': t.id,
                'code': t.display_code,
                'service': t.service.name,
                'status': t.status,
                'counter': t.current_counter.name if t.current_counter else '–',
            }
            for t in tickets
        ],
        'counters': [{'id': c.id, 'name': c.name} for c in counters],
    })


@login_required
@user_passes_test(is_supervisor)
def supervisor_export_csv(request):
    today = timezone.localdate()
    start_str = request.GET.get('start', str(today))
    end_str = request.GET.get('end', str(today))

    # Validate date format to prevent ORM errors
    try:
        start = date.fromisoformat(start_str)
        end = date.fromisoformat(end_str)
    except (ValueError, TypeError):
        return HttpResponse('Invalid date format. Use YYYY-MM-DD.', status=400)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="queue_report_{start}_to_{end}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Type', 'Period', 'Count'])

    qs_generated = Ticket.objects.filter(date__range=(start, end))
    for row in (qs_generated.values('date')
                .annotate(count=Count('id')).order_by('date')):
        writer.writerow(['Generated', row['date'], row['count']])

    qs_served = Ticket.objects.filter(served_at__date__range=(start, end))
    for row in (qs_served.annotate(srv_date=Func('served_at', function='DATE', output_field=DateField()))
                .values('srv_date').annotate(count=Count('id')).order_by('srv_date')):
        writer.writerow(['Served', row['srv_date'], row['count']])

    return response


# ------------------------------------------------------------
# HEALTH CHECK
# ------------------------------------------------------------
def health_check(request):
    try:
        from django.db import connection
        connection.ensure_connection()
        Ticket.objects.exists()
        return HttpResponse("OK", content_type="text/plain")
    except Exception as e:
        logger.critical(f"Health check failed: {e}")
        return HttpResponse("ERROR", status=500)


# ------------------------------------------------------------
# AUTH VIEWS
# ------------------------------------------------------------
def forgot_password(request):
    return render(request, 'registration/forgot.html')


def logout_view(request):
    logout(request)
    next_url = request.GET.get('next', '/login/')
    # Prevent open redirect — only allow URLs on the same host
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = '/login/'
    return redirect(next_url)


class CustomLoginView(LoginView):
    template_name = 'registration/login.html'


# ------------------------------------------------------------
# PROFILE
# ------------------------------------------------------------
@login_required
def profile(request):
    user = request.user
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        username = request.POST.get('username', '').strip()
        old_password = request.POST.get('old_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        can_update = True

        if new_password or confirm_password:
            if not old_password or not check_password(old_password, user.password):
                messages.error(request, 'Old password is incorrect.')
                can_update = False
            elif not new_password or not confirm_password:
                messages.error(request, 'Please fill both new password fields.')
                can_update = False
            elif new_password != confirm_password:
                messages.error(request, 'New password and confirm password do not match.')
                can_update = False

        if can_update:
            # Check username uniqueness before saving
            if username and username != user.username:
                if User.objects.filter(username=username).exclude(pk=user.pk).exists():
                    messages.error(request, 'That username is already taken.')
                    can_update = False

        if can_update:
            user.first_name = first_name
            user.last_name = last_name
            user.username = username
            fields_to_update = ['first_name', 'last_name', 'username']
            if new_password:
                try:
                    validate_password(new_password, user)
                except ValidationError as errs:
                    messages.error(request, ' '.join(errs.messages))
                    return redirect('profile')
                user.set_password(new_password)
                fields_to_update.append('password')
                update_session_auth_hash(request, user)
            user.save(update_fields=fields_to_update)
            messages.success(request, 'Profile updated successfully.')

        return redirect('profile')

    return render(request, 'queue/profile.html', {'user': user})


# ------------------------------------------------------------
# PASSWORD CHANGE
# FIX: duplicate URL registration in urls.py meant only this view was ever reached.
# Keeping only this one and removing the built-in PasswordChangeView from urls.py.
# ------------------------------------------------------------
@login_required
def password_change_view(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Your password has been updated successfully!')
            return redirect('profile')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = PasswordChangeForm(user=request.user)

    return render(request, 'queue/password_change.html', {'form': form})

@login_required
@role_required(is_staff_user)
def staff_recall_list(request):
    """
    Returns all tickets for this counter that have been processed
    (appeared in a NEXT or RECALL CallEvent) and are not yet
    served, skipped, or canceled.
 
    WHY USE CallEvent INSTEAD OF called_at / current_counter:
    ──────────────────────────────────────────────────────────
    • called_at is only set by claim_next_ticket — tickets recalled
      via the old recall button do not always refresh called_at.
    • current_counter is set on the ticket that is currently active
      but old tickets may have been reassigned or transferred.
    • The only authoritative record of "this counter processed this
      ticket" is the CallEvent table, which logs every NEXT, PREV,
      and RECALL action against a specific counter.
 
    Using CallEvent gives us exactly the set the staff member expects:
    every ticket this counter has ever called, minus those that are
    done or skipped.
    """
    counter_id = request.GET.get('counter_id')
    if not counter_id:
        return JsonResponse({'ok': False, 'tickets': [], 'message': 'No counter specified.'})
 
    try:
        counter = Counter.objects.select_related('current_ticket').get(id=counter_id)
    except Counter.DoesNotExist:
        return JsonResponse({'ok': False, 'tickets': []})
 
    try:
        # Get IDs of all tickets this counter has ever processed
        processed_ids = (
            CallEvent.objects
            .filter(
                counter=counter,
                action__in=['NEXT', 'PREV', 'RECALL'],
            )
            .values_list('ticket_id', flat=True)
            .distinct()
        )
 
        # Fetch those tickets that are still active (not finished).
        # Also exclude tickets that were TRANSFERRED away to a different counter —
        # once a ticket leaves this counter it should not remain in this panel.
        tickets = (
            Ticket.objects
            .filter(id__in=processed_ids)
            .exclude(status__in=[
                Ticket.STATUS_SERVED,
                Ticket.STATUS_SKIPPED,
                Ticket.STATUS_CANCELED,
            ])
            .exclude(
                Q(status=Ticket.STATUS_TRANSFERRED) & ~Q(current_counter=counter)
            )
            .select_related('service')
            .order_by('service__prefix', 'sequence')
        )
 
        current_id = counter.current_ticket_id
 
        data = [
            {
                'id':         t.id,
                'code':       t.display_code,        # e.g. "A001"
                'service':    t.service.name,
                'prefix':     t.service.prefix,      # sent so JS can search on it
                'sequence':   t.sequence,             # sent so JS can search numerically
                'status':     t.status,
                'is_current': t.id == current_id,
            }
            for t in tickets
        ]
 
        return JsonResponse({'ok': True, 'tickets': data})
 
    except Exception as e:
        logger.exception("staff_recall_list error")
        return JsonResponse({'ok': False, 'tickets': [], 'message': 'An unexpected error occurred.'})
 
 
@login_required
@role_required(is_staff_user)
@require_POST
def staff_recall_ticket(request):
    """
    Sets a specific ticket (by id) as the counter's current ticket
    and broadcasts the announcement.
    The ticket must have been previously processed by this counter.
    """
    ticket_id  = request.POST.get('ticket_id')
    counter_id = request.session.get('counter_id')
 
    if not ticket_id or not counter_id:
        return JsonResponse({'ok': False, 'message': 'Missing ticket or counter.'})
 
    try:
        counter = get_object_or_404(
            Counter.objects.select_related('current_ticket'),
            id=counter_id,
        )
 
        # Verify this ticket was actually processed by this counter
        # (security: staff can only recall their own counter's tickets)
        was_processed = CallEvent.objects.filter(
            counter=counter,
            ticket_id=ticket_id,
            action__in=['NEXT', 'PREV', 'RECALL'],
        ).exists()
 
        if not was_processed:
            return JsonResponse({
                'ok': False,
                'message': 'Ticket not found in this counter\'s history.',
            })
 
        ticket = get_object_or_404(
            Ticket.objects.select_related('service'),
            id=ticket_id,
        )
 
        # Guard: don't recall a ticket that's already finished
        if ticket.status in [Ticket.STATUS_SERVED, Ticket.STATUS_SKIPPED, Ticket.STATUS_CANCELED]:
            return JsonResponse({
                'ok': False,
                'message': f'Ticket {ticket.display_code} is already {ticket.get_status_display().lower()}.',
            })
 
        # Set as current and mark CALLING
        counter.current_ticket = ticket
        counter.save(update_fields=['current_ticket'])
 
        if ticket.status != Ticket.STATUS_CALLING:
            ticket.status    = Ticket.STATUS_CALLING
            ticket.called_at = timezone.now()
            ticket.save(update_fields=['status', 'called_at'])
 
        # Log the event
        CallEvent.objects.create(
            ticket=ticket,
            counter=counter,
            action='RECALL',
            user=request.user,
        )
 
        # Broadcast
        broadcast_ticket_call(
            ticket_code=ticket.display_code,
            service_name=ticket.service.name,
            counter_name=counter.name,
        )
 
        counts = counter.tickets.aggregate(
            total=Count('id'),
            # Include TRANSFERRED so received transfers count as waiting
            waiting=Count('id', filter=Q(status__in=[Ticket.STATUS_WAITING, Ticket.STATUS_CALLING, Ticket.STATUS_TRANSFERRED])),
            served=Count('id', filter=Q(status=Ticket.STATUS_SERVED)),
            skipped=Count('id', filter=Q(status=Ticket.STATUS_SKIPPED)),
        )
 
        return JsonResponse({
            'ok': True,
            'message': f'Ticket {ticket.display_code} recalled.',
            'current': {
                'id':      ticket.id,
                'code':    ticket.display_code,
                'service': ticket.service.name,
            },
            'ticket_counts': counts,
        })
 
    except Exception as e:
        logger.exception("staff_recall_ticket error")
        return JsonResponse({'ok': False, 'message': 'An unexpected error occurred.'})