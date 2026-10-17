from flask import Flask, render_template, request, jsonify
import csv, os, random, re, threading, time

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
WIDTH_LIMIT = 1900  # 비경차 너비 기준 n(mm). 실제 주차장에 맞춰 바꿀 수 있는 설정값
PREPARKED = {'C1': '12사3456'}  # 프로그램 시작할 때부터 주차되어 있는 차량 {공간 번호: 차량 번호}
lock = threading.Lock()  # 여러 차량이 동시에 배정 요청을 해도 같은 칸이 두 번 배정되지 않게 막음

def normalize_plate(value):
    # 번호판의 공백, '-', '_'를 지우고 대문자로 통일 (예: '18나 7315' → '18나7315')
    return re.sub(r'[\s_-]+', '', str(value)).upper()

def read_csv(name, kind):
    path = os.path.join(BASE, name)
    if not os.path.exists(path):
        return []
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    rows = [[c.strip() for c in r] for r in rows if r and any(c.strip() for c in r)]
    if not rows:
        return []
    first = [x.replace(' ', '').lower() for x in rows[0]]
    header = any(x in first for x in ['차량번호', '차량 번호', '차량종류', '주차공간번호', '주차공간종류', '공간번호', '공간종류', 'vehicle_number', 'space_number'])
    if header:
        keys = first
        data = rows[1:]
    else:
        keys = []
        data = rows
    result = []
    for r in data:
        if kind == 'vehicle' and len(r) >= 3:
            if keys:
                d = dict(zip(keys, r))
                plate = next((d[k] for k in keys if k in ('차량번호','차량번호판','vehicle_number','plate','번호판')), r[0])
                typ = next((d[k] for k in keys if k in ('차량종류','차종','vehicle_type','type')), r[1])
                width = next((d[k] for k in keys if k in ('너비','차량너비','width','vehicle_width')), r[2])
            else:
                plate, typ, width = r[:3]
            try: width = int(float(width.replace('mm','').strip()))
            except ValueError: width = 0
            result.append({'plate': normalize_plate(plate), 'type': typ, 'width': width})
        elif kind == 'space' and len(r) >= 2:
            if keys:
                d = dict(zip(keys, r))
                sid = next((d[k] for k in keys if k in ('주차공간번호','주차번호','공간번호','space_number','number','id')), r[0])
                typ = next((d[k] for k in keys if k in ('주차공간종류','주차공간유형','공간종류','space_type','type')), r[1])
            else:
                sid, typ = r[:2]
            result.append({'id': sid, 'type': normalize_type(typ)})
    return result

def normalize_type(value):
    v = str(value).strip().upper()
    if v in ('K','경','경차','COMPACT'): return 'K'
    if v in ('B','대형','대','LARGE'): return 'B'
    if v in ('S','소형','소','SMALL'): return 'S'
    return v

vehicles = read_csv('vehicles.csv', 'vehicle')
spaces = read_csv('parking.csv', 'space')
if not spaces:
    spaces = [{'id': str(i), 'type': t} for i, t in enumerate(['S','B','S','B','B','K','S','B','B'], 1)]
# status: free / assigned / occupied
state = {s['id']: {'status':'free', 'plate':None} for s in spaces}
vehicle_assignments = {}
for sid, plate in PREPARKED.items():  # 처음부터 주차된 차량은 주차 완료 상태로 등록
    if sid in state:
        state[sid] = {'status':'occupied', 'plate':normalize_plate(plate)}
        vehicle_assignments[normalize_plate(plate)] = sid
current = {'plate': None}  # 지금 두 화면(관리자/내비)이 함께 보고 있는 차량 번호
# 카메라(final.py)가 마지막으로 인식한 번호판. seq는 새 인식이 들어올 때마다 1씩 커짐
detected = {'seq': 0, 'plate': None, 'time': None}
# 카메라 상태 (final.py로 실행했을 때만 on이 됨). status: 인식 상태, text: 카메라가 지금 읽은 글자
camera = {'on': False, 'status': '카메라 꺼짐 (final.py로 실행하면 켜집니다)', 'text': ''}
# 수위 센서 상태 (final.py가 아두이노에서 읽어 채움). level: off / normal / risk(침수 위험 예측) / flood(침수)
water = {'on': False, 'value': None, 'predict': None, 'level': 'off', 'time': None, 'limit': None, 'minutes': None, 'esp32': None, 'method': None}

def set_detected(plate):
    # final.py가 번호판을 확정하면 호출 → 관리자 화면의 차량번호 칸에 채워짐 (배정은 관리자가 직접)
    with lock:
        detected.update(seq=detected['seq'] + 1, plate=normalize_plate(plate), time=time.strftime('%H:%M:%S'))

def classify(vehicle):
    # 경차 → K / 비경차: 너비 >= 기준 → B, 너비 < 기준 → S
    # 차량 정보가 비었거나 잘못되면 임의로 분류하지 않고 None을 돌려줌
    t = str(vehicle['type']).strip().lower()
    if not t:
        return None
    if '경차' in t or t in ('경','k','compact','compact car'):
        return 'K'
    if vehicle['width'] <= 0:
        return None
    return 'B' if vehicle['width'] >= WIDTH_LIMIT else 'S'

def snapshot():
    return {'spaces': [{'id':s['id'], 'type':s['type'], **state[s['id']]} for s in spaces],
            'vehicles': [{**v, 'need': classify(v)} for v in vehicles], 'assignments': vehicle_assignments,
            'width_limit': WIDTH_LIMIT, 'current': current['plate'], 'detected': dict(detected), 'camera': dict(camera), 'water': dict(water)}

@app.get('/')
def home(): return render_template('index.html')  # 관리자 / 내비게이션 대시보드 선택 화면
@app.get('/admin')
def admin(): return render_template('admin.html')
@app.get('/guide')
def guide(): return render_template('guide.html')
@app.get('/api/state')
def api_state(): return jsonify(snapshot())

@app.post('/api/assign')
def assign():
    plate = normalize_plate(request.json.get('plate',''))
    with lock:
        # 1) 이미 배정된 차량이면 새로 배정하지 않고 기존 공간을 다시 안내
        if plate in vehicle_assignments:
            sid = vehicle_assignments[plate]
            current['plate'] = plate
            if detected['plate'] == plate: detected['plate'] = None
            return jsonify(ok=True, message=f"이미 {sid}번 공간에 배정되어 있습니다. {sid}번 공간으로 안내합니다.", space=sid)
        # 2) 등록된 차량인지 확인
        vehicle = next((v for v in vehicles if v['plate'] == plate), None)
        if not vehicle: return jsonify(ok=False, message='등록되지 않은 차량입니다.'), 404
        # 3) 차량 유형(K/B/S) 결정
        needed = classify(vehicle)
        if not needed: return jsonify(ok=False, message='차량 정보(종류/너비)가 올바르지 않아 배정할 수 없습니다.'), 400
        # 4) 유형이 같고, 비어 있고, 다른 차량에 배정되지 않은 공간만 후보로 선택
        taken = set(vehicle_assignments.values())
        candidates = [s for s in spaces if s['type'] == needed and state[s['id']]['status'] == 'free' and s['id'] not in taken]
        if not candidates: return jsonify(ok=False, message=f'{needed} 유형의 빈 주차 공간이 없습니다.'), 400
        # 5) 후보 중 하나를 무작위로 배정 (아직 '주차 완료'가 아니므로 occupied가 아닌 assigned 상태)
        space = random.choice(candidates)
        state[space['id']] = {'status':'assigned', 'plate':plate}
        vehicle_assignments[plate] = space['id']
        current['plate'] = plate  # 배정한 차량을 두 화면의 현재 차량으로 설정
        if detected['plate'] == plate: detected['plate'] = None  # 카메라 인식 알림 처리 완료
    return jsonify(ok=True, message=f"{space['id']}번 공간으로 안내합니다. (차량 유형 {needed})", space=space['id'])

@app.post('/api/select')
def select():
    # 어느 화면에서든 차량 번호를 입력하면 두 화면이 모두 그 차량을 보도록 현재 차량을 바꿈
    plate = normalize_plate(request.json.get('plate',''))
    if plate not in vehicle_assignments and not any(v['plate'] == plate for v in vehicles):
        return jsonify(ok=False, message='등록되지 않은 차량입니다.'), 404
    current['plate'] = plate
    sid = vehicle_assignments.get(plate)
    return jsonify(ok=True, message=f'{sid}번 공간으로 안내합니다.' if sid else '배정된 주차 공간이 없습니다.', space=sid)

@app.post('/api/detected/clear')
def clear_detected():
    # 관리자가 카메라 인식 결과를 무시(오인식 등)할 때 사용
    with lock: detected['plate'] = None
    return jsonify(ok=True, message='카메라 인식 결과를 지웠습니다.')

@app.post('/api/complete')
def complete():
    plate = normalize_plate(request.json.get('plate',''))
    sid = vehicle_assignments.get(plate)
    if sid is None: return jsonify(ok=False, message='배정된 주차 공간이 없습니다.'), 404
    state[sid]['status'] = 'occupied'
    return jsonify(ok=True, message='주차 완료 처리되었습니다.')

@app.post('/api/exit')
def exit_vehicle():
    plate = normalize_plate(request.json.get('plate',''))
    with lock:
        sid = vehicle_assignments.pop(plate, None)
        if sid is None: return jsonify(ok=False, message='배정된 공간이 없습니다.'), 404
        state[sid] = {'status':'free', 'plate':None}
        if current['plate'] == plate: current['plate'] = None  # 출차한 차량이면 화면 안내도 종료
    return jsonify(ok=True, message='출차 처리되었습니다.')

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
