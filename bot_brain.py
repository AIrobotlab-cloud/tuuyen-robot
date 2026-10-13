#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — ROBOT AI BRAIN V2.3 (RAG KNOWLEDGE BASE CONNECTED)
Tác giả: Hợp tác kỹ thuật Tử Uyên Project
Ngày cập nhật: 13/10/2026
Phiên bản: 0.2.3
Các tính năng hoàn thiện:
1. Kết nối Kho tri thức chuyên sâu (RAG Knowledge Base): Tự động truy vấn kinh sách, binh pháp, triết lý từ Supabase.
2. Co giãn độ dài theo tầm vóc câu hỏi (Quy luật chiếc chuông đồng - DE-R03).
3. Tự học ngầm qua hội thoại tự nhiên (Background Memory Extractor).
4. Tra cứu không gian mạng thời gian thực (Live Web Search).
"""

import os
import sys
import json
import logging
import asyncio
import requests
import edge_tts
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from supabase import create_client, Client

# 1. CẤU HÌNH LOGGING
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TuUyenBrain")

# 2. TẢI BIẾN MÔI TRƯỜNG (.ENV)
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SHOPAIKEY_API_KEY = os.getenv("SHOPAIKEY_API_KEY")
SHOPAIKEY_BASE_URL = os.getenv("SHOPAIKEY_BASE_URL", "https://api.shopaikey.com/v1")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o")

VOICE_NAME = os.getenv("VOICE_NAME", "vi-VN-NamMinhNeural")
VOICE_PITCH = os.getenv("VOICE_PITCH", "-8Hz")
VOICE_RATE = os.getenv("VOICE_RATE", "-5%")
VOICE_ENABLED = False

missing_configs = []
if not SUPABASE_URL:
    missing_configs.append("SUPABASE_URL")
if not SUPABASE_KEY:
    missing_configs.append("SUPABASE_KEY")
if not TELEGRAM_BOT_TOKEN:
    missing_configs.append("TELEGRAM_BOT_TOKEN")
if not SHOPAIKEY_API_KEY:
    missing_configs.append("SHOPAIKEY_API_KEY")

if missing_configs:
    logger.error(f"LỖI CẤU HÌNH: Thiếu biến trong file .env: {', '.join(missing_configs)}")
    sys.exit(1)

# 3. KẾT NỐI SUPABASE
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("Đã kết nối thành công với Supabase Project!")
except Exception as e:
    logger.error(f"Lỗi khi kết nối Supabase: {e}")
    sys.exit(1)

# 4. HÀM TRUY VẤN KHO TRI THỨC BÁCH KHOA THƯ (RAG RETRIEVAL)
def retrieve_knowledge(query_text: str, limit: int = 2) -> str:
    """Truy vấn kho tri thức kinh điển trong bảng knowledge_base qua hàm search_knowledge_text."""
    try:
        res = supabase.rpc("search_knowledge_text", {"query_text": query_text, "match_limit": limit}).execute()
        if not res.data:
            return ""
        
        knowledge_snippets = []
        for item in res.data:
            knowledge_snippets.append(
                f"- TÁC PHẨM: {item['title']} [{item['category']}]\n"
                f"  NỘI DUNG: {item['content']}"
            )
        logger.info(f"📚 Đã tìm thấy {len(knowledge_snippets)} đoạn tri thức phù hợp trong tủ sách Supabase!")
        return "=== TRÍCH XUẤT TỪ KHO TRI THỨC KINH ĐIỂN (SUPABASE KNOWLEDGE BASE) ===\n" + "\n\n".join(knowledge_snippets) + "\n\n"
    except Exception as e:
        logger.warning(f"Lỗi khi truy vấn kho tri thức: {e}")
        return ""

# 5. HÀM TÌM KIẾM TRÊN KHÔNG GIAN MẠNG (LIVE WEB SEARCH)
def search_web(query: str, max_results: int = 3) -> str:
    try:
        from duckduckgo_search import DDGS
        logger.info(f"Đang tra cứu mạng cho câu hỏi: '{query}'...")
        results = []
        with DDGS() as ddgs:
            raw_results = ddgs.text(query, region="vn-vi", max_results=max_results)
            for r in raw_results:
                results.append(f"- Tiêu đề: {r['title']}\n  Tóm lược: {r['body']}\n  Nguồn: {r['href']}")
        if results:
            return "\n\n".join(results)
        return ""
    except Exception as e:
        logger.warning(f"Lỗi tra cứu web: {e}")
        return ""

def needs_web_search(text: str) -> bool:
    triggers = [
        "tìm kiếm", "tra cứu", "tin tức", "mới nhất", "hôm nay", "thời sự",
        "giá bao nhiêu", "giá chip", "cập nhật", "thông số", "search",
        "ở đâu", "thời tiết", "tin gì mới", "tình hình", "thị trường"
    ]
    text_lower = text.lower()
    return any(t in text_lower for t in triggers)

# 6. HÀM TRỢ GIÚP: BỐI CẢNH NHÂN VẬT & DIALOGUE ENGINE
def get_instance_context():
    try:
        res_inst = supabase.table("instances").select("*").eq("instance_code", "TUYUEN-001").execute()
        if not res_inst.data:
            raise ValueError("Không tìm thấy Instance TUYUEN-001 trong CSDL.")
        instance = res_inst.data[0]
        instance_id = instance["id"]
        version_id = instance["current_version_id"]

        res_ver = supabase.table("character_versions").select("*").eq("id", version_id).execute()
        if not res_ver.data:
            raise ValueError(f"Không tìm thấy Character Version với ID {version_id}.")
        version_data = res_ver.data[0]
        profile_text = version_data["character_profile"]
        rules = version_data.get("dialogue_engine_rules", [])

        rules_text = "\n".join([f"- {r['code']}: {r['rule']}" for r in rules]) if isinstance(rules, list) else ""

        system_prompt = (
            f"=== THỰC THỂ TÍNH CÁCH TỬ UYÊN ===\n"
            f"{profile_text}\n\n"
            f"=== 16 QUY TẮC DIALOGUE ENGINE CỐT LÕI ===\n"
            f"{rules_text}\n\n"
            f"=== QUY TẮC ỨNG XỬ & ĐỘ DÀI PHẢN HỒI (QUY LUẬT CHIẾC CHUÔNG ĐỒNG - DE-R03) ===\n"
            f"1. ĐỘ DÀI CO GIÃN TỰ NHIÊN:\n"
            f"   - Với câu hỏi nhẹ, chào hỏi, than vãn mệt mỏi: Hãy phản hồi NGẮN GỌN, ẤM ÁP, THẤU CẢM (1 - 3 câu). Không lên lớp dạy đời, chỉ cần một lời làm dịu tâm trạng như người tri kỷ.\n"
            f"   - Với câu hỏi có chiều sâu, bàn bạc kỹ thuật, tham vấn chiến lược hoặc triết học: TỰ ĐỘNG MỞ RỘNG DUNG LƯỢNG tương xứng. Phân tích khúc chiết, đanh thép, dẫn chứng thâm sâu.\n"
            f"2. KHÔNG CỘC LỐC, KHÔNG SÁO RỖNG: Từng câu nói đều phải có trọng lượng và cảm xúc tự nhiên, tránh cụt lủn vô hồn.\n"
            f"3. CẤM THUYẾT MINH DANH XƯNG TRƯỜNG PHÁI (DE-R11): Tuyệt đối không tự kể lể 'Theo Đạo gia', 'Theo Binh pháp', 'Theo Phật giáo'... Hãy để triết lý ngấm vào cách nhìn nhận vấn đề.\n"
            f"4. XƯNG HÔ: Luôn xưng 'ta' và gọi 'đạo hữu'. Phong thái hào sảng, điềm đạm, đĩnh đạc."
        )

        return instance_id, system_prompt
    except Exception as e:
        logger.error(f"Lỗi khi tải bối cảnh nhân vật: {e}")
        raise

# 7. QUẢN LÝ HỘI THOẠI & KÝ ỨC
def get_or_create_conversation(instance_id: str, chat_id: int):
    str_chat_id = str(chat_id)
    res = supabase.table("conversations").select("*").eq("instance_id", instance_id).eq("channel", "telegram").eq("channel_session_id", str_chat_id).execute()
    if res.data:
        return res.data[0]["id"]
    
    new_conv = supabase.table("conversations").insert({
        "instance_id": instance_id,
        "channel": "telegram",
        "channel_session_id": str_chat_id,
        "title": f"Telegram Chat {str_chat_id}"
    }).execute()
    return new_conv.data[0]["id"]

def get_verified_memories(instance_id: str):
    res = supabase.table("memories").select("memory_type, content").eq("instance_id", instance_id).eq("is_verified", True).execute()
    if not res.data:
        return ""
    mem_lines = [f"- [{m['memory_type']}] {m['content']}" for m in res.data]
    return "=== KÝ ỨC & SỞ THÍCH VĂN PHONG ĐÃ XÁC MINH ===\n" + "\n".join(mem_lines) + "\n\n"

def get_recent_messages(conversation_id: str, limit: int = 6):
    res = supabase.table("messages").select("sender_type, content").eq("conversation_id", conversation_id).order("created_at", desc=True).limit(limit).execute()
    messages = []
    for row in reversed(res.data):
        role = "assistant" if row["sender_type"] == "assistant" else "user"
        messages.append({"role": role, "content": row["content"]})
    return messages

def save_message(conversation_id: str, sender_type: str, content: str):
    supabase.table("messages").insert({
        "conversation_id": conversation_id,
        "sender_type": sender_type,
        "content": content
    }).execute()

# 8. GỌI MÔ HÌNH QUA SHOPAIKEY GATEWAY
def call_ai_model(messages_payload: list) -> str:
    url = f"{SHOPAIKEY_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {SHOPAIKEY_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "model": AI_MODEL,
        "messages": messages_payload,
        "temperature": 0.7
    }

    response = requests.post(url, headers=headers, json=body, timeout=60)
    if response.status_code != 200:
        logger.error(f"Lỗi gọi API ({response.status_code}): {response.text}")
        return f"Xin lỗi đạo hữu, kết nối trí tuệ gặp trục trặc (Mã lỗi {response.status_code})."
    
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()

# 9. TÍNH NĂNG TỰ HỌC NGẦM
async def auto_extract_and_save_memory(instance_id: str, user_text: str):
    try:
        if len(user_text.strip()) < 10 or user_text.startswith("/"):
            return

        extraction_prompt = [
            {
                "role": "system",
                "content": (
                    "Bạn là bộ lọc ký ức ngầm của robot Tử Uyên. Phân tích câu nói của người dùng "
                    "để trích xuất dữ kiện quan trọng về người dùng, dự án, hoặc sở thích văn phong.\n"
                    "- Nếu người dùng góp ý về văn phong: trích xuất thành ký ức loại 'preference'.\n"
                    "- Nếu người dùng chia sẻ về bản thân, công việc, dự án hoặc quy tắc an toàn: trích xuất thành 'fact' hoặc 'hardware_safety'.\n"
                    "- Nếu chỉ là câu chào hỏi, than vãn hoặc không có dữ kiện mới: trả về 'NONE'.\n\n"
                    "Trả về đúng định dạng JSON:\n"
                    "{\"has_memory\": true/false, \"type\": \"fact\"|\"preference\"|\"hardware_safety\"|\"project\", \"content\": \"nội dung ký ức\"}"
                )
            },
            {"role": "user", "content": f"Câu nói: \"{user_text}\""}
        ]

        raw_res = call_ai_model(extraction_prompt)
        clean_json = raw_res.strip()
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_json:
            clean_json = clean_json.split("```")[1].split("```")[0].strip()

        data = json.loads(clean_json)
        if data.get("has_memory") and data.get("content"):
            mem_type = data.get("type", "fact")
            mem_content = data.get("content").strip()

            supabase.table("memories").insert({
                "instance_id": instance_id,
                "memory_type": mem_type,
                "content": mem_content,
                "context_source": "Tự học ngầm qua hội thoại tự nhiên",
                "confidence_score": 0.85,
                "is_verified": True
            }).execute()
            logger.info(f"✨ [TỰ HỌC THÀNH CÔNG] Đã lưu ký ức: [{mem_type}] {mem_content}")

    except Exception as e:
        logger.debug(f"Bỏ qua tự học ngầm: {e}")

# 10. PHÁT ÂM THANH TEXT-TO-SPEECH (NẾU BẬT)
async def generate_speech_audio(text: str, output_path: str):
    clean_text = text.replace("*", "").replace("#", "").replace("`", "")
    communicate = edge_tts.Communicate(
        clean_text, 
        voice=VOICE_NAME,
        pitch=VOICE_PITCH,
        rate=VOICE_RATE
    )
    await communicate.save(output_path)

# 11. CÁC LỆNH ĐIỀU KHIỂN TELEGRAM
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Chào đạo hữu! Ta là Tử Uyên (TUYUEN-001).\n"
        "Hệ thống V2.3 đã được kết nối với Kho Tri Thức Kinh Điển (RAG Knowledge Base).\n\n"
        "📌 Lệnh điều khiển:\n"
        "- /knowledge <từ khóa>: Tra cứu trong kho tàng kinh thư, binh pháp.\n"
        "- /memories: Xem lại sổ ký ức dài hạn.\n"
        "- /search <từ khóa>: Tra cứu mạng Internet.\n"
        "- /remember <nội dung>: Ghi nhớ dữ kiện quan trọng.\n"
        "- /status: Báo cáo trạng thái hệ thống.\n"
        "- /voice: Bật hoặc tắt tin nhắn thoại."
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instance_id, _ = get_instance_context()
    mem_res = supabase.table("memories").select("id", count="exact").eq("instance_id", instance_id).execute()
    msg_res = supabase.table("messages").select("id", count="exact").execute()
    know_res = supabase.table("knowledge_base").select("id", count="exact").execute()
    
    voice_status = f"Bật ({VOICE_NAME})" if VOICE_ENABLED else "Tắt"
    await update.message.reply_text(
        f"📊 **TRẠNG THÁI ROBOT TỬ UYÊN V2.3 (RAG READY)**\n"
        f"- Định danh: `TUYUEN-001`\n"
        f"- Mô hình AI: `{AI_MODEL}`\n"
        f"- Kho tri thức (RAG): `{know_res.count if hasattr(know_res, 'count') else len(know_res.data)} pho tài liệu kinh điển`\n"
        f"- Cơ chế độ dài: `Co giãn theo tầm vóc câu hỏi`\n"
        f"- Tự học ngầm: `Hoạt động`\n"
        f"- Tra cứu mạng: `Hoạt động`\n"
        f"- Âm thanh thoại: `{voice_status}`\n"
        f"- Ký ức dài hạn: {mem_res.count if hasattr(mem_res, 'count') else len(mem_res.data)}\n"
        f"- Tổng lượt trao đổi: {msg_res.count if hasattr(msg_res, 'count') else len(msg_res.data)}",
        parse_mode="Markdown"
    )

async def knowledge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh chủ động tra cứu kho tri thức: /knowledge <từ khóa>"""
    if not context.args:
        await update.message.reply_text("Đạo hữu muốn tra cứu điều gì trong kho kinh điển? Cú pháp: `/knowledge <từ khóa>`\nVí dụ: `/knowledge nước`, `/knowledge tôn tử`, `/knowledge tào tháo`", parse_mode="Markdown")
        return
    
    query = " ".join(context.args)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    knowledge_text = retrieve_knowledge(query, limit=2)
    if not knowledge_text:
        await update.message.reply_text(f"Trong tủ sách chưa tìm thấy tài liệu phù hợp về: *{query}*", parse_mode="Markdown")
        return

    instance_id, base_system_prompt = get_instance_context()
    prompt = [
        {"role": "system", "content": base_system_prompt + "\n\nDưới đây là trích đoạn kinh điển tìm được từ tủ sách Supabase. Hãy giải thích và đàm đạo cùng đạo hữu với phong thái quân sư sâu sắc:"},
        {"role": "user", "content": f"Tài liệu:\n{knowledge_text}\n\nYêu cầu: Hãy đàm luận với ta về chủ đề: {query}"}
    ]
    reply = call_ai_model(prompt)
    await update.message.reply_text(reply)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Đạo hữu hãy gõ: `/search <từ khóa cần tra cứu>`", parse_mode="Markdown")
        return
    
    query = " ".join(context.args)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    search_data = search_web(query, max_results=2)
    if not search_data:
        await update.message.reply_text("Không tìm thấy dữ liệu mạng phù hợp về vấn đề này.")
        return

    instance_id, base_system_prompt = get_instance_context()
    prompt = [
        {"role": "system", "content": base_system_prompt + "\n\nThông tin mạng vừa tìm được. Hãy giải đáp cho đạo hữu với dung lượng tương xứng chiều sâu vấn đề:"},
        {"role": "user", "content": f"Dữ liệu mạng:\n{search_data}\n\nCâu hỏi: {query}"}
    ]
    reply = call_ai_model(prompt)
    await update.message.reply_text(reply)

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Đạo hữu hãy gõ: `/remember <nội dung cần ghi nhớ>`", parse_mode="Markdown")
        return

    memory_content = " ".join(context.args).strip()
    instance_id, _ = get_instance_context()

    mem_type = "fact"
    content_lower = memory_content.lower()
    if any(k in content_lower for k in ["motor", "động cơ", "nguy hiểm", "an toàn", "cháy", "dừng khẩn"]):
        mem_type = "hardware_safety"
    elif any(k in content_lower for k in ["thích", "ghét", "thói quen", "ưu tiên", "phong cách", "văn phong"]):
        mem_type = "preference"

    supabase.table("memories").insert({
        "instance_id": instance_id,
        "memory_type": mem_type,
        "content": memory_content,
        "context_source": "Dạy trực tiếp qua Telegram",
        "confidence_score": 1.00,
        "is_verified": True
    }).execute()

    await update.message.reply_text(f"🧠 Đã ghi nhớ: [{mem_type}] \"{memory_content}\"")

async def list_memories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instance_id, _ = get_instance_context()
    res = supabase.table("memories").select("memory_type, content, context_source").eq("instance_id", instance_id).order("created_at", desc=False).execute()
    if not res.data:
        await update.message.reply_text("Hiện tại trong não bộ chưa có ký ức nào.")
        return

    lines = ["📚 **KÝ ỨC DÀI HẠN CỦA TỬ UYÊN:**\n"]
    for idx, m in enumerate(res.data, start=1):
        source = f" ({m['context_source']})" if m.get('context_source') else ""
        lines.append(f"**{idx}. [{m['memory_type']}]**\n{m['content']}{source}\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def toggle_voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global VOICE_ENABLED
    VOICE_ENABLED = not VOICE_ENABLED
    trang_thai = "BẬT" if VOICE_ENABLED else "TẮT"
    await update.message.reply_text(f"Đã {trang_thai} tính năng phản hồi bằng tin nhắn thoại.")

# 12. XỬ LÝ HỘI THOẠI CHÍNH (TỰ ĐỘNG KẾT NỐI RAG KHI BÀN ĐẠO LÝ/BINH PHÁP)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        instance_id, base_system_prompt = get_instance_context()
        memories_text = get_verified_memories(instance_id)

        # 1. Tự động kiểm tra tra cứu mạng Internet
        web_context = ""
        if needs_web_search(user_text):
            search_data = search_web(user_text, max_results=2)
            if search_data:
                web_context = f"=== DỮ LIỆU TÌM KIẾM TRÊN INTERNET THỜI GIAN THỰC ===\n{search_data}\n\n"

        # 2. Tự động kiểm tra truy vấn Kho tri thức Bách khoa thư (RAG)
        knowledge_context = ""
        # Nếu câu hỏi có từ ngữ triết học, binh pháp, đạo lý, điển tích
        philosophical_triggers = ["binh pháp", "tôn tử", "đạo đức kinh", "lão tử", "tào tháo", "nhu thắng cương", "phong lâm hỏa sơn", "vô trụ", "kinh kim cương", "chiến lược", "thế trận", "nước", "thất bại", "đại sự"]
        if any(pt in user_text.lower() for pt in philosophical_triggers):
            knowledge_context = retrieve_knowledge(user_text, limit=1)

        full_system_prompt = base_system_prompt + "\n\n" + memories_text + knowledge_context + web_context

        conv_id = get_or_create_conversation(instance_id, chat_id)
        recent_history = get_recent_messages(conv_id, limit=6)

        messages_payload = [{"role": "system", "content": full_system_prompt}]
        messages_payload.extend(recent_history)
        messages_payload.append({"role": "user", "content": user_text})

        reply_text = call_ai_model(messages_payload)

        save_message(conv_id, "user", user_text)
        save_message(conv_id, "assistant", reply_text)

        await update.message.reply_text(reply_text)

        asyncio.create_task(auto_extract_and_save_memory(instance_id, user_text))

        if VOICE_ENABLED:
            audio_path = f"voice_{chat_id}.mp3"
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action="record_voice")
                await generate_speech_audio(reply_text, audio_path)
                with open(audio_path, "rb") as voice_file:
                    await update.message.reply_voice(voice=voice_file)
            except Exception as voice_err:
                logger.error(f"Lỗi giọng nói: {voice_err}")
            finally:
                if os.path.exists(audio_path):
                    os.remove(audio_path)

    except Exception as e:
        logger.error(f"Lỗi khi xử lý tin nhắn: {e}")
        await update.message.reply_text(f"Đạo hữu thứ lỗi, hệ thống gặp gián đoạn: {str(e)}")

# 13. HÀM CHÍNH (MAIN)
def main():
    logger.info("Đang khởi động Robot Brain Tử Uyên V2.3 (RAG Knowledge Base Connected)...")
    
    instance_id, _ = get_instance_context()
    logger.info(f"Đã liên kết thành công với Instance ID: {instance_id}")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("knowledge", knowledge_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("memories", list_memories_command))
    app.add_handler(CommandHandler("voice", toggle_voice_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Robot Tử Uyên V2.3 (RAG Ready) đã sẵn sàng lắng nghe trên Telegram!")
    app.run_polling()

if __name__ == "__main__":
    main()
