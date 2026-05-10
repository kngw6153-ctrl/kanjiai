import base64
import hashlib
import hmac
import os
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, abort, request, send_from_directory, render_template_string
from openai import OpenAI

load_dotenv()

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_ID = os.environ.get("LINE_ID", "")  # 例: @yourbot
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "80"))

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    print("WARNING: LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN are not set.")

client = OpenAI()
app = Flask(__name__, static_folder="public", static_url_path="")

# source_id -> deque of message records
histories = defaultdict(lambda: deque(maxlen=MAX_HISTORY))


def verify_line_signature(body: bytes, signature: str) -> bool:
    mac = hmac.new(LINE_CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature or "")


def reply_to_line(reply_token: str, text: str) -> None:
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    text = text[:4500]
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    r.raise_for_status()


def source_key(event: dict) -> str:
    src = event.get("source", {})
    if src.get("groupId"):
        return f"group:{src['groupId']}"
    if src.get("roomId"):
        return f"room:{src['roomId']}"
    if src.get("userId"):
        return f"user:{src['userId']}"
    return "unknown"


def is_command(text: str) -> bool:
    t = text.strip().lower()
    triggers = ["まとめ", "要約", "次", "未決定", "決める", "help", "ヘルプ", "todo", "todo整理"]
    return t.startswith("/ai") or t.startswith("@ai") or any(k in t for k in triggers)


def help_text() -> str:
    return (
        "AI幹事です。グループの話を整理します。\n\n"
        "使い方：\n"
        "・『まとめ』：決まったこと/未決定/次アクションを整理\n"
        "・『未決定』：決まっていない論点だけ整理\n"
        "・『次』：そのまま送れる文案を作成\n"
        "・『決める』：仮決定案を作成\n\n"
        "※このBotが参加してからの発言だけを使います。\n"
        "※会話内容をAIに送るので、グループ内で同意を取ってから使ってください。"
    )


def make_prompt(command: str, messages: list[dict]) -> str:
    transcript = "\n".join(
        f"[{m['time']}] {m['user']}: {m['text']}" for m in messages[-60:]
    )
    return f"""
あなたはグループLINEの会話を進める「AI幹事」です。
目的は、参加者を責めずに、決まっていること・未決定事項・次の一手を短く整理することです。

ユーザーの依頼/コマンド:
{command}

会話ログ:
{transcript}

出力ルール:
- 日本語で返す
- 300〜700字程度
- 断定しすぎず、会話ログから分かることだけを書く
- 未回答者を責めない
- 最後に、そのまま送れる「次に送る文案」を1つ付ける
- 形式は以下にする

【決まっていること】
...

【まだ決まっていないこと】
...

【次にやること】
...

【送る文案】
...
""".strip()


def ai_summarize(command: str, messages: list[dict]) -> str:
    if not messages:
        return "まだ整理できる会話がありません。日程・場所・目的などが少し出てきたら、まとめられます。"

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=make_prompt(command, messages),
    )
    return response.output_text.strip()


@app.get("/")
def landing_page():
    index_path = Path(app.static_folder) / "index.html"
    html = index_path.read_text(encoding="utf-8")
    add_friend_url = "#setup-line-id"
    recommend_url = "#setup-line-id"
    if LINE_ID:
        encoded = LINE_ID.replace("@", "%40")
        add_friend_url = f"https://line.me/R/ti/p/{encoded}"
        recommend_url = f"https://line.me/R/nv/recommendOA/{LINE_ID}"
    return render_template_string(html, line_id=LINE_ID or "@your_line_id", add_friend_url=add_friend_url, recommend_url=recommend_url)


@app.get("/health")
def healthcheck():
    return "LINE AI Kanji Bot is running."


@app.post("/callback")
def callback():
    body = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")
    if not verify_line_signature(body, signature):
        abort(403)

    payload = request.get_json(force=True)
    for event in payload.get("events", []):
        event_type = event.get("type")

        # 友だち追加時の案内
        if event_type == "follow":
            reply_to_line(event["replyToken"], help_text())
            continue

        # グループ参加時の案内
        if event_type in ["join", "memberJoined"]:
            if event.get("replyToken"):
                reply_to_line(event["replyToken"], "招待ありがとうございます。\n\n" + help_text())
            continue

        if event_type != "message":
            continue
        message = event.get("message", {})
        if message.get("type") != "text":
            continue

        text = message.get("text", "").strip()
        key = source_key(event)
        user_id = event.get("source", {}).get("userId", "unknown")
        ts = event.get("timestamp")
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=9)))
            time_str = dt.strftime("%m/%d %H:%M")
        else:
            time_str = "unknown"

        histories[key].append({"time": time_str, "user": user_id[-6:], "text": text})

        if is_command(text):
            if text.lower() in ["help", "ヘルプ", "/ai help", "@ai help"]:
                reply = help_text()
            else:
                try:
                    reply = ai_summarize(text, list(histories[key]))
                except Exception as e:
                    reply = f"整理に失敗しました。設定やAPIキーを確認してください。\nerror: {type(e).__name__}"
            reply_to_line(event["replyToken"], reply)

    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
