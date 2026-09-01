import sys
import os
import time
import cv2
from picamera2 import Picamera2

# TODO: confirm orientation once camera is reconnected — pick whichever
# makes faces appear upright: ROTATE_90_CLOCKWISE / ROTATE_90_COUNTERCLOCKWISE / None
ROTATE = None


def main():
    if len(sys.argv) < 2:
        print("usage: capture_faces.py <name> [num_images]")
        sys.exit(1)
    name = sys.argv[1]
    num = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    base = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(base, "dataset", name)
    os.makedirs(out_dir, exist_ok=True)

    cascade = cv2.CascadeClassifier(os.path.join(base, "haarcascade_frontalface_default.xml"))

    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(main={"size": (1280, 720), "format": "RGB888"}))
    picam2.start()
    time.sleep(1)

    count = 0
    while count < num:
        frame = picam2.capture_array()
        if ROTATE is not None:
            frame = cv2.rotate(frame, ROTATE)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(80, 80))
        for (x, y, w, h) in faces:
            face = gray[y:y + h, x:x + w]
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
