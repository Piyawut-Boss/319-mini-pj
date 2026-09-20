"""Shared face-detection (self-trained YOLO26n) and face-identification
(OpenCV SFace) helpers used by app.py, train_model.py, capture_faces.py and
recognize.py."""
import os
import json
import cv2
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
SFACE_MODEL_PATH = os.path.join(BASE, "models", "face_recognition_sface_2021dec.onnx")
YOLO_MODEL_PATH = os.path.join(BASE, "models", "yolo26n_face.onnx")
EMBEDDINGS_PATH = os.path.join(BASE, "embeddings.json")

# OpenCV Zoo's own recommended cosine-similarity accept threshold for this model.
MATCH_THRESHOLD = 0.363

YOLO_INPUT_SIZE = 640
YOLO_CONF_THRESHOLD = 0.4
YOLO_NMS_THRESHOLD = 0.45


class FaceDetector:
    """Wraps our own YOLO26n-face ONNX model (trained on WIDER FACE, see
    D:\\yolo26-face-training on the dev machine) via cv2.dnn. The export is
    raw YOLO output (1, 5, 8400) — [cx, cy, w, h, conf] per anchor in
    letterboxed 640x640 space, no built-in NMS — so decoding + NMS happens
    here."""

    def __init__(self):
        self._net = cv2.dnn.readNetFromONNX(YOLO_MODEL_PATH)

    def detect(self, frame_bgr):
        """Returns a list of (x, y, w, h, score) boxes in frame_bgr's own
        pixel coordinates, already NMS-deduplicated."""
        h, w = frame_bgr.shape[:2]
        scale = min(YOLO_INPUT_SIZE / w, YOLO_INPUT_SIZE / h)
        nw, nh = int(w * scale), int(h * scale)
        canvas = np.zeros((YOLO_INPUT_SIZE, YOLO_INPUT_SIZE, 3), dtype=np.uint8)
        canvas[:nh, :nw] = cv2.resize(frame_bgr, (nw, nh))

        blob = cv2.dnn.blobFromImage(canvas, scalefactor=1 / 255.0, size=(YOLO_INPUT_SIZE, YOLO_INPUT_SIZE), swapRB=True, crop=False)
        self._net.setInput(blob)
        out = self._net.forward()  # (1, 5, 8400)

        preds = out[0].T  # (8400, 5)
        boxes, scores = [], []
        for cx, cy, bw, bh, conf in preds:
            if conf < YOLO_CONF_THRESHOLD:
                continue
            x = (cx - bw / 2) / scale
            y = (cy - bh / 2) / scale
            boxes.append([int(x), int(y), int(bw / scale), int(bh / scale)])
            scores.append(float(conf))

        if not boxes:
            return []

        idxs = cv2.dnn.NMSBoxes(boxes, scores, YOLO_CONF_THRESHOLD, YOLO_NMS_THRESHOLD)
        results = []
        for i in np.array(idxs).flatten():
            x, y, bw, bh = boxes[i]
            results.append((x, y, bw, bh, scores[i]))
        return results


class FaceIdentifier:
    """Wraps cv2.FaceRecognizerSF: turns a BGR face crop into a 128-d embedding
    and matches it against a gallery of previously-registered embeddings.

    No 5-point landmark alignment is used — the registration flow only has a
    plain bounding-box crop from YOLO, not eye/nose/mouth landmarks — so crops
    are just resized to 112x112 directly. This costs some accuracy but still
    separates people clearly in practice (verified: same-person cosine
    ~0.86-0.94, different-person ~0.22-0.31 vs the 0.363 threshold)."""

    def __init__(self):
        self._recognizer = cv2.FaceRecognizerSF.create(SFACE_MODEL_PATH, "")

    def embed(self, face_bgr):
        face = cv2.resize(face_bgr, (112, 112))
        return self._recognizer.feature(face)

    def similarity(self, emb_a, emb_b):
        return self._recognizer.match(emb_a, emb_b, cv2.FaceRecognizerSF_FR_COSINE)

    def best_match(self, embedding, gallery):
        """gallery: dict[name] -> list of embeddings (as produced by embed()).
        Returns (name, score) for the closest person, or (None, score) if the
        best score doesn't clear MATCH_THRESHOLD. score is always returned so
        callers can show/log it even on a non-match."""
        best_name, best_score = None, -1.0
        for name, embeddings in gallery.items():
            for stored in embeddings:
                score = self.similarity(embedding, stored)
                if score > best_score:
                    best_name, best_score = name, score
        if best_score >= MATCH_THRESHOLD:
            return best_name, best_score
        return None, best_score


def load_embeddings():
    if os.path.exists(EMBEDDINGS_PATH):
        with open(EMBEDDINGS_PATH) as f:
            raw = json.load(f)
        return {name: [np.array(v, dtype="float32").reshape(1, -1) for v in vs] for name, vs in raw.items()}
    return {}


def save_embeddings(gallery):
    serializable = {name: [v.reshape(-1).tolist() for v in vs] for name, vs in gallery.items()}
    with open(EMBEDDINGS_PATH, "w") as f:
        json.dump(serializable, f)
