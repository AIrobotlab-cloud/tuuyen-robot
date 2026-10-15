#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — MÁY CHỦ ROBOT WEB V3.5 (CHỐNG CÂM VOICE, CHỐNG TỰ NGHĨ & NẠP TRI THỨC SÂU)
Phiên bản: 3.5.0 (Investor Demo & Live Coaching Ready)
Các nâng cấp cốt lõi:
1. Chống tự nghĩ khi im lặng: Kiểm tra chốt chặn nội dung rỗng (empty message guard).
2. Chống lỗi câm tiếng (Audio Drop):
   - Tạo âm thanh trực tiếp trong RAM (in-memory bytearray), không ghi file tạm trên ổ cứng.
   - Cơ chế tự động thử lại 2 tầng (Auto-Retry) nếu máy chủ TTS Microsoft bị nghẽn mạng.
   - Tự động chuyển giọng dự phòng mượt mà nếu Fish Audio vượt quá 10 giây.
3. Mở rộng truy xuất kho sách RAG: Tự động tra cứu kiến thức dự án, phần cứng V2, chi phí BOM và chiến lược gọi vốn.
4. Cơ chế tự học ngầm (Background Memory Extractor): Tự động trích xuất bài học và quy tắc văn phong vào Supabase.
"""

import os
import sys
import json
import base64
import asyncio
import logging
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import edge_tts
import requests
import bot_brain

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TuUyenWebServer")

FISH_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "").strip()
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "7788e6c42a1a4e169935b417ff65a453").strip()

FREE_VOICES = {
    "nam_minh": {"voice": "vi-VN-NamMinhNeural", "pitch": "-14Hz", "rate": "-12%"},
    "hoai_my":  {"voice": "vi-VN-HoaiMyNeural",  "pitch": "-2Hz",  "rate": "-6%"}
}

DEFAULT_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")


# 1. HÀM TẠO ÂM THANH QUA FISH AUDIO (CÓ TIMEOUT 10S AN TOÀN)
def generate_fish_audio(text: str) -> str:
    if not FISH_API_KEY or not FISH_VOICE_ID:
        return None
    try:
        url = "https://api.fish.audio/v1/tts"
        headers = {
            "Authorization": f"Bearer {FISH_API_KEY}",
            "Content-Type": "application/json"
        }
        clean_text = text.replace("*", "").replace("#", "").replace("`", "").replace("~", "").strip()
        payload = {
            "text": clean_text,
            "reference_id": FISH_VOICE_ID,
            "format": "mp3",
            "latency": "balanced"
        }
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 200 and len(res.content) > 500:
            return base64.b64encode(res.content).decode("utf-8")
        logger.warning(f"Fish Audio trả về mã {res.status_code}, chuyển sang giọng Nam Minh.")
    except Exception as e:
        logger.warning(f"Fish Audio bận hoặc quá 10s ({e}), tự động chuyển sang giọng Nam Minh dự phòng.")
    return None


# 2. HÀM TẠO ÂM THANH EDGE-TTS TRONG BỘ NHỚ RAM (CÓ AUTO-RETRY CHỐNG CÂM TIẾNG)
async def generate_free_voice_stream(text: str, voice_key: str) -> str:
    cfg = FREE_VOICES.get(voice_key, FREE_VOICES["nam_minh"])
    clean_text = text.replace("*", "").replace("#", "").replace("`", "").replace("~", "").strip()
    if not clean_text:
        return None

    # Thử tối đa 2 lần nếu mạng bị nghẽn
    for attempt in range(2):
        try:
            comm = edge_tts.Communicate(clean_text, voice=cfg["voice"], pitch=cfg["pitch"], rate=cfg["rate"])
            audio_buffer = bytearray()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    audio_buffer.extend(chunk["data"])
            
            if len(audio_buffer) > 500:
                return base64.b64encode(bytes(audio_buffer)).decode("utf-8")
        except Exception as err:
            logger.warning(f"Thử tạo giọng {voice_key} lần {attempt + 1} gặp lỗi: {err}")
            await asyncio.sleep(0.3)
    
    # Nếu Nam Minh lỗi thì thử cứu cánh bằng Hoài My
    if voice_key != "hoai_my":
        try:
            cfg_backup = FREE_VOICES["hoai_my"]
            comm_backup = edge_tts.Communicate(clean_text, voice=cfg_backup["voice"], pitch=cfg_backup["pitch"], rate=cfg_backup["rate"])
            audio_buffer = bytearray()
            async for chunk in comm_backup.stream():
                if chunk["type"] == "audio":
                    audio_buffer.extend(chunk["data"])
            if len(audio_buffer) > 500:
                return base64.b64encode(bytes(audio_buffer)).decode("utf-8")
        except Exception:
            pass

    return None


def synthesize_audio_safe(text: str, selected_voice: str) -> str:
    audio_b64 = None
    if selected_voice == "vip_taothao":
        audio_b64 = generate_fish_audio(text)

    if not audio_b64:
        voice_fallback = selected_voice if selected_voice in FREE_VOICES else "nam_minh"
        try:
            audio_b64 = asyncio.run(generate_free_voice_stream(text, voice_fallback))
        except Exception as e:
            logger.error(f"Lỗi loop âm thanh: {e}")
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                audio_b64 = loop.run_until_complete(generate_free_voice_stream(text, voice_fallback))
                loop.close()
            except Exception as loop_err:
                logger.error(f"Lỗi cưỡng chế loop: {loop_err}")

    return audio_b64


# 3. CHẠY TỰ HỌC NGẦM ĐỂ KHÔNG LÀM CHẬM PHẢN HỒI
def run_background_learning(instance_id: str, user_msg: str):
    try:
        asyncio.run(bot_brain.auto_extract_and_save_memory(instance_id, user_msg))
    except Exception as e:
        logger.debug(f"Bỏ qua tự học ngầm: {e}")


# 4. BỘ ĐIỀU PHỐI MÁY CHỦ WEB ROBOT
class RobotWebHandler(SimpleHTTPRequestHandler):
    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Connection", "close")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path in ["/", "/index.html"] or self.path.startswith("/?"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            with open("index.html", "rb") as f:
                self.wfile.write(f.read())
        elif self.path in ["/api/status", "/health"]:
            status_data = json.dumps({"status": "healthy", "version": "V3.5-Protected"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(status_data)))
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(status_data)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/chat":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode("utf-8")) if post_data else {}

                user_msg = (data.get("message") or data.get("text") or "").strip()
                selected_model = data.get("model") or data.get("model_mode") or DEFAULT_MODEL
                selected_voice = data.get("voice") or data.get("voice_mode") or "vip_taothao"

                # CHỐT CHẶN CHỐNG NGHĨ BỪA: Nếu câu hỏi rỗng hoặc quá ngắn -> Dừng ngay, không gọi AI
                if len(user_msg) < 2:
                    logger.info("Nhận tin nhắn rỗng hoặc tạp âm, từ chối xử lý.")
                    resp_bytes = json.dumps({"reply": "", "audio": None}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(resp_bytes)))
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(resp_bytes)
                    return

                bot_brain.AI_MODEL = selected_model
                logger.info(f"🎙️ Nhận câu hỏi [{selected_model}]: '{user_msg}'")

                # 1. Bối cảnh nhân vật & Ký ức
                instance_id, base_system_prompt = bot_brain.get_instance_context()
                memories_text = bot_brain.get_verified_memories(instance_id)

                # 2. Truy vấn Kho bách khoa thư RAG mở rộng (bao gồm cả dự án, phần cứng, gọi vốn và triết lý)
                knowledge_context = ""
                project_triggers = [
                    "binh pháp", "tôn tử", "đạo đức kinh", "lão tử", "tào tháo", "nước", "thất bại", "chiến lược",
                    "robot", "phần cứng", "dự án", "tử uyên", "gọi vốn", "đầu tư", "chi phí", "raspberry",
                    "esp32", "giá", "bom", "mạch", "khớp cổ", "servo", "loa", "mic", "ký ức", "mô hình b"
                ]
                msg_lower = user_msg.lower()
                if any(k in msg_lower for k in project_triggers) or len(user_msg.split()) >= 4:
                    knowledge_context = bot_brain.retrieve_knowledge(user_msg, limit=2)

                # NGUYÊN TẮC CO GIÃN ĐỘ DÀI & VĂN PHONG QUÂN SƯ (DE-R03)
                depth_rule = (
                    "\n\n=== QUY TẮC ĐỘ DÀI & VĂN PHONG PHÁT ÂM (DE-R03) ===\n"
                    "- Với câu chào hỏi, tán gẫu xã giao: Trả lời ngắn gọn, ấm áp, điềm đạm (1 - 3 câu).\n"
                    "- Với câu hỏi về dự án, kỹ thuật phần cứng, chiến lược gọi vốn hoặc triết lý: TỰ DO MỞ RỘNG DUNG LƯỢNG tương xứng tầm vóc câu hỏi. "
                    "Phân tích khúc chiết, đanh thép, có chính kiến rõ ràng. Tuyệt đối không cụt lủn vô hồn.\n"
                    "- VĂN PHONG NÓI: Không dùng dấu gạch đầu dòng, không đánh số 1, 2, 3, không dùng ký tự Markdown (*, #). Trả lời thành đoạn văn liền mạch để phát âm qua loa nghe tự nhiên và truyền cảm."
                )

                full_prompt = base_system_prompt + depth_rule + "\n\n" + memories_text + knowledge_context

                # Nối mạch hội thoại trên Web
                conv_id = bot_brain.get_or_create_conversation(instance_id, 999999)
                recent_history = bot_brain.get_recent_messages(conv_id, limit=6)

                payload = [{"role": "system", "content": full_prompt}]
                payload.extend(recent_history)
                payload.append({"role": "user", "content": user_msg})

                # 3. Gọi mô hình AI
                reply_text = bot_brain.call_ai_model(payload)

                # 4. Lưu vào database & Kích hoạt tự học ngầm
                bot_brain.save_message(conv_id, "user", user_msg)
                bot_brain.save_message(conv_id, "assistant", reply_text)
                threading.Thread(target=run_background_learning, args=(instance_id, user_msg), daemon=True).start()

                # 5. Sinh âm thanh an toàn tuyệt đối
                audio_b64 = synthesize_audio_safe(reply_text, selected_voice)

                # 6. Phản hồi về điện thoại
                resp_bytes = json.dumps({"reply": reply_text, "audio": audio_b64}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(resp_bytes)

            except Exception as e:
                logger.error(f"Lỗi xử lý /api/chat: {e}")
                err_bytes = json.dumps({"reply": "Bộ não đang điều phối, đạo hữu thong thả nói lại cùng ta nhé.", "audio": None}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(err_bytes)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(err_bytes)
        else:
            self.send_error(404, "Endpoint not found")


def run(port=None):
    if port is None:
        port = int(os.environ.get("PORT", 8000))
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, RobotWebHandler)
    logger.info(f"🚀 MẶT ROBOT TỬ UYÊN V3.5 (AUDIO & SILENCE GUARD ACTIVE) LIVE TẠI: {port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run()