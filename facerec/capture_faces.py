import sys
import os
import time
import cv2
from picamera2 import Picamera2
from face_engine import FaceDetector

# keep this in sync with app.py / recognize.py
ROTATE = None


def main():
    if len(sys.argv) < 2:
        print("usage: capture_faces.py <name> [num_images]")
        sys.exit(1)
    name = sys.argv[1]
    num = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    base = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(base, "dataset", name)
    os.makedirs(out_dir, exist_ok=True)

    detector = FaceDetector()

    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(main={"size": (1280, 720), "format": "RGB888"}))
    picam2.start()
    time.sleep(1)

    count = 0
    while count < num:
        frame = picam2.capture_array()
        if ROTATE is not None:
            frame = cv2.rotate(frame, ROTATE)
        h, w = frame.shape[:2]
        faces = detector.detect(frame)
        for (x, y, fw, fh, _score) in faces:
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + fw), min(h, y + fh)
            face = frame[y0:y1, x0:x1]
            if face.size == 0:
                continue
            face = cv2.resize(face, (200, 200))
            count += 1
            cv2.imwrite(os.path.join(out_dir, f"{count:03d}.jpg"), face)
            print(f"saved {count}/{num}")
            time.sleep(0.3)
            break
        if count >= num:
            break

    picam2.stop()
    print("done")


if __name__ == "__main__":
    main()
