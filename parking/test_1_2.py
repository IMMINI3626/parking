
import sys
import cv2
import easyocr
import flask
import pandas
import serial

print("===== 1. 환경 테스트 =====")
print("Python:", sys.version)
print("OpenCV:", cv2.__version__)
print("필요한 라이브러리 설치 확인 완료")

print("\n===== 2. 실시간 카메라 및 OCR 테스트 =====")

# 0번 카메라 실행
cap = cv2.VideoCapture(1)

if not cap.isOpened():
    print("카메라를 열 수 없습니다. 카메라 번호와 연결 상태를 확인하세요.")
    raise SystemExit(1)

print("카메라 실행 성공")
print("EasyOCR 모델을 준비합니다. 처음에는 시간이 걸릴 수 있습니다.")

reader = easyocr.Reader(["ko", "en"], gpu=False)
frame_count = 0
last_texts = []

try:
    while True:
        ret, frame = cap.read()

        if not ret:
            print("카메라 영상을 읽을 수 없습니다.")
            break

        # OCR을 매 프레임 실행하면 느릴 수 있어 10프레임마다 인식
        frame_count += 1

        if frame_count % 10 == 0:
            results = reader.readtext(frame, detail=1)
            last_texts = [
                (text, confidence)
                for box, text, confidence in results
            ]

            print("\n===== 인식 결과 =====")
            if last_texts:
                for text, confidence in last_texts:
                    print(f"{text} (신뢰도: {confidence:.2f})")
            else:
                print("인식된 글자가 없습니다.")

        # 인식된 글자를 카메라 화면에 표시
        for box, text, confidence in last_texts:
            points = [(int(x), int(y)) for x, y in box]
            for i in range(4):
                cv2.line(
                    frame,
                    points[i],
                    points[(i + 1) % 4],
                    (0, 255, 0),
                    2
                )
            cv2.putText(
                frame,
                text,
                points[0],
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

        cv2.imshow("Live Camera OCR Test", frame)

        # q 키를 누르면 종료
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()
    print("카메라 테스트를 종료했습니다.")
