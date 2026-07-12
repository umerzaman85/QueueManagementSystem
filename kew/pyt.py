import pyttsx3

print("Testing pyttsx3...")
engine = pyttsx3.init()
print("Engine initialized")

# Set voice to Zira (female)
voices = engine.getProperty('voices')
engine.setProperty('voice', voices[1].id)  # Zira is index 1
print(f"Voice set to: {voices[1].name}")

# Test speech
engine.setProperty('rate', 160)
engine.setProperty('volume', 1.0)

print("About to speak...")
engine.say("Testing one two three")
engine.runAndWait()
print("Speech completed")