#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — MÁY CHỦ ROBOT WEB V3.0 (DUAL GPT MODEL SWITCHER & HIGH-DEF VOICE STREAMING)
- Hỗ trợ chuyển đổi linh hoạt: GPT-4o-mini (Huấn luyện ~2đ) <-> GPT-4o (Demo gọi vốn)
- Âm thanh chuẩn Bản số 2: Nam Minh (-14Hz, -12%) truyền trực tiếp từ Server
"""

import os
import json
import base64
import asyncio
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
import edge_tts
import bot_brain

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("TuUyenWebServer")

# CẤU HÌNH GIỌNG BẢN SỐ 2 (TÀO THÁO)
VOICE_NAME = "vi-VN-NamMinhNeural"
VOICE_PITCH = "-14Hz"  # Trầm sâu âm vực lồng ngực
VOICE_RATE = "-12%"    # Nhả chữ đĩnh đạc, từ tốn

# Mặc định ban đầu: Mô hình huấn luyện tiết kiệm gpt-4o-mini
DEFAULT_MODEL = "gpt-4o-mini"

async def generate_audio_base64(text: str) -> str:
    """Tạo âm thanh MP3 chất lượng cao Bản số 2 từ Microsoft Edge-TTS."""
    clean_text = text.replace("*", "").replace("#", "").replace("`", "")
    temp_file = "temp_voice_stream.mp3"
    communicate = edge_tts.Communicate(clean_text, voice=VOICE_NAME, pitch=VOICE_PITCH, rate=VOICE_RATE)
    await communicate.save(temp_file)
    with open(temp_file, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("utf-8")
    if os.path.exists(temp_file):
        os.remove(temp_file)
    return audio_b64

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
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            with open("index.html", "rb") as f:
                self.wfile.write(f.read())
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/chat":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode("utf-8"))
            user_msg = data.get("message", "").strip()
            
            # NHẬN MODEL LINH HOẠT TỪ GIAO DIỆN NGƯỜI DÙNG CHỌN
            active_model = data.get("model", DEFAULT_MODEL)
            bot_brain.AI_MODEL = active_model

            logger.info(f"🎙️ Nhận câu hỏi [Model: {active_model}]: {user_msg}")

            try:
                # 1. Bối cảnh nhân vật và tri thức RAG
                instance_id, base_system_prompt = bot_brain.get_instance_context()
                memories_text = bot_brain.get_verified_memories(instance_id)

                knowledge_context = ""
                triggers = ["binh pháp", "tôn tử", "đạo đức kinh", "lão tử", "tào tháo", "nước", "thất bại", "chiến lược"]
                if any(k in user_msg.lower() for k in triggers):
                    knowledge_context = bot_brain.retrieve_knowledge(user_msg, limit=1)

                full_prompt = base_system_prompt + "\n\n" + memories_text + knowledge_context

                # 2. Hội thoại & Lịch sử
                conv_id = bot_brain.get_or_create_conversation(instance_id, 999999)
                recent_history = bot_brain.get_recent_messages(conv_id, limit=6)

                payload = [{"role": "system", "content": full_prompt}]
                payload.extend(recent_history)
                payload.append({"role": "user", "content": user_msg})

                # 3. Gọi AI theo đúng mô hình được chọn
                reply_text = bot_brain.call_ai_model(payload)

                bot_brain.save_message(conv_id, "user", user_msg)
                bot_brain.save_message(conv_id, "assistant", reply_text)

                # 4. TẠO ÂM THANH BẢN SỐ 2 (TÀO THÁO) TRỰC TIẾP TỪ SERVER
                audio_b64 = None
                try:
                    logger.info(f"🎵 Đang tạo âm thanh Bản số 2 (Nam Minh {VOICE_PITCH})...")
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    audio_b64 = loop.run_until_complete(generate_audio_base64(reply_text))
                    loop.close()
                    logger.info("✅ Đã tạo âm thanh Bản số 2 thành công!")
                except Exception as a_err:
                    logger.warning(f"Lỗi tạo âm thanh server: {a_err}")

                resp_bytes = json.dumps({"reply": reply_text, "audio": audio_b64, "model": active_model}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(resp_bytes)

            except Exception as e:
                logger.error(f"Lỗi: {e}")
                err_bytes = json.dumps({"reply": f"Đạo hữu thứ lỗi, bộ não gặp gián đoạn: {str(e)}"}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(err_bytes)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(err_bytes)

def run(port=None):
    if port is None:
        port = int(os.environ.get("PORT", 8000))
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, RobotWebHandler)
    logger.info(f"🚀 MẶT ROBOT TỬ UYÊN ĐÃ SẴN SÀNG TẠI CỔNG: {port}")
    httpd.serve_forever()

if __name__ == "__main__":
    run()