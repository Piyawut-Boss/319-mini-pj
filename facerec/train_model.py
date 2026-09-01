import os
import json
import cv2
import numpy as np

base = os.path.dirname(os.path.abspath(__file__))
dataset_dir = os.path.join(base, "dataset")

faces = []
labels = []
label_map = {}

for idx, name in enumerate(sorted(os.listdir(dataset_dir))):
    person_dir = os.path.join(dataset_dir, name)
    if not os.path.isdir(person_dir):
        continue
    label_map[idx] = name
    for fname in os.listdir(person_dir):
        img = cv2.imread(os.path.join(person_dir, fname), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        faces.append(img)
        labels.append(idx)

if not faces:
    raise SystemExit("no training images found in dataset/ — run capture_faces.py first")

recognizer = cv2.face.LBPHFaceRecognizer_create()
recognizer.train(faces, np.array(labels))
recognizer.save(os.path.join(base, "trainer.yml"))

with open(os.path.join(base, "labels.json"), "w") as f:
    json.dump(label_map, f)

print(f"trained on {len(faces)} images, {len(label_map)} people: {list(label_map.values())}")
