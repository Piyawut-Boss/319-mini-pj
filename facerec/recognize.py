import os
import json
import time
import cv2
from picamera2 import Picamera2

# keep this in sync with capture_faces.py
ROTATE = None

# LBPH distance: lower = better match. Tune after seeing real confidence values.
CONFIDENCE_THRESHOLD = 70

base = os.path.dirname(os.path.abspath(__file__))
cascade = cv2.CascadeClassifier(os.path.join(base, "haarcascade_frontalface_default.xml"))
recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.read(os.path.join(base, "trainer.yml"))
with open(os.path.join(base, "labels.json")) as f:
    label_map = {int(k): v for k, v in json.load(f).items()}

picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (1280, 720), "format": "RGB888"}))
picam2.start()
time.sleep(1)

print("watching... Ctrl+C to stop")
try:
    while True:
        frame = picam2.capture_array()
        if ROTATE is not None:
            frame = cv2.rotate(frame, ROTATE)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(80, 80))
        for (x, y, w, h) in faces:
            face = cv2.resize(gray[y:y + h, x:x + w], (200, 200))
            label, confidence = recognizer.predict(face)
            if confidence < CONFIDENCE_THRESHOLD:
                name = label_map.get(label, "unknown")
                print(f"MATCH: {name} (confidence {confidence:.1f})")
            else:
                print(f"unknown face (confidence {confidence:.1f})")
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
finally:
    picam2.stop()
