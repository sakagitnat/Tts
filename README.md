---
title: Thai TTS MP3
emoji: 🔊
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

# แปลงข้อความไทยเป็น MP3 (edge-tts)

เว็บแอปสำหรับใช้บน iPad (หรือเครื่องไหนก็ได้): วางข้อความภาษาไทยยาว ๆ → กดปุ่ม → ได้ไฟล์ MP3 เสียง
**th-TH-PremwadeeNeural** ฟังในหน้าเว็บได้ทันทีและกดดาวน์โหลดได้

- ข้อความยาวจะถูกแบ่งเป็นท่อน (ไม่เกิน ~1,500 ตัวอักษร ตัดตามย่อหน้า/ช่องว่าง) แล้วรวมเป็นไฟล์เดียว
- คำ/ประโยคภาษาอังกฤษในข้อความ อ่านด้วยเสียงภาษาอังกฤษอัตโนมัติ (เลือกสำเนียงอเมริกัน en-US-JennyNeural
  หรืออังกฤษ en-GB-SoniaNeural หรือปิดเพื่อให้เปรมวดีอ่านทั้งหมด)
- ตอนสลับไทย↔อังกฤษ ตัดช่วงเงียบให้สั้น: กลางประโยคเว้น ~0.25 วินาที, จบประโยคหรือขึ้นบรรทัดใหม่เว้น ~0.5 วินาที
  (ปรับได้ที่ `PAUSE_INLINE` / `PAUSE_SENTENCE` ใน `app.py`)
- ปรับความเร็วภาษาไทยและภาษาอังกฤษแยกกันได้ (ภาษาอังกฤษตั้งต้นให้ช้าลง 25% ฟังง่าย เหมาะกับการฝึกฟัง)
- มีแถบแสดงความคืบหน้า
- ข้อความที่พิมพ์ไว้จะถูกจำไว้ในเบราว์เซอร์ ปิดหน้าแล้วเปิดใหม่ก็ยังอยู่

ไฟล์สำคัญ: `app.py` (เซิร์ฟเวอร์), `templates/index.html` (หน้าเว็บ), `requirements.txt`, `Dockerfile`, `render.yaml`

---

## วิธีที่ 1: Deploy บน Render (แนะนำ — ต่อกับ GitHub ได้เลย ไม่ต้องอัปโหลดไฟล์เอง)

ทำบน iPad ผ่าน Safari ได้ทั้งหมด

1. เปิด <https://render.com> → กด **Get Started** → เลือก **GitHub** เพื่อสมัครด้วยบัญชี GitHub
2. ในหน้า Dashboard กด **+ New** (มุมขวาบน) → เลือก **Web Service**
3. เลือก **Git Provider → GitHub** แล้วอนุญาตให้ Render เข้าถึง repo **`Tts`** → กด **Connect** ที่ repo นั้น
4. กรอกค่าตามนี้ (ช่องไหนไม่ได้บอก ปล่อยไว้ตามเดิม):

   | ช่อง | ใส่ค่า |
   |---|---|
   | Name | `thai-tts` (ชื่ออะไรก็ได้ จะกลายเป็นชื่อเว็บ) |
   | Branch | branch ที่มีโค้ดนี้ (เช่น `main` หรือ `claude/thai-text-to-mp3-ipad-8gntbb`) |
   | Language / Runtime | `Python 3` |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 300` |
   | Instance Type | **Free** |

5. กด **Deploy Web Service** แล้วรอ 2–5 นาที จนขึ้นคำว่า **Live** สีเขียว
6. กดลิงก์ด้านบน (หน้าตาแบบ `https://thai-tts-xxxx.onrender.com`) → ใช้งานได้เลย

> **หมายเหตุแพ็กฟรีของ Render:** ถ้าไม่มีคนใช้ ~15 นาที เว็บจะ "หลับ" ครั้งต่อไปที่เปิดจะช้าราว 1 นาที — เป็นเรื่องปกติ รอสักครู่ก็ใช้ได้
>
> ถ้าแก้โค้ดใน GitHub แล้ว Render จะ deploy ใหม่ให้อัตโนมัติ

## วิธีที่ 2: Deploy บน Hugging Face Spaces (ฟรี)

1. สมัคร/ล็อกอินที่ <https://huggingface.co>
2. ไปที่ <https://huggingface.co/new-space>
   - **Space name**: `thai-tts`
   - **Select the Space SDK**: เลือก **Docker** → **Blank**
   - **Space hardware**: `CPU basic · FREE`
   - **Public / Private**: เลือกตามต้องการ (Private = คนอื่นเข้าไม่ได้)
   - กด **Create Space**
3. เอาไฟล์จาก GitHub มาไว้ใน iPad: เปิดหน้า repo ใน GitHub → ปุ่มเขียว **Code** → **Download ZIP**
   → เปิดแอป **ไฟล์ (Files)** → โฟลเดอร์ดาวน์โหลด → แตะไฟล์ ZIP หนึ่งครั้งเพื่อแตกไฟล์
4. กลับไปที่หน้า Space → แท็บ **Files** → **+ Contribute** → **Upload files**
   → เลือกไฟล์ `app.py`, `requirements.txt`, `Dockerfile`, `README.md` → กด **Commit changes to main**
5. สร้างไฟล์หน้าเว็บ: **+ Contribute** → **Create a new file** → ตั้งชื่อ `templates/index.html`
   → คัดลอกเนื้อหาจากไฟล์ `templates/index.html` ใน GitHub มาวาง → **Commit new file to main**
6. รอให้สถานะด้านบนเปลี่ยนจาก **Building** เป็น **Running** (3–5 นาที) → แอปจะขึ้นในแท็บ **App**

> ต้องใช้ไฟล์ `README.md` ตัวนี้ (มีส่วนหัว `sdk: docker` / `app_port: 7860`) เพื่อให้ Hugging Face รู้ว่าจะรันแอปอย่างไร

## เคล็ดลับการใช้บน iPad

- **เพิ่มไว้ที่หน้าจอโฮม:** ใน Safari กดปุ่มแชร์ → **เพิ่มไปยังหน้าจอโฮม** จะได้ไอคอนเปิดเหมือนแอป
- **ไฟล์ที่ดาวน์โหลด** อยู่ในแอป **ไฟล์ (Files) → ดาวน์โหลด**
- ข้อความยาวมาก (เช่น 50,000 ตัวอักษร) อาจใช้เวลาหลายนาที ให้เปิดหน้าเว็บค้างไว้จนเสร็จ
- ถ้าขึ้นข้อผิดพลาด ลองกดแปลงอีกครั้ง (บริการเสียงของ Microsoft บางครั้งตอบช้า)

## รันบนคอมพิวเตอร์ (สำหรับนักพัฒนา)

```bash
pip install -r requirements.txt
python app.py        # แล้วเปิด http://localhost:7860
```

ตั้งค่าเพิ่มเติมได้ผ่าน environment variable `MAX_TEXT_CHARS` (ค่าเริ่มต้น 100000 ตัวอักษร)
