# B4 — COLMI R02 BLE 受信プロトコル仕様書

**更新日:** 2026-05-04
**ステータス:** Draft (実機リング未到着、 プロトコル層は OSS リファレンスから推定)
**前提:** memory のバイオメトリクス入力方針 (COLMI R02 採用) を実装する第一歩
**関連:** `colmi_r02_protocol.py`, `colmi_r02_client.py`, `hrv_calculator.py`,
`specs/a6_soul_binary_format.md` (HEALTH_VITALS 暗号化セクション)

---

## 0. 設計目的

Ghost-Printer のバイオメトリクス入力ソースとして COLMI R02 系スマートリングを
採用する (memory 参照)。 リングから心拍 / RR間隔 / 睡眠 / バッテリ等を BLE 経由で
読み取り、 HRV を計算して SOUL の `HEALTH_VITALS` セクション (A6 で AES-GCM 暗号化)
に格納する。

**スコープ:**
- ✅ プロトコル層: 16B パケット + チェックサム、 主要コマンドの builder/parser
- ✅ Backend 抽象: BleakBackend (実 BLE) / SimulatedBackend (テスト用)
- ✅ ColmiClient: 高レベル async API
- ✅ HRV 計算: RMSSD / SDNN / pNN50 / mean_hr / stress_score
- ⏳ 実機ペアリング: SD 到着 + リング入手後
- ⏳ A6 HEALTH_VITALS への永続化配線: ColmiClient → SOUL update のフロー
- ⏳ COLMI 専用 systemd unit (常駐ペアリング保持 + 切断自動復旧)

---

## 1. プロトコル層

### 1.1 BLE GATT 構造

| 項目 | UUID | 役割 |
|------|------|------|
| Service | `6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E` | Nordic UART 風カスタム |
| TX (write) | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` | ホスト → リング |
| RX (notify) | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` | リング → ホスト |

> UUID は OSS 実装 (tahnok/colmi_r02_client / ATC_RF03_Ring) を参照。 COLMI 系は
> ファームウェア違いで微妙に揺れる可能性あるため、 実機到着後に scan で検証する。

### 1.2 16 バイトパケット形式

```
byte 0     1                            14    15
┌───┬─────────────────────────────────┬─────┐
│cmd│        payload (14 bytes)        │ chk │
└───┴─────────────────────────────────┴─────┘
```

- `cmd` (1B): コマンド ID
- `payload` (14B): コマンド固有のデータ。 残余は 0 埋め
- `chk` (1B): チェックサム = `sum(packet[0:15]) & 0xFF`

`build_packet(cmd, payload)` でこの形式に整形、 `parse_packet(packet)` で
チェックサム検証 + 分解。

### 1.3 主要コマンド (推定値、 実機で要検証)

| Cmd | 名称 | 用途 |
|-----|------|------|
| `0x01` | SET_TIME | リング内蔵 RTC を同期 |
| `0x03` | BATTERY | バッテリ残量と充電中フラグ取得 |
| `0x08` | REBOOT | リング再起動 |
| `0x15` | GET_HR_HISTORY | 直近 N 件の心拍履歴 |
| `0x21` | START_REALTIME_HR | リアルタイム HR ストリーム開始 |
| `0x22` | STOP_REALTIME_HR | 停止 |
| `0x27` | GET_SLEEP | 睡眠サマリ取得 |

### 1.4 応答パース

| 応答 | payload 構造 |
|------|--------------|
| `BatteryStatus` | byte0=level(0..100), byte1=charging(0/1) |
| `HeartRateSample` | bytes0-1=bpm(LE u16), bytes2-3=rri_ms(LE u16), bytes4-7=ts_s(LE u32) |
| `SleepSummary` | bytes0-1=total_min(LE u16), bytes2-3=deep_min(LE u16), bytes4-5=light_min(LE u16), byte6=quality(0..100) |

不明なコマンドが届いても parser はエラーを投げず無視する (= 実機の挙動が
推定と違っても致命的にならない)。

---

## 2. Backend 抽象

`flash_cortex.py` のシミュレータパターンに倣い、 実 BLE と擬似 BLE を切替可能に
する。

```python
class ColmiBackend(ABC):
    async def scan(self, timeout_s: float) -> list[str]: ...
    async def connect(self, address: str) -> bool: ...
    async def disconnect(self) -> None: ...
    async def write(self, packet: bytes) -> None: ...
    async def subscribe(self, handler: Callable[[bytes], None]) -> None: ...

class BleakBackend(ColmiBackend):
    """実 BLE。 bleak ライブラリを遅延 import"""

class SimulatedBackend(ColmiBackend):
    """擬似 BLE。 リアルな HR/battery 応答を合成して
    notify ハンドラに渡す。 テストおよび OPi 不在時の開発用"""
```

`SimulatedBackend` の使い道:
- 実機リング未到着でも上位ロジック (SOUL 更新 / HRV 計算) を開発できる
- CI/CD で BLE スタック不要 (Mac/Linux 共通でテストが回る)
- 実機が壊れたとき / 電池切れのときの代替

### 2.1 SimulatedBackend の動作モデル

- スキャンで仮想アドレス `00:11:22:33:44:55` を返す
- HR ストリーム要求を受けると、 sin 波で揺れる擬似 HR を生成 (60-80 bpm)
- バッテリ要求は 85% / 充電中=False を返す
- HR サンプルには現実的な RR 間隔 (= 60000/bpm + ジッター) を入れる

実機のクセを完全に再現はしないが、 上位パイプラインのテストには十分。

---

## 3. ColmiClient (高レベル async API)

```python
class ColmiClient:
    def __init__(self, backend: ColmiBackend): ...
    
    async def connect(self) -> str:
        """スキャン + 接続 + 通知購読を一括"""
    
    async def disconnect(self) -> None: ...
    
    async def get_battery(self, timeout_s: float = 3.0) -> BatteryStatus: ...
    
    async def stream_heart_rate(self, duration_s: float) -> list[HeartRateSample]:
        """N 秒間 HR ストリーミングして全サンプルを返す"""
    
    async def get_sleep_summary(self, timeout_s: float = 3.0) -> SleepSummary: ...
```

応答待ちは `asyncio.Event` で同期する。 timeout で例外。

### 3.1 リトライ / 切断対応 (将来)

- BLE 切断 → 自動再接続 (5 秒バックオフ)
- write タイムアウト → 1 回リトライ
- subscribe 失敗 → connect からやり直し

これらは Pre-MVP では未実装。 systemd unit で常駐させて、 落ちたら全体を
restart する方針で当面しのぐ。

---

## 4. HRV 計算

`hrv_calculator.py` で RR 間隔列から HRV 指標を算出する。 **純関数**で BLE
依存なし、 完全 sync。

| 指標 | 式 | 意味 |
|------|----|------|
| `mean_hr` | `60_000 / mean(rri_ms)` | 平均心拍 (BPM) |
| `RMSSD` | `sqrt(mean((rri[i+1] - rri[i])^2))` | 副交感神経活性。 高い = リラックス |
| `SDNN` | `std(rri_ms)` | 全体の心拍変動。 高い = 自律神経の幅広さ |
| `pNN50` | `count(\|rri[i+1]-rri[i]\| > 50ms) / N` | 微小揺らぎの割合 |
| `stress_score` | `clamp(1 - rmssd/baseline)` | 0..1 のストレス度 (baseline 50ms) |

### 4.1 ストレススコアの単純化

実装は **線形マッピング**で:

```
score = clamp(1.0 - rmssd_ms / 50.0, 0.0, 1.0)
```

- `rmssd >= 50ms` → score = 0.0 (リラックス)
- `rmssd == 25ms` → score = 0.5
- `rmssd <= 0ms`  → score = 1.0 (高ストレス)

実機で個人差があるため、 baseline_rmssd は将来 CORTEX.bin で個別チューニング
可能にする (現時点では固定 50ms)。

### 4.2 SOUL HEALTH_VITALS への投入

```python
soul["health_vitals"] = {
    "hr_avg": metrics.mean_hr,
    "rmssd_ms": metrics.rmssd_ms,
    "sdnn_ms": metrics.sdnn_ms,
    "pnn50": metrics.pnn50,
    "stress_level": metrics.stress_score,
    "sample_count": metrics.sample_count,
    "measured_at_ms": int(time.time() * 1000),
}
```

A6 の `encode_soul()` は `health_vitals` 等の top-level field を **既定で**
AES-GCM 暗号化セクション (`SEC_HEALTH_VITALS = 0x09`) に入れる。 master_key を
持たない外部 AI は health_vitals scope の token を持っていない限り読めない。

---

## 5. 実機ペアリング手順 (リング到着後)

### 5.1 スキャンと初回接続

```bash
# OPi 3B 上で
python -c "
import asyncio
from colmi_r02_client import ColmiClient, BleakBackend
async def main():
    client = ColmiClient(BleakBackend())
    addr = await client.connect()
    print(f'connected: {addr}')
    bat = await client.get_battery()
    print(f'battery: {bat.level}% charging={bat.charging}')
    await client.disconnect()
asyncio.run(main())
"
```

### 5.2 60 秒 HR ストリーム + HRV

```bash
python -c "
import asyncio
from colmi_r02_client import ColmiClient, BleakBackend
from hrv_calculator import compute_hrv
async def main():
    client = ColmiClient(BleakBackend())
    await client.connect()
    samples = await client.stream_heart_rate(60.0)
    rri = [s.rri_ms for s in samples if s.rri_ms > 0]
    hrv = compute_hrv(rri)
    print(hrv)
    await client.disconnect()
asyncio.run(main())
"
```

### 5.3 A6 と接続して SOUL 更新

```python
from soul_storage import ShadowStorage
storage = ShadowStorage(json_path=..., bin_path=..., log_path=..., master_key=master)
soul = storage.load()
soul["health_vitals"] = compute_hrv(rri).to_dict()
storage.append_update(soul, raw_text="[bio update]", delta={...})
```

---

## 6. リスクと対策

| リスク | 対策 |
|--------|------|
| OSS 推定プロトコルが実機と違う | SimulatedBackend で動作確認、 実機到着後に scan + パケットダンプで検証 |
| BLE 接続が頻繁に切れる | systemd Restart=always で再接続。 BlueZ ログを定期監視 |
| Bonsai 推論で BLE スレッドが詰まる | asyncio で別タスク化。 BLE 受信 → SQLite キュー → 別ワーカで HRV 計算 |
| 心拍データの永続化が SD 摩耗 | A6 ShadowStorage の APPEND_LOG モードで追記のみ。 30 分毎にバッチで HEALTH_VITALS セクションを更新 |
| 個人差で stress_score が外れる | 初期 7 日でユーザ baseline RMSSD を学習、 CORTEX.bin に保存して以後それを使う |

---

## 7. テスト方針 (今回実装分)

| テスト | 範囲 |
|--------|------|
| パケット形式 | build/parse round-trip, チェックサム検証, payload 長検証, 不正パケット検出 |
| コマンド builder | battery / realtime_hr_start/stop / set_time / sleep |
| 応答 parser | BatteryStatus / HeartRateSample / SleepSummary, 不正値の処理 |
| SimulatedBackend | scan/connect, HR ストリーム合成, battery 応答, 切断 |
| ColmiClient | connect → get_battery → disconnect の async フロー |
| HRV 計算 | RMSSD / SDNN / pNN50 / mean_hr の既知値, 空入力 / 1 件のエッジ, stress_score クランプ |

実 BLE を要するテストは省略 (実機到着後に手動検証)。

---

## 8. 次のステップ

1. 本仕様承認後 → プロトコル層 + Backend + Client + HRV を実装
2. テスト 25+ 件で round-trip と HRV 数学的正確性を担保
3. 実機リング到着後: scan + パケットダンプで実プロトコル検証 → §1.3 を実値で書換
4. ColmiClient と ShadowStorage を結ぶ常駐ワーカ実装
5. Watch Point からの利用: HR 異常値で `health_anomaly` WP を生成 (Track A7 連携)
