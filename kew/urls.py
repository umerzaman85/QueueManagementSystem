from django.urls import path
from . import views
from .views import CustomLoginView, logout_view

urlpatterns = [
    # Kiosk
    path('', views.kiosk, name='kiosk'),
    path('kiosk/print', views.kiosk_print, name='kiosk_print'),

    # Staff Dashboard
    path('after-login/', views.after_login, name='after_login'),
    path('staff/', views.staff_dashboard, name='staff_dashboard'),
    path('staff/action', views.staff_action, name='staff_action'),

    # Supervisor
    path('supervisor/', views.supervisor_dashboard, name='supervisor_dashboard'),
    path('supervisor/stats/', views.supervisor_stats, name='supervisor_stats'),
    path('supervisor/export', views.supervisor_export_csv, name='supervisor_export_csv'),
    path('supervisor/transfer/', views.supervisor_transfer_ticket, name='supervisor_transfer_ticket'),
    path('supervisor/active-tickets/', views.supervisor_active_tickets_api, name='supervisor_active_tickets'),
    path('health/', views.health_check, name='health_check'),

    # Custom Forgot Password (your own view)
    path('forgot/', views.forgot_password, name='forgot_password'),

    # ------------------------------
    # 🔐 Authentication handled here
    # ------------------------------
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('password_change/', views.password_change_view, name='password_change'),
    path('staff/transfer_ticket/', views.staff_transfer_ticket, name='staff_transfer_ticket'),
    path('staff/transfer-list/', views.staff_transfer_list, name='staff_transfer_list'),
    path('api/forgot-password/', views.forgot_password_api, name='forgot_password_api'),
    path('ping/', views.ping, name='kiosk_ping'),
    path('staff/current-ticket/', views.staff_current_ticket, name='staff_current_ticket'),
    path('staff/recall-list/',   views.staff_recall_list,   name='staff_recall_list'),
    path('staff/recall-ticket/', views.staff_recall_ticket, name='staff_recall_ticket'),
]
