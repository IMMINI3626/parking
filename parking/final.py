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
import logging, re, sys, threading, time, webbrowser
import cv2, easyocr
import numpy as np
from flask import Response
from PIL import Image, ImageDraw, ImageFont
import app as server  # app.py의 Flask 서버와 주차 상태를 그대로 사용

# 카메라 번호. 'python final.py 2'처럼 실행하면 2번 카메라를 씀 (없으면 아래 기본값)
# 이 노트북: 0 = 내장 카메라, 1 = 외장 USB 카메라, 2 = 가상 카메라(영상 없음, 빨간 X 화면). USB를 다시 꽂으면 번호가 바뀔 수 있음
CAMERA_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 1
MIN_CONF = 0.3       # 신뢰도가 이 값보다 낮은 글자는 버림
CONFIRM_COUNT = 2    # 같은 번호가 연속 몇 번 읽혀야 확정할지 (한 번만 잘못 읽힌 번호는 무시)
COOLDOWN = 10        # 같은 차량을 다시 관리자 화면에 알리기까지 기다리는 시간(초)
OPEN_BROWSER = True  # 실행하면 관리자/내비 화면을 브라우저에 자동으로 열지
URL = 'http://127.0.0.1:5000'
WINDOW = 'Parking Camera (q: quit)'
PLATE_RE = re.compile(r'\d{2,3}[가-힣]\d{4}')  # 번호판 모양: 12가3456 또는 123가4567

# 카메라 화면에 한글을 쓰기 위한 글꼴 (cv2.putText는 한글이 ???로 나옴)
try: FONT = ImageFont.truetype('C:/Windows/Fonts/malgun.ttf', 22)
except OSError: FONT = None

# 카메라 스레드, 글자 인식 스레드, 웹 서버가 함께 쓰는 값
shared = {'frame': None, 'jpg': None, 'boxes': [], 'running': True}

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
