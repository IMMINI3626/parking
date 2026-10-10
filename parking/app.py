from flask import Flask, render_template, request, jsonify
import csv, os

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
WIDTH_LIMIT = 1900

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
    header = any(x in first for x in ['차량번호', '차량 번호', '차량종류', '주차공간번호', '주차공간종류', 'vehicle_number', 'space_number'])
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
            result.append({'plate': plate, 'type': typ, 'width': width})
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

def classify(vehicle):
    t = str(vehicle['type']).strip().lower()
    if '경차' in t or t in ('경','k','compact'):
        return 'K'
    return 'B' if vehicle['width'] >= WIDTH_LIMIT else 'S'

def snapshot():
    return {'spaces': [{'id':s['id'], 'type':s['type'], **state[s['id']]} for s in spaces],
            'vehicles': vehicles, 'assignments': vehicle_assignments, 'width_limit': WIDTH_LIMIT}

@app.get('/')
def home(): return render_template('admin.html')
@app.get('/admin')
def admin(): return render_template('admin.html')
@app.get('/guide')
def guide(): return render_template('guide.html')
@app.get('/api/state')
def api_state(): return jsonify(snapshot())

@app.post('/api/assign')
def assign():
    plate = request.json.get('plate','').strip()
    vehicle = next((v for v in vehicles if v['plate'] == plate), None)
    if not vehicle: return jsonify(ok=False, message='차량 정보가 없습니다.'), 404
    if plate in vehicle_assignments: return jsonify(ok=False, message=f"이미 {vehicle_assignments[plate]}번 공간에 배정되어 있습니다."), 400
    needed = classify(vehicle)
    space = next((s for s in spaces if s['type'] == needed and state[s['id']]['status'] == 'free'), None)
    if not space: return jsonify(ok=False, message=f'{needed} 유형의 빈 주차 공간이 없습니다.'), 400
    state[space['id']] = {'status':'assigned', 'plate':plate}
    vehicle_assignments[plate] = space['id']
    return jsonify(ok=True, message=f"{space['id']}번 공간으로 안내합니다.", space=space['id'])

@app.post('/api/complete')
def complete():
    plate = request.json.get('plate','').strip()
    sid = vehicle_assignments.get(plate)
    if sid is None: return jsonify(ok=False, message='배정된 주차 공간이 없습니다.'), 404
    state[sid]['status'] = 'occupied'
    return jsonify(ok=True, message='주차 완료 처리되었습니다.')

@app.post('/api/exit')
def exit_vehicle():
    plate = request.json.get('plate','').strip()
    sid = vehicle_assignments.pop(plate, None)
    if sid is None: return jsonify(ok=False, message='배정된 공간이 없습니다.'), 404
    state[sid] = {'status':'free', 'plate':None}
    return jsonify(ok=True, message='출차 처리되었습니다.')

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
