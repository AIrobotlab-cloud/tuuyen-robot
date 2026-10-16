#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — MÁY CHỦ ROBOT WEB V3.8.5 (FAST VOICE FIRST CHUNK & CHẨN ĐOÁN LLM/TTS)
Khắc phục triệt để:
1. Tách luồng hội thoại sạch (bỏ qua toàn bộ tin nhắn rỗng cũ trong CSDL làm AI bị kẹt ở câu đầu tiên).
2. Khôi phục cơ chế ghi file âm thanh chuẩn của V3.4 kèm chống trùng file, đảm bảo phát tiếng 100%.
3. HTTP nhiều luồng để nhận kết nối/health check; khóa tuần tự các thao tác dùng bot_brain/Supabase.
4. Giữ nguyên DE-R03, RAG, RPG, giọng nói và các API hiện hành.
"""

import os
import time
import json
import base64
import asyncio
import logging
import threading
import secrets
import re
from urllib.parse import urlsplit, parse_qs
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import edge_tts
import requests
import bot_brain

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("TuUyenWebServer")

# Bảo vệ bot_brain.AI_MODEL, phiên chat và Supabase dùng chung.
# GET /healthz vẫn được trả lời độc lập khi một lượt chat đang chạy.
BRAIN_LOCK = threading.Lock()
TTS_LOCK = threading.Lock()  # Limit external synthesis to one request at a time.
VOICE_JOBS_LOCK = threading.Lock()
VOICE_JOBS = {}  # volatile short-lived chunks: no persistence or personal data in logs
VOICE_JOB_TTL = 900  # 15 minutes
VOICE_JOB_LIMIT = 5


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



def split_voice_text(text):
    """Short initial utterance, then natural longer segments. Preserves all words."""
    cleaned = text.replace("*", "").replace("#", "").replace("`", "").strip()
    sentences = re.split(r"(?<=[.!?。！？;；])\s+|\n+", cleaned)
    parts = []
    for sentence in sentences:
        words = sentence.split()
        current = ""
        for word in words:
            limit = 115 if not parts else 260
            if current and len(current) + 1 + len(word) > limit:
                parts.append(current)
                current = word
            else:
                current = f"{current} {word}" if current else word
        if current:
            parts.append(current)
    # Small introductory fragments should be spoken together if space allows.
    packed = []
    for part in parts:
        limit = 115 if not packed else 260
        if packed and len(packed[-1]) + 1 + len(part) <= (115 if len(packed) == 1 else 260):
            packed[-1] += " " + part
        else:
            packed.append(part)
    if len(packed) > 12:
        packed = packed[:11] + [" ".join(packed[11:])]
    return packed or [cleaned]


def create_voice_job(chunks, voice):
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with VOICE_JOBS_LOCK:
        for old_key, value in list(VOICE_JOBS.items()):
            if now - value["created"] > VOICE_JOB_TTL:
                del VOICE_JOBS[old_key]
        while len(VOICE_JOBS) >= VOICE_JOB_LIMIT:
            del VOICE_JOBS[min(VOICE_JOBS, key=lambda k: VOICE_JOBS[k]["created"])]
        VOICE_JOBS[token] = {"created": now, "chunks": chunks, "voice": voice}
    return token


def get_voice_chunk(token, idx):
    with VOICE_JOBS_LOCK:
        job = VOICE_JOBS.get(token)
        if not job or time.monotonic() - job["created"] > VOICE_JOB_TTL:
            VOICE_JOBS.pop(token, None)
            return None
        if idx < 1 or idx >= len(job["chunks"]):
            return None
        return job["chunks"][idx], job["voice"]


def make_voice_audio(text, voice):
    with TTS_LOCK:
        if voice == "vip_taothao":
            fish_audio = generate_fish_audio(text)
            if fish_audio:
                return fish_audio
        fallback = voice if voice in FREE_VOICES else "nam_minh"
        # asyncio.run() makes and closes its own loop in the request thread.
        return asyncio.run(generate_free_voice(text, fallback))


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
        if self.path.split("?", 1)[0] == "/api/voice_chunk":
            params = parse_qs(urlsplit(self.path).query)
            token = params.get("job", [""])[0]
            try:
                idx = int(params.get("n", ["-1"])[0])
            except ValueError:
                idx = -1
            item = get_voice_chunk(token, idx)
            if item is None:
                self.send_rpg_json({"ok": False, "error": "Voice segment expired or invalid"}, 404)
                return
            text, voice = item
            started = time.perf_counter()
            logger.info("[TY-DIAG] CHUNK_START index=%d", idx)
            try:
                audio = make_voice_audio(text, voice)
                if not audio:
                    raise RuntimeError("Voice synthesis returned empty content")
                logger.info("[TY-DIAG] CHUNK_DONE index=%d duration=%.2fs", idx, time.perf_counter() - started)
                self.send_rpg_json({"ok": True, "audio": audio})
            except Exception:
                logger.exception("[TY-DIAG] CHUNK_FAILED index=%d", idx)
                self.send_rpg_json({"ok": False, "error": "Voice temporarily unavailable"}, 503)
            return

        if self.path.split("?", 1)[0] == "/healthz":
            self.send_rpg_json({"ok": True, "service": "tuuyen-robot"})
            return

        # V3.8.1: Public RPG showcase. Only allowlisted, aggregate, non-personal fields.
        # Never include memory content, conversation text, account IDs, or quest prompts.
        if self.path.split("?", 1)[0] == "/api/rpg":
            with BRAIN_LOCK:
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

            BRAIN_LOCK.acquire()
            brain_locked = True
            try:
                bot_brain.AI_MODEL = selected_model
                logger.info("🎙️ Nhận câu hỏi model=%s length=%s", selected_model, len(user_msg))
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
                llm_start = time.perf_counter()
                logger.info("[TY-DIAG] LLM_START")
                reply_text = bot_brain.call_ai_model(payload)
                llm_elapsed = time.perf_counter() - llm_start
                logger.info("[TY-DIAG] LLM_DONE duration=%.2fs", llm_elapsed)

                # 6. Lưu tin nhắn hợp lệ vào CSDL
                bot_brain.save_message(conv_id, "user", user_msg)
                bot_brain.save_message(conv_id, "assistant", reply_text)

                # Keep serialized brain/Supabase access, but do not hold its lock during TTS.
                BRAIN_LOCK.release()
                brain_locked = False

                # V3.8.5: synthesize only the short first part before HTTP response.
                chunks = split_voice_text(reply_text)
                voice_job = create_voice_job(chunks, selected_voice) if len(chunks) > 1 else None
                tts_start = time.perf_counter()
                logger.info("[TY-DIAG] TTS_START voice=%s first_chars=%d chunks=%d", selected_voice, len(chunks[0]), len(chunks))
                audio_b64 = make_voice_audio(chunks[0], selected_voice)
                tts_elapsed = time.perf_counter() - tts_start
                logger.info("[TY-DIAG] TTS_DONE duration=%.2fs audio_ok=%s", tts_elapsed, bool(audio_b64))

                # 8. Phản hồi về điện thoại
                resp_bytes = json.dumps({"reply": reply_text, "audio": audio_b64,
                                         "voice_job": voice_job, "voice_chunks": len(chunks)}, ensure_ascii=False).encode("utf-8")
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
            finally:
                if brain_locked:
                    BRAIN_LOCK.release()


def run(port=None):
    if port is None:
        port = int(os.environ.get("PORT", 8000))
    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, RobotWebHandler)
    logger.info(f"🚀 MẶT ROBOT TỬ UYÊN V3.8.5 (FAST VOICE + THREADED HTTP + DIAG) LIVE TẠI: {port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run()