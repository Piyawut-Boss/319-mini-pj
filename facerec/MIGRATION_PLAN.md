# แทน Haar Cascade + LBPH ด้วย YOLO26n (self-trained) + SFace

## Context

ระบบ door access บน `jocasta` (`/home/jocasta/facerec/app.py`, Tkinter kiosk app คุมกล้อง Picamera2 + Arduino RFID) ปัจจุบันตรวจจับหน้าด้วย `cv2.CascadeClassifier` (Haar) และจดจำตัวตนด้วย `cv2.face.LBPHFaceRecognizer_create()` เทรนจากรูปที่ถ่ายตอนลงทะเบียน ผู้ใช้ต้องการเปลี่ยนไปใช้ **YOLO26 Nano** แทนทั้ง pipeline (ตรวจจับ + จดจำ) เพื่อความแม่นยำและความเร็วที่ดีกว่า โดยเฉพาะบน Pi 5 (CPU-only) ที่ YOLO26 ถูกออกแบบมาให้เร็วเป็นพิเศษ

YOLO26 official (Ultralytics) เทรนบน COCO ไม่มีคลาส "หน้า" ผู้ใช้เลือกให้ **เทรน YOLO26n เองด้วย WIDER FACE dataset** (ไม่ใช้ไฟล์โมเดลสำเร็จรูปจาก GitHub repo บุคคลที่สาม เพื่อเลี่ยงความเสี่ยง supply-chain บนระบบที่คุมประตูจริง) ส่วนการ "จดจำว่าใครเป็นใคร" หลัง detect แล้ว จะใช้ **OpenCV SFace** (`cv2.FaceRecognizerSF`) ซึ่งเป็นโมเดลทางการจาก OpenCV Zoo ที่ opencv-contrib บน Pi รองรับอยู่แล้ว (ยืนยันแล้ว: `opencv-contrib-python-headless 5.0.0.93` บน Python 3.13.5, aarch64 มี `FaceRecognizerSF` และ `FaceDetectorYN`)

**สภาพแวดล้อมที่ยืนยันแล้ว:**
- เครื่อง dev (Windows, `D:\`): มี GPU NVIDIA RTX A1000 Laptop (4GB VRAM, driver รองรับ CUDA 13.2), Python 3.12.4 — ใช้เทรนโมเดล (เร็วกว่า Pi มาก)
- `jocasta` (Pi 5): ใช้รันจริงอย่างเดียว (inference only), มี venv ที่ `/home/jocasta/facerec/venv`, disk ว่าง 98G, ต่อเน็ตได้
- โปรเจกต์บน jocasta **ไม่ได้อยู่ใน git** — จะ `git init` + commit สถานะปัจจุบันก่อนแก้ไข เพื่อให้ rollback ได้
- `app.py` รันเป็น Tkinter kiosk แบบ manual (ไม่มี systemd service/autostart ที่หาเจอ) มี X session (labwc, Wayland) ทำงานอยู่ที่ seat0 อยู่แล้ว ทดสอบ GUI จริงจะรันผ่าน SSH โดยตั้ง `DISPLAY`/`WAYLAND_DISPLAY` ให้ตรงกับ session นั้น

## แผนการทำงาน

### 1. เตรียม dataset (บนเครื่อง Windows)
- โหลด WIDER FACE จาก Hugging Face `CUHK-CSE/wider_face` (แหล่งทางการจากผู้สร้าง dataset เอง มี bbox เป็น COCO format อยู่แล้ว ไม่ต้องแกะ .mat file)
- เขียนสคริปต์แปลง annotation → YOLO txt format (class 0 = face, x_center y_center w h แบบ normalized) เก็บโครงสร้างเป็น `images/train`, `images/val`, `labels/train`, `labels/val`
- สร้าง `widerface.yaml` (nc: 1, names: ["face"])

### 2. เทรนโมเดล (บนเครื่อง Windows, ใช้ GPU)
- ตั้ง venv ใหม่ ติดตั้ง `ultralytics` + torch ที่ build มาพร้อม CUDA (ตรวจสอบ `torch.cuda.is_available()` หลังติดตั้ง)
- เทรน `yolo26n.pt` (official, COCO-pretrained) ต่อด้วย `model.train(data="widerface.yaml", epochs=100, imgsz=640, patience=20)` — รันเป็น background process เพราะใช้เวลาหลายชั่วโมง จะมอนิเตอร์ log เป็นระยะ
- หลังเทรนเสร็จ: ดู mAP บน validation split, export เป็น ONNX (`model.export(format="onnx")`) แล้วทดสอบโหลดด้วย `cv2.dnn.readNetFromONNX` บน Windows ก่อน (debug ง่ายกว่าไปดีบักบน Pi)

### 3. เตรียมโมเดล recognition (SFace)
- ดาวน์โหลด `face_recognition_sface_2021dec.onnx` จาก opencv_zoo (official OpenCV org) มาไว้ที่ `/home/jocasta/facerec/models/` บน jocasta

### 4. แก้ไขโค้ดบน jocasta (`/home/jocasta/facerec/`)
- `git init` + commit baseline ก่อนแก้ไขไฟล์ใดๆ
- ย้าย `yolo26n_face.onnx` (จากขั้นตอน 2) และ `face_recognition_sface_2021dec.onnx` ไปที่ `models/`
- **`app.py`**: (ไฟล์หลักที่ใช้งานจริง)
  - แทน `self.cascade = cv2.CascadeClassifier(...)` ด้วยตัวโหลด YOLO26n-face ผ่าน `cv2.dnn.readNetFromONNX` (output แบบ NMS-free `(1,300,6)` = x1,y1,x2,y2,conf,class_id ตามที่ YOLO26 export)
  - เพิ่มตัวโหลด `cv2.FaceRecognizerSF.create(...)` สำหรับ embedding
  - แทนที่ `.app.lock`-based `_load_recognizer()` (อ่าน `trainer.yml`/`labels.json`) ด้วยการอ่าน `embeddings.json` ใหม่ (mapping name → embedding vector เฉลี่ยหรือ list of vectors)
  - `_update_preview()`: เปลี่ยนจาก Haar detect + LBPH predict เป็น YOLO26n detect ทุกกล่อง → crop+resize 112×112 (ไม่มี 5-point landmark alignment เพราะ WIDER FACE ไม่มี landmark annotation — ยอมรับความแม่นยำที่ลดลงเล็กน้อยจากการไม่ align) → SFace `.feature()` → เทียบ cosine similarity กับ embeddings ที่เก็บไว้ (`cv2.FaceRecognizerSF.match(..., cv2.FaceRecognizerSF_FR_COSINE)`, threshold แนะนำจาก OpenCV Zoo ~0.363) → เขียว/แดงเหมือนเดิม
  - `save_person()`/`start_capture()`: เก็บภาพสี (ไม่ crop เป็น grayscale เหมือนเดิม เพราะ SFace ต้องการภาพสี) แล้วคำนวณ embedding ทันทีตอนถ่าย เก็บลง `embeddings.json`
  - `retrain()` (ปุ่ม "เทรนโมเดลใหม่"): เปลี่ยนจากเรียก `train_model.py` (เทรน LBPH) เป็นสคริปต์ที่ scan `dataset/<name>/*.jpg` ทั้งหมด คำนวณ SFace embedding ใหม่ทั้งหมด เขียนทับ `embeddings.json` — ใช้กรณีต้องการ rebuild index จาก dataset เดิม
  - ปรับค่าคงที่ที่เกี่ยวข้อง (ลบ `CASCADE_PATH`/`TRAINER_PATH`/`LABELS_PATH` เดิม, เพิ่ม path โมเดลใหม่, เปลี่ยนความหมาย threshold จาก "ยิ่งน้อยยิ่งดี" เป็น "ยิ่งมากยิ่งดี")
- **`train_model.py`**: เขียนใหม่ตามลอจิก retrain ข้างบน (scan dataset → SFace embeddings → `embeddings.json`)
- **`capture_faces.py`** และ **`recognize.py`** (CLI dev utilities แยกจาก GUI): ปรับให้ตรงกับ pipeline ใหม่เช่นกัน (ภาพสี, YOLO detect, SFace recognize) เพื่อให้ยังใช้ debug ได้แบบ headless
- ผู้ใช้เดิม 3 คน (boss, bil, view) มีรูป grayscale 200×200 เก็บไว้ใน `dataset/` อยู่แล้ว — รัน retrain ใหม่จากรูปเดิมได้เลยโดยไม่ต้องถ่ายใหม่ (แจ้งผู้ใช้ว่าความแม่นยำอาจไม่เต็มที่เพราะรูปเดิมไม่ใช่ภาพสี/ไม่มี margin รอบหน้า แนะนำให้ถ่ายใหม่ภายหลังถ้าความแม่นยำไม่พอ)

### 5. ทดสอบ
- ทดสอบ detector/recognizer แบบ headless ก่อนด้วยภาพนิ่งจากกล้อง (script เล็กๆ, ไม่ต้องเปิด GUI)
- รัน `app.py` จริงผ่าน SSH โดยตั้ง env ให้ตรงกับ X session ที่ทำงานอยู่บนจอจริงของ Pi แล้วให้ผู้ใช้ดูผลที่หน้าจอกายภาพ พร้อมอ่าน stdout log ผ่าน SSH ไปด้วยเพื่อดักข้อผิดพลาด
- ตรวจสอบ: กรอบเขียว/ชื่อสำหรับคนที่ลงทะเบียนแล้ว, กรอบแดง/"stranger" สำหรับคนไม่รู้จัก, ความหน่วง/FPS เทียบกับของเดิม, flow ลงทะเบียนผู้ใช้ใหม่ทำงานถูกต้อง (ถ่ายรูป → บันทึก → embeddings อัปเดต)

## ไฟล์หลักที่จะแก้/เพิ่ม
- `/home/jocasta/facerec/app.py` (แก้เยอะสุด)
- `/home/jocasta/facerec/train_model.py` (เขียนใหม่)
- `/home/jocasta/facerec/capture_faces.py`, `recognize.py` (ปรับให้ตรงกับ pipeline ใหม่)
- `/home/jocasta/facerec/models/yolo26n_face.onnx`, `models/face_recognition_sface_2021dec.onnx` (ไฟล์ใหม่)
- `/home/jocasta/facerec/embeddings.json` (แทน `trainer.yml` + `labels.json`)
- เครื่อง Windows: โฟลเดอร์ training ชั่วคราว (dataset + สคริปต์เทรน) ไม่ commit เข้า repo ของ jocasta
