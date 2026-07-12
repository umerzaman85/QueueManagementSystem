"""
ASGI config for qms_project project.
"""

import os

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import kew.routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'qms_project.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(kew.routing.websocket_urlpatterns)
    ),
})