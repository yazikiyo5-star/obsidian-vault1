# COLMI R02 プロトタイピング：購入＆セットアップガイド

**対象**: Ghost-Printer のバイオメトリクス入力源として COLMI R02 系スマートリングを検証する
**開発環境前提**: macOS（Apple Silicon / Intel どちらも可）
**最終形態前提**: Ghost-Printer 本体（Radxa Zero 3W / RK3566 Linux SBC）が BLE を直接受信する構成
**作成日**: 2026-04-18（2026-04-18 更新：メインボードを Radxa Zero 3W に確定、ESP32 案を削除）

---

## 0. TL;DR

1. **サイジングキットだけ先に買う**（$5〜）。リング本体はサイズが合わないと計測精度が落ちるため。
2. サイズ確定後、**COLMI R02（または R03 / R06 どれでも可）**を AliExpress / DHgate / Amazon で購入（$20〜25）。
3. macOS に `pipx install git+https://github.com/tahnok/colmi_r02_client` で Python クライアント導入。
4. `colmi_r02_util scan` → `colmi_r02_client --address=... get-real-time heart-rate` で動作確認。
5. Ghost-Printer 本体への統合は **Radxa Zero 3W 内蔵 BT + BlueZ + Python(bleak)** 構成。`colmi_r02_client` を systemd サービスとして常駐させる。

---

## 1. モデル選定：R02 / R03 / R06 どれを買うか

調べた結論：**内部部品はすべて同じ**。BOM（部品表）もPCBも共通で、違うのは外観と型番だけ。センサー・チップ・ファームウェアは同じ系譜。

| 項目 | 仕様 |
|---|---|
| SoC | BlueX Micro RF03（200KB RAM / 512KB Flash） |
| 心拍センサー | VCare VC30F（PPG） |
| 加速度センサー | STK8321 |
| バッテリー | 17mAh |
| 防水 | 5ATM |
| 公称電池持ち | 5〜7日（実測4日程度） |
| 充電 | 専用ドック経由 |

**選び方の指針**：
- **R02**：最も出回っており情報・作例が多い → **プロトタイピングには R02 推奨**
- **R03**：デザインがよりスタイリッシュ、価格はやや上
- **R06**：低価格帯、ユーザーレビュー良好

**`colmi_r02_client` の対応型番**: R02 / R06 / R10（R03 は明記なし、ただし内部同一のため動作する可能性は高い）

→ 迷ったら **R02 を選ぶのが最も安全**。

---

## 2. サイジング

これ、**必ず先にやる**。リングは物理デバイスなのでサイズが合わないと：
- 血流の光学的計測（PPG）の精度が落ちる
- 動いてしまって測定失敗
- 返品・再購入で時間がかかる

### サイズ範囲
US 7〜12（日本サイズだとだいたい13号〜24号に相当）。

### 推奨手順
1. **COLMI Sizing Kit を先に買う**（$5 前後）。各サイズの樹脂製ダミーリングが入っている
2. 2〜3日、測定したい指（推奨：人差し指 / 中指 / 薬指）に実装着して違和感ないサイズを特定
3. 特定したサイズで本体を注文

装着指について：薬指が一般的だが、**PPGセンサーの精度は人差し指・中指の方が良い**との情報あり。Ghost-Printer 用途では計測精度優先で人差し指も検討の価値あり。

---

## 3. 購入先

| 販路 | 価格帯 | 納期 | 備考 |
|---|---|---|---|
| AliExpress | $18〜25 | 2〜4週間 | 最安。複数の出品者あり。「COLMI R02」で検索 |
| DHgate | $20〜28 | 2〜3週間 | 業者色が強い。卸し寄り |
| Amazon.com | $30〜40 | 数日〜1週間 | 高いが速い。サイジングキット同梱版あり |
| Amazon.co.jp | ¥5,000〜7,000 | 数日 | 在庫あれば最速。公式ストアは無い |

**Ghost-Printer のプロトタイピング観点での推奨**:
- **まず Amazon.co.jp でサイジングキット + 本体セット**を買って即着手
- スケールアウトが必要になったら AliExpress で複数個まとめ買い
- 将来の分解・改造用に R02 を 2〜3 個持っておくと実験がはかどる

---

## 4. macOS セットアップ

### 4.1 前提環境

- macOS 12 以降（Core Bluetooth が現行 API）
- Python 3.11+ 推奨
- Homebrew 導入済み

### 4.2 pipx のインストール（未導入なら）

```bash
brew install pipx
pipx ensurepath
```

`pipx` を使う理由：`colmi_r02_client` をシステム Python や venv 汚染なしに隔離インストールするため。

### 4.3 colmi_r02_client のインストール

```bash
pipx install git+https://github.com/tahnok/colmi_r02_client
```

依存の `bleak`（macOS 側 Core Bluetooth 抽象化ライブラリ）は自動で入る。

### 4.4 Bluetooth 権限

macOS はアプリごとに Bluetooth 権限を要求する。初回実行時にターミナル（または iTerm / VS Code のターミナル）に Bluetooth アクセス許可を出す：

- システム設定 → プライバシーとセキュリティ → Bluetooth
- ターミナル系アプリを許可

---

## 5. 最小動作確認

### 5.1 リングのスキャン

まずリングを手に装着した状態（またはペアリング済み状態）で：

```bash
colmi_r02_util scan
```

出力例：
```
R02_1234 70:CB:0D:D0:34:1C
```

MAC アドレス（`70:CB:0D:D0:34:1C`）をメモする。

### 5.2 リアルタイム心拍取得

```bash
colmi_r02_client --address=70:CB:0D:D0:34:1C get-real-time heart-rate
```

### 5.3 SpO2 取得

```bash
colmi_r02_client --address=70:CB:0D:D0:34:1C get-real-time spo2
```

### 5.4 ローカル SQLite へ全量同期

```bash
colmi_r02_client --address=70:CB:0D:D0:34:1C sync
```

これでステップログ・心拍ログ・デバイス情報が SQLite に書き込まれる。
→ **Ghost-Printer の最初の統合ポイントはこの SQLite を読む方式が最短**。

### 5.5 使えるコマンド一覧

```bash
colmi_r02_client --help
```

---

## 6. 取得できるデータ（2026-04 時点）

### 実装済み
- リアルタイム心拍数
- リアルタイム SpO2（血中酸素飽和度）
- ステップログ（歩数 / 消費カロリー）
- 心拍ログ（周期的測定 / デフォルト 30 分間隔 or 設定可）
- デバイス情報
- バッテリーレベル
- 時刻設定
- 心拍ログ頻度設定

### 未実装（将来拡張の余地）
- SpO2 ログ（周期的）
- 睡眠追跡
- ストレス測定

**Ghost-Printer 視点での評価**：
- 「緊張・興奮」推定に必要な **心拍変動（HRV）は生データ（心拍間隔）からこちら側で計算可能**。リングが HRV を直接出す必要はない
- 睡眠は未実装だが、心拍ログ + 加速度情報があれば睡眠ステージ推定は自前実装可能
- 入力として十分な「密度」がある

---

## 7. BLE 通信プロトコル仕様（Ghost-Printer 直接実装用）

Ghost-Printer 本体（Radxa Zero 3W）が直接 BLE 受信する上での、プロトコル仕様。基本的に `bleak` / `colmi_r02_client` がラップしてくれるため通常は意識不要だが、カスタムコマンド送信や独自パーサ実装時の参考用。

### 7.1 サービス / キャラクタリスティック

Nordic UART Service（NUS）に類似した設計：

| 項目 | UUID |
|---|---|
| Service | `6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E` |
| RX (書込) | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` |
| TX (通知購読) | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` |

標準 Nordic UART Service と先頭の `6E400001` vs `6E40FFF0` だけ違うことに注意（完全互換ではない）。

### 7.2 パケットフォーマット

固定 **16 バイト** の双方向通信：

```
+------+-----------------------------+--------+
| CMD  |       PAYLOAD (14 bytes)    | CKSUM  |
+------+-----------------------------+--------+
  1B              14B                    1B
```

- `CMD`: コマンドタイプ / データタイプ（1 バイト）
- `PAYLOAD`: コマンドごとのペイロード（14 バイト）
- `CKSUM`: 先頭 15 バイトの合計 **mod 255**

### 7.3 セキュリティ

- **バインディングなし**
- **ペアリングキーなし**
- 誰でも接続可能（近傍のみだが）

これはメリット（プロトタイピング容易）でもあるし、**製品化時のリスク**でもある。Ghost-Printer 本体側で「信頼する MAC アドレスリスト」の仕組みを作るなどの対策が必要。

---

## 8. Ghost-Printer 本体直結構成（Radxa Zero 3W）

**確定方針**: Ghost-Printer 本体は **Radxa Zero 3W（RK3566, Cortex-A55 × 4, NPU 0.8 TOPS）** を採用。WiFi 6 + BT 5.0 が標準搭載されているため、**BLE 通信用の外部マイコンは不要**。Linux カーネル上の BlueZ スタックを Python から叩く構成で、 `colmi_r02_client` がそのまま動きます。

### 8.1 なぜ Radxa Zero 3W か

- **BLE スタックが標準搭載**：別途 ESP32 / nRF52 を載せる必要がない → 基板設計がシンプル
- **NPU 0.8 TOPS**：Bonsai 1.7B（意味抽出・重要度判定エンジン、1bit量子化 240MB）のローカル推論を加速。※ 会話生成AIではない点に注意
- **Linux（Debian / Ubuntu / Armbian）フル機能**：`colmi_r02_client`（Python）+ `bleak` がそのまま動く
- **サイズ 65×30mm**：Pi Zero フォームファクタ、最終形状に収まる
- **Tier 2 移行が楽**：Radxa CM3（同じ RK3566 SoM）にコード資産 100% 流用可能
- **ESP32-S3 案を却下した理由**：SBC にすでに BLE があるのに別チップを増やすと電源系・パケット転送・冗長性で損をする

### 8.2 推奨 SKU

| 項目 | 推奨 |
|---|---|
| 型番 | Radxa Zero 3W |
| RAM | **4GB LPDDR4**（2GB でも動くが Bonsai に余裕がない） |
| eMMC | **なし（microSD 運用）** ← 開発中は差し替え容易の方が楽 |
| WiFi/BT | 標準搭載（AP6212A 系） |
| 目安価格 | $28〜33 |

### 8.3 OS 選定

| OS | Pros | Cons |
|---|---|---|
| **Debian 12 (Radxa 公式)** | RKNPU ドライバ標準、情報量多い | 初期イメージに要手動カスタム |
| Armbian | H6/RK3566 系で長期サポート | NPU 利用は手動セットアップ |
| Ubuntu 22.04 | パッケージ新しい | RK3566 向けは Radxa 公式推奨から一歩外れる |

**推奨**: **Debian 12 公式イメージ** を microSD に焼いてスタート。Bonsai 推論時に NPU ドライバ（RKNPU2）を追加導入。

### 8.4 セットアップフロー（到着後）

```bash
# 1. microSD に Debian イメージ焼き（Mac 上で）
# Radxa 公式 wiki の latest image を取得
# → balenaEtcher / dd でSDに書き込み

# 2. 初回ブート → SSH 有効化（HDMI + キーボード or UART）
sudo systemctl enable ssh
sudo systemctl start ssh

# 3. パッケージ更新と必要物インストール
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip pipx bluetooth bluez libbluetooth-dev sqlite3 git
pipx ensurepath

# 4. BT チップの確認
hciconfig -a          # hci0 が UP になっていることを確認
bluetoothctl          # BlueZ 対話シェル
# > scan on で COLMI R02 が見えるか確認

# 5. colmi_r02_client 導入
pipx install git+https://github.com/tahnok/colmi_r02_client

# 6. 動作確認
colmi_r02_util scan
colmi_r02_client --address=XX:XX:XX:XX:XX:XX sync
```

macOS 用セットアップ（章4）とほぼ同じ手順が Linux で動きます。

### 8.5 常駐サービス化（systemd）

Ghost-Printer 運用では常時データ取得するため、systemd ユニット化する：

```ini
# /etc/systemd/system/ghost-ring.service
[Unit]
Description=Ghost-Printer ring data collector
After=bluetooth.target network.target
Requires=bluetooth.service

[Service]
Type=simple
User=ghost
ExecStart=/home/ghost/.local/bin/colmi_r02_client --address=XX:XX:XX:XX:XX:XX sync --loop
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable ghost-ring.service
sudo systemctl start ghost-ring.service
journalctl -u ghost-ring -f
```

※ `--loop` は `colmi_r02_client` の機能として存在しない場合は、シェルループで代用するか薄いラッパーを書く。

### 8.6 Python 薄いラッパーの骨格（カスタム処理用）

`colmi_r02_client` 内部は `bleak` を使っているので、カスタムロジックを足す場合は同じ UUID を使って直接 `bleak` を叩ける：

```python
import asyncio
from bleak import BleakClient

SERVICE_UUID = "6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E"
TX_UUID      = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # 通知購読
RX_UUID      = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # 書き込み

RING_ADDR = "XX:XX:XX:XX:XX:XX"  # colmi_r02_util scan で取得

def verify_checksum(pkt: bytes) -> bool:
    return sum(pkt[:15]) % 255 == pkt[15]

def handle_notify(_sender, data: bytearray):
    if len(data) != 16 or not verify_checksum(bytes(data)):
        return
    cmd = data[0]
    payload = data[1:15]
    # cmd ごとのディスパッチ → 心拍/SpO2/歩数をGhost-Printerの内部状態 or SQLite に書き込み
    print(f"cmd=0x{cmd:02x} payload={payload.hex()}")

async def main():
    async with BleakClient(RING_ADDR) as client:
        await client.start_notify(TX_UUID, handle_notify)
        # 例: リアルタイム心拍取得コマンド（実際のパケット仕様は colmi_r02_client を参照）
        # await client.write_gatt_char(RX_UUID, build_command(...))
        await asyncio.sleep(600)

asyncio.run(main())
```

**bleak は Linux（BlueZ） / macOS（Core Bluetooth） / Windows（WinRT）で同一 API**なので、Mac で書いた試作をそのまま Radxa に持ち込める。これが Python 構成の最大の利点。

### 8.7 データフロー（改訂版）

重要: Bonsai は **意味抽出 / 重要度判定エンジン**。対話生成は行わず、SOUL 構築の上流で働く。実際の「AIとの会話」は、Ghost-Printer の外（Claude 等の外部AI）で起きる。

```
[COLMI R02]
     │ BLE 5.0 (NUS風プロトコル, 16バイト固定パケット)
     ▼
[Radxa Zero 3W 内蔵 BT チップ (AP6212A 系)]
     │ Linux カーネル / BlueZ
     ▼
[Python: bleak + colmi_r02_client (systemd 常駐)]
     │ 生データ → SQLite
     ▼
[HRV計算 / 状態推定 (NumPy / SciPy)]
     │ 正規化イベント列（時系列 + バイタル + コンテキスト）
     ▼
[Bonsai 1.7B ローカル推論 (NPU + CPU)]
     │ 意味抽出デルタ: {importance, emotion, personality_signals, summary}
     ▼
[ベイズ更新 (μ, σ) で SOUL にマージ]
     │
     ▼
[.soul バイナリ (4層構造: Core / Episodic / Semantic / Temporal)]
     │ Permission Gateway で選択的に開示
     ▼
[System Prompt 化]
     │
     ▼
[外部AI (Claude 等) が受信 → ユーザーとの会話を生成]
```

### 8.8 プロセス分離の推奨

BLE 接続は持続的、意味抽出推論はバースト的という**非対称性**があるため、以下のように疎結合にすると安定する：

- **Process A**: `ghost-ring.service` — BLE 常駐、SQLite に write-only
- **Process B**: `ghost-bonsai.service` — SQLite を読み、Bonsai で意味抽出デルタを生成
- **Process C**: `ghost-soul.service` — デルタをベイズ更新で SOUL にマージし、`.soul` バイナリを書き出す
- **（Process D はデバイス内には存在しない）** — 外部AI への橋渡しは Permission Gateway + System Prompt 化のみ

Bonsai 推論中に BLE が切れるリスクを構造的に排除でき、また「対話レイテンシ」はデバイス側の仕事ではないため、Bonsai は非同期・バッチ寄りで回してよい。

### 8.9 Tier 2 への移行性

Radxa Zero 3W（$28）→ **Radxa CM3**（同 RK3566 SoM、$35〜55）にはソフトウェア 100% 互換で移行可能。カスタムキャリアボード設計時は：

- RK3566 用ピン配置を CM3 データシートから流用
- BlueZ + bleak のコードは変更不要
- NPU ドライバも同一

つまり章 8 の構成で作ったソフトウェアは、Tier 2 プロダクト設計でもそのまま生きる。

---

## 9. 既知の制約 / 注意点

### 9.1 ファームウェア仕様
- センサーのサンプリング間隔はファームウェア側で制御（デフォルト 30 分）
- より高頻度に取りたい場合は `colmi_r02_client` 経由で頻度設定コマンドを送信
- OTA でカスタムファームウェア焼きもあり得る（`ATC_RF03_Ring` 参照）

### 9.2 同時接続
- リングは **1 BLE セントラル**にしかペアリングできない
- プロトタイピング中に純正アプリと colmi_r02_client を交互に使うと再ペアリングが発生しがち
- **純正アプリは最初のセットアップだけで、以降は colmi_r02_client 一本**にするのが吉

### 9.3 計測精度
- 格安帯リングなので Oura / Apple Watch と比較すると精度は見劣りする
- 特に運動中の心拍は乖離しやすい（PPG の宿命）
- プロトタイプ用途には十分だが、「本番で医療っぽい主張をしない」ことは守る

### 9.4 ベンダーリスク
- COLMI は中国の中堅メーカー。突然のディスコンやアプリ提供停止リスクはある
- ただし **OSS クライアント + BLE 直結**という構成ならベンダー依存は低く、突然アプリが止まっても影響は最小
- ファームウェア書き換え経路（ATC1441 の作業）も既に確立されているため、いざとなれば完全自前運用も可能

---

## 10. 推奨アクションプラン

### Week 1
- [ ] COLMI R02 + サイジングキットを注文（Amazon.co.jp 即発送 or Ali の本命）
- [ ] macOS に pipx + colmi_r02_client を導入
- [ ] `colmi_r02_util scan` が空振りしないかダミーリング準備中に確認

### Week 2
- [ ] 実機到着 → サイズ確認 → ペアリング
- [ ] `get-real-time heart-rate` と `sync` で基礎データ取得
- [ ] SQLite の中身を見て、どんなフィールドがあるか把握

### Week 3
- [ ] 心拍の生データから HRV（RMSSD / SDNN）を自前計算してみる
- [ ] 緊張・運動・睡眠の状態ラベルを作る簡易ルールベースを試作
- [ ] SOUL フォーマットへの変換スクリプトを書く

### Week 4 以降
- [ ] Radxa Zero 3W 到着 → Debian 12 イメージを microSD へ書き込み、SSH 接続
- [ ] Radxa 上で `pipx install colmi_r02_client` → mac での試作を移植して動作確認
- [ ] `ghost-ring.service` を systemd ユニット化、SQLite ベースのプロセス分離設計
- [ ] Bonsai 1.7B 導入（意味抽出エンジン）と RKNPU2 経由の推論パス検証 — 入出力は `{importance, emotion, personality_signals, summary}` デルタ
- [ ] Tier 2（Radxa CM3 + カスタムキャリアボード）への移行検討

---

## 11. 参考リンク集

### OSS / 実装
- [tahnok/colmi_r02_client (Python, MIT)](https://github.com/tahnok/colmi_r02_client)
- [atc1441/ATC_RF03_Ring (カスタムFW, OTA ツール)](https://github.com/atc1441/ATC_RF03_Ring)
- [Pinta365/oura_api (TS リファレンス)](https://github.com/Pinta365/oura_api) ※比較用
- [hbldh/bleak (クロスプラットフォーム BLE ライブラリ)](https://github.com/hbldh/bleak)

### Radxa Zero 3W 関連
- [Radxa Zero 3W 公式 Wiki](https://radxa.com/products/zeros/zero3w/)
- [Radxa 公式ディスクイメージ](https://github.com/radxa-build/radxa-zero3)
- [RKNPU2 (Rockchip NPU driver)](https://github.com/airockchip/rknn-toolkit2)
- [Armbian for RK3566](https://www.armbian.com/)

### エコシステム
- [Gadgetbridge (Codeberg)](https://codeberg.org/Freeyourgadget/Gadgetbridge) - Android ローカル管理
- [Halo (Cyril Zakka, HF Blog)](https://huggingface.co/blog/cyrilzakka/halo-introduction) - COLMI R02 ベース OSS
- [Open Wearables](https://github.com/the-momentum/open-wearables) - 統合レイヤー参考

### レビュー / 解説
- [Colmi R02 Review (Tedium)](https://tedium.co/2024/11/08/colmi-r02-hacker-ring-review/)
- [Cheap Hackable Smart Ring (Hackaday)](https://hackaday.com/2025/03/04/cheap-hackable-smart-ring-gets-a-command-line-client/)
- [Adafruit Blog 紹介記事](https://blog.adafruit.com/2024/10/15/an-open-source-python-client-to-read-data-from-colmi-r02-smartrings-wearables-opensource-python/)

### 公式
- [COLMI 公式](https://www.colmi.info/collections/colmi-smart-rings)
