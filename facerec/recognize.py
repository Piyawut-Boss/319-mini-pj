import time
import cv2
from picamera2 import Picamera2
from face_engine import FaceDetector, FaceIdentifier, load_embeddings

# keep this in sync with app.py / capture_faces.py
ROTATE = None

detector = FaceDetector()
identifier = FaceIdentifier()
gallery = load_embeddings()

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
        h, w = frame.shape[:2]
        faces = detector.detect(frame)
        for (x, y, fw, fh, det_score) in faces:
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + fw), min(h, y + fh)
            face = frame[y0:y1, x0:x1]
            if face.size == 0:
                continue
            embedding = identifier.embed(face)
            name, sim = identifier.best_match(embedding, gallery)
            if name is not None:
                print(f"MATCH: {name} (similarity {sim:.3f}, det {det_score:.2f})")
            else:
                print(f"unknown face (best similarity {sim:.3f}, det {det_score:.2f})")
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
finally:
    picam2.stop()
