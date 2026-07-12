import logging
import time
from functools import wraps
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.db.models import Max
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Ticket, CallEvent

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------
# Broadcast deduplication
# NOTE: This dict is per-process. Under multi-worker deployments
#       (e.g. multiple Daphne workers) each worker has its own copy,
#       so deduplication only applies within a single process.
#       For multi-worker setups, replace with a Redis-backed solution.
# --------------------------------------------------------------------
_broadcast_lock = {}


def broadcast_ticket_call(ticket_code, service_name, counter_name):
    """
    SINGLE SOURCE OF TRUTH for all ticket announcements.
    Sends to call_group → speaker_client.py (Python TTS process).
    Includes per-process duplicate detection to prevent double-broadcasts.
    """
    key = f"{ticket_code}:{counter_name}"
    now = time.time()

    # Block if the same ticket was broadcast less than 1 second ago
    if key in _broadcast_lock:
        last_time = _broadcast_lock[key]
        if now - last_time < 1.0:
            logger.warning(
                f"⚠️ BLOCKED duplicate broadcast: {ticket_code} → {counter_name} "
                f"(sent {now - last_time:.2f}s ago)"
            )
            return

    _broadcast_lock[key] = now

    # Purge stale entries older than 5 seconds
    stale = [k for k, v in _broadcast_lock.items() if now - v > 5.0]
    for k in stale:
        del _broadcast_lock[k]

    try:
        channel_layer = get_channel_layer()

        logger.info(f"📢 BROADCASTING: {ticket_code} → {counter_name}")

        # ── Speaker clients (Python TTS process via speaker_client.py) ──
        async_to_sync(channel_layer.group_send)(
            'call_group',
            {
                'type': 'call_ticket',
                'code': ticket_code,
                'service': service_name,
                'counter': counter_name,
            }
        )

        logger.info(f"Broadcast complete: {ticket_code}")

    except Exception as e:
        logger.error(f"❌ Broadcast failed: {e}")


# --------------------------------------------------------------------
# Ticket creation
# --------------------------------------------------------------------
def create_ticket(service, priority=0, max_retries=5):
    """Creates a new ticket with sequential numbering per service/day."""
    today = timezone.localdate()
    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                max_seq = (
                    Ticket.objects.filter(service=service, date=today)
                    .aggregate(Max('sequence'))['sequence__max'] or 0
                )
                seq = max_seq + 1
                ticket = Ticket.objects.create(
                    service=service,
                    date=today,
                    sequence=seq,
                    priority=priority,
                )
                logger.info(f"Created ticket #{seq} for service {service.id}")
                return ticket
        except IntegrityError as e:
            logger.warning(f"Ticket creation collision on attempt {attempt + 1}: {e}")
            continue

    raise IntegrityError("Could not create ticket after multiple retries.")


# --------------------------------------------------------------------
# Claim next ticket
# --------------------------------------------------------------------
def claim_next_ticket(counter, user=None):
    """Claims next waiting ticket for the counter. Broadcasts ONCE."""
    services = counter.services.all()
    if not services.exists():
        logger.info(f"Counter {counter.id} has no services assigned")
        return None

    with transaction.atomic():
        candidate = (
            Ticket.objects.select_for_update(skip_locked=True)
            .filter(status=Ticket.STATUS_WAITING, service__in=services)
            .order_by('priority', 'created_at')
            .first()
        )

        if not candidate:
            logger.info(f"No waiting tickets for counter {counter.id}")
            counter.current_ticket = None
            counter.save(update_fields=['current_ticket'])
            return None

        logger.info(f"Counter {counter.id} claiming ticket {candidate.id} ({candidate.display_code})")

        candidate.status = Ticket.STATUS_CALLING
        candidate.current_counter = counter
        candidate.called_at = timezone.now()
        candidate.save(update_fields=['status', 'current_counter', 'called_at'])

        counter.current_ticket = candidate
        counter.save(update_fields=['current_ticket'])

        if user:
            CallEvent.objects.create(
                ticket=candidate,
                counter=counter,
                action='NEXT',
                user=user,
            )

    # Broadcast ONCE after transaction commits
    logger.info(f"About to broadcast: {candidate.display_code}")
    broadcast_ticket_call(
        ticket_code=candidate.display_code,
        service_name=candidate.service.name,
        counter_name=counter.name,
    )
    return candidate


# --------------------------------------------------------------------
# Claim transferred ticket
# --------------------------------------------------------------------
def claim_transferred_ticket(counter, user=None):
    """
    Claims the oldest ticket that was transferred to this counter.
    FIX: previously the NEXT/PREV views fetched a transferred ticket
    but never updated its status, counter assignment, or broadcast it.
    This function is the single correct path for that logic.
    """
    with transaction.atomic():
        t = (
            counter.tickets
            .select_for_update(skip_locked=True)
            .filter(status=Ticket.STATUS_TRANSFERRED)
            .order_by('created_at')
            .first()
        )
        if not t:
            return None

        logger.info(f"Counter {counter.id} claiming transferred ticket {t.id} ({t.display_code})")

        t.status = Ticket.STATUS_CALLING
        t.current_counter = counter
        t.called_at = timezone.now()
        t.save(update_fields=['status', 'current_counter', 'called_at'])

        counter.current_ticket = t
        counter.save(update_fields=['current_ticket'])

        if user:
            CallEvent.objects.create(
                ticket=t,
                counter=counter,
                action='NEXT',
                user=user,
            )

    broadcast_ticket_call(
        ticket_code=t.display_code,
        service_name=t.service.name,
        counter_name=counter.name,
    )
    return t


# --------------------------------------------------------------------
# Claim previous ticket
# --------------------------------------------------------------------
def claim_previous_ticket(counter, user=None):
    """
    Re-calls the ticket that was served just before the current one.
    FIX: original code used order_by('-created_at') then did index+1,
    which moved FORWARD in time (i.e. to a newer ticket) instead of
    backward. Corrected to order ascending and use index-1.
    """
    services = counter.services.all()
    if not services.exists():
        return None

    with transaction.atomic():
        # Ascending order: index 0 = oldest, last = newest
        tickets = list(
            Ticket.objects.select_for_update(skip_locked=True)
            .filter(service__in=services)
            .exclude(status__in=[Ticket.STATUS_SERVED, Ticket.STATUS_SKIPPED])
            .order_by('created_at')  # FIX: was '-created_at'
        )

        if not tickets:
            return None

        current = counter.current_ticket

        if not current:
            t = tickets[-1]  # Most recently created if nothing is active
        else:
            try:
                index = next(i for i, ticket in enumerate(tickets) if ticket.id == current.id)
            except StopIteration:
                t = tickets[-1]
            else:
                if index == 0:
                    # Already at the oldest ticket, nowhere to go back
                    return None
                t = tickets[index - 1]  # FIX: was index + 1

        logger.info(f"Counter {counter.id} claiming previous ticket {t.id} ({t.display_code})")

        if t.status == Ticket.STATUS_WAITING:
            t.status = Ticket.STATUS_CALLING
            t.called_at = timezone.now()
            t.current_counter = counter
            t.save(update_fields=['status', 'current_counter', 'called_at'])

        counter.current_ticket = t
        counter.save(update_fields=['current_ticket'])

        if user:
            CallEvent.objects.create(
                ticket=t,
                counter=counter,
                action='PREV',
                user=user,
            )

    broadcast_ticket_call(
        ticket_code=t.display_code,
        service_name=t.service.name,
        counter_name=counter.name,
    )
    return t


# --------------------------------------------------------------------
# Recall current ticket
# --------------------------------------------------------------------
def recall_current(counter, user=None):
    """Recalls the current ticket. Broadcasts ONCE."""
    t = counter.current_ticket
    if not t:
        logger.warning(f"Counter {counter.id} tried to recall but has no current ticket")
        return None

    logger.info(f"Counter {counter.id} recalling ticket {t.id} ({t.display_code})")

    CallEvent.objects.create(
        ticket=t,
        counter=counter,
        action='RECALL',
        user=user,
    )

    broadcast_ticket_call(
        ticket_code=t.display_code,
        service_name=t.service.name,
        counter_name=counter.name,
    )
    return t


# --------------------------------------------------------------------
# Mark done
# --------------------------------------------------------------------
def mark_done(counter, user=None):
    """Marks current ticket as served. No broadcast."""
    t = counter.current_ticket
    if not t:
        return None

    logger.info(f"Counter {counter.id} marking ticket {t.id} ({t.display_code}) as DONE")

    Ticket.objects.filter(id=t.id).update(
        status=Ticket.STATUS_SERVED,
        served_at=timezone.now(),
    )
    CallEvent.objects.create(
        ticket=t,
        counter=counter,
        action='DONE',
        user=user,
    )
    counter.current_ticket = None
    counter.save(update_fields=['current_ticket'])
    return t


# --------------------------------------------------------------------
# Skip current ticket
# --------------------------------------------------------------------
def skip_current(counter, user=None):
    """Skips current ticket. No broadcast."""
    t = counter.current_ticket
    if not t:
        return None

    logger.info(f"Counter {counter.id} skipping ticket {t.id} ({t.display_code})")

    Ticket.objects.filter(id=t.id).update(status=Ticket.STATUS_SKIPPED)
    CallEvent.objects.create(
        ticket=t,
        counter=counter,
        action='SKIP',
        user=user,
    )
    counter.current_ticket = None
    counter.save(update_fields=['current_ticket'])
    return t


# --------------------------------------------------------------------
# Logging helpers
# --------------------------------------------------------------------
def log_action(action: str):
    """Decorator for logging staff actions. Preserves the wrapped function's name."""

    def _decorator(func):
        @wraps(func)
        def _wrapped(request, *args, **kwargs):
            counter_id = request.session.get("counter_id")
            user = request.user
            logger.info(f"Staff action: {action} by {user} on counter {counter_id}")
            return func(request, *args, **kwargs)
        return _wrapped
    return _decorator