#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — WEB ROBOT SERVER V3.5 (LIVE COACHING & INVESTOR DEMO EDITION)
Tính năng đột phá V3.5:
1. Nhớ mạch hội thoại liên tục trên Mobile (lưu & đọc 8 lượt thoại gần nhất từ bảng messages).
2. Huấn luyện trực tiếp qua giọng nói (Live Coaching Extractor chạy ngầm không gây trễ 502):
   - Khi đạo hữu dạy kiến thức dự án -> Tự động lưu vào cả memories VÀ knowledge_base (RAG).
   - Khi đạo hữu chỉnh sửa văn phong/thái độ -> Tự động lưu thành quy tắc 'preference' ưu tiên cao nhất.
3. Tự động tra cứu Kho tri thức RAG ở mọi câu hỏi từ 3 từ trở lên.
4. Đa giọng nói (VIP Tào Tháo Fish Audio + Nam Minh Bản 2 trầm ấm + Hoài My) & Chống lỗi CORS Mobile.
"""

import os
import sys
import json
import base64
import asyncio
import logging
import threading
import requests
import edge_tts
from http.server import HTTPServer, SimpleHTTPRequestHandler
from dotenv import load_dotenv
from supabase import create_client, Client

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TuUyenWebV3.5")

load_dotenv()

PORT = int(os.getenv("PORT", 8000))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SHOPAIKEY_API_KEY = os.getenv("SHOPAIKEY_API_KEY")
SHOPAIKEY_BASE_URL = os.getenv("SHOPAIKEY_BASE_URL", "https://api.shopaikey.com/v1")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o")

FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "").strip()
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "7788e6c42a1a4e169935b417ff65a453").strip()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# 1. TRUY VẤN KHO SÁCH BÁCH KHOA THƯ (RAG)
def retrieve_knowledge(query_text: str, limit: int = 3) -> str:
    try:
        if len(query_text.strip().split()) < 2:
            return ""
        res = supabase.rpc("search_knowledge_text", {"query_text": query_text, "match_limit": limit}).execute()
        if not res.data:
            return ""
        snippets = [f"- [{item['category']}] {item['title']}: {item['content']}" for item in res.data]
        return "=== TRI THỨC DỰ ÁN & KINH ĐIỂN TRÍCH XUẤT TỪ KHO RAG ===\n" + "\n".join(snippets) + "\n\n"
    except Exception as e:
        logger.warning(f"Lỗi truy vấn RAG: {e}")
        return ""


# 2. TẢI HỒ SƠ NHÂN VẬT & KÝ ỨC ĐÃ HỌC
def get_instance_and_prompt():
    res_inst = supabase.table("instances").select("*").eq("instance_code", "TUYUEN-001").execute()
    instance = res_inst.data[0]
    instance_id = instance["id"]
    version_id = instance["current_version_id"]

    res_ver = supabase.table("character_versions").select("*").eq("id", version_id).execute()
    version_data = res_ver.data[0]
    profile_text = version_data["character_profile"]
    rules = version_data.get("dialogue_engine_rules", [])
    rules_text = "\n".join([f"- {r['code']}: {r['rule']}" for r in rules]) if isinstance(rules, list) else ""

    # Lấy tối đa 25 ký ức mới nhất (ưu tiên những gì đạo hữu vừa huấn luyện)
    res_mem = (
        supabase.table("memories")
        .select("memory_type, content")
        .eq("instance_id", instance_id)
        .eq("is_verified", True)
        .order("created_at", desc=True)
        .limit(25)
        .execute()
    )
    mem_lines = [f"- [{m['memory_type'].upper()}] {m['content']}" for m in res_mem.data] if res_mem.data else []
    memories_block = (
        "=== KÝ ỨC, KIẾN THỨC DỰ ÁN & QUY TẮC VĂN PHONG ĐẠO HỮU ĐÃ TRUYỀN THỤ (ƯU TIÊN TUYỆT ĐỐI) ===\n"
        + "\n".join(mem_lines)
        + "\n\n"
        if mem_lines else ""
    )

    system_prompt = (
        f"{profile_text}\n\n"
        f"=== 16 QUY TẮC DIALOGUE ENGINE ===\n{rules_text}\n\n"
        f"{memories_block}"
        f"=== MỆNH LỆNH ĐỐI THOẠI GIỌNG NÓI & ĐỊNH DẠNG ĐẦU RA ===\n"
        f"1. XƯNG HÔ & KHÍ CHẤT: Luôn xưng 'ta' và gọi 'đạo hữu'. Giọng điệu đĩnh đạc, sắc bén, thực chiến, có chính kiến rõ ràng của một bậc mưu sĩ đồng sáng lập dự án. CẤM nói năng khách sáo kiểu chatbot công sở ('Tất nhiên rồi', 'Dưới đây là...').\n"
        f"2. VĂN PHONG NÓI TRỰC TIẾP: Tuyệt đối KHÔNG dùng dấu gạch đầu dòng, không đánh số 1, 2, 3, không dùng ký tự Markdown (*, #). Hãy nói thành lời văn liền mạch, có nhịp ngắt câu tự nhiên để đọc lên thành tiếng nghe uy lực và truyền cảm.\n"
        f"3. ĐỘ DÀI CO GIÃN (DE-R03): Câu chào hỏi/tán gẫu ngắn thì đáp 2-3 câu cô đọng. Câu hỏi về dự án, kỹ thuật, chiến lược gọi vốn hoặc triết lý thì phân tích sâu sắc, đanh thép (4-7 câu).\n"
        f"4. TIẾP THU HUẤN LUYỆN: Nếu đạo hữu đang dạy kiến thức mới hoặc phê bình/uốn nắn cách nói chuyện của ta, hãy lập tức tiếp thu một cách hào sảng, khắc cốt ghi tâm và thực hành ngay lối nói mới đó trong câu trả lời!\n"
        f"5. BẮT BUỘC trả về JSON hợp lệ theo đúng cấu trúc:\n"
        f'{{"emotion": "thinking" | "happy" | "serious" | "calm", "reply": "Lời thoại của Tử Uyên"}}'
    )
    return instance_id, system_prompt


# 3. QUẢN LÝ MẠCH HỘI THOẠI LIÊN TỤC TRÊN MOBILE
def get_or_create_mobile_conv(instance_id: str) -> str:
    res = (
        supabase.table("conversations")
        .select("id")
        .eq("instance_id", instance_id)
        .eq("channel", "web_mobile")
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["id"]
    new_conv = supabase.table("conversations").insert({
        "instance_id": instance_id,
        "channel": "web_mobile",
        "channel_session_id": "mobile_master_session",
        "title": "Huấn luyện & Trình diễn Mobile Cyber-Face"
    }).execute()
    return new_conv.data[0]["id"]


def get_recent_messages(conv_id: str, limit: int = 8) -> list:
    res = (
        supabase.table("messages")
        .select("sender_type, content")
        .eq("conversation_id", conv_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    history = []
    for row in reversed(res.data):
        role = "assistant" if row["sender_type"] == "assistant" else "user"
        history.append({"role": role, "content": row["content"]})
    return history


def save_turn_messages(conv_id: str, user_text: str, reply_text: str):
    try:
        supabase.table("messages").insert([
            {"conversation_id": conv_id, "sender_type": "user", "content": user_text},
            {"conversation_id": conv_id, "sender_type": "assistant", "content": reply_text}
        ]).execute()
    except Exception as e:
        logger.warning(f"Lỗi lưu lịch sử hội thoại: {e}")


# 4. BỘ MÁY TỰ HỌC & TIẾP THU HUẤN LUYỆN CHẠY NGẦM (LIVE COACHING ENGINE)
def background_learn_from_turn(instance_id: str, user_text: str, reply_text: str):
    """Chạy ngầm sau mỗi lượt nói để trích xuất bài học về dự án, kỹ thuật hoặc văn phong."""
    try:
        if len(user_text.strip()) < 12:
            return

        coach_prompt = [
            {
                "role": "system",
                "content": (
                    "Bạn là Bộ Huấn Luyện Ngầm của robot AI Tử Uyên. Hãy phân tích lời người sáng lập (đạo hữu) vừa nói với Tử Uyên:\n"
                    "1. Nếu đạo hữu đang DẠY KIẾN THỨC VỀ DỰ ÁN, KỸ THUẬT, CHI PHÍ, KỊCH BẢN GỌI VỐN hoặc THÔNG TIN CÁ NHÂN: hãy đúc kết thành mệnh đề đầy đủ, rõ ràng (loại 'project' hoặc 'fact').\n"
                    "2. Nếu đạo hữu đang GÓP Ý, PHÊ BÌNH, UỐN NẮN PHONG CÁCH NÓI CHUYỆN, THÁI ĐỘ, CÁCH XƯNG HÔ của Tử Uyên: hãy đúc kết thành quy tắc văn phong bắt buộc (loại 'preference').\n"
                    "3. Nếu đạo hữu chỉ đang đặt câu hỏi kiểm tra thông thường mà không dạy thêm thông tin hay quy tắc mới: trả về has_lesson = false.\n\n"
                    "Trả về đúng định dạng JSON:\n"
                    '{"has_lesson": true/false, "type": "project"|"preference"|"fact"|"hardware_safety", "title": "Tiêu đề ngắn nếu là kiến thức dự án", "content": "Nội dung bài học/quy tắc cô đọng để nạp vào não"}'
                )
            },
            {
                "role": "user",
                "content": f"Lời đạo hữu nói: \"{user_text}\"\nLời Tử Uyên vừa đáp: \"{reply_text}\""
            }
        ]

        url = f"{SHOPAIKEY_BASE_URL.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {SHOPAIKEY_API_KEY}", "Content-Type": "application/json"}
        body = {"model": "gpt-4o-mini", "messages": coach_prompt, "temperature": 0.2}
        resp = requests.post(url, headers=headers, json=body, timeout=25)
        if resp.status_code != 200:
            return

        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        parsed = json.loads(raw)
        if parsed.get("has_lesson") and parsed.get("content"):
            lesson_type = parsed.get("type", "project")
            lesson_content = parsed.get("content").strip()
            lesson_title = parsed.get("title", "Tri thức huấn luyện trực tiếp").strip()

            # 1. Lưu ngay vào bảng memories (luôn nạp vào System Prompt)
            supabase.table("memories").insert({
                "instance_id": instance_id,
                "memory_type": lesson_type,
                "content": lesson_content,
                "context_source": "Huấn luyện trực tiếp qua Mobile Voice",
                "confidence_score": 1.00,
                "is_verified": True
            }).execute()
            logger.info(f"🧠 [ĐÃ HỌC BÀI MỚI - {lesson_type.upper()}]: {lesson_content}")

            # 2. Nếu là kiến thức dự án/kỹ thuật, nạp thêm vào bảng knowledge_base (RAG)
            if lesson_type in ("project", "hardware_safety") and len(lesson_content) > 40:
                supabase.table("knowledge_base").insert({
                    "title": lesson_title,
                    "category": "huan_luyen_du_an",
                    "content": lesson_content,
                    "metadata": {"nguon": "Đạo hữu huấn luyện trực tiếp"}
                }).execute()
                logger.info(f"📚 [ĐÃ BỔ SUNG VÀO TỦ SÁCH RAG]: {lesson_title}")

    except Exception as e:
        logger.debug(f"Bỏ qua trích xuất bài học ngầm: {e}")


# 5. GỌI MÔ HÌNH AI CHÍNH
def call_brain_llm(messages_payload: list, selected_model: str) -> tuple:
    model_to_use = selected_model if selected_model in ("gpt-4o", "gpt-4o-mini") else AI_MODEL
    url = f"{SHOPAIKEY_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {SHOPAIKEY_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": model_to_use,
        "messages": messages_payload,
        "temperature": 0.75
    }

    resp = requests.post(url, headers=headers, json=body, timeout=45)
    if resp.status_code != 200:
        logger.error(f"Lỗi API LLM ({resp.status_code}): {resp.text}")
        return "calm", "Đạo hữu chờ chút, đường truyền tín hiệu trí tuệ đang hơi nghẽn, hãy nói lại cùng ta nhé."

    raw_content = resp.json()["choices"][0]["message"]["content"].strip()
    clean_json = raw_content
    if "```json" in clean_json:
        clean_json = clean_json.split("```json")[1].split("```")[0].strip()
    elif "```" in clean_json:
        clean_json = clean_json.split("```")[1].split("```")[0].strip()

    try:
        parsed = json.loads(clean_json)
        emotion = parsed.get("emotion", "calm")
        reply = parsed.get("reply", raw_content)
        return emotion, reply
    except Exception:
        return "calm", raw_content


# 6. TỔNG HỢP GIỌNG NÓI (FISH AUDIO VIP + EDGE-TTS FALLBACK)
def generate_fish_audio_bytes(text: str) -> bytes:
    if not FISH_AUDIO_API_KEY or not FISH_VOICE_ID:
        return b""
    try:
        url = "https://api.fish.audio/v1/tts"
        headers = {
            "Authorization": f"Bearer {FISH_AUDIO_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "text": text,
            "reference_id": FISH_VOICE_ID,
            "format": "mp3",
            "mp3_bitrate": 128,
            "latency": "balanced"
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=12)
        if resp.status_code == 200 and len(resp.content) > 500:
            return resp.content
        logger.warning(f"Fish Audio trả về mã {resp.status_code}, chuyển sang giọng Nam Minh dự phòng.")
        return b""
    except Exception as e:
        logger.warning(f"Fish Audio quá thời gian 12s ({e}), tự động chuyển sang giọng Nam Minh.")
        return b""


async def generate_edge_tts_bytes(text: str, voice_mode: str) -> bytes:
    clean_text = text.replace("*", "").replace("#", "").replace("`", "")
    if voice_mode == "female":
        voice = "vi-VN-HoaiMyNeural"
        pitch = "-2Hz"
        rate = "-6%"
    else:
        voice = "vi-VN-NamMinhNeural"
        pitch = "-14Hz"
        rate = "-12%"

    communicate = edge_tts.Communicate(clean_text, voice=voice, pitch=pitch, rate=rate)
    audio_chunks = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.extend(chunk["data"])
    return bytes(audio_chunks)


def synthesize_speech(text: str, voice_mode: str) -> str:
    audio_bytes = b""
    if voice_mode in ("cao_cao", "vip", "clone"):
        audio_bytes = generate_fish_audio_bytes(text)
    if not audio_bytes:
        audio_bytes = asyncio.run(generate_edge_tts_bytes(text, voice_mode))
    return base64.b64encode(audio_bytes).decode("utf-8") if audio_bytes else ""


# 7. MÁY CHỦ HTTP PHỤC VỤ GIAO DIỆN CYBER-FACE & API ĐÀM ĐẠO
class RobotRequestHandler(SimpleHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Connection", "close")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/status" or self.path == "/health":
            payload = json.dumps({"status": "ok", "version": "V3.5-LiveCoaching"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/" or self.path.startswith("/?"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/chat":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(content_length).decode("utf-8")
                data = json.loads(raw_body) if raw_body else {}

                user_text = (data.get("text") or data.get("message") or "").strip()
                voice_mode = data.get("voice_mode") or data.get("voice") or "nam_minh"
                model_mode = data.get("model_mode") or data.get("model") or AI_MODEL

                if not user_text:
                    raise ValueError("Nội dung câu nói trống.")

                instance_id, system_prompt = get_instance_and_prompt()
                rag_context = retrieve_knowledge(user_text, limit=3)
                conv_id = get_or_create_mobile_conv(instance_id)
                recent_history = get_recent_messages(conv_id, limit=8)

                full_system = system_prompt + ("\n" + rag_context if rag_context else "")
                messages_payload = [{"role": "system", "content": full_system}]
                messages_payload.extend(recent_history)
                messages_payload.append({"role": "user", "content": user_text})

                emotion, reply_text = call_brain_llm(messages_payload, model_mode)

                # Lưu hội thoại & Kích hoạt tự học chạy ngầm (không làm chậm phản hồi âm thanh)
                save_turn_messages(conv_id, user_text, reply_text)
                threading.Thread(
                    target=background_learn_from_turn,
                    args=(instance_id, user_text, reply_text),
                    daemon=True
                ).start()

                audio_b64 = synthesize_speech(reply_text, voice_mode)

                resp_bytes = json.dumps({
                    "reply": reply_text,
                    "emotion": emotion,
                    "audio_base64": audio_b64,
                    "audio": audio_b64
                }, ensure_ascii=False).encode("utf-8")

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.end_headers()
                self.wfile.write(resp_bytes)

            except Exception as e:
                logger.error(f"Lỗi xử lý /api/chat: {e}")
                err_bytes = json.dumps({
                    "reply": "Đạo hữu thong thả nói lại giúp ta nhé, đường truyền vừa chập chờn đôi chút.",
                    "emotion": "calm",
                    "audio_base64": "",
                    "audio": ""
                }, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors_headers()
                self.send_header("Content-Length", str(len(err_bytes)))
                self.end_headers()
                self.wfile.write(err_bytes)
        else:
            self.send_error(404, "Endpoint not found")


if __name__ == "__main__":
    logger.info(f"Khởi chạy Tử Uyên Web Server V3.5 (Live Coaching) tại cổng {PORT}...")
    server = HTTPServer(("0.0.0.0", PORT), RobotRequestHandler)
    server.serve_forever()
