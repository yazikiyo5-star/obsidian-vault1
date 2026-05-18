# Ghost-Print  ·  Teaser & Site  ·  Concept Pack v0.3

> 作成日: 2026-05-13
> ステータス: **コンセプト v0.3 ・ レビュー待ち**

---

## v0.1 → v0.2 → v0.3 の変更点

**v0.1 → v0.2**
1. **タグライン再編成**: 主タグラインを「**人類、 あなたの選択は？ / What will you give them?**」 (エヴァ的な問いかけ型) に変更
2. **SOUL → GHOST 統一**: バイナリ名 (GHOST.bin)、 Soul Dock → Ghost Dock、 Soul Protocol → Ghost Protocol、 全資料で置換
3. **CORE + SHELL コンセプトの追加**: ハードウェア哲学として 2 層構造を明示
4. **3D Core + Shell ビューア**: HTML モック (04_mockup.html) に Three.js で実装

**v0.2 → v0.3**
5. **Three Components セクションを追加**: Ring / Core / Shell の役割を **画像付き 3 カラム** で並べる新セクション (LP §03)
6. **3 つの動詞**: RING = FEEL / 感じる ・ CORE = EDIT / 編集する ・ SHELL = DRESS / 装う
7. **SVG イラスト 3 点**: `assets/ring.svg` `core.svg` `shell.svg` を独立ファイルでも配布
8. **動詞ペアの哲学**: 身体 → 認知 → 表現の連鎖。 Three Pillars (データ動詞) と並走する物理レイヤー

---

## このフォルダの中身

| # | ファイル | 内容 | 想定読者 |
|---|----------|------|---------|
| 00 | `00_README.md` | この案内 | 全員 |
| 01 | `01_worldview.md` | 世界観・トーン・配色・タイポ・ブランドボイス・**Ring/Core/Shell 哲学** | デザイン/ディレクション |
| 02 | `02_script.md` | ティザー脚本 38秒 + 15秒 + 6秒 ・ カット表 | 監督/撮影/CG/編集 |
| 03 | `03_site.md` | サイト構成 (LP + /device + /shells + 技術ドキュメント) | フロントエンド/コピー |
| 04 | `04_mockup.html` | LP のビジュアルモック (**3 役割カード + 3D ビューア入り**) | 全員 ・ ブラウザで開く |
| -- | `assets/ring.svg` | Ring (FEEL) のイラスト ・ 単体配布可 | プレス/別資料 |
| -- | `assets/core.svg` | Core (EDIT) のイラスト ・ 単体配布可 | プレス/別資料 |
| -- | `assets/shell.svg` | Shell (DRESS) のイラスト ・ 単体配布可 | プレス/別資料 |

---

## 30 秒で要点

- **方向性**: サイバーパンク・ダーク（GitS / Blade Runner 2049 系譜）
- **主タグライン**: 「**人類、 あなたの選択は？** / What will you give them?」
- **副タグライン**: Print your ghost.
- **メッセージ**: AI に魂を奪われる時代に、 **自分で印字する** 装置
- **3 つの物理コンポーネント**:
  - **RING ・ FEEL / 感じる** (肌の上 / COLMI R02)
  - **CORE ・ EDIT / 編集する** (机の上 / rev.A6 + Bonsai 1.7B)
  - **SHELL ・ DRESS / 装う** (手の中 / STL 公開)
- **ハードウェア哲学**: **コアは閉じる、 シェルは開ける** (= シェル STL 配布)
- **ティザー**: 38 秒（主編） + 15 秒（SNS） + 6 秒（バンパー）
- **サイト**: 1 ページ LP + `/device` (3D) + `/shells` (STL DL) + 技術ドキュメント
- **配色**: void #050507 / cyan #00FFE5 / magenta #FF1F8F (GHOST 専用)
- **タイポ**: モノスペース見出し (Berkeley Mono 系) + Noto Sans JP
- **ボイス**: 断定・問い・数字。 謝らない。 絵文字なし

---

## 確認をお願いしたいポイント

レビュー時に判断してほしい順序:

1. **タグライン主候補** ・ 「人類、 あなたの選択は？」 で確定して良いか (01_worldview.md §2 / 02_script.md CUT 11)
2. **3D ビューア** ・ 04_mockup.html を **ブラウザで開いて触る** ・ 4 シェル切替・回転の感触
3. **シェル 4 種の方向性** ・ Minimal / Organic / 和 / Naked ・ 過不足や別種の希望
4. **見せる範囲** ・ Ghost-Print 筐体は CUT 5 で 3 秒だけ。 これで足りるか過剰か (02_script.md CUT 5)
5. **/shells コミュニティ路線** ・ STL 投稿・月例企画など、 どこまで踏み込むか (03_site.md §S3.5)

---

## ブラウザで HTML モックを開くには

```
open "/Users/haru/Documents/Claude/Projects/Ghost-print/teaser/04_mockup.html"
```

Mac の Finder で `04_mockup.html` をダブルクリックでも OK。
3D ビューア (S3 セクション) はマウスドラッグで回転、 シェルボタンで切替。

---

## 次フェーズ（承認後の制作プラン）

### Phase 1 ・ 美術確定 (1.5 週間)
- **CORE rev.A6 の 3D モデル** (現状: モック内のシンプル立体 → フォトリアル CG へ)
- **SHELL 公式 4 種の STL データ** (Minimal / Organic / 和 / Naked 透明アクリル)
- Ghost Dock コネクタの確定形状
- GHOST カートリッジのプロップ
- ロゴマーク final

### Phase 2 ・ サイト実装 (2 週間)
- Astro + Tailwind プロジェクト立ち上げ
- 04_mockup.html を分解してコンポーネント化
- **/device の本格 3D ビューア化** (現状モック → glTF モデルロード対応)
- **/shells ギャラリー実装** (STL プレビュー・ DL ・ ライセンス表示)
- /spec, /bonsai, /open の MDX 流し込み
- ステージング公開

### Phase 3 ・ ティザー制作 (2-3 週間)
- 02_script.md の Day 0-4 スケジュールに従う
- 実写撮影 (リング・暗室・プロダクトショット)
- CG 制作 (Bonsai 回転・GHOST バイナリ生成・**CORE+SHELL 分解アニメ**)
- 編集 / カラー / MA
- 4 アスペクト × 5 言語字幕で書き出し

### Phase 4 ・ 同時公開 (1 週間)
- サイト本番デプロイ
- ティザー Vimeo / YouTube 同時公開
- HN / X / Are.na への投下
- ウェイトリスト + /shells 投稿受付開始

合計: **約 6.5-8.5 週間** (外注ラインの太さ次第)

---

## 開発上の注記 (プロジェクトメモリと整合)

このコンセプトパックは、 既存のプロジェクト前提と矛盾しないよう以下を守った:

- **Bonsai 1.7B は対話 AI ではない** ・ 意味抽出・重要度判定エンジンとして描写
- **Pre-MVP は Ghost Dock 有線のみ** (旧 Soul Dock) ・ BLE/Wi-Fi 転送は登場させない
- **OPi 3B (RK3566, 4GB) ・ Bonsai Q1_0 ・ 3.03 t/s ・ 72℃** ・ 実測値をスペック表に直接掲載
- **GHOST v0.1 ・ HEADER 128B + TOC + sections** (旧 SOUL v0.1) ・ 構造を映像に出す
- **Trixie 26.2.0-trunk.843** ・ OS バージョンを HTML モックに掲載
- **Selective self-disclosure (Track C)** ・ "あなたが選ぶ" を 3 ピラーの 1 つに

**命名統一について**: 内部技術ドキュメント (a6/b2/c6 仕様書) は引き続き SOUL のままだが、 **対外コピーはすべて GHOST に統一** する方針。 仕様書の改訂は別途実施 (内部一致のため)。

---

## 質問・要修正があれば

このパックは **動かせる出発点** であって、 完成形ではない。

- "もっとエヴァに寄せたい" → CUT 11 の問いかけをさらに強く ・ シンプルなフォントで
- "もっと攻めたい" → タグライン E「渡す。 戻す。 選ぶ。」を主に格上げ
- "シェルの方向性を変えたい" → Bonsai 風以外に "義体風" "ガジェット風" など追加可
- "3D ビューアの挙動が違う" → 自動回転速度、 ライティング、 配色を調整可

どこを動かしたいか、 具体的に言ってもらえれば全体の整合を取りながら直す。
