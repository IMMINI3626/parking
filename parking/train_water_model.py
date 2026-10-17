# train_water_model.py
# water_log.csv(final.py가 1초마다 저장한 수위 기록)로 'N분 뒤 수위'를 예측하는 선형회귀 모델을 학습
# 실행: python train_water_model.py            (15분 뒤 예측, 침수 기준 500)
#       python train_water_model.py --minutes 5  (시연용으로 5분 뒤 예측)
# 결과: water_model.pkl → final.py를 다시 실행하면 자동으로 이 모델로 예측함
import argparse, os, time
from datetime import datetime
import numpy as np
import joblib
from sklearn.linear_model import LinearRegression
import water_model as wm

BASE = os.path.dirname(os.path.abspath(__file__))
GAP_SEC = 10  # 기록이 이 시간 이상 끊기면 다른 실험(프로그램을 껐다 켬)으로 봄
STEP = 5      # 학습 데이터를 몇 초 간격으로 만들지 (바로 옆 초끼리는 거의 같은 값이라 5초마다면 충분)

def load_runs(path):
    # 기록을 읽어 실험(끊기지 않은 구간)별 1초 간격 값 목록으로 나눔
    times, values = [], []
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                t, v = line.strip().split(',')
                times.append(datetime.strptime(t, '%Y-%m-%d %H:%M:%S').timestamp()); values.append(int(v))
            except ValueError: continue  # 잘못된 줄은 건너뜀
    runs, start = [], 0
    for i in range(1, len(times) + 1):
        if i == len(times) or times[i] - times[i - 1] > GAP_SEC:
            if i - start > 1: runs.append(wm.per_second(times[start:i], values[start:i]))
            start = i
    return runs

def make_samples(series, horizon):
    # 각 시점의 특징(X)과 정답(y = horizon초 뒤까지 오른 양), 비교용 추세 예측값을 만듦
    X, y, now, trend = [], [], [], []
    for i in range(wm.FEATURE_SEC, len(series) - horizon, STEP):
        X.append(wm.features_at(series, i)); y.append(series[i + horizon] - series[i]); now.append(series[i])
        slope = wm.slope(series, i - 119, i + 1) / 60  # final.py의 기존 추세 예측과 같은 계산 (1초 기울기)
        trend.append(min(series[i] + max(slope, 0) * horizon, 1023))
    return X, y, now, trend

def report(name, pred, real, limit):
    # 평균 오차와, '침수(기준 이상)'를 미리 맞혔는지 확인
    pred, real = np.asarray(pred), np.asarray(real)
    mae = np.abs(pred - real).mean()
    flood = real >= limit; warned = pred >= limit
    recall = f'{(warned & flood).sum()}/{flood.sum()}' if flood.any() else '침수 사례 없음'
    false_alarm = (warned & ~flood).sum()
    print(f'  {name:<12} 평균 오차 {mae:6.1f}   침수 미리 맞힘 {recall:<10} 잘못된 경보 {false_alarm}')
    return mae

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--log', default=os.path.join(BASE, 'water_log.csv'))
    ap.add_argument('--minutes', type=float, default=15, help='몇 분 뒤를 예측할지')
    ap.add_argument('--limit', type=int, default=500, help='침수 기준값 (final.py의 FLOOD_LIMIT와 같게)')
    ap.add_argument('--out', default=wm.MODEL_FILE)
    a = ap.parse_args()
    horizon = int(a.minutes * 60)

    if not os.path.exists(a.log): return print(f'{a.log} 파일이 없습니다. final.py를 실행해 수위 기록을 먼저 모으세요.')
    runs = load_runs(a.log)
    need = wm.FEATURE_SEC + horizon + 1
    usable = [r for r in runs if len(r) >= need]
    print(f'기록: 실험 {len(runs)}개 중 {len(usable)}개 사용 (한 실험이 최소 {need // 60}분 {need % 60}초 이상이어야 함)')
    if not usable: return print('학습할 수 있는 실험이 없습니다. 더 길게 기록하거나 --minutes를 줄이세요.')

    # 평가: 실험이 3개 이상이면 실험을 하나씩 빼고 학습 → 뺀(처음 보는) 실험으로 시험하는 것을 모든 실험에 반복
    #       3개 미만이면 각 실험의 앞 80% 시간으로 학습, 뒤 20%로 시험
    data = [make_samples(r, horizon) for r in usable]
    join = lambda parts, j: [v for p in parts for v in p[j]]
    if len(usable) >= 3:
        folds = [([d for j, d in enumerate(data) if j != k], [data[k]]) for k in range(len(data))]
        print(f'평가 방법: 실험 {len(usable)}개를 하나씩 빼고 학습한 뒤, 뺀 실험으로 시험 (처음 보는 상황에서의 성능)')
    else:
        cut = [int(len(d[0]) * 0.8) for d in data]
        folds = [([tuple(x[:c] for x in d) for d, c in zip(data, cut)], [tuple(x[c:] for x in d) for d, c in zip(data, cut)])]
        print('평가 방법: 실험이 3개 미만이라 각 실험의 앞 80% 시간으로 학습, 뒤 20%로 시험 (실험을 3개 이상 모으면 더 정확한 평가)')
    pred, test = [], []
    for train, tst in folds:
        m = LinearRegression().fit(join(train, 0), join(train, 1))
        pred += list(np.clip(np.array(join(tst, 2)) + m.predict(join(tst, 0)), 0, 1023)); test += tst
    now, real = np.array(join(test, 2)), np.array(join(test, 2)) + np.array(join(test, 1))
    print(f'\n[시험 결과] {a.minutes:g}분 뒤 수위 예측 (시험 데이터 {len(real)}개)')
    mae = report('선형회귀', pred, real, a.limit)
    trend_mae = report('기존 추세', join(test, 3), real, a.limit)
    report('변화 없음', now, real, a.limit)
    print('  → ' + ('선형회귀가 기존 추세 예측보다 정확합니다.' if mae < trend_mae else '기존 추세 예측이 더 정확합니다. 실험 데이터를 더 모아 보세요.'))

    # 저장할 모델은 모든 데이터로 다시 학습
    model = LinearRegression().fit(join(data, 0), join(data, 1))
    print('\n[학습된 식] 오를 양 = ' + ' + '.join(f'{c:.3f}×{n}' for c, n in zip(model.coef_, wm.FEATURES)) + f' + {model.intercept_:.1f}')
    joblib.dump({'model': model, 'minutes': a.minutes, 'features': wm.FEATURES, 'limit': a.limit,
                 'mae': round(float(mae), 1), 'trend_mae': round(float(trend_mae), 1), 'trained': time.strftime('%Y-%m-%d %H:%M'), 'samples': len(join(data, 0))}, a.out)
    print(f'저장: {a.out}  → final.py를 다시 실행하면 이 모델로 {a.minutes:g}분 뒤 수위를 예측합니다.')

if __name__ == '__main__':
    main()
