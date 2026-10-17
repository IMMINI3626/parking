# water_model.py
# 수위 예측 모델(선형회귀)에서 학습(train_water_model.py)과 실시간 예측(final.py)이 함께 쓰는 부분
# 두 곳이 입력값(특징)을 똑같이 만들어야 예측이 맞으므로 여기 한 곳에만 둠
import os
import numpy as np
import joblib

FEATURE_SEC = 300  # 특징을 만들려면 최근 5분 값이 필요함
FEATURES = ['현재 값', '최근 2분 기울기', '최근 5분 기울기', '가속도']  # 기울기 단위: 1분에 오르는 값
MODEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'water_model.pkl')

def per_second(times, values):
    # 1초 간격으로 정리한 값 목록 (빠진 초는 바로 앞 값으로 채움). times는 초 단위 시각
    times = np.asarray(times, dtype=float); values = np.asarray(values, dtype=float)
    sec = np.round(times - times[0]).astype(int)
    series = np.full(sec[-1] + 1, np.nan)
    series[sec] = values
    for i in range(1, len(series)):  # 빈칸은 앞 값으로
        if np.isnan(series[i]): series[i] = series[i - 1]
    return series

def slope(s, a, b):
    # s[a:b] 값에 직선을 맞춘 기울기 (1분에 얼마나 오르는지). 여러 값으로 계산해 센서 잡음에 덜 흔들림
    return np.polyfit(np.arange(b - a), s[a:b], 1)[0] * 60

def features_at(s, i):
    # i초 시점의 특징 4개. s[i-300] ~ s[i] 값이 있어야 함
    recent = slope(s, i - 119, i + 1)     # 최근 2분 기울기
    before = slope(s, i - 239, i - 119)   # 그 전 2분 기울기
    return [s[i], recent, slope(s, i - FEATURE_SEC + 1, i + 1), recent - before]  # 마지막: 빨라지는 정도(가속도)

def latest_features(history):
    # final.py용: 최근 (시각, 값) 목록으로 지금 시점의 특징을 만듦. 5분이 안 모였으면 None
    if not history or history[-1][0] - history[0][0] < FEATURE_SEC: return None
    s = per_second([h[0] for h in history], [h[1] for h in history])
    return features_at(s, len(s) - 1)

def load(path=MODEL_FILE):
    # 학습된 모델 파일을 읽음. 없거나 읽을 수 없으면 None
    if not os.path.exists(path): return None
    try: return joblib.load(path)
    except Exception as e:
        print(f'[수위] 예측 모델을 읽을 수 없습니다 ({e}). 추세 예측을 사용합니다.')
        return None

def predict(bundle, feats):
    # 모델은 '지금부터 몇 분 뒤까지 오를 양'을 예측함 → 현재 값에 더해서 0~1023 범위로 맞춤
    rise = bundle['model'].predict(np.array([feats]))[0]
    return int(round(min(max(feats[0] + rise, 0), 1023)))
