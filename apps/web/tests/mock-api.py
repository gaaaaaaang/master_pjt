"""Local UI fault fixtures. No model or production connections.
Run python3 tests/mock-api.py and FAB_API_TARGET=http://127.0.0.1:8769 npm run dev -- --port 5176.
Questions: retry (first fails), stop (delayed), disconnect (truncated SSE).
Feedback defaults to HTTP 503; MOCK_FEEDBACK_OK=1 enables a recording success fixture.
The comment "simulate failure" still returns HTTP 503.
"""
import json
import os
from uuid import uuid4
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

attempts = {}
feedback_records = []
feedback_ok = os.environ.get("MOCK_FEEDBACK_OK") == "1"
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(feedback_records if self.path == '/api/test-feedback' else {'status': 'ok'}).encode())

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if self.path == '/api/feedback':
            if not feedback_ok or payload.get('comment') == 'simulate failure':
                self.send_response(503)
                self.end_headers()
                return
            feedback_records.append(payload)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'accepted', 'feedback_id': str(uuid4())}).encode())
            return
        message = payload.get('message', '')
        attempts[message] = attempts.get(message, 0) + 1
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        def event(data):
            self.wfile.write(('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode())
            self.wfile.flush()
        try:
            event({'type':'node_completed','node':'planner','message':'UI 테스트 요청을 확인했어요.','data':{}})
            if message == 'retry' and attempts[message] == 1:
                event({'type':'run_failed','message':'테스트 오류: 다시 시도해 주세요.'})
                return
            if message == 'disconnect':
                return
            if message == 'stop':
                time.sleep(15)
            event({'type':'run_completed','data':{'conversation_id':payload.get('conversation_id') or 'mock-conversation','message_id':str(uuid4()),'status':'succeeded','answer':f"테스트 응답 · FAB: {payload.get('fab', '자동')} · 대화: {payload.get('conversation_id', '새 대화')}",'evidence':[],'limitations':['로컬 UI 테스트 결과이며 실제 데이터가 아니에요.']}})
        except (BrokenPipeError, ConnectionResetError):
            pass

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8769), Handler).serve_forever()
