from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model

User = get_user_model()

def localdate_default():
    return timezone.localdate()

class Service(models.Model):
    """A service type offered at the kiosk (e.g. Admissions, Finance)."""
    name = models.CharField(max_length=100)
    icon_class = models.CharField(max_length=50, default='fas fa-cog')
    code = models.CharField(max_length=10, unique=True)
    prefix = models.CharField(max_length=5, blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class Counter(models.Model):
    """A service counter staffed by one user at a time."""
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    services = models.ManyToManyField(Service, related_name='counters', blank=True)
    current_ticket = models.ForeignKey('Ticket', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    assigned_user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_counter')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class Ticket(models.Model):
    STATUS_WAITING = 'WAITING'
    STATUS_CALLING = 'CALLING'
    STATUS_SERVED = 'SERVED'
    STATUS_SKIPPED = 'SKIPPED'
    STATUS_CANCELED = 'CANCELED'
    STATUS_TRANSFERRED = 'TRANSFERRED'
    STATUS_CHOICES = [
        (STATUS_WAITING, 'Waiting'),
        (STATUS_CALLING, 'Calling'),
        (STATUS_SERVED, 'Served'),
        (STATUS_SKIPPED, 'Skipped'),
        (STATUS_CANCELED, 'Canceled'),
        (STATUS_TRANSFERRED, 'Transferred'),
    ]

    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name='tickets')
    date = models.DateField(default=localdate_default)
    sequence = models.PositiveIntegerField()
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_WAITING)
    created_at = models.DateTimeField(auto_now_add=True)
    called_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)
    current_counter = models.ForeignKey(Counter, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    priority = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ( 'service', 'date', 'sequence' )
        indexes = [
            models.Index(fields=['status', 'service', 'date']),
            models.Index(fields=['created_at']),
        ]
    @property
    def served_by(self):
        # Use prefetched _done_events if available (avoids N+1 in supervisor dashboard)
        if hasattr(self, '_done_events'):
            return self._done_events[0].user if self._done_events else None
        ev = self.events.filter(action='DONE').select_related('user').order_by('-timestamp').first()
        return ev.user if ev else None
    
    @property
    def display_code(self):
        return f"{self.service.prefix}{self.sequence:03d}"

    def __str__(self):
        return f"{self.display_code} ({self.get_status_display()})"

class CallEvent(models.Model):
    """Audit log entry for every staff action on a ticket."""
    ACTIONS = [
        ('NEXT', 'Next'),
        ('PREV', 'Previous'),
        ('RECALL', 'Recall'),
        ('DONE', 'Done'),
        ('SKIP', 'Skip'),
    ]
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='events')
    counter = models.ForeignKey(Counter, on_delete=models.CASCADE, related_name='events')
    action = models.CharField(max_length=10, choices=ACTIONS)
    timestamp = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True, default='')
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='queue_events')

    def __str__(self):
        return f"{self.counter} {self.action} {self.ticket.display_code} @ {self.timestamp:%H:%M:%S}"