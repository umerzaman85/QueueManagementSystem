# queue/routing.py
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # Speaker clients (Python TTS via speaker_client.py)
    re_path(r'^ws/call/$', consumers.CallConsumer.as_asgi()),
]