#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — MÁY CHỦ ROBOT WEB V3.2 (PRODUCTION READY)
- Kết nối Fish Audio API cho giọng VIP Tào Tháo Clone (7788e6c42a1a4e169935b417ff65a453)
- Tự động Fallback sang Edge-TTS Nam Minh (-14Hz, -12%) hoặc Hoài My (-2Hz, -6%)
- Hỗ trợ đổi linh hoạt giữa GPT-4o-mini (~2đ) và GPT-4o
- Khắc phục 100% lỗi CORS & Content-Length mạng di động
"""

import os
import json
import base64
import asyncio
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
import edge_tts
import requests
import bot_brain

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("TuUyenWebServer")

# CẤU HÌNH FISH AUDIO CLONE
FISH_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "7788e6c42a1a4e169935b417ff65a453")

# CẤU HÌNH 2 GIỌNG MIỄN PHÍ ĐÃ CĂN CHỈNH TRÒN VÀNH RÕ CHỮ
FREE_VOICES = {
    "nam_minh": {"voice": "vi-VN-NamMinhNeural", "pitch": "-14Hz", "rate": "-12%"},
    "hoai_my":  {"voice": "vi-VN-HoaiMyNeural",  "pitch": "-2Hz",  "rate": "-6%"}
}

DEFAULT_MODEL = "gpt-4o-mini"

def generate_fish_audio(text: str) -> str:
    """Gọi API Fish Audio sinh giọng Tào Tháo Clone."""
    if not FISH_API_KEY:
        logger.warning("Chưa cấu hình FISH_AUDIO_API_KEY. Chuyển sang giọng dự phòng.")
        return None
    try:
        url = "https://api.fish.audio/v1/tts"
        headers = {
            "Authorization": f"Bearer {FISH_API_KEY}",
            "Content-Type": "application/json"
        }
        clean_text = text.replace("*", "").replace("#", "").replace("`", "")
        payload = {
            "text": clean_text,
            "reference_id": FISH_VOICE_ID,
            "format": "mp3"
        }
        res = requests.post(url, headers=headers, json=payload, timeout=25)
        if res.status_code == 200:
            logger.info("✅ Gọi thành công Fish Audio Clone!")
            return base64.b64encode(res.content).decode("utf-8")
        else:
            logger.error(f"Lỗi Fish Audio ({res.status_code}): {res.text}")
            return None
    except Exception as e:
        logger.error(f"Lỗi kết nối Fish Audio: {e}")
        return None

async def generate_free_voice(text: str, voice_key: str) -> str:
    """Tạo âm thanh miễn phí Edge-TTS chất lượng cao."""
    cfg = FREE_VOICES.get(voice_key, FREE_VOICES["nam_minh"])
    clean_text = text.replace("*", "").replace("#", "").replace("`", "")
    temp_file = f"temp_voice_{voice_key}.mp3"
    comm = edge_tts.Communicate(clean_text, voice=cfg["voice"], pitch=cfg["pitch"], rate=cfg["rate"])
    await comm.save(temp_file)
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
        if self.path in ["/", "/index.html"]:
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
            selected_model = data.get("model", DEFAULT_MODEL)
            selected_voice = data.get("voice", "vip_taothao")

            bot_brain.AI_MODEL = selected_model
            logger.info(f"🎙️ [Model: {selected_model} | Voice: {selected_voice}]: {user_msg}")

            try:
                # 1. Bối cảnh nhân vật và tri thức RAG
                instance_id, base_system_prompt = bot_brain.get_instance_context()
                memories_text = bot_brain.get_verified_memories(instance_id)

                knowledge_context = ""
                triggers = ["binh pháp", "tôn tử", "đạo đức kinh", "lão tử", "tào tháo", "nước", "thất bại", "chiến lược"]
                if any(k in user_msg.lower() for k in triggers):
                    knowledge_context = bot_brain.retrieve_knowledge(user_msg, limit=1)

                full_prompt = base_system_prompt + "\n\n" + memories_text + knowledge_context

                # 2. Quản lý hội thoại
                conv_id = bot_brain.get_or_create_conversation(instance_id, 999999)
                recent_history = bot_brain.get_recent_messages(conv_id, limit=6)

                payload = [{"role": "system", "content": full_prompt}]
                payload.extend(recent_history)
                payload.append({"role": "user", "content": user_msg})

                # 3. Gọi AI
                reply_text = bot_brain.call_ai_model(payload)

                bot_brain.save_message(conv_id, "user", user_msg)
                bot_brain.save_message(conv_id, "assistant", reply_text)

                # 4. SINH ÂM THANH THEO GIỌNG ĐƯỢC CHỌN
                audio_b64 = None
                if selected_voice == "vip_taothao":
                    audio_b64 = generate_fish_audio(reply_text)

                # NẾU KHÔNG PHẢI VIP HOẶC VIP LỖI/CHƯA CÓ KEY -> DÙNG GIỌNG EDGE-TTS MIỄN PHÍ
                if not audio_b64:
                    voice_fallback = selected_voice if selected_voice in FREE_VOICES else "nam_minh"
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    audio_b64 = loop.run_until_complete(generate_free_voice(reply_text, voice_fallback))
                    loop.close()

                resp_bytes = json.dumps({"reply": reply_text, "audio": audio_b64, "model": selected_model}, ensure_ascii=False).encode("utf-8")
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
    logger.info(f"🚀 MẶT ROBOT TỬ UYÊN V3.2 LIVE TẠI CỔNG: {port}")
    httpd.serve_forever()

if __name__ == "__main__":
    run()