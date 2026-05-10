import base64
import hashlib
import hmac
import os
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, abort, request, render_template_string
from openai import OpenAI

load_dotenv()

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_ID = "@641ylztu"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "80"))

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    print("WARNING: LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN are not set.")

client = OpenAI()
app = Flask(__name__, static_folder="public", static_url_path="")

histories = defaultdict(lambda: deque(maxlen=MAX_HISTORY))


def verify_line_signature(body: bytes, signature: str) -> bool:
    mac = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature or "")


def reply_to_line(reply_token: str, text: str) -> None:
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    text = text[:4500]
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
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
    return "@AI幹事" in text


def help_text() -> str:
    return (
        "AI幹事です。グループの話を整理して、次に確認すべきことを問いかけます。\n\n"
        "使い方：\n"
        "・『@AI幹事 今の話をまとめて』\n"
        "・『@AI幹事 未決定事項を整理して』\n"
        "・『@AI幹事 次に決めることは？』\n\n"
        "※『まとめ』だけでは反応しません。\n"
        "※このBotが参加してからの発言だけを使います。\n"
        "※会話内容をAIに送るので、グループ内で同意を取ってから使ってください。"
    )


def make_prompt(command: str, messages: list[dict]) -> str:
    transcript = "\n".join(
        f"[{m['time']}] {m['user']}: {m['text']}" for m in messages[-60:]
    )

    return f"""
あなたはグループLINEの会話を進める「AI幹事」です。

あなたの役割は、会話全体をそのまま要約することではありません。
会話ログの中から、意思決定に必要な情報だけを取捨選択し、グループ全員が次に何を決めればよいか分かるように整理してください。

ユーザーの依頼/コマンド:
{command}

会話ログ:
{transcript}

重要な方針:
- 日本語で返す
- 300〜700字程度
- 雑談、冗談、相づち、脱線した話題は基本的に省く
- 会話ログから分かることだけを書く
- 決定に関係する情報を優先する
- 断定しすぎない
- 参加者を責めない
- 未回答者を名指しで責めない
- 「送る文案」は出さない
- 最後は、グループ全員に向けた確認質問にする
- 質問は3つ以内
- 質問はYes/No、選択式、短く答えられる形にする
- 公式LINE自身がグループ内で直接問いかける文章にする

出力形式:

【決まっていること】
...

【まだ決まっていないこと】
...

【次に決めること】
...

【みんなに確認したいこと】
・...
・...
・...
""".strip()


def ai_summarize(command: str, messages: list[dict]) -> str:
    useful_messages = [
        m for m in messages
        if "@AI幹事" not in m["text"]
    ]

    if not useful_messages:
        return (
            "まだ整理できる会話がありません。\n"
            "日程・場所・目的・候補などが少し出てきたら、@AI幹事 と呼んでください。"
        )

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=make_prompt(command, useful_messages),
    )
    return response.output_text.strip()


@app.get("/")
def landing_page():
    index_path = Path(app.static_folder) / "index.html"

    if not index_path.exists():
        return (
            "<h1>AI幹事</h1>"
            "<p>グループLINEの話し合いを整理するAIです。</p>"
            f"<p><a href='https://line.me/R/ti/p/%40641ylztu'>LINEで友だち追加</a></p>"
        )

    html = index_path.read_text(encoding="utf-8")

    encoded = LINE_ID.replace("@", "%40")
    add_friend_url = f"https://line.me/R/ti/p/{encoded}"
    recommend_url = f"https://line.me/R/nv/recommendOA/{LINE_ID}"

    return render_template_string(
        html,
        line_id=LINE_ID,
        add_friend_url=add_friend_url,
        recommend_url=recommend_url,
    )


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

        if event_type == "follow":
            reply_to_line(event["replyToken"], help_text())
            continue

        if event_type in ["join", "memberJoined"]:
            if event.get("replyToken"):
                reply_to_line(
                    event["replyToken"],
                    "招待ありがとうございます。\n\n" + help_text(),
                )
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
            dt = datetime.fromtimestamp(
                ts / 1000,
                tz=timezone.utc,
            ).astimezone(timezone(timedelta(hours=9)))
            time_str = dt.strftime("%m/%d %H:%M")
        else:
            time_str = "unknown"

        histories[key].append(
            {
                "time": time_str,
                "user": user_id[-6:],
                "text": text,
            }
        )

        if is_command(text):
            try:
                reply = ai_summarize(text, list(histories[key]))
            except Exception as e:
                print(f"AI summarize error: {type(e).__name__}: {e}")
                reply = (
                    "すみません、今はうまく整理できませんでした。\n"
                    "少し時間を空けて、もう一度 @AI幹事 と呼んでください。"
                )

            reply_to_line(event["replyToken"], reply)

    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
