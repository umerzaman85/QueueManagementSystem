#!/usr/bin/env python3
"""
Production Server Diagnostic Script
Run this on your production Windows server to identify issues
"""

import sys
import os

print("=" * 70)
print("🔍 SPEAKER CLIENT DIAGNOSTICS")
print("=" * 70)

# 1. Python Version
print("\n1️⃣ PYTHON VERSION")
print(f"   Python: {sys.version}")
print(f"   Platform: {sys.platform}")

# 2. Check Required Packages
print("\n2️⃣ CHECKING REQUIRED PACKAGES")
packages = ['pyttsx3', 'channels_redis', 'redis', 'asyncio']
for pkg in packages:
    try:
        __import__(pkg)
        print(f"   ✓ {pkg}")
    except ImportError:
        print(f"   ❌ {pkg} - NOT INSTALLED")
        print(f"      Install: pip install {pkg}")

# 3. Check winsound
print("\n3️⃣ CHECKING WINSOUND (Windows Audio)")
try:
    import winsound
    print("   ✓ winsound available")
    try:
        winsound.Beep(1000, 200)
        print("   ✓ Audio output working (beep test passed)")
    except Exception as e:
        print(f"   ❌ Audio output FAILED: {e}")
except ImportError:
    print("   ❌ winsound not available (not Windows?)")

# 4. Check TTS Engine
print("\n4️⃣ CHECKING TTS ENGINE")
try:
    import pyttsx3
    engine = pyttsx3.init()
    voices = engine.getProperty('voices')
    print(f"   ✓ pyttsx3 initialized")
    print(f"   Found {len(voices)} voice(s):")
    
    for i, voice in enumerate(voices, 1):
        print(f"\n   Voice {i}:")
        print(f"     Name: {voice.name}")
        print(f"     ID: {voice.id}")
        print(f"     Languages: {voice.languages}")
        
        # Check for female voice
        if "female" in voice.name.lower() or "female" in voice.id.lower():
            print(f"     👩 FEMALE VOICE")
        
        # Check for Urdu
        if "urdu" in voice.name.lower() or "ur-" in voice.id.lower():
            print(f"     🇵🇰 URDU VOICE")
    
    # Test speaking
    print("\n   Testing TTS...")
    try:
        engine.say("Test")
        engine.runAndWait()
        print("   ✓ TTS test successful")
    except Exception as e:
        print(f"   ❌ TTS test FAILED: {e}")
    
    engine.stop()
    
except Exception as e:
    print(f"   ❌ TTS initialization FAILED: {e}")

# 5. Check chime.wav
print("\n5️⃣ CHECKING CHIME FILE")
CHIME_FILE = "chime.wav"
if os.path.exists(CHIME_FILE):
    abs_path = os.path.abspath(CHIME_FILE)
    size = os.path.getsize(CHIME_FILE)
    print(f"   ✓ Chime file found")
    print(f"     Path: {abs_path}")
    print(f"     Size: {size} bytes")
    
    # Test playing chime
    try:
        import winsound
        winsound.PlaySound(CHIME_FILE, winsound.SND_FILENAME)
        print(f"   ✓ Chime playback successful")
    except Exception as e:
        print(f"   ❌ Chime playback FAILED: {e}")
else:
    print(f"   ❌ Chime file NOT FOUND")
    print(f"     Looking in: {os.path.abspath('.')}")
    print(f"     Please copy chime.wav to this directory")

# 6. Check Redis Connection
print("\n6️⃣ CHECKING REDIS CONNECTION")
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379

try:
    import redis
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_connect_timeout=2)
    r.ping()
    print(f"   ✓ Redis connected ({REDIS_HOST}:{REDIS_PORT})")
    
    # Get Redis info
    info = r.info()
    print(f"   Redis version: {info['redis_version']}")
    print(f"   Connected clients: {info['connected_clients']}")
    
except Exception as e:
    print(f"   ❌ Redis connection FAILED: {e}")
    print(f"   Check:")
    print(f"     - Is Redis running? (net start Redis)")
    print(f"     - Is port {REDIS_PORT} open? (netstat -an | findstr {REDIS_PORT})")
    print(f"     - Firewall blocking connection?")

# 7. Check Channels Redis
print("\n7️⃣ CHECKING CHANNELS REDIS")
try:
    from channels_redis.core import RedisChannelLayer
    channel_layer = RedisChannelLayer(hosts=[(REDIS_HOST, REDIS_PORT)])
    print("   ✓ RedisChannelLayer initialized")
    
    # Test async functionality
    import asyncio
    
    async def test_channel():
        try:
            channel_name = await channel_layer.new_channel()
            await channel_layer.group_add("test_group", channel_name)
            await channel_layer.group_discard("test_group", channel_name)
            return True
        except Exception as e:
            print(f"   ❌ Channel test FAILED: {e}")
            return False
    
    success = asyncio.run(test_channel())
    if success:
        print("   ✓ Channel operations working")
    
except Exception as e:
    print(f"   ❌ Channels Redis FAILED: {e}")

# 8. Check Windows Audio Service
print("\n8️⃣ CHECKING WINDOWS AUDIO SERVICE")
try:
    import subprocess
    result = subprocess.run(
        ['sc', 'query', 'Audiosrv'],
        capture_output=True,
        text=True
    )
    
    if 'RUNNING' in result.stdout:
        print("   ✓ Windows Audio service is running")
    else:
        print("   ❌ Windows Audio service NOT running")
        print("   Start it with: net start Audiosrv")
        
except Exception as e:
    print(f"   ⚠ Could not check audio service: {e}")

# 9. Summary
print("\n" + "=" * 70)
print("📊 DIAGNOSTIC SUMMARY")
print("=" * 70)
print("\nIf you see ❌ errors above, fix them in order:")
print("  1. Install missing packages (pip install)")
print("  2. Ensure Redis is running")
print("  3. Copy chime.wav to script directory")
print("  4. Start Windows Audio service")
print("  5. Check TTS voices are available")
print("\nIf all checks pass ✓ but still not working:")
print("  - Check if running as a service (services can't access audio)")
print("  - Verify user permissions")
print("  - Check antivirus/firewall settings")
print("=" * 70)