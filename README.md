# AI幹事｜グループLINE導線つきMVP

グループLINEで話が進まないときに、会話を要約し、未決定事項と次に送る文案を返すBotです。  
この版には、公開用LP（ランディングページ）と「友だち追加 → グループ招待」の導線も入っています。

## できること

- Webサイト `/` に公開用LPを表示
- LPの「LINEで友だち追加」ボタンから公式アカウントへ誘導
- 友だち追加時に使い方を自動返信
- グループ招待時に使い方を自動返信
- グループで「まとめ」「未決定」「次」「決める」と送るとAIが整理

Botが参加してから受け取った発言だけを使います。過去ログは読めません。

## ファイル構成

```text
app.py                 Flaskアプリ本体。LP表示 + LINE Webhook + OpenAI要約
public/index.html      公開用ランディングページ
public/style.css       LPのスタイル
requirements.txt       依存ライブラリ
.env.example           環境変数サンプル
```

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` に以下を入れてください。

```env
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
LINE_ID=@your_line_official_account_id
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
PORT=8080
```

`LINE_ID` は LINE Official Account Manager のヘッダーなどで確認できる `@` から始まる公式アカウントIDです。

## 起動

```bash
python app.py
```

ローカルでLINEのWebhookを受けるには ngrok などで公開URLを作ります。

```bash
ngrok http 8080
```

表示されたURLにアクセスするとLPが見られます。

```text
https://xxxxx.ngrok-free.app/
```

LINE Developers Console の Webhook URL には以下を設定します。

```text
https://xxxxx.ngrok-free.app/callback
```

## LINE側で必要なこと

1. LINE DevelopersでProviderを作成
2. Messaging APIチャネルを作成
3. Channel secret と Channel access token を取得
4. Webhookを有効化
5. 「グループトーク・複数人トークへの参加を許可」をON
6. `.env` に `LINE_ID` を設定
7. サイトを公開
8. ユーザーがサイトから友だち追加
9. ユーザーがグループLINEでBotを招待
10. グループで「まとめ」と送る

## 公開するなら

### Render / Railway / Fly.io などに載せる場合

- Start command: `python app.py`
- 環境変数に `.env` と同じ値を設定
- 公開URLの `/callback` をLINE DevelopersのWebhook URLに設定
- 公開URLの `/` をLPとして使う

## ユーザー導線

LP上の流れは以下です。

1. 「LINEで友だち追加」
2. LINE公式アカウントを追加
3. グループLINEの「メンバー招待」からAI幹事を招待
4. グループで「まとめ」と送る

LINEの仕様上、URLを踏むだけで既存グループにBotを強制参加させることはできません。ユーザーがグループへ招待する操作が必要です。

## 使えるコマンド

```text
まとめ
未決定
次
決める
ヘルプ
```

## 注意

- 会話内容をAIに送るため、グループメンバーへの説明と同意が必要です。
- 本番化するなら、履歴保存をDB化し、グループごとの同意状態・削除機能・ログ保持期間を入れてください。
- このMVPはメモリ保存なので、サーバー再起動で履歴が消えます。
- グループトークに同時参加できるLINE公式アカウントは1つだけです。
