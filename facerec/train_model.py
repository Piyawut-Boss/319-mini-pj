"""Rebuild embeddings.json from dataset/<name>/*.jpg using SFace.
Replaces the old LBPH trainer.yml/labels.json step — there is no classifier
to "train" anymore, just embeddings to (re)compute, but the script is kept
under the same name since app.py's "retrain" button shells out to it."""
import os
import cv2
from face_engine import FaceIdentifier, save_embeddings

BASE = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE, "dataset")

identifier = FaceIdentifier()
gallery = {}

for name in sorted(os.listdir(DATASET_DIR)):
    person_dir = os.path.join(DATASET_DIR, name)
    if not os.path.isdir(person_dir):
        continue
    embeddings = []
    for fname in sorted(os.listdir(person_dir)):
        img = cv2.imread(os.path.join(person_dir, fname))
        if img is None:
            continue
        embeddings.append(identifier.embed(img))
    if embeddings:
        gallery[name] = embeddings

if not gallery:
    raise SystemExit("no training images found in dataset/ — capture some faces first")

save_embeddings(gallery)
print(f"built embeddings for {len(gallery)} people: {list(gallery.keys())}")
for name, embeddings in gallery.items():
    print(f"  {name}: {len(embeddings)} images")
