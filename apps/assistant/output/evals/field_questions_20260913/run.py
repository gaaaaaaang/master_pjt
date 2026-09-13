import concurrent.futures
import datetime
import json
from pathlib import Path
import subprocess
import time

import httpx

OUT = Path(__file__).resolve().parent
ORIGINAL = [
    '[LOT_ID]의 공정 진행 제한 이력을 조회하고, 제한 설정자를 알려줘.',
    '[LOT_ID]에 설정된 공정 진행 제한 사유를 알려줘.',
    '[EQUIPMENT_ID] 장비의 현재 비가동 사유를 알려줘.',
    '[LOT_ID]의 현재 상태를 확인해줘.',
    '[AREA_ID] 기준으로 특정 자원 그룹 내 사용 가능한 빈 자원을 알려줘.',
    '[AREA_ID] 기준으로 운영 시스템에 등록된 작업 코드 종류를 알려줘.',
    '등록된 계측 공정 매핑 정보를 조회해줘.',
]
MAPPING = {
    '[LOT_ID]': 'FAB10-IM-09121559-015',
    '[EQUIPMENT_ID]': 'FAB10_ETCH_TG01_EQ03',
    '[AREA_ID]': 'etch',
}

def run_case(item):
    number, original = item
    message = original
    for key, value in MAPPING.items():
        message = message.replace(key, value)
    if number == 5:
        message = message.replace('특정 자원 그룹', '자원 그룹 FAB10_ETCH_TG01')
    payload = {'message': message, 'fab': 'fab10'}
    record = {'number': number, 'original': original, 'request': payload,
              'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'events': []}
    started = time.monotonic()
    print(f'START Q{number}: {message}', flush=True)
    try:
        with httpx.Client(timeout=httpx.Timeout(660.0, connect=10.0), trust_env=False) as client:
            with client.stream('POST', 'http://127.0.0.1:8000/api/chat/stream', json=payload) as response:
                record['http_status'] = response.status_code
                event_name, data_lines = None, []
                with (OUT / f'q{number}.sse').open('w') as raw:
                    for line in response.iter_lines():
                        raw.write(line + '\n')
                        raw.flush()
                        if line.startswith('event:'):
                            event_name = line[6:].strip()
                        elif line.startswith('data:'):
                            data_lines.append(line[5:].strip())
                        elif not line and data_lines:
                            event = {'event': event_name, 'payload': json.loads('\n'.join(data_lines))}
                            record['events'].append(event)
                            if event_name == 'final':
                                record['final'] = event['payload'].get('data', event['payload'])
                            elif event_name in ('error', 'cancelled'):
                                record['error'] = event['payload']
                            else:
                                p = event['payload']
                                print(f"TRACE Q{number} {time.monotonic()-started:.1f}s {p.get('node')} {p.get('type')}", flush=True)
                            event_name, data_lines = None, []
    except Exception as exc:
        record['transport_error'] = {'type': type(exc).__name__, 'message': str(exc)}
    record['seconds'] = round(time.monotonic() - started, 2)
    (OUT / f'q{number}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    f = record.get('final', {})
    print(f"DONE Q{number} {record['seconds']}s status={f.get('status')} sql={bool(f.get('sql'))} error={record.get('error', record.get('transport_error'))}", flush=True)
    return record

if __name__ == '__main__':
    manifest = {'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'endpoint': 'http://127.0.0.1:8000/api/chat/stream',
                'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'scope': 'Seven independent live HTTP SSE requests, no mocks; FAB10 synthetic data, IDs substituted from DB; resource group supplied for Q5.',
                'mapping': MAPPING}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        cases = list(executor.map(run_case, enumerate(ORIGINAL, 1)))
    manifest['cases'] = cases
    (OUT / 'results.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print('ALL FINISHED', flush=True)
