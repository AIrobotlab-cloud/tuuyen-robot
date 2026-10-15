#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — MÁY CHỦ ROBOT WEB V3.7 (ỔN ĐỊNH TUYỆT ĐỐI ÂM THANH & HỘI THOẠI LIÊN TỤC)
Khắc phục triệt để:
1. Tách luồng hội thoại sạch (bỏ qua toàn bộ tin nhắn rỗng cũ trong CSDL làm AI bị kẹt ở câu đầu tiên).
2. Khôi phục cơ chế ghi file âm thanh chuẩn của V3.4 kèm chống trùng file, đảm bảo phát tiếng 100%.
3. Không dùng đa luồng xung đột kết nối Supabase; giữ trọn vẹn độ sâu câu trả lời (DE-R03) và kho sách RAG.
"""

import os
import time
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

FISH_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "").strip()
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "7788e6c42a1a4e169935b417ff65a453").strip()

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
        clean_text = text.replace("*", "").replace("#", "").replace("`", "").strip()
        payload = {
            "text": clean_text,
            "reference_id": FISH_VOICE_ID,
            "format": "mp3"
        }
        res = requests.post(url, headers=headers, json=payload, timeout=9)
        if res.status_code == 200 and len(res.content) > 500:
            return base64.b64encode(res.content).decode("utf-8")
    except Exception as e:
        logger.warning(f"Fish Audio bận, tự động chuyển sang Nam Minh bản số 2: {e}")
    return None


async def generate_free_voice(text: str, voice_key: str) -> str:
    cfg = FREE_VOICES.get(voice_key, FREE_VOICES["nam_minh"])
    clean_text = text.replace("*", "").replace("#", "").replace("`", "").strip()
    temp_file = f"temp_voice_{voice_key}_{int(time.time() * 1000)}.mp3"
    try:
        comm = edge_tts.Communicate(clean_text, voice=cfg["voice"], pitch=cfg["pitch"], rate=cfg["rate"])
        await comm.save(temp_file)
        with open(temp_file, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")
        return audio_b64
    finally:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass


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

    def send_rpg_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # V3.8.1: Public RPG showcase. Only allowlisted, aggregate, non-personal fields.
        # Never include memory content, conversation text, account IDs, or quest prompts.
        if self.path.split("?", 1)[0] == "/api/rpg":
            try:
                robot_code = "TUYUEN-001"  # fixed public demo instance, not caller-controlled
                dashboard_fields = (
                    "ma_robot,cap_bac_hien_tai,giai_doan_gan_ket,"
                    "tong_diem_exp,moc_exp_cap_tiep_theo,phan_tram_len_cap,"
                    "tong_ky_uc_ca_nhan,tong_luot_dam_dao,diem_thong_minh_iq_100"
                )
                quest_fields = (
                    "ma_nhiem_vu,ten_nhiem_vu,tien_do_hien_tai,"
                    "muc_tieu_can_dat,diem_exp_thuong,da_hoan_thanh"
                )
                db = bot_brain.supabase
                dash_result = (db.table("robot_intelligence_dashboard")
                    .select(dashboard_fields).eq("ma_robot", robot_code).limit(1).execute())
                if not dash_result.data:
                    self.send_rpg_json({"ok": False, "error": "RPG dashboard unavailable"}, 503)
                    return
                quest_result = (db.table("v_instance_quest_tracker")
                    .select(quest_fields).eq("ma_robot", robot_code).limit(50).execute())
                self.send_rpg_json({
                    "ok": True,
                    "dashboard": dash_result.data[0],
                    "quests": sorted(quest_result.data or [], key=lambda q: q.get("ma_nhiem_vu") or "")
                })
            except Exception:
                logger.exception("Public RPG endpoint failed")
                self.send_rpg_json({"ok": False, "error": "RPG temporarily unavailable"}, 503)
            return

        if self.path in ["/", "/index.html"] or self.path.startswith("/?"):
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
            data = json.loads(post_data.decode("utf-8")) if post_data else {}

            user_msg = (data.get("message") or "").strip()
            selected_model = data.get("model", DEFAULT_MODEL)
            selected_voice = data.get("voice", "nam_minh")

            if len(user_msg) < 2:
                resp_bytes = json.dumps({"reply": "", "audio": None}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(resp_bytes)
                return

            bot_brain.AI_MODEL = selected_model
            logger.info(f"🎙️ Nhận câu hỏi ({selected_model}): {user_msg}")

            try:
                # 1. Bối cảnh nhân vật & Ký ức dài hạn
                instance_id, base_system_prompt = bot_brain.get_instance_context()
                memories_text = bot_brain.get_verified_memories(instance_id)

                # 2. Mở rộng truy vấn Kho tri thức RAG
                knowledge_context = ""
                triggers = [
                    "binh pháp", "tôn tử", "đạo đức kinh", "lão tử", "tào tháo", "nước", "thất bại", "chiến lược",
                    "robot", "phần cứng", "dự án", "tử uyên", "gọi vốn", "đầu tư", "chi phí", "raspberry",
                    "esp32", "giá", "bom", "mạch", "khớp cổ", "servo", "loa", "mic", "ký ức", "mô hình b", "supabase"
                ]
                if any(k in user_msg.lower() for k in triggers) or len(user_msg.split()) >= 4:
                    knowledge_context = bot_brain.retrieve_knowledge(user_msg, limit=2)

                # 3. Quy tắc độ dài co giãn tự nhiên (DE-R03)
                depth_rule = (
                    "\n\nQUY TẮC ĐỘ DÀI VÀ VĂN PHONG (DE-R03):\n"
                    "- Với câu hỏi xã giao, than vãn: Trả lời ngắn gọn, ấm áp, thấu cảm (1 - 3 câu).\n"
                    "- Với câu hỏi có chiều sâu về dự án, kỹ thuật, mưu lược hoặc triết học: HÃY TỰ DO MỞ RỘNG DUNG LƯỢNG tương xứng với tầm vóc câu hỏi. "
                    "Phân tích khúc chiết, đanh thép, sắc bén, viết thành đoạn văn liền mạch không gạch đầu dòng để đọc qua loa tự nhiên nhất."
                )

                full_prompt = base_system_prompt + depth_rule + "\n\n" + memories_text + knowledge_context

                # 4. Dùng mã phiên sạch 888999 và lọc bỏ hoàn toàn các tin nhắn rỗng cũ
                conv_id = bot_brain.get_or_create_conversation(instance_id, 888999)
                raw_history = bot_brain.get_recent_messages(conv_id, limit=6)
                clean_history = [
                    m for m in raw_history
                    if m.get("content") and len(m["content"].strip()) >= 2
                ]

                payload = [{"role": "system", "content": full_prompt}]
                payload.extend(clean_history)
                payload.append({"role": "user", "content": user_msg})

                # 5. Gọi mô hình AI
                reply_text = bot_brain.call_ai_model(payload)

                # 6. Lưu tin nhắn hợp lệ vào CSDL
                bot_brain.save_message(conv_id, "user", user_msg)
                bot_brain.save_message(conv_id, "assistant", reply_text)

                # 7. Sinh âm thanh theo chuẩn ổn định của V3.4
                audio_b64 = None
                if selected_voice == "vip_taothao":
                    audio_b64 = generate_fish_audio(reply_text)

                if not audio_b64:
                    voice_fallback = selected_voice if selected_voice in FREE_VOICES else "nam_minh"
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        audio_b64 = loop.run_until_complete(generate_free_voice(reply_text, voice_fallback))
                    finally:
                        loop.close()

                # 8. Phản hồi về điện thoại
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
    logger.info(f"🚀 MẶT ROBOT TỬ UYÊN V3.7 (STABLE AUDIO & MULTI-TURN) LIVE TẠI: {port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run()