# final.py
# 카메라 번호판 인식(test_1_2.py) + 주차 안내 웹 서버(app.py)를 한 번에 실행하는 프로그램
# 1) 웹 서버를 뒤에서 실행하고, 관리자/내비 화면을 브라우저에 자동으로 엶
# 2) 카메라로 번호판을 읽음 (카메라 영상과 인식 상태는 카메라 창 + 관리자 화면에서 볼 수 있음)
# 3) 같은 번호가 여러 번 연속으로 읽히면 확정 → 관리자 화면의 차량번호 칸에 자동으로 채움
# 4) 관리자가 번호를 확인하고 [배정]을 눌러야 실제로 배정됨 (오인식 방지)
# 카메라는 입구(입차) 전용이고, 출차는 관리자 화면의 [출차] 버튼으로 처리함
import os
# Ctrl+C로 끌 때 'forrtl: error (200)'로 강제 종료되는 것을 막음 (numpy/scipy가 쓰는 Intel 라이브러리 문제)
os.environ.setdefault('FOR_DISABLE_CONSOLE_CTRL_HANDLER', '1')
import logging, re, sys, threading, time, urllib.error, urllib.request, webbrowser
import cv2, easyocr, serial
import numpy as np
from flask import Response
from PIL import Image, ImageDraw, ImageFont
import app as server  # app.py의 Flask 서버와 주차 상태를 그대로 사용
import water_model    # 수위 예측 모델(선형회귀)

# 카메라 번호. 'python final.py 2'처럼 실행하면 2번 카메라를 씀 (없으면 아래 기본값)
# 이 노트북: 0 = 내장 카메라, 1 = 외장 USB 카메라, 2 = 가상 카메라(영상 없음, 빨간 X 화면). USB를 다시 꽂으면 번호가 바뀔 수 있음
CAMERA_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 1
MIN_CONF = 0.3       # 신뢰도가 이 값보다 낮은 글자는 버림
CONFIRM_COUNT = 2    # 같은 번호가 연속 몇 번 읽혀야 확정할지 (한 번만 잘못 읽힌 번호는 무시)
COOLDOWN = 10        # 같은 차량을 다시 관리자 화면에 알리기까지 기다리는 시간(초)
OPEN_BROWSER = True  # 실행하면 관리자/내비 화면을 브라우저에 자동으로 열지
URL = 'http://127.0.0.1:5000'
WINDOW = 'Parking Camera (q: quit)'
ARDUINO_PORT = 'COM6'  # 아두이노 우노가 연결된 포트
ARDUINO_BAUD = 9600    # 아두이노 코드의 Serial.begin() 값과 같아야 함
# 수위 센서 (아두이노가 1초마다 A0 값 0~1023을 한 줄씩 보냄)
FLOOD_LIMIT = 500      # 이 값 이상이면 침수. 실제 센서를 물에 담가 보고 맞출 것
PREDICT_MIN = 15       # 몇 분 뒤를 예측할지
TREND_SEC = 120        # 최근 몇 초 동안의 값으로 변화 추세를 계산할지 (2분)
# 침수 위험 알림을 보낼 ESP32 주소 (같은 와이파이에 연결돼 있어야 함). 실제 ESP32 IP와 주소로 바꿀 것
ESP32_RISK_URL = 'http://192.168.0.100/flood'   # 침수 위험이 시작될 때 접속할 주소
ESP32_CLEAR_URL = 'http://192.168.0.100/normal'  # 다시 정상이 될 때 접속할 주소 (필요 없으면 None)
WATER_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'water_log.csv')  # 예측 모델 학습용 기록
PLATE_RE = re.compile(r'\d{2,3}[가-힣]\d{4}')  # 번호판 모양: 12가3456 또는 123가4567

# 카메라 화면에 한글을 쓰기 위한 글꼴 (cv2.putText는 한글이 ???로 나옴)
try: FONT = ImageFont.truetype('C:/Windows/Fonts/malgun.ttf', 22)
except OSError: FONT = None

# 카메라 스레드, 글자 인식 스레드, 웹 서버가 함께 쓰는 값
shared = {'frame': None, 'jpg': None, 'boxes': [], 'running': True}
arduino = {'conn': None}
arduino_lock = threading.Lock()
water_history = []  # 최근 수위 값 (시각, 값) 목록
WATER_MODEL = water_model.load()  # train_water_model.py로 학습한 모델. 없으면 None → 추세 예측 사용
if WATER_MODEL:
    PREDICT_MIN = WATER_MODEL['minutes']  # 모델이 학습한 예측 시간에 맞춤
    print(f"[수위] 예측 모델 사용: {PREDICT_MIN:g}분 뒤 예측 (학습 {WATER_MODEL['trained']}, 평균 오차 {WATER_MODEL['mae']})")

def open_arduino():
    arduino['conn'] = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
    time.sleep(2)  # 포트를 열면 아두이노가 재시작되므로 준비될 때까지 기다림
    # 재시작된 아두이노의 LED를 현재 주차 상태와 맞추기 위해 모든 공간 상태를 한 번 보냄
    for sid, s in server.state.items():
        arduino['conn'].write(f"{sid},{1 if s['status'] == 'occupied' else 0}\n".encode())
    print(f'[아두이노] {ARDUINO_PORT} 연결됨 (전체 공간 상태 전송)')

def connect_arduino():
    # 프로그램 시작할 때 미리 연결해 둠 (실패해도 [주차 완료] 때 다시 시도)
    with arduino_lock:
        try: open_arduino()
        except (serial.SerialException, OSError) as e:
            print(f'[아두이노] {ARDUINO_PORT}에 연결할 수 없습니다. 연결을 확인하세요: {e}')

def send_arduino(space_id, occupied):
    # 아두이노로 '공간번호,점유상태' 한 줄을 보냄 (점유 1, 빈자리 0). 예: 'C2,1\n'
    # 아두이노가 없거나 오류가 나도 웹 서버는 멈추지 않도록 뒤에서 실행하고 오류는 출력만 함
    msg = f'{space_id},{1 if occupied else 0}\n'
    def run():
        with arduino_lock:
            try:
                if arduino['conn'] is None: open_arduino()
                arduino['conn'].write(msg.encode())
                print(f'[아두이노] 보냄: {msg.strip()}')
            except (serial.SerialException, OSError) as e:
                print(f'[아두이노] {ARDUINO_PORT} 전송 실패 ({msg.strip()}): {e}')
                if arduino['conn']: arduino['conn'].close()
                arduino['conn'] = None  # 다음 전송 때 다시 연결 시도
    threading.Thread(target=run, daemon=True).start()

def predict_water(history):
    # PREDICT_MIN분 뒤 수위를 예측. 돌려주는 값: (예측값, 사용한 방법)
    # 1) 학습한 선형회귀 모델이 있고 최근 5분 값이 모였으면 모델로 예측
    feats = water_model.latest_features(history) if WATER_MODEL else None
    if feats: return water_model.predict(WATER_MODEL, feats), '선형회귀'
    # 2) 아니면 간단한 추세 예측: 최근 TREND_SEC초 값에 직선을 맞춰 계산
    recent = [h for h in history if history[-1][0] - h[0] <= TREND_SEC]
    if len(recent) < 30: return None, None  # 30초 이상 모이기 전에는 예측하지 않음
    t = np.array([h[0] for h in recent]); v = np.array([h[1] for h in recent])
    slope = np.polyfit(t - t[0], v, 1)[0]  # 1초에 값이 얼마나 변하는지
    return int(round(v[-1] + max(slope, 0) * PREDICT_MIN * 60)), '추세'  # 줄어드는 추세는 위험이 아니므로 0으로 봄

def notify_esp32(url, label):
    # ESP32 주소로 접속해서 그쪽 프로그램을 동작시킴. 실패해도 프로그램은 멈추지 않고 결과만 표시
    if not url: return
    def run():
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                result = f'{label} 보냄 ({r.status})'
        except (urllib.error.URLError, OSError) as e:
            result = f'{label} 전송 실패: {getattr(e, "reason", e)}'
        server.water['esp32'] = f"{time.strftime('%H:%M:%S')} {result}"
        print(f'[ESP32] {url} → {result}')
    threading.Thread(target=run, daemon=True).start()

def update_water(value):
    # 아두이노에서 받은 수위 값으로 침수/침수 위험을 판단해 관리자 화면에 보여 줌
    now = time.time()
    water_history.append((now, value))
    keep = max(TREND_SEC, water_model.FEATURE_SEC) + 5  # 모델에 최근 5분 값이 필요함
    while water_history and now - water_history[0][0] > keep: water_history.pop(0)
    predict, method = predict_water(water_history)
    if value >= FLOOD_LIMIT: level = 'flood'
    elif predict is not None and predict >= FLOOD_LIMIT: level = 'risk'
    else: level = 'normal'
    if level != server.water['level'] and level != 'normal':
        print(f"[수위] {'침수' if level == 'flood' else f'{PREDICT_MIN}분 뒤 침수 위험'}: 현재 {value}, 예측 {predict}")
    # 위험(예측 또는 이미 침수)이 시작될 때 / 정상으로 돌아올 때 한 번씩만 ESP32에 알림
    was_alert, is_alert = server.water['level'] in ('risk', 'flood'), level in ('risk', 'flood')
    if is_alert and not was_alert: notify_esp32(ESP32_RISK_URL, '침수 위험 신호')
    if was_alert and not is_alert: notify_esp32(ESP32_CLEAR_URL, '해제 신호')
    server.water.update(on=True, value=value, predict=predict, level=level, time=time.strftime('%H:%M:%S'), limit=FLOOD_LIMIT, minutes=PREDICT_MIN, method=method)
    with open(WATER_LOG, 'a', encoding='utf-8') as f:  # 시각, 값을 계속 기록 → 나중에 예측 모델 학습에 사용
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{value}\n")

def read_arduino():
    # 아두이노가 보내는 수위 센서 값을 계속 읽음. 연결이 끊기면 5초마다 다시 연결 시도
    while shared['running']:
        conn = arduino['conn']
        if conn is None:
            time.sleep(5)
            if arduino['conn'] is None: connect_arduino()
            continue
        try:
            line = conn.readline().decode(errors='ignore').strip()
        except (serial.SerialException, OSError, TypeError, AttributeError):
            with arduino_lock:
                if arduino['conn'] is conn:
                    conn.close(); arduino['conn'] = None
            server.water.update(on=False, level='off')
            print('[아두이노] 연결이 끊겼습니다. 다시 연결을 시도합니다.')
            continue
        if line.isdigit(): update_water(int(line))  # 숫자가 아닌 줄은 무시

def notify_arduino(view):
    # app.py의 [주차 완료]/[출차] 처리를 감싸서, 처리 뒤 점유 상태가 바뀐 공간만 아두이노에 알림
    # (배정만 된 자리는 점유가 아니므로 보내지 않음)
    def wrapper(*args, **kwargs):
        before = {sid: s['status'] == 'occupied' for sid, s in server.state.items()}
        result = view(*args, **kwargs)
        for sid, s in server.state.items():
            now = s['status'] == 'occupied'
            if now != before[sid]: send_arduino(sid, now)
        return result
    return wrapper

def set_status(text, ocr_text=None):
    # 인식 상태를 바꾸면 카메라 창, 관리자 화면, 터미널에 모두 반영됨
    changed = server.camera['status'] != text
    server.camera['status'] = text
    if ocr_text is not None: server.camera['text'] = ocr_text
    if changed: print('[카메라]', text)

def find_plate(results):
    # EasyOCR 결과에서 번호판 모양의 글자를 찾아 돌려줌. 없으면 None
    items = sorted([r for r in results if r[2] >= MIN_CONF], key=lambda r: r[0][0][0])  # 왼쪽 → 오른쪽 순서
    # 1) 글자 상자 하나에 번호 전체가 들어 있는 경우
    for _, text, _ in items:
        m = PLATE_RE.search(server.normalize_plate(text))
        if m: return m.group()
    # 2) '12가' + '3456'처럼 나뉘어 읽힌 경우 이어 붙여서 확인
    m = PLATE_RE.search(server.normalize_plate(''.join(t for _, t, _ in items)))
    return m.group() if m else None

def draw(frame, boxes, status):
    # 인식한 글자 상자(초록색)와 상태 문구를 카메라 화면에 그림
    for box, _, _ in boxes:
        pts = np.array([[int(x), int(y)] for x, y in box])
        cv2.polylines(frame, [pts], True, (0, 255, 0), 2)
    if FONT is None:  # 한글 글꼴이 없으면 영어/숫자만 표시
        cv2.putText(frame, status.encode('ascii', 'ignore').decode(), (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        return frame
    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(img)
    for box, text, conf in boxes:
        d.text((int(box[0][0]), max(0, int(box[0][1]) - 26)), f'{text} ({conf:.2f})', font=FONT, fill=(0, 255, 0))
    d.rectangle((0, 0, img.width, 34), fill=(0, 0, 0))
    d.text((10, 5), status, font=FONT, fill=(255, 255, 0))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

def camera_stream():
    # 관리자 화면에 카메라 영상을 보내 줌 (사진을 계속 이어 보내는 MJPEG 방식)
    def gen():
        while shared['running']:
            jpg = shared['jpg']
            if jpg: yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n'
            time.sleep(0.1)  # 1초에 10장 정도
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

def run_server():
    # Flask 웹 서버를 별도 스레드에서 실행 (카메라 창은 메인 스레드에서 띄워야 함)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)  # 1초마다 오는 화면 갱신 요청 로그는 숨김
    server.app.add_url_rule('/camera.mjpg', 'camera_stream', camera_stream)
    for name in ('complete', 'exit_vehicle'):  # 주차 완료 / 출차 때 아두이노로 점유 상태 전송
        server.app.view_functions[name] = notify_arduino(server.app.view_functions[name])
    threading.Thread(target=lambda: (connect_arduino(), read_arduino()), daemon=True).start()  # 연결 후 수위 값 읽기
    server.app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False, threaded=True)

def ocr_loop():
    # 글자 인식은 한 번에 몇 초 걸리므로 따로 실행 → 카메라 화면은 멈추지 않음
    # 모델 준비(30초 정도)도 여기서 하므로 그동안에도 카메라 창은 바로 뜸
    set_status('글자 인식 모델(EasyOCR) 준비 중... 처음에는 30초 정도 걸릴 수 있습니다')
    reader = easyocr.Reader(['ko', 'en'], gpu=False)
    set_status('번호판을 비춰 주세요')
    candidate, streak = None, 0   # 지금 연속으로 읽히고 있는 번호와 그 횟수
    last_sent = {}                # 번호 → 마지막으로 관리자 화면에 알린 시각
    while shared['running']:
        frame, shared['frame'] = shared['frame'], None  # 가장 최근 화면 한 장을 가져감
        if frame is None:
            time.sleep(0.05)
            continue
        results = reader.readtext(frame, detail=1)
        shared['boxes'] = [r for r in results if r[2] >= MIN_CONF]
        ocr_text = ' / '.join(f'{t} ({c:.2f})' for _, t, c in shared['boxes']) or '(읽힌 글자 없음)'
        plate = find_plate(results)

        # 같은 번호가 연속으로 읽히면 횟수를 늘리고, 다른 번호면 처음부터 다시 셈
        if plate and plate == candidate: streak += 1
        else: candidate, streak = plate, (1 if plate else 0)

        if not plate:
            set_status('번호판을 비춰 주세요', ocr_text)
        elif streak < CONFIRM_COUNT:
            set_status(f'인식 중: {plate} ({streak}/{CONFIRM_COUNT})', ocr_text)
        elif streak == CONFIRM_COUNT and time.time() - last_sent.get(plate, 0) > COOLDOWN:
            # 번호 확정 → 관리자 화면에 전달 (배정은 관리자가 확인 후 직접)
            last_sent[plate] = time.time()
            server.set_detected(plate)
            set_status(f'확정: {plate} → 관리자 화면에서 확인하세요', ocr_text)
        else:
            server.camera['text'] = ocr_text

def main():
    threading.Thread(target=run_server, daemon=True).start()
    print(f'웹 서버 실행: {URL}  (관리자 {URL}/admin , 내비 {URL}/guide)')
    if OPEN_BROWSER:  # 관리자 화면과 내비 화면을 브라우저 탭으로 따로 엶
        time.sleep(1)
        webbrowser.open(URL + '/admin')
        webbrowser.open(URL + '/guide')

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        set_status(f'카메라 {CAMERA_INDEX}번을 열 수 없습니다. CAMERA_INDEX와 연결 상태를 확인하세요.')
        print('웹 화면에서 차량번호를 직접 입력하는 기능은 계속 사용할 수 있습니다. (종료: Ctrl+C)')
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            return

    server.camera['on'] = True
    threading.Thread(target=ocr_loop, daemon=True).start()
    print('카메라 인식 시작 (카메라 창에서 q 또는 터미널에서 Ctrl+C를 누르면 카메라와 웹 서버가 함께 종료됩니다)')

    show_window, first = True, True
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                set_status('카메라 영상을 읽을 수 없습니다.')
                break
            shared['frame'] = frame.copy()  # 인식 스레드에 최신 화면을 넘김
            view = draw(frame, shared['boxes'], server.camera['status'])
            shared['jpg'] = cv2.imencode('.jpg', view, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()  # 관리자 화면용
            if show_window:
                try:
                    cv2.imshow(WINDOW, view)
                    if first:  # 카메라 창이 다른 창 뒤에 숨지 않게 맨 앞에 띄움
                        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
                        first = False
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
                except cv2.error:
                    # opencv-python-headless에는 창 기능이 없음 → 창 없이 계속 (영상은 관리자 화면에서 볼 수 있음)
                    show_window = False
                    print('카메라 창을 띄울 수 없습니다. 카메라 영상은 관리자 화면에서 확인하세요. 종료: Ctrl+C')
    except KeyboardInterrupt:
        pass
    finally:
        shared['running'] = False
        server.camera['on'] = False
        cap.release()
        try: cv2.destroyAllWindows()
        except cv2.error: pass
        print('프로그램을 종료했습니다.')

if __name__ == '__main__':
    main()
