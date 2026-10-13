#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỰ ÁN TỬ UYÊN — MÁY CHỦ ROBOT WEB CYBER-FACE
Cung cấp giao diện mặt robot và cổng API đàm thoại thời gian thực.
"""

import os
import json
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
import bot_brain  # Tận dụng toàn bộ não bộ Tử Uyên sẵn có!

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("TuUyenWebServer")

class RobotWebHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # Trả về giao diện mặt robot
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            with open("index.html", "rb") as f:
                self.wfile.write(f.read())
        else:
            super().do_GET()

    def do_POST(self):
        # Cổng API tiếp nhận giọng nói từ Web
        if self.path == "/api/chat":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode("utf-8"))
            user_msg = data.get("message", "")
            logger.info(f"🎙️ Web Voice Input: {user_msg}")

            try:
                # 1. Lấy bối cảnh nhân vật, ký ức và RAG từ database
                instance_id, base_system_prompt = bot_brain.get_instance_context()
                memories_text = bot_brain.get_verified_memories(instance_id)
                
                knowledge_context = ""
                if any(k in user_msg.lower() for k in ["binh pháp", "tôn tử", "đạo đức kinh", "lão tử", "tào tháo", "nước", "thất bại", "chiến lược"]):
                    knowledge_context = bot_brain.retrieve_knowledge(user_msg, limit=1)

                full_prompt = base_system_prompt + "\n\n" + memories_text + knowledge_context

                # 2. Lấy phiên trò chuyện Web
                conv_id = bot_brain.get_or_create_conversation(instance_id, 999999) # 999999 là ID kênh Web
                recent_history = bot_brain.get_recent_messages(conv_id, limit=6)

                payload = [{"role": "system", "content": full_prompt}]
                payload.extend(recent_history)
                payload.append({"role": "user", "content": user_msg})

                # 3. Gọi AI sinh phản hồi
                reply_text = bot_brain.call_ai_model(payload)

                # 4. Lưu hội thoại vào Supabase
                bot_brain.save_message(conv_id, "user", user_msg)
                bot_brain.save_message(conv_id, "assistant", reply_text)

                # Trả về kết quả cho trình duyệt
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                response_json = json.dumps({"reply": reply_text}, ensure_ascii=False)
                self.wfile.write(response_json.encode("utf-8"))

            except Exception as e:
                logger.error(f"Lỗi xử lý: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"reply": "Tr\xe1\xbb\xa5c tr\xe1\xba\xb7c b\xe1\xbb\x99 n\xc3\xa3o"}')

def run(port=8000):
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, RobotWebHandler)
    logger.info(f"🚀 MẶT ROBOT TỬ UYÊN ĐÃ SẴN SÀNG TẠI: http://localhost:{port}")
    logger.info(f"📱 Trên điện thoại: Mở trình duyệt gõ http://<IP_MÁY_TÍNH>:{port}")
    httpd.serve_forever()

if __name__ == "__main__":
    run()