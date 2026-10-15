#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — MÁY CHỦ ROBOT WEB V3.6 (BẢN HỢP NHẤT HOÀN CHỈNH)
Phiên bản: 3.6.0 (Elastic Depth + Anti-502 + Audio Guard + Silence Guard + Live Coaching)
Tổng hợp toàn bộ tính năng tối ưu:
1. Độ dài câu trả lời co giãn thông minh theo chiều sâu câu hỏi (Quy luật chiếc chuông đồng - DE-R03).
2. Chống lỗi HTTP 502 trên Render: Giới hạn Fish Audio 10s, tự động chuyển sang Edge-TTS RAM Stream siêu tốc (0.8s) khi câu trả lời dài.
3. Chống lỗi câm tiếng (Audio Drop): Tạo âm thanh trực tiếp trong bộ nhớ RAM (bytearray) kèm Auto-Retry 2 tầng.
4. Chống tự nghĩ khi im lặng: Chốt chặn từ chối tin nhắn rỗng hoặc tạp âm (< 2 ký tự).
5. Mở rộng RAG & Tự học ngầm: Tra cứu toàn diện kiến thức dự án/phần cứng/Supabase và tự động học qua trò chuyện.
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
logger = logging.getLogger("TuUyenWebServerV3.6")

FISH_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "").strip()
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "7788e6c42a1a4e169935b417ff65a453").strip()

FREE_VOICES = {
    "nam_minh": {"voice": "vi-VN-NamMinhNeural", "pitch": "-14Hz", "rate": "-12%"},
    "hoai_my":  {"voice": "vi-VN-HoaiMyNeural",  "pitch": "-2Hz",  "rate": "-6%"}
}

DEFAULT_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")


# 1. HÀM TẠO ÂM THANH FISH AUDIO VIP (CÓ CHỐT CHẶN 10 GIÂY CHỐNG LỖI 502 RENDER)
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
        logger.warning(f"Fish Audio quá 10s ({e}), tự động chuyển sang Nam Minh bản số 2 để chống lỗi 502.")
    return None


# 2. HÀM TẠO ÂM THANH EDGE-TTS TRONG RAM (SIÊU TỐC & CHỐNG CÂM TIẾNG)
async def generate_free_voice_stream(text: str, voice_key: str) -> str:
    cfg = FREE_VOICES.get(voice_key, FREE_VOICES["nam_minh"])
    clean_text = text.replace("*", "").replace("#", "").replace("`", "").replace("~", "").strip()
    if not clean_text:
        return None

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
        except Exception:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                audio_b64 = loop.run_until_complete(generate_free_voice_stream(text, voice_fallback))
                loop.close()
            except Exception as loop_err:
                logger.error(f"Lỗi tạo âm thanh dự phòng: {loop_err}")

    return audio_b64


# 3. CHẠY TỰ HỌC NGẦM QUA HỘI THOẠI (KHÔNG LÀM CHẬM PHẢN HỒI VOICE)
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
            status_data = json.dumps({"status": "healthy", "version": "V3.6-Master"}).encode("utf-8")
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

                # CHỐT CHẶN CHỐNG NGHĨ BỪA: Nếu tin nhắn rỗng hoặc < 2 ký tự -> Dừng ngay
                if len(user_msg) < 2:
                    logger.info("Nhận tín hiệu rỗng hoặc tạp âm, bỏ qua.")
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

                # 1. Tải bối cảnh nhân vật & Ký ức dài hạn
                instance_id, base_system_prompt = bot_brain.get_instance_context()
                memories_text = bot_brain.get_verified_memories(instance_id)

                # 2. Truy xuất Kho tri thức RAG mở rộng (Binh pháp, Triết học, Dự án, Phần cứng V2, Supabase, Gọi vốn)
                knowledge_context = ""
                project_triggers = [
                    "binh pháp", "tôn tử", "đạo đức kinh", "lão tử", "tào tháo", "nước", "thất bại", "chiến lược",
                    "robot", "phần cứng", "dự án", "tử uyên", "gọi vốn", "đầu tư", "chi phí", "raspberry",
                    "esp32", "giá", "bom", "mạch", "khớp cổ", "servo", "loa", "mic", "ký ức", "mô hình b", "supabase"
                ]
                msg_lower = user_msg.lower()
                if any(k in msg_lower for k in project_triggers) or len(user_msg.split()) >= 4:
                    knowledge_context = bot_brain.retrieve_knowledge(user_msg, limit=2)

                # 3. QUY LUẬT CHIẾC CHUÔNG ĐỒNG (DE-R03): ĐỘ DÀI CO GIÃN TƯƠNG XỨNG TẦM VÓC CÂU HỎI
                depth_rule = (
                    "\n\n=== QUY TẮC ĐỘ DÀI CO GIÃN & VĂN PHONG ĐỐI THOẠI (DE-R03) ===\n"
                    "- Với câu chào hỏi, tán gẫu hoặc chia sẻ mệt mỏi: Phản hồi ngắn gọn, ấm áp, thấu cảm (1 - 3 câu).\n"
                    "- Với câu hỏi có chiều sâu về dự án, kỹ thuật phần cứng, kiến trúc dữ liệu, chiến lược gọi vốn hoặc triết lý: "
                    "TỰ DO MỞ RỘNG DUNG LƯỢNG tương xứng tầm vóc câu hỏi (4 - 7 câu). Phân tích đa tầng nấc, khúc chiết, đanh thép, có chính kiến rõ ràng. Không được cụt lủn.\n"
                    "- VĂN PHONG PHÁT ÂM: Tuyệt đối KHÔNG dùng gạch đầu dòng, không đánh số 1, 2, 3, không dùng ký tự Markdown (*, #). "
                    "Hãy nói thành đoạn văn liền mạch, có nhịp ngắt câu tự nhiên để khi đọc lên qua loa nghe uy lực như một vị quân sư."
                )

                full_prompt = base_system_prompt + depth_rule + "\n\n" + memories_text + knowledge_context

                # 4. Nối mạch lịch sử trò chuyện liên tục trên Mobile
                conv_id = bot_brain.get_or_create_conversation(instance_id, 999999)
                recent_history = bot_brain.get_recent_messages(conv_id, limit=6)

                payload = [{"role": "system", "content": full_prompt}]
                payload.extend(recent_history)
                payload.append({"role": "user", "content": user_msg})

                # 5. Gọi mô hình AI suy nghĩ
                reply_text = bot_brain.call_ai_model(payload)

                # 6. Lưu hội thoại & Kích hoạt tự học ngầm
                bot_brain.save_message(conv_id, "user", user_msg)
                bot_brain.save_message(conv_id, "assistant", reply_text)
                threading.Thread(target=run_background_learning, args=(instance_id, user_msg), daemon=True).start()

                # 7. Tổng hợp giọng nói an toàn (Không bao giờ câm tiếng hoặc quá 15s gây lỗi 502)
                audio_b64 = synthesize_audio_safe(reply_text, selected_voice)

                # 8. Trả kết quả về điện thoại
                resp_bytes = json.dumps({
                    "reply": reply_text,
                    "audio": audio_b64,
                    "audio_base64": audio_b64
                }, ensure_ascii=False).encode("utf-8")

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(resp_bytes)

            except Exception as e:
                logger.error(f"Lỗi xử lý /api/chat: {e}")
                err_bytes = json.dumps({
                    "reply": "Bộ não đang điều phối, đạo hữu thong thả nói lại cùng ta nhé.",
                    "audio": None,
                    "audio_base64": None
                }, ensure_ascii=False).encode("utf-8")
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
    logger.info(f"🚀 MẶT ROBOT TỬ UYÊN V3.6 (FULL MASTER EDITION) LIVE TẠI CỔNG: {port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run()