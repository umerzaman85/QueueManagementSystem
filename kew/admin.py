from django.contrib import admin
from .models import Service, Counter, Ticket, CallEvent

@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'prefix', 'is_active')
    search_fields = ('name', 'code')

@admin.register(Counter)
class CounterAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'assigned_user')
    filter_horizontal = ('services',)
    search_fields = ('name', 'code', 'assigned_user__username', 'assigned_user__first_name', 'assigned_user__last_name')

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('display_code', 'service', 'date', 'status', 'current_counter', 'created_at', 'called_at', 'served_at')
    list_filter = ('service', 'status', 'date')
    search_fields = ('sequence',)

@admin.register(CallEvent)
class CallEventAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'counter', 'action', 'user', 'timestamp')
    list_filter = ('action', 'counter', 'user')