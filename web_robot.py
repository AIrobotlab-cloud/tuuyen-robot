#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — MÁY CHỦ ROBOT WEB V2.6
1. Chuẩn hóa Content-Length mạng di động (chống lỗi kết nối).
2. Tích hợp chuyển đổi nhanh Model: Training (qwen-flash ~2đ) <-> Demo (gpt-4o).
"""

import os
import json
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
import bot_brain

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("TuUyenWebServer")

# Mặc định dùng mô hình siêu rẻ qwen-flash để huấn luyện tiết kiệm (~2đ/câu)
CURRENT_MODEL = os.getenv("AI_MODEL", "qwen-flash")

class RobotWebHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open("index.html", "rb") as f:
                self.wfile.write(f.read())
        else:
            super().do_GET()

    def do_POST(self):
        global CURRENT_MODEL
        if self.path == "/api/chat":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode("utf-8"))
            user_msg = data.get("message", "").strip()
            logger.info(f"🎙️ Nhận tin nhắn: {user_msg}")

            # Lệnh chuyển đổi mô hình nhanh
            if user_msg.lower().startswith("/model"):
                parts = user_msg.split()
                if len(parts) > 1:
                    target = parts[1].lower()
                    if "4o" in target or "pro" in target:
                        CURRENT_MODEL = "gpt-4o"
                        bot_brain.AI_MODEL = "gpt-4o"
                        reply_text = "Đã chuyển sang mô hình CAO CẤP (GPT-4o) để đi gặp nhà đầu tư."
                    elif "flash" in target or "re" in target or "cheap" in target:
                        CURRENT_MODEL = "qwen-flash"
                        bot_brain.AI_MODEL = "qwen-flash"
                        reply_text = "Đã chuyển sang mô hình TIẾT KIỆM (Qwen-Flash ~2đ/câu) để huấn luyện liên tục."
                    else:
                        CURRENT_MODEL = parts[1]
                        bot_brain.AI_MODEL = parts[1]
                        reply_text = f"Đã chuyển sang mô hình: {CURRENT_MODEL}"
                else:
                    reply_text = f"Mô hình hiện tại đang chạy là: {CURRENT_MODEL}"

                resp_bytes = json.dumps({"reply": reply_text}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(resp_bytes)
                return

            try:
                # 1. Bối cảnh nhân vật và tri thức RAG
                instance_id, base_system_prompt = bot_brain.get_instance_context()
                memories_text = bot_brain.get_verified_memories(instance_id)

                knowledge_context = ""
                triggers = ["binh pháp", "tôn tử", "đạo đức kinh", "lão tử", "tào tháo", "nước", "thất bại", "chiến lược"]
                if any(k in user_msg.lower() for k in triggers):
                    knowledge_context = bot_brain.retrieve_knowledge(user_msg, limit=1)

                full_prompt = base_system_prompt + "\n\n" + memories_text + knowledge_context

                # 2. Quản lý phiên hội thoại
                conv_id = bot_brain.get_or_create_conversation(instance_id, 999999)
                recent_history = bot_brain.get_recent_messages(conv_id, limit=6)

                payload = [{"role": "system", "content": full_prompt}]
                payload.extend(recent_history)
                payload.append({"role": "user", "content": user_msg})

                # Đảm bảo dùng model đã chọn
                bot_brain.AI_MODEL = CURRENT_MODEL

                # 3. Gọi AI
                reply_text = bot_brain.call_ai_model(payload)

                # 4. Lưu CSDL Supabase
                bot_brain.save_message(conv_id, "user", user_msg)
                bot_brain.save_message(conv_id, "assistant", reply_text)

                # 5. Đóng gói phản hồi chuẩn mạng di động
                resp_bytes = json.dumps({"reply": reply_text, "model": CURRENT_MODEL}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(resp_bytes)

            except Exception as e:
                logger.error(f"Lỗi: {e}")
                err_bytes = json.dumps({"reply": f"Đạo hữu thứ lỗi, kết nối gặp gián đoạn: {str(e)}"}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(err_bytes)))
                self.send_header("Connection", "close")
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