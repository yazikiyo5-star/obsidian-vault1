# Ghost-Print / Site Architecture

> ドメイン候補: `ghost-print.io` / `ghostprint.computer` / `soul-dock.com`
> 構成: **LP 1 ページ + 技術ドキュメント別ページ**
> 推奨スタック: **Astro + Tailwind + MDX**（理由: 静的・高速・OSS と親和的・MDX で仕様書をそのまま掲載できる）

---

## 1. サイトマップ

```
/                  Landing (single page, scroll-based)
├── /device        CORE + SHELL の 3D ビューア（製品ページ・目玉）
├── /shells        SHELL ギャラリー (STL ダウンロード・コミュニティ投稿)
├── /spec          GHOST バイナリ仕様 v0.1
├── /protocol      Ghost Protocol 5 層仕様
├── /bonsai        Bonsai 1.7B モデルカード・ベンチ
├── /hardware      OPi 3B / COLMI R02 / Ghost Dock 構成
├── /open          OSS 方針・GitHub リンク・ライセンス
├── /waitlist      Pre-MVP ウェイトリスト登録
└── /press         報道関係者向け（高解像度素材・PDF）
```

優先順位: **/ → /device → /shells → /spec → /bonsai → /open** が初版。 残りは段階的に。

**/device と /shells が新しい目玉:**
- `/device` ・ 3D で CORE と SHELL を回転・分解表示。 シェル種類を切替できる。 製品ページのコア体験
- `/shells` ・ 公式 + コミュニティ投稿の STL を並べる。 ダウンロード可。 印刷ガイド付き

---

## 2. LP（/）の縦スクロール構成

各セクションは画面高さ程度。Linear / Stripe 系の "1 セクション = 1 主張" 原則。

### S0 — HERO
```
レイアウト:
  [全画面ティザー動画ループ（音なし、autoplay）]
  下半分にオーバーレイ:
    GHOST-PRINT
    魂は、あなたが印字する。
    Print your ghost.
    [Pre-MVP ウェイトリスト ›]   [仕様書を読む]
```
- 動画は副編 B（6 秒バンパー）をループ。音は出さない
- スクロールヒント: 下部に縦の細い線とキャレット ▼

### S1 — MANIFESTO
```
左カラム:
  AI に魂を奪われる時代に、
  自分の魂を、自分で印字する。

右カラム（モノスペース、小さく）:
  > Bonsai 1.7B が、あなたの 24 時間から
    意味を抽出する。 Ghost Dock を通じて、
    あなたが選んだものだけが AI に渡る。
    すべてはオフラインで完結する。
```

### S2 — THREE PILLARS
横 3 カラム、各カラムに ASCII アイコン:

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   ◉ ◉ ◉      │  │   ╱│╲        │  │   ▓▒░ → ░░░  │
│              │  │              │  │              │
│  EXTRACT     │  │  DISTILL     │  │  CHOOSE      │
│              │  │              │  │              │
│  リング・声  │  │  Bonsai 1.7B │  │  Ghost Dock を│
│  位置・心拍  │  │  が剪定し、  │  │  挿す瞬間に、│
│  を、デバイス│  │  意味を残す  │  │  所有権が    │
│  内で取得    │  │              │  │  反転する    │
└──────────────┘  └──────────────┘  └──────────────┘
```

### S2.5 — THREE COMPONENTS (Ring / Core / Shell)

3 ピラー (動詞: EXTRACT/DISTILL/CHOOSE) は **データの動き** を語る。 こちらは **物理オブジェクトと役割** を画像付きで並べる別レイヤー。

レイアウト ・ 3 カラム、 各カードに SVG イラスト + 動詞 + 説明 + ミニスペック表:

```
┌─────────────────────┬─────────────────────┬─────────────────────┐
│ [SVG: ring 3/4]     │ [SVG: PCB + bonsai] │ [SVG: 3D printer +  │
│   pulse waveform    │   importance scores │   3 shells overlap] │
│                     │                     │                     │
│  RING  ・  FEEL     │  CORE  ・  EDIT     │  SHELL  ・  DRESS   │
│  感じる              │  編集する            │  装う                │
│  ─────────────────  │  ─────────────────  │  ─────────────────  │
│  あなたの肌の上に、  │  机の上に置かれた、  │  コアを包み、 手で  │
│  24 時間置かれる。   │  小さな脳。 24 時間 │  持ち、 部屋に置く。│
│  ただ感じる。 リング│  ぶんを Bonsai が剪 │  機能は閉じる、 表現│
│  自体は計算しない。 │  定し、 GHOST.bin に│  は開ける。 STL を   │
│                     │  圧縮する。         │  公開、 印刷自由。  │
│                     │                     │                     │
│  device  COLMI R02  │  board   rev.A6     │  format  STL        │
│  uplink  BLE        │  engine  Bonsai 1.7B│  license CC-BY-SA   │
│  battery ~7 days    │  output  GHOST.bin  │  variants 4 + ∞     │
│  worn    finger/24h │  placed  desk       │  kept    hand/room  │
└─────────────────────┴─────────────────────┴─────────────────────┘
```

動詞ペアのポリシー:
- **FEEL / 感じる** ・ 受動的・身体的・常時。 リングは "聴く" 側
- **EDIT / 編集する** ・ 能動的・知的・選別。 コアは "削ぎ落とす" 側
- **DRESS / 装う** ・ 主観的・表現的・所有。 シェルは "着る" 側

3 つで身体 → 認知 → 表現の連鎖になる。 これは Three Pillars (データ流: 入力 → 処理 → 出力) と並走する物理レイヤー。

SVG ファイル:
- `assets/ring.svg` ・ 単体配布可 (プレス・別資料)
- `assets/core.svg` ・ 同上
- `assets/shell.svg` ・ 同上

すべてアニメーション付き (LED 呼吸 / 3D プリンタ・ヘッドの押出ストリーム)。 アニメ無効化は `prefers-reduced-motion` に従う実装を本番では追加。

---

### S3 — DEVICE / 3D Core + Shell ビューア （目玉）

LP のヒーロー直下に置く **動くプロダクトデモ**。 Three.js で 3D 表示。

レイアウト:
```
┌─────────────────────────────┬──────────────────────┐
│                             │   CORE  ・  rev.A6   │
│                             │   ─────────────────  │
│        [3D CANVAS]          │   SoC    RK3566      │
│                             │   RAM    4 GB        │
│       (CORE + SHELL         │   ENGINE Bonsai 1.7B │
│        ドラッグで回転)      │                      │
│                             │   SHELL  ・  あなたが │
│                             │   ─────────────────  │
│   ▼ shell variant picker     │   [Minimal]          │
│   [Min][Org][和][Naked]     │   [Organic]          │
│                             │   [和 / Bonsai]      │
│                             │   [Naked Core]       │
└─────────────────────────────┴──────────────────────┘

           "コアは閉じる。 シェルは、 あなたが決める。"
                  →  STL ダウンロードは /shells へ
```

挙動:
- マウスドラッグ ・ 自由回転。 放置で自動ゆっくり回転
- シェル切替ボタン ・ 4 種類のシェルにモーフ／フェード切替
- "Naked Core" ボタン ・ シェルが消えてコアだけ露出
- "Explode View" トグル（オプション） ・ シェルが上下にスライドして内部が見える
- 右カラムにスペック表とシェル説明が連動

シェル初期 4 バリエーション:
| 名前 | コンセプト | 想定材料 |
|------|----------|---------|
| **MINIMAL** | エッジの効いた直方体 | PLA / PETG |
| **ORGANIC** | 滑らかな卵型 | TPU / レジン |
| **和 (BONSAI)** | 木組み風・千鳥格子 | 木目 PLA / 竹フィラメント |
| **NAKED** | シェルなし、 コアの基板を露出 | 透明アクリル付属可 |

メッセージ:
> *Ghost-Print の中身は、 我々が責任を持つ。
> Ghost-Print の見た目は、 あなたが決める。*

### S3.5 — SHELL CULTURE
シェル投稿コミュニティへの誘導セクション。 小さく:
- 公式 STL は CC-BY-SA で配布
- `/shells` で投稿シェルを閲覧 / 投票 / ダウンロード
- 印刷ガイド (推奨スライサ設定 / サポート設計の注意) を併載
- 月例 "Shell of the Month" 企画

### S4 — HOW IT WORKS（5 ステップ）
横長のタイムライン図:

```
[1] WEAR        →  [2] LISTEN      →  [3] DISTILL     →  [4] PRINT       →  [5] CHOOSE
COLMI R02 を    Bonsai が音声と     重要度スコアで   GHOST.bin が       AI に渡すか、
日常装着         状況をデバイス内   剪定              生成（〜15 KB）   ポケットに戻すか
                  で記録
```

各ステップをホバーすると、関連する技術記事へリンク（/spec, /bonsai 等）。

### S5 — TRUST（4 つの約束）
モノスペース・チェックリスト風:

```
[✓]  Local-first.   Bonsai 1.7B はデバイス内で動作する。
[✓]  Selective.     何を渡すかは、毎回あなたが選ぶ。
[✓]  Open.          GHOST v0.1 は公開仕様。GitHub で読める。
[✓]  Pre-MVP.       完成前から、進捗は公開する。
```

### S6 — TIMELINE（公開できる範囲のロードマップ）
```
2026 Q2  ─── A6 GHOST v0.1 prototype  [DONE]
2026 Q2  ─── Bonsai 1.7B on OPi 3B  [DONE]
2026 Q3  ─── Ghost Dock 確定設計     [WIP]
2026 Q4  ─── Pre-MVP β 配布        [PLANNED]
2027 Q1  ─── 一般販売              [TBD]
```

### S7 — WAITLIST
シンプルなフォーム。メール一行 + 任意の役割 (Maker / Engineer / Artist / Other)。
送信後、同ページにメッセージ:

```
登録ありがとう。
GHOST v0.1 の仕様書を、いま読める:  →  /spec
```

### S8 — FOOTER
- ロゴ（小）
- セクションリンク（spec / protocol / bonsai / hardware / open）
- GitHub / Hacker News / X リンク
- "Made in [city]" 表記
- 著作権表記なし（"License: see /open"）

---

## 3. 技術ドキュメントページの様式

`/spec`, `/protocol`, `/bonsai`, `/hardware` は **同一テンプレート**:

```
左サイドバー:
  目次（H2/H3 自動生成）

メインカラム:
  - MDX で書かれた仕様書をそのまま表示
  - シンタックスハイライト（IBM Plex Mono）
  - 図版は SVG（mermaid もしくは手書き SVG）
  - 各セクションに "edit on GitHub" リンク

右カラム:
  - "このページの最終更新: 2026-05-13"
  - "関連ページ" の自動リンク
```

これは **Anthropic.com docs** や **Stripe docs** の様式に倣う。読むことが体験の一部。

---

## 4. インタラクション・モーション原則

- **ホバー時の発光**: cyan #00FFE5 を 8% opacity で
- **スクロール挙動**: スナップではなく自然スクロール。ただしヒーローからの初回スクロールだけは S1 にスナップ
- **遅延ロード**: ヒーロー動画 + S2 までを初期ロード、それ以降は遅延
- **アニメーション**: フェード + 上方 12px シフトのみ。回転やスケールは禁止
- **カーソル**: ターミナル風ブロックカーソル（オプショナル、好みに応じて）
- **ファビコン**: ASCII の `▓▒░` を 16x16 SVG で
- **OG 画像**: ティザーの CUT 9（手のひらに乗る GHOST）を 1200x630 で

---

## 5. パフォーマンス・SEO

- LCP < 1.5s（ヒーロー動画は H.265 / WebM AV1 のフォールバック）
- 動画は **<video poster>** で最初に静止画
- 全ページに JSON-LD（Product, Article）
- noindex は無し（PR 戦略は SEO ありき）
- og:title / twitter:card を全ページに

---

## 6. アクセシビリティ

サイバーパンクは "暗くて読めない" になりがち。配慮:

- 本文コントラスト: WCAG AA 以上（#E6E8EE on #050507 は十分）
- スキャンライン・CRT エフェクトに `prefers-reduced-motion` 対応
- ヒーロー動画は字幕トラック付き
- フォーカスリング: cyan の 2px 実線

---

## 7. 段階公開プラン

| フェーズ | タイミング | 公開範囲 |
|---------|----------|---------|
| α | ティザー完成日 | / + /waitlist のみ。/spec はパスワード付き |
| β | Pre-MVP β 配布開始 | + /spec, /bonsai, /open |
| 1.0 | 一般販売開始 | 全ページ + /press |

ティザー先行公開時点では **製品ページは "ある" が "売れない"** 状態にする（Pre-MVP の誠実さ）。
