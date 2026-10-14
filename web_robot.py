#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — MÁY CHỦ ROBOT WEB V3.4 (TRI THỨC SÂU SẮC & CHỐNG TIMEOUT 502)
- Độ dài co giãn tự nhiên theo chiều sâu câu hỏi (DE-R03)
- Tối ưu hóa luồng gọi AI và âm thanh để triệt tiêu lỗi 502
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

FISH_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "7788e6c42a1a4e169935b417ff65a453")

FREE_VOICES = {
    "nam_minh": {"voice": "vi-VN-NamMinhNeural", "pitch": "-14Hz", "rate": "-12%"},
    "hoai_my":  {"voice": "vi-VN-HoaiMyNeural",  "pitch": "-2Hz",  "rate": "-6%"}
}

DEFAULT_MODEL = "gpt-4o-mini"

def generate_fish_audio(text: str) -> str:
    if not FISH_API_KEY:
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
        res = requests.post(url, headers=headers, json=payload, timeout=12)
        if res.status_code == 200:
            return base64.b64encode(res.content).decode("utf-8")
    except Exception as e:
        logger.warning(f"Fish Audio bận, tự động chuyển sang Nam Minh bản số 2: {e}")
    return None

async def generate_free_voice(text: str, voice_key: str) -> str:
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
            logger.info(f"🎙️ Nhận câu hỏi ({selected_model}): {user_msg}")

            try:
                # 1. Bối cảnh tri thức chuyên sâu
                instance_id, base_system_prompt = bot_brain.get_instance_context()
                memories_text = bot_brain.get_verified_memories(instance_id)

                knowledge_context = ""
                triggers = ["binh pháp", "tôn tử", "đạo đức kinh", "lão tử", "tào tháo", "nước", "thất bại", "chiến lược"]
                if any(k in user_msg.lower() for k in triggers):
                    knowledge_context = bot_brain.retrieve_knowledge(user_msg, limit=2)

                # NGUYÊN TẮC: ĐỘ DÀI CO GIÃN THEO HÀM LƯỢNG TRI THỨC (DE-R03)
                depth_rule = (
                    "\n\nQUY TẮC ĐỘ DÀI VÀ VĂN PHONG (DE-R03):\n"
                    "- Với câu hỏi xã giao, than vãn: Trả lời ngắn gọn, ấm áp, thấu cảm (1 - 3 câu).\n"
                    "- Với câu hỏi có chiều sâu, bàn mưu lược, triết học hoặc kỹ thuật: HÃY TỰ DO MỞ RỘNG DUNG LƯỢNG tương xứng với tầm vóc câu hỏi. "
                    "Phân tích đa tầng nấc, khúc chiết, đanh thép, dẫn giải sắc bén. Không được gò bó cụt lủn."
                )

                full_prompt = base_system_prompt + depth_rule + "\n\n" + memories_text + knowledge_context

                conv_id = bot_brain.get_or_create_conversation(instance_id, 999999)
                recent_history = bot_brain.get_recent_messages(conv_id, limit=6)

                payload = [{"role": "system", "content": full_prompt}]
                payload.extend(recent_history)
                payload.append({"role": "user", "content": user_msg})

                # 2. Gọi mô hình AI
                reply_text = bot_brain.call_ai_model(payload)

                # 3. Lưu vào database
                bot_brain.save_message(conv_id, "user", user_msg)
                bot_brain.save_message(conv_id, "assistant", reply_text)

                # 4. Sinh âm thanh (Ưu tiên Fish Audio, tự fallback Nam Minh nếu chậm)
                audio_b64 = None
                if selected_voice == "vip_taothao":
                    audio_b64 = generate_fish_audio(reply_text)

                if not audio_b64:
                    voice_fallback = selected_voice if selected_voice in FREE_VOICES else "nam_minh"
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    audio_b64 = loop.run_until_complete(generate_free_voice(reply_text, voice_fallback))
                    loop.close()

                # 5. Phản hồi về điện thoại
                resp_bytes = json.dumps({"reply": reply_text, "audio": audio_b64}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(resp_bytes)

            except Exception as e:
                logger.error(f"Lỗi: {e}")
                err_bytes = json.dumps({"reply": "Bộ não đang điều phối...", "audio": None}, ensure_ascii=False).encode("utf-8")
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
    logger.info(f"🚀 MẶT ROBOT TỬ UYÊN V3.4 (ELASTIC DEPTH) LIVE TẠI: {port}")
    httpd.serve_forever()

if __name__ == "__main__":
    run()