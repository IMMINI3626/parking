
# test_3_4_5.py
import pandas as pd
import random
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VEHICLES_FILE = BASE_DIR / "vehicles.csv"
PARKING_FILE = BASE_DIR / "parking.csv"

# 비경차의 B/S 분류 기준(mm). 실제 기준은 추후 확정
WIDTH_THRESHOLD_MM = 1900

def normalize(value):
    return re.sub(r"[\s_-]+", "", str(value)).upper()

def find_column(df, candidates, file_name):
    columns = {normalize(col): col for col in df.columns}
    for candidate in candidates:
        if normalize(candidate) in columns:
            return columns[normalize(candidate)]
    raise ValueError(
        f"{file_name}의 열 이름을 확인하세요.\n"
        f"필요한 열 후보: {candidates}\n"
        f"현재 열: {list(df.columns)}"
    )

def load_csv():
    vehicles = pd.read_csv(VEHICLES_FILE, encoding="utf-8-sig")
    parking = pd.read_csv(PARKING_FILE, encoding="utf-8-sig")

    v_plate = find_column(
        vehicles, ["차량번호", "vehicle_number", "plate"], "vehicles.csv"
    )
    v_type = find_column(
        vehicles, ["차량종류", "vehicle_type", "type"], "vehicles.csv"
    )
    v_width = find_column(
        vehicles, ["너비", "차량너비", "width"], "vehicles.csv"
    )
    p_number = find_column(
        parking, ["주차공간번호", "공간번호", "space_number"], "parking.csv"
    )
    p_type = find_column(
        parking, ["주차공간종류", "공간종류", "space_type"], "parking.csv"
    )

    vehicles = vehicles.rename(columns={
        v_plate: "plate",
        v_type: "vehicle_type",
        v_width: "width"
    })
    parking = parking.rename(columns={
        p_number: "space",
        p_type: "space_type"
    })

    if vehicles[["plate", "vehicle_type", "width"]].isnull().any().any():
        raise ValueError("vehicles.csv에 비어 있는 값이 있습니다.")
    if parking[["space", "space_type"]].isnull().any().any():
        raise ValueError("parking.csv에 비어 있는 값이 있습니다.")

    vehicles["plate"] = vehicles["plate"].map(normalize)
    vehicles["vehicle_type"] = vehicles["vehicle_type"].astype(str).str.strip()
    vehicles["width"] = pd.to_numeric(vehicles["width"], errors="raise")
    parking["space"] = parking["space"].astype(str).str.strip()
    parking["space_type"] = parking["space_type"].astype(str).str.strip().str.upper()

    if vehicles["plate"].eq("").any() or parking["space"].eq("").any():
        raise ValueError("차량 번호나 공간 번호가 비어 있습니다.")
    if vehicles["plate"].duplicated().any():
        raise ValueError("vehicles.csv에 중복 차량 번호가 있습니다.")
    if parking["space"].duplicated().any():
        raise ValueError("parking.csv에 중복 공간 번호가 있습니다.")
    if (vehicles["width"] <= 0).any():
        raise ValueError("차량 너비는 0보다 커야 합니다.")
    if not set(parking["space_type"]).issubset({"B", "S", "K"}):
        raise ValueError("주차 공간 종류는 B, S, K만 사용할 수 있습니다.")

    return vehicles, parking

def classify_vehicle(vehicle, threshold):
    vehicle_type = vehicle["vehicle_type"].strip().lower()

    if vehicle_type in {"경차", "k", "compact", "compact car"}:
        return "K"

    return "B" if vehicle["width"] >= threshold else "S"

def show_status(parking, status, assigned_cars):
    print("\n===== 주차 현황 =====")
    for _, row in parking.iterrows():
        space = row["space"]

        if status[space] == "점유":
            state = "점유"
        elif space in assigned_cars.values():
            state = "배정됨(주차 전)"
        else:
            state = "빈 공간"

        print(f"{space} | 종류: {row['space_type']} | 상태: {state}")

def main():
    global WIDTH_THRESHOLD_MM

    try:
        vehicles, parking = load_csv()
    except Exception as e:
        print("CSV 검사 실패:", e)
        return

    print("CSV 검사 성공")
    print(f"등록 차량: {len(vehicles)}대")
    print(f"주차 공간: {len(parking)}개")

    non_compact = ~vehicles["vehicle_type"].str.strip().str.lower().isin(
        {"경차", "k", "compact", "compact car"}
    )

    if non_compact.any():
        try:
            WIDTH_THRESHOLD_MM = float(
                input("비경차 너비 기준 n(mm)을 입력하세요: ")
            )
            if WIDTH_THRESHOLD_MM <= 0:
                raise ValueError("기준은 0보다 커야 합니다.")
        except ValueError as e:
            print("너비 기준 입력 오류:", e)
            return

    status = {space: "빈 공간" for space in parking["space"]}
    assigned_cars = {}

    while True:
        print("\n===== 메뉴 =====")
        print("1. 차량 입차 및 공간 배정")
        print("2. 주차 완료")
        print("3. 출차")
        print("4. 주차 현황")
        print("0. 종료")

        choice = input("선택: ").strip()

        if choice == "1":
            plate = normalize(input("차량 번호: "))

            if plate in assigned_cars:
                print("이미 배정된 공간:", assigned_cars[plate])
                continue

            matched = vehicles[vehicles["plate"] == plate]
            if matched.empty:
                print("등록되지 않은 차량입니다.")
                continue

            vehicle = matched.iloc[0]
            required_type = classify_vehicle(
                vehicle, WIDTH_THRESHOLD_MM or 0
            )

            reserved = set(assigned_cars.values())
            candidates = parking[
                (parking["space_type"] == required_type)
                & (parking["space"].map(lambda s: status[s] == "빈 공간"))
                & (~parking["space"].isin(reserved))
            ]["space"].tolist()

            print(
                f"차량: {plate} | 종류: {vehicle['vehicle_type']} | "
                f"너비: {vehicle['width']}mm | 필요한 공간: {required_type}"
            )

            if not candidates:
                print("적합한 빈 주차 공간이 없습니다.")
                continue

            space = random.choice(candidates)
            assigned_cars[plate] = space
            print(f"배정 완료: {space}")
            print("아직 주차 완료 전이므로 점유 상태가 아닙니다.")

        elif choice == "2":
            plate = normalize(input("주차 완료한 차량 번호: "))

            if plate not in assigned_cars:
                print("배정된 공간이 없는 차량입니다.")
                continue

            space = assigned_cars[plate]
            status[space] = "점유"
            print(f"{space} 공간이 점유 상태로 변경되었습니다.")

        elif choice == "3":
            plate = normalize(input("출차할 차량 번호: "))

            if plate not in assigned_cars:
                print("배정된 공간이 없는 차량입니다.")
                continue

            space = assigned_cars.pop(plate)
            status[space] = "빈 공간"
            print(f"출차 완료: {plate} / 해제된 공간: {space}")

        elif choice == "4":
            show_status(parking, status, assigned_cars)

        elif choice == "0":
            print("테스트를 종료합니다.")
            break

        else:
            print("올바른 메뉴를 선택하세요.")

if __name__ == "__main__":
    main()
