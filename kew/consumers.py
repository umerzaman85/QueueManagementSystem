# queue/consumers.py
from channels.generic.websocket import AsyncWebsocketConsumer
import json
import logging

logger = logging.getLogger(__name__)


class CallConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for speaker clients (Python TTS via speaker_client.py).
    Joins call_group to receive ticket call announcements.
    """
    
    async def connect(self):
        # Speaker clients are headless services that only receive broadcast
        # announcements (no sensitive data), so authentication is not required.
        await self.channel_layer.group_add('call_group', self.channel_name)
        await self.accept()
        logger.info(f"Speaker connected: {self.channel_name}")
    
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard('call_group', self.channel_name)
        logger.info(f"Speaker disconnected: {self.channel_name}")
    
    async def receive(self, text_data):
        """Handle messages from client (if needed)"""
        pass
    
    async def call_ticket(self, event):
        """
        Handle ticket call broadcasts from call_group.
        Forwards code/counter/service to the speaker client.
        """
        logger.info(f"Broadcasting to {self.channel_name}: {event['code']} -> {event['counter']}")
        
        await self.send(text_data=json.dumps({
            'code': event['code'],
            'counter': event['counter'],
            'service': event['service'],
        }))